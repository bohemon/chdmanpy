"""Smoke coverage for both package entry points."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from chdmanpy import __version__


def test_test_suite_imports_the_src_package() -> None:
    assert __version__ == "0.1.0"


@pytest.mark.parametrize(
    "command",
    [
        [sys.executable, "-m", "chdmanpy", "--version"],
        [
            sys.executable,
            "-c",
            "from chdmanpy.cli import entrypoint; entrypoint()",
            "--version",
        ],
    ],
)
def test_package_entry_points_report_version(
    command: list[str], tmp_path: Path
) -> None:
    completed = subprocess.run(
        command,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=10,
    )

    assert completed.returncode == 0
    assert completed.stdout == "chdmanpy 0.1.0\n"
    assert completed.stderr == ""
