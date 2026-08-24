from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from conftest import FakeChdman

from chdmanpy.chdman import (
    build_argv,
    discover_chdman,
    spawn_chdman,
    validate_operation_options,
)
from chdmanpy.config import RuntimeConfig
from chdmanpy.errors import ChdmanError


def _probe_result() -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout=b"chdman - MAME Compressed Hunks of Data manager\n",
        stderr=b"",
    )


@pytest.mark.parametrize(
    "explicit, environment, runtime, expected_source, expected_program",
    [
        (
            "explicit",
            {"CHDMANPY_CHDMAN": "environment"},
            "config",
            "explicit",
            "explicit",
        ),
        (
            None,
            {"CHDMANPY_CHDMAN": "environment"},
            "config",
            "environment",
            "environment",
        ),
        (None, {}, RuntimeConfig("config"), "configuration", "config"),
        (None, {}, None, "PATH", "chdman"),
    ],
)
def test_discovery_precedence_and_real_nonzero_help_exit(
    explicit: str | None,
    environment: dict[str, str],
    runtime: RuntimeConfig | str | None,
    expected_source: str,
    expected_program: str,
) -> None:
    resolved_directory = Path.cwd() / "resolved"
    with (
        patch(
            "chdmanpy.chdman.shutil.which",
            side_effect=lambda value, path=None: str(resolved_directory / value),
        ),
        patch("chdmanpy.chdman.subprocess.run", return_value=_probe_result()) as probe,
    ):
        executable = discover_chdman(
            explicit=explicit,
            runtime=runtime,
            environment=environment,
        )

    assert executable.source == expected_source
    resolved_program = str(resolved_directory / expected_program)
    assert executable.command == (resolved_program,)
    assert "Compressed Hunks" in executable.description
    call = probe.call_args
    assert call.args[0] == [resolved_program, "-help"]
    assert call.kwargs["shell"] is False
    assert call.kwargs["stdin"] is subprocess.DEVNULL
    assert call.kwargs["capture_output"] is True


def test_probe_rejects_non_chdman_output() -> None:
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=b"different utility\n", stderr=b""
    )
    with (
        patch("chdmanpy.chdman.shutil.which", return_value="/tool"),
        patch("chdmanpy.chdman.subprocess.run", return_value=completed),
        pytest.raises(ChdmanError, match="signature"),
    ):
        discover_chdman(explicit="tool", environment={})


@pytest.mark.parametrize(
    "operation, options",
    [
        ("copy", []),
        ("createcd", ["-i", "source"]),
        ("createdvd", ["--output=dest"]),
        ("createcd", ["--force"]),
        ("createcd", [""]),
        ("createcd", ["value\0tail"]),
        ("createcd", "-c zstd"),
    ],
)
def test_operation_options_reject_unsafe_values(
    operation: str, options: object
) -> None:
    with pytest.raises(ChdmanError):
        validate_operation_options(operation, options)  # type: ignore[arg-type]


def test_build_and_spawn_use_exact_safe_argument_order_and_closed_stdin(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    source = tmp_path / "入力 disc.iso"
    staging = tmp_path / ".出力 disc.chd.staging"
    source.write_bytes(b"iso")
    arguments = [
        "createdvd",
        "-c",
        "zstd",
        "-i",
        str(source),
        "-o",
        str(staging),
    ]
    fake = fake_chdman_factory(
        {
            "by_input": {
                str(source): {
                    "expected_args": arguments,
                    "expected_stdin_eof": True,
                    "probe_stdin": True,
                }
            }
        }
    )
    executable = discover_chdman(
        explicit=fake.command,
        environment=fake.environment,
    )
    assert build_argv(executable, "createdvd", ["-c", "zstd"], source, staging) == [
        *fake.command,
        *arguments,
    ]
    log_path = tmp_path / "job.log"
    with log_path.open("xb") as log:
        process = spawn_chdman(
            executable,
            "createdvd",
            ["-c", "zstd"],
            source,
            staging,
            log=log,
            environment=fake.environment,
            cwd=tmp_path,
        )
        assert process.wait(timeout=10) == 0

    assert staging.read_bytes().startswith(b"MComprHD")
    record = fake.read_record()
    assert record["args"] == arguments
    assert record["stdin_eof"] is True
    assert record["cwd"] == str(tmp_path)
    assert executable.command[0] == sys.executable


def test_command_rejects_empty_and_missing_programs() -> None:
    with pytest.raises(ChdmanError, match="empty"):
        discover_chdman(explicit=[], environment={})
    with pytest.raises(ChdmanError, match="not found"):
        discover_chdman(explicit="definitely-missing-chdman", environment={}, path="")


@pytest.mark.parametrize(
    "explicit, environment, timeout",
    [
        (23, {}, 15.0),
        (None, {"CHDMANPY_CHDMAN": ""}, 15.0),
        ("chdman", {}, 0),
        ("chdman", {}, float("nan")),
    ],
)
def test_discovery_rejects_invalid_commands_and_timeouts(
    explicit: object, environment: dict[str, str], timeout: float
) -> None:
    with pytest.raises(ChdmanError):
        discover_chdman(
            explicit=explicit,  # type: ignore[arg-type]
            environment=environment,
            probe_timeout=timeout,
        )
