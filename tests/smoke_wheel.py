"""Inspect built artifacts and smoke-test a clean wheel installation."""

from __future__ import annotations

import os
import posixpath
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

EXPECTED_VERSION = "chdmanpy 0.1.0\n"
EXPECTED_SDIST_DOCUMENTS = {
    "README.md",
    "README.ja.md",
    "docs/arcshuttle-schema-v2.ja.md",
    "docs/arcshuttle-schema-v2.md",
    "docs/schema-v1.md",
    "docs/testing.md",
    "docs/usage.ja.md",
    "docs/usage.md",
    "install-chdman.ps1",
}
EXPECTED_WHEEL_DOCUMENTS = {
    "chdmanpy/README.ja.md",
    "chdmanpy/README.md",
    "chdmanpy/docs/arcshuttle-schema-v2.ja.md",
    "chdmanpy/docs/arcshuttle-schema-v2.md",
    "chdmanpy/docs/schema-v1.md",
    "chdmanpy/docs/testing.md",
    "chdmanpy/docs/usage.ja.md",
    "chdmanpy/docs/usage.md",
}
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")


def _invoke(
    command: list[str], *, cwd: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )


def _run(command: list[str], *, cwd: Path, environment: dict[str, str]) -> str:
    completed = _invoke(command, cwd=cwd, environment=environment)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed with exit {completed.returncode}: {command!r}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed.stdout


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    distribution_directory = Path(arguments[0] if arguments else "dist").resolve()
    wheels = sorted(distribution_directory.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one wheel in {distribution_directory}")
    wheel = wheels[0]
    if not wheel.name.endswith("-py3-none-any.whl"):
        raise RuntimeError(f"expected a universal Python wheel, found {wheel.name}")
    with zipfile.ZipFile(wheel) as archive:
        wheel_members = set(archive.namelist())
        missing_wheel_documents = EXPECTED_WHEEL_DOCUMENTS - wheel_members
        for document in EXPECTED_WHEEL_DOCUMENTS - missing_wheel_documents:
            text = archive.read(document).decode("utf-8")
            for raw_target in MARKDOWN_LINK_RE.findall(text):
                target = raw_target.split("#", maxsplit=1)[0]
                if not target or "://" in target or target.startswith("mailto:"):
                    continue
                resolved = posixpath.normpath(
                    posixpath.join(posixpath.dirname(document), target)
                )
                if resolved.startswith("../") or resolved not in wheel_members:
                    raise RuntimeError(
                        f"broken wheel-local link in {document}: {raw_target}"
                    )
    if missing_wheel_documents:
        raise RuntimeError(
            "wheel is missing required documentation: "
            + ", ".join(sorted(missing_wheel_documents))
        )
    source_distributions = sorted(distribution_directory.glob("*.tar.gz"))
    if len(source_distributions) != 1:
        raise RuntimeError(
            f"expected exactly one sdist in {distribution_directory}, "
            f"found {len(source_distributions)}"
        )
    with tarfile.open(source_distributions[0], mode="r:gz") as archive:
        relative_members = {
            "/".join(Path(member.name).parts[1:]) for member in archive.getmembers()
        }
    missing_documents = EXPECTED_SDIST_DOCUMENTS - relative_members
    if missing_documents:
        raise RuntimeError(
            "sdist is missing required documentation: "
            + ", ".join(sorted(missing_documents))
        )

    with tempfile.TemporaryDirectory(prefix="chdmanpy-wheel-smoke-") as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment["PYTHONNOUSERSITE"] = "1"
        virtual_environment = root / "venv"
        _run(
            [sys.executable, "-m", "venv", str(virtual_environment)],
            cwd=root,
            environment=environment,
        )
        scripts = virtual_environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        console = scripts / ("chdmanpy.exe" if os.name == "nt" else "chdmanpy")
        _run(
            [
                str(python),
                "-m",
                "pip",
                "--disable-pip-version-check",
                "install",
                "--no-deps",
                "--no-index",
                str(wheel),
            ],
            cwd=root,
            environment=environment,
        )
        module_version = _run(
            [str(python), "-m", "chdmanpy", "--version"],
            cwd=root,
            environment=environment,
        )
        console_version = _run(
            [str(console), "--version"],
            cwd=root,
            environment=environment,
        )
        module_plan_help = _run(
            [str(python), "-m", "chdmanpy", "plan", "--help"],
            cwd=root,
            environment=environment,
        )
        console_plan_help = _run(
            [str(console), "plan", "--help"],
            cwd=root,
            environment=environment,
        )
        preset_smoke = (
            "from chdmanpy.config import load_preset;"
            "names=('others','ps2','psp');"
            "formats={name:set(load_preset(name)) for name in names};"
            "expected={'others':{'.cue'},'ps2':{'.cue','.iso'},'psp':{'.iso'}};"
            "raise SystemExit(0 if formats == expected else repr(formats))"
        )
        _run(
            [str(python), "-c", preset_smoke],
            cwd=root,
            environment=environment,
        )
        if module_version != EXPECTED_VERSION or console_version != EXPECTED_VERSION:
            raise RuntimeError(
                "installed entry points returned unexpected versions: "
                f"module={module_version!r}, console={console_version!r}"
            )
        if module_plan_help != console_plan_help:
            raise RuntimeError(
                "installed console and module entry points expose different plan help"
            )
        source = root / "disc 日本語.iso"
        source.write_bytes(b"iso")
        processing_arguments = [
            "plan",
            str(source),
            "--preset",
            "ps2",
            "--output-dir",
            str(root / "output"),
        ]
        module_plan = _invoke(
            [str(python), "-m", "chdmanpy", *processing_arguments],
            cwd=root,
            environment=environment,
        )
        console_plan = _invoke(
            [str(console), *processing_arguments],
            cwd=root,
            environment=environment,
        )
        if (
            module_plan.returncode,
            module_plan.stdout,
            module_plan.stderr,
        ) != (
            console_plan.returncode,
            console_plan.stdout,
            console_plan.stderr,
        ) or module_plan.returncode != 0:
            raise RuntimeError(
                "installed console and module processing paths differ:\n"
                f"module={module_plan!r}\nconsole={console_plan!r}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
