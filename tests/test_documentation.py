from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from chdmanpy.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = (
    ROOT / "README.md",
    ROOT / "README.ja.md",
    *sorted((ROOT / "docs").glob("*.md")),
)
USAGE_DOCUMENTS = (ROOT / "docs" / "usage.md", ROOT / "docs" / "usage.ja.md")
LINK_RE = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")


def _public_options(parser: argparse.ArgumentParser) -> set[str]:
    options: set[str] = set()
    for action in parser._actions:
        options.update(
            option for option in action.option_strings if option.startswith("--")
        )
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                options.update(_public_options(child))
    return options


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: path.name)
def test_local_markdown_links_resolve_inside_repository(document: Path) -> None:
    text = document.read_text(encoding="utf-8")
    for raw_target in LINK_RE.findall(text):
        target = raw_target.split("#", maxsplit=1)[0]
        if not target or "://" in target or target.startswith("mailto:"):
            continue
        resolved = (document.parent / target).resolve()
        assert resolved.is_relative_to(ROOT), f"link escapes repository: {raw_target}"
        assert resolved.exists(), f"missing link target in {document}: {raw_target}"


@pytest.mark.parametrize("document", USAGE_DOCUMENTS, ids=lambda path: path.name)
def test_usage_manual_covers_the_complete_public_cli(document: Path) -> None:
    text = document.read_text(encoding="utf-8")
    public_options = _public_options(build_parser()) - {"--help", "--version"}
    missing = sorted(option for option in public_options if option not in text)
    assert not missing, f"{document.name} omits public options: {missing}"

    required_contract_terms = (
        "pipx install chdmanpy",
        "python -m chdmanpy",
        "chdmanpy plan",
        "chdmanpy run",
        "chdmanpy convert",
        "CHDMANPY_CHDMAN",
        "ArcShuttle",
        "PowerShell 7",
        "pipefail",
        "```bash\nset -o pipefail",
        "$chdmanpyStatus = $LASTEXITCODE",
        "exit $chdmanpyStatus",
        "stdout",
        "stderr",
        "log_path",
        ".failed",
        "install-chdman.ps1",
        "python chdmanpy.py",
        "--temp-dir",
    )
    missing_terms = [term for term in required_contract_terms if term not in text]
    assert not missing_terms, f"{document.name} omits contract terms: {missing_terms}"

    aligned_configuration_terms = (
        "| `others` | `.cue` | `createcd` |",
        "| `ps2` | `.cue` | `createcd` |",
        "| `ps2` | `.iso` | `createdvd -c zlib` |",
        "| `psp` | `.iso` | `createdvd -hs 2048 -c zstd` |",
        "CHDMANPY_OUTPUT_DIR",
        "CHDMANPY_EXISTING",
        "CHDMANPY_PRIORITY",
        "CHDMANPY_PRESET",
        "CHDMANPY_CHDMAN",
        "| `CHDMANPY_OUTPUT_DIR` | `<path>`:",
        "| `CHDMANPY_EXISTING` | `fail` / `skip` / `rename`",
        "| `CHDMANPY_PRIORITY` | `-2147483648..2147483647`:",
        "| `CHDMANPY_PRESET` | `others` / `ps2` / `psp`",
        "| `CHDMANPY_CHDMAN` | `<executable-name-or-path>`:",
        "shell fragment",
    )
    missing_configuration = [
        term for term in aligned_configuration_terms if term not in text
    ]
    assert not missing_configuration, (
        f"{document.name} omits aligned configuration: {missing_configuration}"
    )


def test_readmes_describe_the_packaged_pipeline_interface() -> None:
    for document in (ROOT / "README.md", ROOT / "README.ja.md"):
        text = document.read_text(encoding="utf-8")
        assert "pipx install chdmanpy" in text
        assert "chdmanpy convert" in text
        assert "--arcshuttle-results -" in text
        assert "python ./chdmanpy.py" not in text
        assert "--temp-dir" not in text
        assert "Recursively search <input_dir>" not in text
        assert "ZIP files in advance" not in text
        assert "unzip_zip_files" not in text
        assert "_extracted" not in text


def test_metadata_readme_uses_absolute_links_for_pypi() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    targets = LINK_RE.findall(text)
    assert targets
    assert all(target.startswith("https://") for target in targets)
