"""Inspect release artifacts and exercise an isolated installed wheel."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import posixpath
import re
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any

VERSION = "0.1.0"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION_LINE = f"chdmanpy {VERSION}\n"
EXPECTED_PACKAGE_FILES = {
    "chdmanpy/__init__.py",
    "chdmanpy/__main__.py",
    "chdmanpy/arcshuttle.py",
    "chdmanpy/chdman.py",
    "chdmanpy/cli.py",
    "chdmanpy/config.py",
    "chdmanpy/errors.py",
    "chdmanpy/input.py",
    "chdmanpy/jsonl.py",
    "chdmanpy/manifest.py",
    "chdmanpy/planner.py",
    "chdmanpy/results.py",
    "chdmanpy/runner.py",
    "chdmanpy/presets/__init__.py",
    "chdmanpy/presets/others.toml",
    "chdmanpy/presets/ps2.toml",
    "chdmanpy/presets/psp.toml",
}
EXPECTED_SDIST_DOCUMENTS = {
    "README.md",
    "README.ja.md",
    "docs/arcshuttle-schema-v2.ja.md",
    "docs/arcshuttle-schema-v2.md",
    "docs/release-notes-0.1.0.md",
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
    "chdmanpy/docs/release-notes-0.1.0.md",
    "chdmanpy/docs/schema-v1.md",
    "chdmanpy/docs/testing.md",
    "chdmanpy/docs/usage.ja.md",
    "chdmanpy/docs/usage.md",
}
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")


def _validate_wheel_documents(
    archive: zipfile.ZipFile, wheel_members: set[str]
) -> None:
    missing = EXPECTED_WHEEL_DOCUMENTS - wheel_members
    if missing:
        raise RuntimeError(
            "wheel is missing required documentation: " + ", ".join(sorted(missing))
        )
    for document in EXPECTED_WHEEL_DOCUMENTS:
        packaged = archive.read(document)
        source = PROJECT_ROOT / document.removeprefix("chdmanpy/")
        if packaged != source.read_bytes():
            raise RuntimeError(
                f"wheel document differs from the repository source: {document!r}"
            )
        text = packaged.decode("utf-8", errors="strict")
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


UPSTREAM_STATUSES = ("success", "warning", "failed", "skipped", "interrupted")
RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "record_type",
        "run_id",
        "job_id",
        "plan_index",
        "status",
        "source_path",
        "output_path",
        "staging_path",
        "log_path",
        "chdman_exit_code",
        "started_at",
        "finished_at",
        "duration_ms",
        "error",
        "warnings",
    }
)
SUMMARY_FIELDS = frozenset(
    {
        "schema_version",
        "record_type",
        "run_id",
        "total",
        *UPSTREAM_STATUSES,
        "duration_ms",
    }
)
SHORT_HEX_RE = re.compile(r"[0-9a-f]{24}\Z")
RFC3339_UTC_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z\Z"
)
EXPECTED_CLASSIFIERS = (
    "Development Status :: 3 - Alpha",
    "Environment :: Console",
    "License :: OSI Approved :: MIT License",
    "Operating System :: Microsoft :: Windows",
    "Operating System :: POSIX :: Linux",
    "Programming Language :: Python :: 3 :: Only",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Programming Language :: Python :: 3.14",
)
EXPECTED_PROJECT_URLS = (
    "Homepage, https://github.com/bohemon/chdmanpy",
    "Documentation, https://github.com/bohemon/chdmanpy/blob/main/docs/usage.md",
    "Issues, https://github.com/bohemon/chdmanpy/issues",
    "Source, https://github.com/bohemon/chdmanpy",
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def _execute(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    input_bytes: bytes | None = None,
) -> CommandResult:
    run_options: dict[str, Any] = {
        "cwd": cwd,
        "env": environment,
        "capture_output": True,
        "check": False,
        "timeout": 60,
    }
    if input_bytes is None:
        run_options["stdin"] = subprocess.DEVNULL
    else:
        run_options["input"] = input_bytes
    completed = subprocess.run(
        command,
        **run_options,
    )
    return CommandResult(
        completed.returncode,
        completed.stdout.decode("utf-8", errors="strict"),
        completed.stderr.decode("utf-8", errors="strict"),
    )


def _expect_exit(result: CommandResult, expected: int, command: list[str]) -> None:
    if result.returncode != expected:
        raise RuntimeError(
            f"command returned {result.returncode}, expected {expected}: {command!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def _run_ok(
    command: list[str], *, cwd: Path, environment: dict[str, str]
) -> CommandResult:
    result = _execute(command, cwd=cwd, environment=environment)
    _expect_exit(result, 0, command)
    return result


def _json_records(output: str) -> list[dict[str, Any]]:
    if not output or not output.endswith("\n"):
        raise RuntimeError("JSON Lines output must be nonempty and newline-terminated")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(output.splitlines(), start=1):
        if not line:
            raise RuntimeError(f"blank JSON Lines record at line {line_number}")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RuntimeError(f"JSON Lines record {line_number} is not an object")
        records.append(value)
    return records


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_exact_fields(
    record: dict[str, Any], expected: frozenset[str], location: str
) -> None:
    actual = frozenset(record)
    if actual != expected:
        raise RuntimeError(
            f"{location} fields differ from schema v1; "
            f"missing={sorted(expected - actual)!r}, "
            f"unknown={sorted(actual - expected)!r}"
        )


def _require_nonempty_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RuntimeError(f"{location} must be a nonempty NUL-free string")
    return value


def _require_absolute_path(
    value: object, location: str, *, nullable: bool = False
) -> str | None:
    if value is None and nullable:
        return None
    path = _require_nonempty_string(value, location)
    if not Path(path).is_absolute():
        raise RuntimeError(f"{location} must be absolute")
    return path


def _require_timestamp(
    value: object, location: str, *, nullable: bool = False
) -> str | None:
    if value is None and nullable:
        return None
    timestamp = _require_nonempty_string(value, location)
    if not RFC3339_UTC_RE.fullmatch(timestamp):
        raise RuntimeError(f"{location} must be an RFC 3339 UTC timestamp")
    try:
        datetime.fromisoformat(f"{timestamp[:-1]}+00:00")
    except ValueError as error:
        raise RuntimeError(f"{location} is not a valid timestamp") from error
    return timestamp


def _validate_result_record(record: dict[str, Any], index: int) -> None:
    location = f"result record {index + 1}"
    _require_exact_fields(record, RESULT_FIELDS, location)
    if record["schema_version"] != 1 or not _is_integer(record["schema_version"]):
        raise RuntimeError(f"{location} has an unsupported schema version")
    if record["record_type"] != "result":
        raise RuntimeError(f"{location} has an unsupported record type")
    _require_nonempty_string(record["run_id"], f"{location}.run_id")
    job_id = _require_nonempty_string(record["job_id"], f"{location}.job_id")
    if not SHORT_HEX_RE.fullmatch(job_id):
        raise RuntimeError(f"{location}.job_id must contain 24 lowercase hex digits")
    plan_index = record["plan_index"]
    if not _is_integer(plan_index) or plan_index < 0:
        raise RuntimeError(f"{location}.plan_index must be a nonnegative integer")
    if record["status"] not in UPSTREAM_STATUSES:
        raise RuntimeError(f"{location}.status is unsupported")
    _require_absolute_path(record["source_path"], f"{location}.source_path")
    _require_absolute_path(record["output_path"], f"{location}.output_path")
    _require_absolute_path(
        record["staging_path"], f"{location}.staging_path", nullable=True
    )
    _require_absolute_path(record["log_path"], f"{location}.log_path", nullable=True)
    exit_code = record["chdman_exit_code"]
    if exit_code is not None and not _is_integer(exit_code):
        raise RuntimeError(f"{location}.chdman_exit_code must be an integer or null")
    started_at = _require_timestamp(
        record["started_at"], f"{location}.started_at", nullable=True
    )
    finished_at = _require_timestamp(
        record["finished_at"], f"{location}.finished_at", nullable=True
    )
    if (started_at is None) != (finished_at is None):
        raise RuntimeError(f"{location} must set both timestamps or neither")
    duration_ms = record["duration_ms"]
    if not _is_integer(duration_ms) or duration_ms < 0:
        raise RuntimeError(f"{location}.duration_ms must be a nonnegative integer")
    error = record["error"]
    if error is not None:
        _require_nonempty_string(error, f"{location}.error")
    warnings = record["warnings"]
    if not isinstance(warnings, list):
        raise RuntimeError(f"{location}.warnings must be an array")
    for warning_index, warning in enumerate(warnings):
        _require_nonempty_string(warning, f"{location}.warnings[{warning_index}]")


def _validate_summary_record(summary: dict[str, Any]) -> None:
    _require_exact_fields(summary, SUMMARY_FIELDS, "summary record")
    if summary["schema_version"] != 1 or not _is_integer(summary["schema_version"]):
        raise RuntimeError("summary has an unsupported schema version")
    if summary["record_type"] != "summary":
        raise RuntimeError("summary has an unsupported record type")
    _require_nonempty_string(summary["run_id"], "summary.run_id")
    for field in ("total", *UPSTREAM_STATUSES, "duration_ms"):
        value = summary[field]
        if not _is_integer(value) or value < 0:
            raise RuntimeError(f"summary.{field} must be a nonnegative integer")


def _result_stream(
    result: CommandResult, *, expected_exit: int, expected_statuses: list[str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if result.returncode != expected_exit:
        raise RuntimeError(
            f"result stream returned {result.returncode}, expected {expected_exit}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    records = _json_records(result.stdout)
    if not records or records[-1].get("record_type") != "summary":
        raise RuntimeError("result stream is missing its terminal summary")
    results = records[:-1]
    summary = records[-1]
    for index, record in enumerate(results):
        _validate_result_record(record, index)
    _validate_summary_record(summary)
    run_id = summary["run_id"]
    if any(record["run_id"] != run_id for record in results):
        raise RuntimeError("result and summary run_id values differ")
    job_ids = [record["job_id"] for record in results]
    if len(set(job_ids)) != len(job_ids):
        raise RuntimeError("result stream contains duplicate job IDs")
    statuses = [str(record.get("status")) for record in results]
    if statuses != expected_statuses:
        raise RuntimeError(f"unexpected result statuses: {statuses!r}")
    if [record.get("plan_index") for record in results] != list(range(len(results))):
        raise RuntimeError("result stream is not in deterministic plan order")
    if summary.get("total") != len(results):
        raise RuntimeError("summary total does not match result count")
    for status in UPSTREAM_STATUSES:
        expected_count = statuses.count(status)
        if summary.get(status) != expected_count:
            raise RuntimeError(f"summary {status} count does not match results")
    if sum(summary[status] for status in UPSTREAM_STATUSES) != summary["total"]:
        raise RuntimeError("summary status counts do not add up to total")
    return results, summary


def _metadata(data: bytes, location: str) -> None:
    message = BytesParser(policy=policy.default).parsebytes(data)
    if message.defects:
        raise RuntimeError(f"{location} contains parser defects: {message.defects!r}")
    expected_singletons = {
        "Name": "chdmanpy",
        "Version": VERSION,
        "Summary": "A pipeline-friendly command-line frontend for CHDMAN",
        "Author": "bohemon",
        "Requires-Python": ">=3.11",
        "License-Expression": "MIT",
        "License-File": "LICENSE",
        "Description-Content-Type": "text/markdown",
    }
    expected_header_counts = Counter(
        {
            "Metadata-Version": 1,
            **{field: 1 for field in expected_singletons},
            "Project-URL": len(EXPECTED_PROJECT_URLS),
            "Classifier": len(EXPECTED_CLASSIFIERS),
        }
    )
    actual_header_counts = Counter(message.keys())
    if actual_header_counts != expected_header_counts:
        raise RuntimeError(
            f"{location} header multiplicities differ; "
            f"expected={expected_header_counts!r}, actual={actual_header_counts!r}"
        )
    metadata_version = str(message["Metadata-Version"])
    version_match = re.fullmatch(r"2\.([0-9]+)", metadata_version)
    if version_match is None or int(version_match.group(1)) < 4:
        raise RuntimeError(
            f"{location} uses unsupported Metadata-Version {metadata_version!r}"
        )
    for field, value in expected_singletons.items():
        observed = [str(item) for item in message.get_all(field, [])]
        if observed != [value]:
            raise RuntimeError(f"{location} has unexpected {field}: {observed!r}")
    project_urls = Counter(str(item) for item in message.get_all("Project-URL", []))
    if project_urls != Counter(EXPECTED_PROJECT_URLS):
        raise RuntimeError(f"{location} has unexpected Project-URL values")
    classifiers = Counter(str(item) for item in message.get_all("Classifier", []))
    if classifiers != Counter(EXPECTED_CLASSIFIERS):
        raise RuntimeError(f"{location} has unexpected classifiers")
    description_bytes = message.get_payload(decode=True)
    if not isinstance(description_bytes, bytes):
        raise RuntimeError(f"{location} README payload is not bytes")
    description = description_bytes.decode("utf-8", errors="strict")
    expected_description = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    if description != expected_description:
        raise RuntimeError(f"{location} README payload differs from README.md")
    forbidden_claims = ("python ./chdmanpy.py", "extracts ZIP files in advance")
    if any(claim in description for claim in forbidden_claims):
        raise RuntimeError(f"{location} contains historical interface claims")


def _forbidden_artifact_path(relative: str) -> bool:
    folded = relative.replace("\\", "/").casefold()
    parts = tuple(part for part in folded.split("/") if part)
    basename = parts[-1] if parts else ""
    if basename == "chdmanpy.py" and len(parts) == 1:
        return True
    if basename.endswith((".pyc", ".exe")) or (
        basename.startswith("mame") and basename.endswith(".zip")
    ):
        return True
    if basename == "chdman" or "__pycache__" in parts:
        return True
    return bool(
        {
            "tests",
            "test",
            "build",
            "dist",
            ".venv",
            "venv",
            ".pytest_cache",
            ".ruff_cache",
        }
        & set(parts)
    )


def _inspect_wheel(wheel: Path) -> None:
    expected_dist_info = f"chdmanpy-{VERSION}.dist-info"
    expected_files = {
        *EXPECTED_PACKAGE_FILES,
        *EXPECTED_WHEEL_DOCUMENTS,
        f"{expected_dist_info}/METADATA",
        f"{expected_dist_info}/RECORD",
        f"{expected_dist_info}/WHEEL",
        f"{expected_dist_info}/entry_points.txt",
        f"{expected_dist_info}/licenses/LICENSE",
    }
    with zipfile.ZipFile(wheel) as archive:
        infos = archive.infolist()
        name_counts = Counter(info.filename for info in infos)
        duplicates = sorted(name for name, count in name_counts.items() if count != 1)
        if duplicates:
            raise RuntimeError(f"wheel contains duplicate member names: {duplicates!r}")
        contents: dict[str, bytes] = {}
        for info in infos:
            if (
                info.is_dir()
                or info.flag_bits & 0x1
                or info.orig_filename != info.filename
                or "\x00" in info.orig_filename
            ):
                raise RuntimeError(
                    f"wheel contains an unsafe member: {info.filename!r}"
                )
            mode = info.external_attr >> 16
            if stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
                raise RuntimeError(
                    f"wheel member is not a regular file: {info.filename!r}"
                )
            value = archive.read(info)
            if len(value) != info.file_size:
                raise RuntimeError(f"wheel member size differs: {info.filename!r}")
            contents[info.filename] = value
        names = set(contents)
        missing = expected_files - names
        unexpected = names - expected_files
        if missing or unexpected:
            raise RuntimeError(
                f"wheel contents differ from the release allowlist; "
                f"missing={sorted(missing)!r}, unexpected={sorted(unexpected)!r}"
            )
        _validate_wheel_documents(archive, names)
        forbidden = sorted(name for name in names if _forbidden_artifact_path(name))
        if forbidden:
            raise RuntimeError(f"wheel contains forbidden paths: {forbidden!r}")
        _metadata(contents[f"{expected_dist_info}/METADATA"], "wheel metadata")
        wheel_message = BytesParser(policy=policy.default).parsebytes(
            contents[f"{expected_dist_info}/WHEEL"]
        )
        wheel_header_counts = Counter(wheel_message.keys())
        expected_wheel_header_counts = Counter(
            {
                "Wheel-Version": 1,
                "Generator": 1,
                "Root-Is-Purelib": 1,
                "Tag": 1,
            }
        )
        if wheel_message.defects or wheel_header_counts != expected_wheel_header_counts:
            raise RuntimeError("wheel metadata headers are malformed or duplicated")
        expected_wheel_values = {
            "Wheel-Version": "1.0",
            "Root-Is-Purelib": "true",
            "Tag": "py3-none-any",
        }
        for field, value in expected_wheel_values.items():
            if [str(item) for item in wheel_message.get_all(field, [])] != [value]:
                raise RuntimeError(f"wheel has unexpected {field}")
        generator = [str(item) for item in wheel_message.get_all("Generator", [])]
        if len(generator) != 1 or not generator[0]:
            raise RuntimeError("wheel Generator must be a nonempty singleton")
        entry_points = contents[f"{expected_dist_info}/entry_points.txt"].decode(
            "utf-8", errors="strict"
        )
        if entry_points.strip() != (
            "[console_scripts]\nchdmanpy = chdmanpy.cli:entrypoint"
        ):
            raise RuntimeError(f"unexpected console entry point: {entry_points!r}")
        expected_license = (PROJECT_ROOT / "LICENSE").read_bytes()
        if contents[f"{expected_dist_info}/licenses/LICENSE"] != expected_license:
            raise RuntimeError("wheel license differs from the repository LICENSE")
        record_name = f"{expected_dist_info}/RECORD"
        try:
            record_text = contents[record_name].decode("utf-8", errors="strict")
            rows = list(csv.reader(io.StringIO(record_text, newline=""), strict=True))
        except (UnicodeDecodeError, csv.Error) as error:
            raise RuntimeError("wheel RECORD is not strict UTF-8 CSV") from error
        if any(len(row) != 3 for row in rows):
            raise RuntimeError("wheel RECORD rows must have exactly three fields")
        record_counts = Counter(row[0] for row in rows)
        duplicate_rows = sorted(
            name for name, count in record_counts.items() if count != 1
        )
        if duplicate_rows or set(record_counts) != names:
            raise RuntimeError(
                "wheel RECORD paths do not map one-to-one to archive members"
            )
        for name, digest, size in rows:
            if name == record_name:
                if digest or size:
                    raise RuntimeError("wheel RECORD must not hash or size itself")
                continue
            value = contents[name]
            encoded = base64.urlsafe_b64encode(hashlib.sha256(value).digest())
            expected_digest = f"sha256={encoded.rstrip(b'=').decode()}"
            if digest != expected_digest or size != str(len(value)):
                raise RuntimeError(f"wheel RECORD integrity differs for {name!r}")


def _inspect_sdist(source_distribution: Path) -> None:
    with tarfile.open(source_distribution, mode="r:gz") as archive:
        members = archive.getmembers()
        root_name = f"chdmanpy-{VERSION}"
        full_name_counts = Counter(member.name for member in members)
        duplicate_names = sorted(
            name for name, count in full_name_counts.items() if count != 1
        )
        if duplicate_names:
            raise RuntimeError(f"sdist contains duplicate names: {duplicate_names!r}")
        contents: dict[str, bytes] = {}
        for member in members:
            if "\\" in member.name or "\x00" in member.name:
                raise RuntimeError(f"sdist contains an unsafe name: {member.name!r}")
            raw_parts = member.name.split("/")
            parsed = PurePosixPath(member.name)
            if (
                parsed.is_absolute()
                or len(raw_parts) < 2
                or raw_parts[0] != root_name
                or any(part in {"", ".", ".."} for part in raw_parts)
                or parsed.parts != tuple(raw_parts)
            ):
                raise RuntimeError(
                    f"sdist member is outside exact root {root_name!r}: {member.name!r}"
                )
            if not member.isfile():
                raise RuntimeError(
                    f"sdist member is not a regular file: {member.name!r}"
                )
            relative = "/".join(raw_parts[1:])
            stream = archive.extractfile(member)
            if stream is None:
                raise RuntimeError(f"cannot read sdist member {member.name!r}")
            value = stream.read()
            if len(value) != member.size:
                raise RuntimeError(f"sdist member size differs: {member.name!r}")
            contents[relative] = value
        relative_members = set(contents)
        expected_members = {
            *EXPECTED_SDIST_DOCUMENTS,
            *(f"src/{name}" for name in EXPECTED_PACKAGE_FILES),
            ".gitignore",
            "LICENSE",
            "PKG-INFO",
            "pyproject.toml",
        }
        missing = expected_members - relative_members
        unexpected = relative_members - expected_members
        if missing or unexpected:
            raise RuntimeError(
                "sdist contents differ from the release allowlist; "
                f"missing={sorted(missing)!r}, unexpected={sorted(unexpected)!r}"
            )
        forbidden = sorted(
            name
            for name in relative_members
            if _forbidden_artifact_path(name)
            or name in {"others.toml", "ps2.toml", "psp.toml"}
        )
        if forbidden:
            raise RuntimeError(f"sdist contains repository-only paths: {forbidden!r}")
        for relative in (*sorted(EXPECTED_SDIST_DOCUMENTS), "LICENSE"):
            if contents[relative] != (PROJECT_ROOT / relative).read_bytes():
                raise RuntimeError(
                    f"sdist member differs from the repository source: {relative!r}"
                )
        _metadata(contents["PKG-INFO"], "sdist metadata")


def _write_fake_wheel(root: Path) -> Path:
    source = Path(__file__).with_name("fake_chdman.py").read_bytes()
    wheel = root / "fake_chdman-0.0.0-py3-none-any.whl"
    dist_info = "fake_chdman-0.0.0.dist-info"
    files: dict[str, bytes] = {
        "fake_chdman.py": source,
        f"{dist_info}/METADATA": (
            b"Metadata-Version: 2.1\n"
            b"Name: fake-chdman\n"
            b"Version: 0.0.0\n"
            b"Summary: chdmanpy release-smoke fixture\n"
        ),
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\n"
            b"Generator: chdmanpy-release-smoke\n"
            b"Root-Is-Purelib: true\n"
            b"Tag: py3-none-any\n"
        ),
        f"{dist_info}/entry_points.txt": (
            b"[console_scripts]\nchdman = fake_chdman:main\n"
        ),
    }
    rows = io.StringIO(newline="")
    writer = csv.writer(rows, lineterminator="\n")
    for name, contents in files.items():
        digest = hashlib.sha256(contents).digest()
        encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        writer.writerow((name, f"sha256={encoded}", len(contents)))
    record_name = f"{dist_info}/RECORD"
    writer.writerow((record_name, "", ""))
    files[record_name] = rows.getvalue().encode()
    with zipfile.ZipFile(wheel, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, contents in files.items():
            archive.writestr(name, contents)
    return wheel


def _fake_environment(
    environment: dict[str, str], control: Path, record: Path
) -> dict[str, str]:
    selected = environment.copy()
    selected["FAKE_CHDMAN_CONTROL"] = str(control)
    selected["FAKE_CHDMAN_RECORD"] = str(record)
    return selected


def _write_control(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _read_fake_record(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _wait_for_fake_conversion(
    record_path: Path,
    source: Path,
    process: subprocess.Popen[bytes],
    *,
    timeout: float = 15.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = _read_fake_record(record_path)
        if (
            record is not None
            and record.get("input_path") == str(source)
            and record.get("state") == "running"
        ):
            return record
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise RuntimeError(
                "installed CLI exited before the fake conversion started; "
                f"exit={process.returncode}, "
                f"stdout={stdout.decode('utf-8', errors='replace')!r}, "
                f"stderr={stderr.decode('utf-8', errors='replace')!r}"
            )
        time.sleep(0.05)
    raise RuntimeError("timed out waiting for the installed fake conversion to start")


def _stop_process_for_cleanup(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def _stop_fake_child_for_cleanup(record_path: Path, child_pid: int | None) -> None:
    record = _read_fake_record(record_path)
    if record is None or record.get("state") != "running":
        return
    if child_pid is None:
        recorded_pid = record.get("pid")
        if not _is_integer(recorded_pid) or recorded_pid <= 0:
            return
        child_pid = recorded_pid
    try:
        os.kill(child_pid, signal.SIGTERM)
    except OSError:
        return
    if os.name == "nt":
        time.sleep(0.25)
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        record = _read_fake_record(record_path)
        if record is None or record.get("state") != "running":
            return
        time.sleep(0.05)


def _interrupt_installed_cli(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    record_path: Path,
    source: Path,
) -> tuple[CommandResult, dict[str, Any]]:
    popen_options: dict[str, Any] = {
        "cwd": cwd,
        "env": environment,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(command, **popen_options)
    child_pid: int | None = None
    try:
        started = _wait_for_fake_conversion(record_path, source, process)
        child_pid_value = started.get("pid")
        if not _is_integer(child_pid_value) or child_pid_value <= 0:
            raise RuntimeError("fake conversion did not record a valid child PID")
        child_pid = child_pid_value
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGINT)
        try:
            stdout, stderr = process.communicate(timeout=25)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                "installed CLI did not finish after interruption"
            ) from error
        result = CommandResult(
            process.returncode,
            stdout.decode("utf-8", errors="strict"),
            stderr.decode("utf-8", errors="strict"),
        )
        child_record = _read_fake_record(record_path)
        if child_record is None:
            raise RuntimeError("fake child termination record is missing")
        return result, child_record
    finally:
        _stop_process_for_cleanup(process)
        _stop_fake_child_for_cleanup(record_path, child_pid)


def _assert_clean_success(
    result: CommandResult,
    *,
    source: Path,
    original_source: bytes,
    expected_log_text: str,
) -> dict[str, Any]:
    results, summary = _result_stream(
        result, expected_exit=0, expected_statuses=["success"]
    )
    if summary.get("success") != 1:
        raise RuntimeError("clean conversion summary did not report one success")
    record = results[0]
    output = Path(record["output_path"])
    if not output.read_bytes().startswith(b"MComprHD"):
        raise RuntimeError("installed conversion did not publish a CHD v5 output")
    if source.read_bytes() != original_source:
        raise RuntimeError("installed conversion modified its source")
    log = Path(record["log_path"])
    if expected_log_text not in log.read_text(encoding="utf-8"):
        raise RuntimeError("fake CHDMAN output was not isolated in the job log")
    if "CHDMAN selected" not in result.stderr or "CHDMAN version" not in result.stderr:
        raise RuntimeError("runtime diagnostics were not written to stderr")
    if (
        "run log:" not in result.stderr
        or expected_log_text in result.stdout
        or expected_log_text in result.stderr
    ):
        raise RuntimeError(
            "CHDMAN output leaked from its log or diagnostics are missing"
        )
    return record


def _arcshuttle_stream(root: Path, output: Path) -> bytes:
    run_id = "20260824T000000Z-release-smoke"
    result = {
        "schema_version": 2,
        "record_type": "result",
        "run_id": run_id,
        "job_id": "1" * 24,
        "path": str(root / "archives" / "game.zip"),
        "status": "success",
        "exit_code": 0,
        "started_at": "2026-08-24T00:00:00Z",
        "finished_at": "2026-08-24T00:00:01Z",
        "duration_ms": 1000,
        "assigned_cpu_tokens": 1,
        "assigned_threads": 1,
        "output_dir": str(output),
        "staging_dir": None,
        "log_path": str(root / "arcshuttle-logs" / "job.log"),
        "warnings": [],
        "operation": "extract",
        "output_path": str(output),
        "staging_path": None,
    }
    summary = {
        "schema_version": 2,
        "record_type": "summary",
        "run_id": run_id,
        "total": 1,
        "success": 1,
        "warning": 0,
        "failed": 0,
        "skipped": 0,
        "interrupted": 0,
        "duration_ms": 1000,
    }
    return (
        json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        + "\n"
        + json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _installed_smoke(wheel: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="chdmanpy-wheel-smoke-") as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        for name in tuple(environment):
            if name.upper().startswith("CHDMANPY_"):
                environment.pop(name)
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment.update(
            {
                "PIP_CONFIG_FILE": os.devnull,
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_NO_INDEX": "1",
                "PYTHONNOUSERSITE": "1",
            }
        )
        virtual_environment = root / "venv"
        _run_ok(
            [sys.executable, "-m", "venv", str(virtual_environment)],
            cwd=root,
            environment=environment,
        )
        scripts = virtual_environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        console = scripts / ("chdmanpy.exe" if os.name == "nt" else "chdmanpy")
        fake_wheel = _write_fake_wheel(root)
        _run_ok(
            [
                str(python),
                "-m",
                "pip",
                "--disable-pip-version-check",
                "install",
                "--no-deps",
                "--no-index",
                str(wheel),
                str(fake_wheel),
            ],
            cwd=root,
            environment=environment,
        )
        fake_chdman = scripts / ("chdman.exe" if os.name == "nt" else "chdman")
        module = [str(python), "-m", "chdmanpy"]
        command = [str(console)]

        origin_check = (
            "import importlib.metadata,json,pathlib,sys,chdmanpy;"
            "p=pathlib.Path(chdmanpy.__file__).resolve();"
            "root=pathlib.Path(sys.prefix).resolve();"
            "assert p.is_relative_to(root),(p,root);"
            "assert chdmanpy.__version__==importlib.metadata.version('chdmanpy')=='0.1.0';"
            "print(json.dumps({'origin':str(p),'version':chdmanpy.__version__}))"
        )
        origin = _run_ok(
            [str(python), "-c", origin_check], cwd=root, environment=environment
        )
        if (
            str(virtual_environment.resolve())
            not in json.loads(origin.stdout)["origin"]
        ):
            raise RuntimeError("installed smoke imported chdmanpy from the checkout")

        for prefix in (module, command):
            version = _run_ok([*prefix, "--version"], cwd=root, environment=environment)
            if version.stdout != EXPECTED_VERSION_LINE or version.stderr:
                raise RuntimeError(f"unexpected installed version output: {version!r}")
        for arguments in (
            ["--help"],
            ["plan", "--help"],
            ["run", "--help"],
            ["convert", "--help"],
        ):
            module_help = _run_ok(
                [*module, *arguments], cwd=root, environment=environment
            )
            console_help = _run_ok(
                [*command, *arguments], cwd=root, environment=environment
            )
            if module_help != console_help:
                raise RuntimeError(
                    f"console and module help differ for arguments {arguments!r}"
                )

        entrypoint_source = root / "entry point disc 日本語.iso"
        entrypoint_source.write_bytes(b"iso")
        processing_arguments = [
            "plan",
            str(entrypoint_source),
            "--preset",
            "ps2",
            "--output-dir",
            str(root / "entry point output"),
        ]
        module_plan = _execute(
            [*module, *processing_arguments],
            cwd=root,
            environment=environment,
        )
        console_plan = _execute(
            [*command, *processing_arguments],
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

        preset_mappings = {
            "others": {".cue": ("createcd", [])},
            "ps2": {
                ".cue": ("createcd", []),
                ".iso": ("createdvd", ["-c", "zlib"]),
            },
            "psp": {
                ".iso": ("createdvd", ["-hs", "2048", "-c", "zstd"]),
            },
        }
        preset_source: Path | None = None
        for preset, expected_mapping in preset_mappings.items():
            preset_directory = root / "preset sources" / preset
            preset_directory.mkdir(parents=True)
            sources: list[Path] = []
            for extension in expected_mapping:
                source = preset_directory / f"disc-{extension[1:]}{extension}"
                source.write_bytes(f"{preset}-{extension}".encode())
                sources.append(source)
                if preset == "ps2" and extension == ".iso":
                    preset_source = source
            preset_arguments = [
                *module,
                "plan",
                *(str(source) for source in sources),
                "--output-dir",
                str(root / "preset output" / preset),
                "--preset",
                preset,
            ]
            preset_plan = _execute(
                preset_arguments,
                cwd=root,
                environment=environment,
            )
            expected_exit = 1 if ".cue" in expected_mapping else 0
            _expect_exit(preset_plan, expected_exit, preset_arguments)
            preset_jobs = _json_records(preset_plan.stdout)
            if len(preset_jobs) != len(expected_mapping):
                raise RuntimeError(
                    f"installed {preset} preset planned an unexpected job count"
                )
            observed_mapping = {
                Path(job["source"]["path"]).suffix.casefold(): (
                    job["chdman"]["operation"],
                    job["chdman"]["options"],
                )
                for job in preset_jobs
            }
            if observed_mapping != expected_mapping:
                raise RuntimeError(
                    f"installed {preset} preset mapping differs: {observed_mapping!r}"
                )
        if preset_source is None:
            raise RuntimeError("PS2 ISO preset fixture was not created")

        historical_config = root / "historical.toml"
        historical_config.write_text(
            '[options]\n".iso" = ["createdvd", "-c", "none"]\n',
            encoding="utf-8",
        )
        historical_plan = _run_ok(
            [
                *command,
                "plan",
                str(preset_source),
                "--config",
                str(historical_config),
                "--output-dir",
                str(root / "historical output"),
            ],
            cwd=root,
            environment=environment,
        )
        historical_jobs = _json_records(historical_plan.stdout)
        if historical_jobs[0]["chdman"]["options"] != ["-c", "none"]:
            raise RuntimeError("historical [options] TOML was not applied")

        direct_source = root / "direct source" / "disc 日本語.iso"
        direct_source.parent.mkdir()
        direct_bytes = b"direct-source"
        direct_source.write_bytes(direct_bytes)
        direct_control = root / "direct-control.json"
        direct_record = root / "direct-record.json"
        direct_log_text = "release smoke direct output"
        _write_control(
            direct_control,
            {"by_input": {str(direct_source): {"stdout": direct_log_text}}},
        )
        direct_args = [
            *command,
            "convert",
            str(direct_source),
            "--output-dir",
            str(root / "direct output"),
            "--preset",
            "ps2",
            "--chdman",
            str(fake_chdman),
            "--workers",
            "1",
            "--log-dir",
            str(root / "direct logs"),
        ]
        direct_result = _execute(
            direct_args,
            cwd=root,
            environment=_fake_environment(environment, direct_control, direct_record),
        )
        _assert_clean_success(
            direct_result,
            source=direct_source,
            original_source=direct_bytes,
            expected_log_text=direct_log_text,
        )

        planned_source = root / "planned source" / "disc.iso"
        planned_source.parent.mkdir()
        planned_bytes = b"planned-source"
        planned_source.write_bytes(planned_bytes)
        planned = _run_ok(
            [
                *command,
                "plan",
                str(planned_source),
                "--output-dir",
                str(root / "planned output"),
                "--preset",
                "ps2",
            ],
            cwd=root,
            environment=environment,
        )
        manifest = root / "jobs.jsonl"
        manifest.write_text(planned.stdout, encoding="utf-8", newline="\n")
        plan_control = root / "plan-control.json"
        plan_record = root / "plan-record.json"
        plan_log_text = "release smoke manifest output"
        _write_control(
            plan_control,
            {"by_input": {str(planned_source): {"stdout": plan_log_text}}},
        )
        run_args = [
            *module,
            "run",
            "--manifest",
            str(manifest),
            "--chdman",
            str(fake_chdman),
            "--log-dir",
            str(root / "planned logs"),
        ]
        run_result = _execute(
            run_args,
            cwd=root,
            environment=_fake_environment(environment, plan_control, plan_record),
        )
        _assert_clean_success(
            run_result,
            source=planned_source,
            original_source=planned_bytes,
            expected_log_text=plan_log_text,
        )

        extracted = root / "extracted" / "arc game"
        extracted.mkdir(parents=True)
        arc_source = extracted / "disc.iso"
        arc_bytes = b"arc-source"
        arc_source.write_bytes(arc_bytes)
        arc_control = root / "arc-control.json"
        arc_record = root / "arc-record.json"
        arc_log_text = "release smoke ArcShuttle output"
        _write_control(
            arc_control,
            {"by_input": {str(arc_source): {"stdout": arc_log_text}}},
        )
        arc_args = [
            *module,
            "convert",
            "--arcshuttle-results",
            "-",
            "--output-dir",
            str(root / "arc output"),
            "--preset",
            "ps2",
            "--chdman",
            str(fake_chdman),
            "--log-dir",
            str(root / "arc logs"),
        ]
        arc_result = _execute(
            arc_args,
            cwd=root,
            environment=_fake_environment(environment, arc_control, arc_record),
            input_bytes=_arcshuttle_stream(root, extracted),
        )
        _assert_clean_success(
            arc_result,
            source=arc_source,
            original_source=arc_bytes,
            expected_log_text=arc_log_text,
        )

        failure_source = root / "failure source" / "disc.iso"
        failure_source.parent.mkdir()
        failure_bytes = b"failure-source"
        failure_source.write_bytes(failure_bytes)
        failure_control = root / "failure-control.json"
        failure_record = root / "failure-record.json"
        _write_control(
            failure_control,
            {
                "by_input": {
                    str(failure_source): {
                        "exit_code": 23,
                        "partial_output_text": "retained-partial",
                    }
                }
            },
        )
        failure_args = [
            *command,
            "convert",
            str(failure_source),
            "--output-dir",
            str(root / "failure output"),
            "--preset",
            "ps2",
            "--chdman",
            str(fake_chdman),
            "--log-dir",
            str(root / "failure logs"),
        ]
        failure_result = _execute(
            failure_args,
            cwd=root,
            environment=_fake_environment(environment, failure_control, failure_record),
        )
        failed, _ = _result_stream(
            failure_result, expected_exit=2, expected_statuses=["failed"]
        )
        if failed[0]["chdman_exit_code"] != 23 or "23" not in failed[0]["error"]:
            raise RuntimeError("fake CHDMAN failure details were not preserved")
        if failure_source.read_bytes() != failure_bytes:
            raise RuntimeError("failed installed conversion modified its source")
        if Path(failed[0]["output_path"]).exists():
            raise RuntimeError("failed installed conversion published a destination")
        staging = Path(failed[0]["staging_path"])
        if not staging.is_dir() or not (staging / "output.chd").is_file():
            raise RuntimeError("failed installed conversion did not retain staging")

        interrupt_source = root / "interrupt source" / "disc.iso"
        interrupt_source.parent.mkdir()
        interrupt_source_bytes = b"interrupt-source"
        interrupt_source.write_bytes(interrupt_source_bytes)
        interrupt_output = root / "interrupt output"
        interrupt_plan = _run_ok(
            [
                *command,
                "plan",
                str(interrupt_source),
                "--output-dir",
                str(interrupt_output),
                "--preset",
                "ps2",
                "--existing",
                "rename",
            ],
            cwd=root,
            environment=environment,
        )
        interrupt_jobs = _json_records(interrupt_plan.stdout)
        interrupt_sentinel = Path(interrupt_jobs[0]["destination"]["path"])
        interrupt_sentinel.parent.mkdir(parents=True)
        interrupt_sentinel_bytes = b"interrupt-pre-existing-sentinel"
        interrupt_sentinel.write_bytes(interrupt_sentinel_bytes)
        interrupt_control = root / "interrupt-control.json"
        interrupt_record = root / "interrupt-record.json"
        interrupt_partial = "retained-interrupted-partial"
        _write_control(
            interrupt_control,
            {
                "by_input": {
                    str(interrupt_source): {
                        "delay_seconds": 60,
                        "partial_output_text": interrupt_partial,
                    }
                }
            },
        )
        interrupt_args = [
            *command,
            "convert",
            str(interrupt_source),
            "--output-dir",
            str(interrupt_output),
            "--preset",
            "ps2",
            "--existing",
            "rename",
            "--chdman",
            str(fake_chdman),
            "--workers",
            "1",
            "--log-dir",
            str(root / "interrupt logs"),
        ]
        interrupt_result, terminated_child = _interrupt_installed_cli(
            interrupt_args,
            cwd=root,
            environment=_fake_environment(
                environment, interrupt_control, interrupt_record
            ),
            record_path=interrupt_record,
            source=interrupt_source,
        )
        interrupted, interrupted_summary = _result_stream(
            interrupt_result, expected_exit=130, expected_statuses=["interrupted"]
        )
        interrupted_record = interrupted[0]
        if interrupted_summary["interrupted"] != 1:
            raise RuntimeError("interrupted summary count is incorrect")
        if (
            "run log:" not in interrupt_result.stderr
            or "Traceback" in interrupt_result.stderr
        ):
            raise RuntimeError(
                "interrupted CLI diagnostics are incomplete or contain a traceback"
            )
        if interrupted_record["chdman_exit_code"] != 130:
            raise RuntimeError("interrupted child exit code was not preserved")
        retained = Path(interrupted_record["staging_path"])
        if not retained.is_dir() or not retained.name.endswith(".failed"):
            raise RuntimeError("interrupted conversion did not retain owned staging")
        if not (retained / ".chdmanpy-owner").is_file():
            raise RuntimeError("interrupted staging ownership marker is missing")
        if (retained / "output.chd").read_text(encoding="utf-8") != interrupt_partial:
            raise RuntimeError("interrupted partial CHD was not retained")
        if Path(interrupted_record["output_path"]).exists():
            raise RuntimeError("interrupted conversion published a destination")
        if interrupt_source.read_bytes() != interrupt_source_bytes:
            raise RuntimeError("interrupted conversion modified its source")
        if interrupt_sentinel.read_bytes() != interrupt_sentinel_bytes:
            raise RuntimeError(
                "interrupted conversion modified an existing destination"
            )
        expected_child_signal = "SIGBREAK" if os.name == "nt" else "SIGTERM"
        if (
            terminated_child.get("state") != "interrupted"
            or terminated_child.get("received_signal") != expected_child_signal
            or terminated_child.get("exit_code") != 130
        ):
            raise RuntimeError(
                f"owned fake child was not terminated cleanly: {terminated_child!r}"
            )

        sentinel_source = root / "sentinel source" / "disc.iso"
        sentinel_source.parent.mkdir()
        sentinel_bytes = b"sentinel-source"
        sentinel_source.write_bytes(sentinel_bytes)
        sentinel_plan = _run_ok(
            [
                *command,
                "plan",
                str(sentinel_source),
                "--output-dir",
                str(root / "sentinel output"),
                "--preset",
                "ps2",
            ],
            cwd=root,
            environment=environment,
        )
        sentinel_jobs = _json_records(sentinel_plan.stdout)
        sentinel_manifest = root / "sentinel.jsonl"
        sentinel_manifest.write_text(
            sentinel_plan.stdout, encoding="utf-8", newline="\n"
        )
        sentinel = Path(sentinel_jobs[0]["destination"]["path"])
        sentinel.parent.mkdir(parents=True)
        sentinel_bytes_on_disk = b"pre-existing-sentinel"
        sentinel.write_bytes(sentinel_bytes_on_disk)
        sentinel_control = root / "sentinel-control.json"
        sentinel_record = root / "sentinel-record.json"
        _write_control(sentinel_control, {})
        sentinel_args = [
            *command,
            "run",
            "--manifest",
            str(sentinel_manifest),
            "--chdman",
            str(fake_chdman),
            "--log-dir",
            str(root / "sentinel logs"),
        ]
        sentinel_result = _execute(
            sentinel_args,
            cwd=root,
            environment=_fake_environment(
                environment, sentinel_control, sentinel_record
            ),
        )
        _result_stream(sentinel_result, expected_exit=2, expected_statuses=["failed"])
        if sentinel.read_bytes() != sentinel_bytes_on_disk:
            raise RuntimeError("existing destination sentinel was overwritten")
        if sentinel_source.read_bytes() != sentinel_bytes:
            raise RuntimeError("existing-output failure modified its source")
        observed = json.loads(sentinel_record.read_text(encoding="utf-8"))
        if observed["args"] != ["-help"]:
            raise RuntimeError("existing-output failure started CHDMAN conversion")

        collision_sources: list[Path] = []
        collision_source_bytes: dict[Path, bytes] = {}
        for index in range(2):
            source = root / f"collision source {index}" / f"disc-{index}.iso"
            source.parent.mkdir()
            contents = f"collision-source-{index}".encode()
            source.write_bytes(contents)
            collision_sources.append(source)
            collision_source_bytes[source] = contents
        collision_plan = _run_ok(
            [
                *module,
                "plan",
                *(str(source) for source in collision_sources),
                "--output-dir",
                str(root / "collision output"),
                "--preset",
                "ps2",
            ],
            cwd=root,
            environment=environment,
        )
        collision_jobs = _json_records(collision_plan.stdout)
        if len(collision_jobs) != 2:
            raise RuntimeError("collision fixture did not plan exactly two jobs")
        collision_jobs[1]["destination"]["path"] = collision_jobs[0]["destination"][
            "path"
        ]
        collision_manifest = root / "collision.jsonl"
        collision_manifest.write_text(
            "".join(
                json.dumps(job, ensure_ascii=False, separators=(",", ":")) + "\n"
                for job in collision_jobs
            ),
            encoding="utf-8",
            newline="\n",
        )
        collision_control = root / "collision-control.json"
        collision_record = root / "collision-record.json"
        collision_logs = root / "collision logs"
        _write_control(collision_control, {})
        collision_args = [
            *command,
            "run",
            "--manifest",
            str(collision_manifest),
            "--chdman",
            str(fake_chdman),
            "--log-dir",
            str(collision_logs),
        ]
        collision_result = _execute(
            collision_args,
            cwd=root,
            environment=_fake_environment(
                environment, collision_control, collision_record
            ),
        )
        _expect_exit(collision_result, 64, collision_args)
        if collision_result.stdout or "collision" not in collision_result.stderr:
            raise RuntimeError("destination collision did not fail as a usage error")
        if collision_record.exists() or collision_logs.exists():
            raise RuntimeError("destination collision started CHDMAN or created logs")
        for source, contents in collision_source_bytes.items():
            if source.read_bytes() != contents:
                raise RuntimeError("destination collision modified a source")

        malformed_source = root / "malformed extracted" / "disc.iso"
        malformed_source.parent.mkdir()
        malformed_bytes = b"malformed-source"
        malformed_source.write_bytes(malformed_bytes)
        malformed_control = root / "malformed-control.json"
        malformed_record = root / "malformed-record.json"
        _write_control(malformed_control, {})
        malformed_stream = (
            _arcshuttle_stream(root, malformed_source.parent) + b"not-json\n"
        )
        malformed_args = [
            *command,
            "convert",
            "--arcshuttle-results",
            "-",
            "--output-dir",
            str(root / "malformed output"),
            "--preset",
            "ps2",
            "--chdman",
            str(fake_chdman),
        ]
        malformed_result = _execute(
            malformed_args,
            cwd=root,
            environment=_fake_environment(
                environment, malformed_control, malformed_record
            ),
            input_bytes=malformed_stream,
        )
        _expect_exit(malformed_result, 64, malformed_args)
        if malformed_result.stdout or malformed_record.exists():
            raise RuntimeError(
                "malformed upstream input started work or emitted records"
            )
        if malformed_source.read_bytes() != malformed_bytes:
            raise RuntimeError("malformed upstream input modified its source")


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    distribution_directory = Path(arguments[0] if arguments else "dist").resolve()
    wheels = sorted(distribution_directory.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one wheel in {distribution_directory}")
    wheel = wheels[0]
    if wheel.name != f"chdmanpy-{VERSION}-py3-none-any.whl":
        raise RuntimeError(f"expected the 0.1.0 universal wheel, found {wheel.name}")
    source_distributions = sorted(distribution_directory.glob("*.tar.gz"))
    if len(source_distributions) != 1:
        raise RuntimeError(
            f"expected exactly one sdist in {distribution_directory}, "
            f"found {len(source_distributions)}"
        )
    if source_distributions[0].name != f"chdmanpy-{VERSION}.tar.gz":
        raise RuntimeError(f"unexpected sdist filename: {source_distributions[0].name}")
    _inspect_wheel(wheel)
    _inspect_sdist(source_distributions[0])
    _installed_smoke(wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
