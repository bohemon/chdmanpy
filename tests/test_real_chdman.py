"""Optional smoke test for a locally installed real CHDMAN."""

from __future__ import annotations

import shutil
import subprocess

import pytest


@pytest.mark.real_chdman
def test_real_chdman_help() -> None:
    executable = shutil.which("chdman")
    if executable is None:
        pytest.skip("real CHDMAN is not installed")

    completed = subprocess.run(
        [executable, "-help"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=15,
    )

    # CHDMAN treats help as a diagnostic request and currently returns 1.
    assert completed.returncode == 1
    assert completed.stdout or completed.stderr
