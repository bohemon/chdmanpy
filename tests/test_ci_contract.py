"""Regression tests for the cross-platform CI contract."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_repository_text_files_use_lf_on_every_platform() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()

    assert "* text=auto eol=lf" in attributes
