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
INSTALL_DOCUMENTS = (ROOT / "README.md", ROOT / "README.ja.md", *USAGE_DOCUMENTS)
LINK_RE = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")
TAGGED_INSTALL = (
    'pipx install "chdmanpy @ git+https://github.com/bohemon/chdmanpy.git@v0.1.0"'
)
DOCUMENTED_EXAMPLES = (
    (
        "chdmanpy plan ./input --output-dir ./chd --preset ps2 >jobs.jsonl",
        ["plan", "./input", "--output-dir", "./chd", "--preset", "ps2"],
    ),
    (
        "chdmanpy run --manifest jobs.jsonl >results.jsonl",
        ["run", "--manifest", "jobs.jsonl"],
    ),
    (
        "chdmanpy convert ./input --output-dir ./chd --preset ps2 >results.jsonl",
        ["convert", "./input", "--output-dir", "./chd", "--preset", "ps2"],
    ),
    (
        "chdmanpy convert --arcshuttle-results - --output-dir ./chd --preset ps2",
        [
            "convert",
            "--arcshuttle-results",
            "-",
            "--output-dir",
            "./chd",
            "--preset",
            "ps2",
        ],
    ),
)


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
        "pipx install ./chdmanpy-0.1.0-py3-none-any.whl",
        "pipx upgrade chdmanpy",
        "pipx uninstall chdmanpy",
        "python -m pip install --upgrade chdmanpy",
        "python -m pip uninstall chdmanpy",
        "virtual environment",
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
        "$pipelineSucceeded = $?",
        "if (-not $pipelineSucceeded -and $chdmanpyStatus -eq 0) { exit 1 }",
        "exit $chdmanpyStatus",
        "stdout",
        "stderr",
        "log_path",
        ".failed",
        "install-chdman.ps1",
        "python chdmanpy.py",
        "--temp-dir",
        "`success`",
        "`warning`",
        "`failed`",
        "`skipped`",
        "`interrupted`",
        "EOF",
        "`summary`",
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


@pytest.mark.parametrize(("documented", "arguments"), DOCUMENTED_EXAMPLES)
def test_documented_command_examples_parse(
    documented: str, arguments: list[str]
) -> None:
    for document in USAGE_DOCUMENTS:
        assert documented in document.read_text(encoding="utf-8")
    parsed = build_parser().parse_args(arguments)
    assert parsed.command == arguments[0]


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
        assert "release-notes-0.1.0.md" in text


@pytest.mark.parametrize("document", INSTALL_DOCUMENTS, ids=lambda path: path.name)
def test_installation_documents_pin_the_tagged_source_url(document: Path) -> None:
    text = document.read_text(encoding="utf-8")
    assert text.count(TAGGED_INSTALL) == 1
    assert "chdmanpy.git@main" not in text


def test_metadata_readme_uses_absolute_links_for_pypi() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    targets = LINK_RE.findall(text)
    assert targets
    assert all(target.startswith("https://") for target in targets)


def test_release_notes_cover_release_contract_and_legacy_examples_are_removed() -> None:
    release_notes = (ROOT / "docs" / "release-notes-0.1.0.md").read_text(
        encoding="utf-8"
    )
    required_terms = (
        "chdmanpy 0.1.0",
        "Windows and Linux",
        "Python 3.11",
        "python chdmanpy.py",
        "[options]",
        "ArcShuttle",
        "external prerequisite",
        "without destructively overwriting",
        "Limitations",
        "hatch run check",
        "hatch build",
        "required",
    )
    missing = [term for term in required_terms if term not in release_notes]
    assert not missing, f"release notes omit required terms: {missing}"
    for filename in ("others.toml", "ps2.toml", "psp.toml"):
        assert not (ROOT / filename).exists(), f"legacy root preset remains: {filename}"
