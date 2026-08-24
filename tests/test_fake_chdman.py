"""Contract tests for the reusable deterministic CHDMAN stand-in."""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeChdman


def _run(
    fake: FakeChdman,
    arguments: list[str],
    *,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*fake.command, *arguments],
        cwd=cwd,
        env=fake.environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=10,
    )


def test_help_records_exact_invocation(
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    fake = fake_chdman_factory({"expected_args": ["-help"]})

    completed = _run(fake, ["-help"])

    assert completed.returncode == 1
    assert "Compressed Hunks of Data manager (fake)" in completed.stdout
    assert completed.stderr == ""
    assert fake.read_record() == {
        "args": ["-help"],
        "cwd": str(Path.cwd()),
        "exit_code": 1,
        "input_path": None,
        "mismatches": [],
        "output_exists": False,
        "output_path": None,
        "pid": fake.read_record()["pid"],
        "received_signal": None,
        "state": "completed",
        "stdin_eof": None,
    }


def test_operation_help_models_real_nonzero_exit(
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    arguments = ["help", "createcd"]
    fake = fake_chdman_factory({"expected_args": arguments})

    completed = _run(fake, arguments)

    assert completed.returncode == 1
    assert "Compressed Hunks of Data manager (fake)" in completed.stdout
    assert fake.read_record()["exit_code"] == 1


def test_createcd_observes_paths_cwd_stdin_logs_and_staging(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    working_directory = tmp_path / "作業 directory"
    working_directory.mkdir()
    source = working_directory / "入力 disc.cue"
    source.write_text('FILE "track.bin" BINARY\n', encoding="utf-8")
    staging = working_directory / ".出力 disc.chd.staging"
    final = working_directory / "出力 disc.chd"
    arguments = [
        "createcd",
        "-i",
        str(source),
        "-o",
        str(staging),
        "-c",
        "zstd",
    ]
    fake = fake_chdman_factory(
        {
            "expected_args": arguments,
            "expected_cwd": str(working_directory),
            "expected_stdin_eof": True,
            "probe_stdin": True,
            "stdout": "compression complete\n",
            "stderr": "diagnostic line\n",
            "output_text": "deterministic CHD bytes",
        }
    )

    completed = _run(fake, arguments, cwd=working_directory)

    assert completed.returncode == 0
    assert completed.stdout == "compression complete\n"
    assert completed.stderr == "diagnostic line\n"
    assert staging.read_text(encoding="utf-8") == "deterministic CHD bytes"
    assert not final.exists()
    record = fake.read_record()
    assert record["args"] == arguments
    assert record["cwd"] == str(working_directory)
    assert record["input_path"] == str(source)
    assert record["output_path"] == str(staging)
    assert record["output_exists"] is True
    assert record["stdin_eof"] is True
    assert record["stdin_bytes_read"] == 0
    assert record["state"] == "completed"
    assert record["mismatches"] == []


def test_createdvd_can_emit_a_successful_warning(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    source = tmp_path / "source.iso"
    destination = tmp_path / "destination.chd"
    source.write_bytes(b"iso")
    arguments = ["createdvd", "-i", str(source), "-o", str(destination)]
    fake = fake_chdman_factory(
        {
            "expected_args": arguments,
            "warning": "fake warning",
        }
    )

    completed = _run(fake, arguments)

    assert completed.returncode == 0
    assert completed.stderr == "fake warning\n"
    output = destination.read_bytes()
    assert len(output) == 124
    assert output[:8] == b"MComprHD"
    assert int.from_bytes(output[8:12], "big") == 124
    assert int.from_bytes(output[12:16], "big") == 5
    assert fake.read_record()["exit_code"] == 0


def test_failure_exit_does_not_create_an_output(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    source = tmp_path / "bad.iso"
    destination = tmp_path / "bad.chd"
    source.write_bytes(b"bad")
    arguments = ["createdvd", "-i", str(source), "-o", str(destination)]
    fake = fake_chdman_factory(
        {
            "create_output": False,
            "exit_code": 23,
            "expected_args": arguments,
            "stderr": "deterministic failure\n",
        }
    )

    completed = _run(fake, arguments)

    assert completed.returncode == 23
    assert completed.stderr == "deterministic failure\n"
    assert not destination.exists()
    assert fake.read_record()["output_exists"] is False


def test_argument_mismatch_has_a_distinct_failure_exit(
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    fake = fake_chdman_factory({"expected_args": ["-help", "unexpected"]})

    completed = _run(fake, ["-help"])

    assert completed.returncode == 97
    assert "fake chdman assertion: args differ" in completed.stderr
    assert len(fake.read_record()["mismatches"]) == 1


@pytest.mark.skipif(
    os.name == "nt" and not hasattr(signal, "SIGBREAK"), reason="SIGBREAK unavailable"
)
def test_delayed_process_records_interruption(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    source = tmp_path / "interrupt.iso"
    staging = tmp_path / ".interrupt.chd.staging"
    source.write_bytes(b"iso")
    arguments = ["createdvd", "-i", str(source), "-o", str(staging)]
    fake = fake_chdman_factory(
        {
            "delay_seconds": 30,
            "expected_args": arguments,
            "partial_output_text": "partial data",
        }
    )
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        [*fake.command, *arguments],
        env=fake.environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=creation_flags,
    )
    try:
        running_record = fake.wait_until_running()
        assert running_record["output_exists"] is True
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
            expected_signal = "SIGBREAK"
        else:
            process.send_signal(signal.SIGTERM)
            expected_signal = "SIGTERM"
        process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)

    assert process.returncode == 130
    record = fake.read_record()
    assert record["state"] == "interrupted"
    assert record["received_signal"] == expected_signal
    assert record["exit_code"] == 130
    assert staging.read_text(encoding="utf-8") == "partial data"
