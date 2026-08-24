"""Strict ArcShuttle schema-v2 extraction-result ingestion."""

from __future__ import annotations

import os
import re
import stat
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Literal

from chdmanpy.errors import ContractError, InputError
from chdmanpy.jsonl import loads_json_lines
from chdmanpy.manifest import is_absolute_path, path_key

ARCSHUTTLE_SCHEMA_VERSION = 2
ARCSHUTTLE_OPERATION = "extract"
UPSTREAM_STATUSES = frozenset(
    {"success", "warning", "failed", "skipped", "interrupted"}
)

_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "record_type",
        "run_id",
        "job_id",
        "path",
        "status",
        "exit_code",
        "started_at",
        "finished_at",
        "duration_ms",
        "assigned_cpu_tokens",
        "assigned_threads",
        "output_dir",
        "staging_dir",
        "log_path",
        "warnings",
        "operation",
        "output_path",
        "staging_path",
    }
)
_SUMMARY_FIELDS = frozenset(
    {
        "schema_version",
        "record_type",
        "run_id",
        "total",
        "success",
        "warning",
        "failed",
        "skipped",
        "interrupted",
        "duration_ms",
    }
)
_SHORT_HEX_RE = re.compile(r"[0-9a-f]{24}\Z")
_RFC3339_UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\Z")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

UpstreamErrorPolicy = Literal["fail", "skip"]


@dataclass(frozen=True, slots=True)
class ArcShuttleDiagnostic:
    """One validated upstream condition for CLI diagnostics."""

    job_id: str
    status: str
    omitted: bool
    messages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArcShuttleSelection:
    """Finalized planner roots selected from one complete upstream run."""

    run_id: str
    roots: tuple[str, ...]
    diagnostics: tuple[ArcShuttleDiagnostic, ...]
    result_count: int
    requires_warning_exit: bool


class ArcShuttleUpstreamError(ContractError):
    """A structurally valid run was not clean under the default policy."""

    def __init__(self, diagnostics: Sequence[ArcShuttleDiagnostic]) -> None:
        self.diagnostics = tuple(diagnostics)
        details = "; ".join(
            f"{item.job_id} ({item.status}): {', '.join(item.messages)}"
            for item in self.diagnostics
        )
        super().__init__(f"ArcShuttle run is not clean: {details}")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_exact_fields(
    value: object, expected: frozenset[str], location: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{location} must be an object")
    actual = frozenset(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise ContractError(f"{location} is missing fields: {', '.join(missing)}")
    if unknown:
        raise ContractError(f"{location} contains unknown fields: {', '.join(unknown)}")
    return value


def _require_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{location} must be a nonempty string")
    if "\x00" in value:
        raise ContractError(f"{location} must not contain NUL")
    return value


def _require_nullable_string(value: object, location: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, location)


def _require_nonnegative_int(value: object, location: str) -> int:
    if not _is_int(value) or value < 0:
        raise ContractError(f"{location} must be a nonnegative integer")
    return value


def _require_positive_int(value: object, location: str) -> int:
    if not _is_int(value) or value < 1:
        raise ContractError(f"{location} must be a positive integer")
    return value


def _validate_absolute_path(
    value: object, location: str, *, windows: bool | None
) -> str:
    path = _require_string(value, location)
    if not is_absolute_path(path, windows=windows):
        raise ContractError(f"{location} must be an absolute path")
    windows_mode = os.name == "nt" if windows is None else windows
    if windows_mode:
        normalized = path.replace("/", "\\")
        if not normalized.startswith(("\\\\?\\", "\\\\.\\")):
            components = (
                component
                for component in normalized.split("\\")
                if component and component not in {".", ".."}
            )
            if any(component.endswith((".", " ")) for component in components):
                raise ContractError(
                    f"{location} must not contain Windows path components ending "
                    "in a period or space"
                )
    return path


def _validate_timestamp(value: object, location: str) -> datetime:
    timestamp = _require_string(value, location)
    if not _RFC3339_UTC_RE.fullmatch(timestamp):
        raise ContractError(f"{location} must be an RFC 3339 UTC timestamp ending in Z")
    try:
        return datetime.fromisoformat(timestamp[:-1] + "+00:00").astimezone(UTC)
    except ValueError as error:
        raise ContractError(f"{location} is not a valid timestamp") from error


def _validate_string_array(value: object, location: str) -> list[str]:
    if not isinstance(value, list):
        raise ContractError(f"{location} must be an array of strings")
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ContractError(f"{location}[{index}] must be a string")
    return value


def _validate_result(
    value: object, index: int, *, windows: bool | None
) -> dict[str, Any]:
    location = f"ArcShuttle result {index}"
    if not isinstance(value, dict):
        raise ContractError(f"{location} must be an object")
    schema_version = value.get("schema_version")
    if not _is_int(schema_version) or schema_version != ARCSHUTTLE_SCHEMA_VERSION:
        raise ContractError(
            f"{location} has unsupported schema_version: {schema_version!r}"
        )
    if value.get("record_type") != "result":
        raise ContractError(f"{location} must have record_type 'result'")
    if value.get("operation") != ARCSHUTTLE_OPERATION:
        raise ContractError(f"{location} must have operation 'extract'")
    result = _require_exact_fields(value, _RESULT_FIELDS, location)

    _require_string(result["run_id"], f"{location}.run_id")
    job_id = _require_string(result["job_id"], f"{location}.job_id")
    if not _SHORT_HEX_RE.fullmatch(job_id):
        raise ContractError(
            f"{location}.job_id must be 24 lowercase hexadecimal digits"
        )
    _validate_absolute_path(result["path"], f"{location}.path", windows=windows)

    status = _require_string(result["status"], f"{location}.status")
    if status not in UPSTREAM_STATUSES:
        raise ContractError(f"{location}.status is unsupported: {status!r}")
    exit_code = result["exit_code"]
    if exit_code is not None and not _is_int(exit_code):
        raise ContractError(f"{location}.exit_code must be an integer or null")
    if status == "success" and exit_code != 0:
        raise ContractError(f"{location} success must have exit_code 0")
    if status == "warning" and exit_code != 1:
        raise ContractError(f"{location} warning must have exit_code 1")
    if status == "failed" and exit_code == 1:
        raise ContractError(f"{location} failed must not have exit_code 1")
    if status == "skipped" and exit_code is not None:
        raise ContractError(f"{location} skipped must have a null exit_code")

    started = _validate_timestamp(result["started_at"], f"{location}.started_at")
    finished = _validate_timestamp(result["finished_at"], f"{location}.finished_at")
    if finished < started:
        raise ContractError(f"{location}.finished_at must not precede started_at")
    _require_nonnegative_int(result["duration_ms"], f"{location}.duration_ms")
    assigned_cpu_tokens = _require_positive_int(
        result["assigned_cpu_tokens"], f"{location}.assigned_cpu_tokens"
    )
    assigned_threads = _require_positive_int(
        result["assigned_threads"], f"{location}.assigned_threads"
    )
    if assigned_threads > assigned_cpu_tokens:
        raise ContractError(
            f"{location}.assigned_threads must not exceed assigned_cpu_tokens"
        )

    output_dir = _validate_absolute_path(
        result["output_dir"], f"{location}.output_dir", windows=windows
    )
    output_path = _validate_absolute_path(
        result["output_path"], f"{location}.output_path", windows=windows
    )
    if output_dir != output_path:
        raise ContractError(f"{location} output_dir and output_path aliases must match")

    staging_dir = _require_nullable_string(
        result["staging_dir"], f"{location}.staging_dir"
    )
    staging_path = _require_nullable_string(
        result["staging_path"], f"{location}.staging_path"
    )
    if staging_dir != staging_path:
        raise ContractError(
            f"{location} staging_dir and staging_path aliases must match"
        )
    if staging_path is not None:
        _validate_absolute_path(
            staging_path, f"{location}.staging_path", windows=windows
        )
    if status == "success" and staging_path is not None:
        raise ContractError(f"{location} success must not retain a staging path")
    if status == "warning" and staging_path is None:
        raise ContractError(f"{location} warning must retain a staging path")
    if status == "skipped" and staging_path is not None:
        raise ContractError(f"{location} skipped must have a null staging path")

    log_path = _require_nullable_string(result["log_path"], f"{location}.log_path")
    if log_path is not None:
        _validate_absolute_path(log_path, f"{location}.log_path", windows=windows)
    _validate_string_array(result["warnings"], f"{location}.warnings")
    return result


def _validate_summary(value: object) -> dict[str, Any]:
    summary = _require_exact_fields(value, _SUMMARY_FIELDS, "ArcShuttle summary")
    schema_version = summary["schema_version"]
    if not _is_int(schema_version) or schema_version != ARCSHUTTLE_SCHEMA_VERSION:
        raise ContractError(
            f"ArcShuttle summary has unsupported schema_version: {schema_version!r}"
        )
    if summary["record_type"] != "summary":
        raise ContractError(
            "terminal ArcShuttle record must have record_type 'summary'"
        )
    _require_string(summary["run_id"], "ArcShuttle summary.run_id")
    for field in ("total", *sorted(UPSTREAM_STATUSES), "duration_ms"):
        _require_nonnegative_int(summary[field], f"ArcShuttle summary.{field}")
    return summary


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT)


def _validate_finalized_directory(path: str) -> None:
    target = Path(path)
    components = [*reversed(target.parents), target]
    target_metadata: os.stat_result | None = None
    for component in components:
        try:
            metadata = os.lstat(component)
        except FileNotFoundError:
            if component == target:
                raise ContractError(
                    f"ArcShuttle finalized output directory does not exist: {path!r}"
                ) from None
            continue
        except OSError as error:
            raise ContractError(
                f"cannot inspect ArcShuttle output component {str(component)!r}: {error}"
            ) from error
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
            raise ContractError(
                "ArcShuttle finalized output must not traverse a symlink, junction, "
                f"or reparse point: {str(component)!r}"
            )
        if component == target:
            target_metadata = metadata
    if target_metadata is None:
        raise ContractError(
            f"ArcShuttle finalized output directory is missing: {path!r}"
        )
    if not stat.S_ISDIR(target_metadata.st_mode):
        raise ContractError(
            f"ArcShuttle finalized output must be a directory: {path!r}"
        )
    marker = target / ".arcshuttle-owned"
    try:
        os.lstat(marker)
    except FileNotFoundError:
        return
    except OSError as error:
        raise ContractError(
            f"cannot inspect ArcShuttle ownership marker {str(marker)!r}: {error}"
        ) from error
    raise ContractError(
        "ArcShuttle finalized output must not be retained staging with an "
        f"ownership marker: {path!r}"
    )


def validate_arcshuttle_records(
    records: Sequence[object],
    *,
    on_upstream_error: UpstreamErrorPolicy = "fail",
    windows: bool | None = None,
) -> ArcShuttleSelection:
    """Completely preflight one ArcShuttle v2 result stream.

    The optional ``skip`` policy retains only finalized successful roots. It never
    treats a retained staging directory or an existing skipped output as finalized.
    """

    if on_upstream_error not in {"fail", "skip"}:
        raise InputError("on_upstream_error must be fail or skip")
    if not records:
        raise ContractError("ArcShuttle result stream must not be empty")
    if len(records) == 1:
        raise ContractError(
            "ArcShuttle result stream must contain results and one terminal summary"
        )

    if (
        not isinstance(records[-1], Mapping)
        or records[-1].get("record_type") != "summary"
    ):
        raise ContractError(
            "ArcShuttle result stream must end with exactly one summary"
        )
    results = [
        _validate_result(record, index, windows=windows)
        for index, record in enumerate(records[:-1], start=1)
    ]
    summary = _validate_summary(records[-1])

    run_id = summary["run_id"]
    seen_ids: set[str] = set()
    outputs: dict[str, str] = {}
    staging_paths: dict[str, tuple[str, str]] = {}
    counts: Counter[str] = Counter()
    for result in results:
        staging_path = result["staging_path"]
        if staging_path is not None:
            staging_paths.setdefault(
                path_key(staging_path, windows=windows),
                (result["job_id"], staging_path),
            )

    for index, result in enumerate(results, start=1):
        if result["run_id"] != run_id:
            raise ContractError(
                f"ArcShuttle result {index} run_id does not match summary"
            )
        job_id = result["job_id"]
        if job_id in seen_ids:
            raise ContractError(f"duplicate ArcShuttle job_id: {job_id}")
        seen_ids.add(job_id)
        output_path = result["output_path"]
        output_key = path_key(output_path, windows=windows)
        if output_key in outputs:
            raise ContractError(
                "duplicate ArcShuttle output_path between "
                f"{outputs[output_key]} and {job_id}: {output_path!r}"
            )
        outputs[output_key] = job_id
        status = result["status"]
        counts[status] += 1

    if summary["total"] != len(results):
        raise ContractError("ArcShuttle summary total does not match result count")
    for status in sorted(UPSTREAM_STATUSES):
        if summary[status] != counts[status]:
            raise ContractError(
                f"ArcShuttle summary {status} count does not match results"
            )
    if sum(summary[status] for status in UPSTREAM_STATUSES) != summary["total"]:
        raise ContractError("ArcShuttle summary status counts do not sum to total")

    for result in results:
        if result["status"] != "success":
            continue
        output_path = result["output_path"]
        retained = staging_paths.get(path_key(output_path, windows=windows))
        if retained is not None:
            staging_job_id, staging_path = retained
            raise ContractError(
                f"ArcShuttle success output_path {output_path!r} collides with retained "
                f"staging_path {staging_path!r} from {staging_job_id}"
            )

    roots: list[str] = []
    diagnostics: list[ArcShuttleDiagnostic] = []
    for result in results:
        job_id = result["job_id"]
        status = result["status"]
        warnings = tuple(result["warnings"])
        if status == "success":
            output_path = result["output_path"]
            _validate_finalized_directory(output_path)
            roots.append(output_path)
            if warnings:
                diagnostics.append(
                    ArcShuttleDiagnostic(job_id, status, False, warnings)
                )
            continue
        messages = warnings or (f"upstream job completed with status {status}",)
        diagnostics.append(ArcShuttleDiagnostic(job_id, status, True, messages))

    if diagnostics and on_upstream_error == "fail":
        raise ArcShuttleUpstreamError(diagnostics)
    return ArcShuttleSelection(
        run_id=run_id,
        roots=tuple(roots),
        diagnostics=tuple(diagnostics),
        result_count=len(results),
        requires_warning_exit=bool(diagnostics),
    )


def load_arcshuttle_results(
    stream: BinaryIO,
    *,
    on_upstream_error: UpstreamErrorPolicy = "fail",
    windows: bool | None = None,
) -> ArcShuttleSelection:
    """Read through EOF and validate a BOM-free UTF-8 ArcShuttle result stream."""

    try:
        records = loads_json_lines(stream)
    except OSError as error:
        raise InputError(f"cannot read ArcShuttle result stream: {error}") from error
    return validate_arcshuttle_records(
        records,
        on_upstream_error=on_upstream_error,
        windows=windows,
    )


def read_arcshuttle_results(
    value: str | os.PathLike[str],
    *,
    stdin: BinaryIO | None = None,
    cwd: str | os.PathLike[str] | None = None,
    on_upstream_error: UpstreamErrorPolicy = "fail",
    windows: bool | None = None,
) -> ArcShuttleSelection:
    """Read an explicit ArcShuttle result filename or ``-`` for binary stdin."""

    name = os.fspath(value)
    if not isinstance(name, str):
        raise InputError("ArcShuttle result filename must be text, not bytes")
    if not name or "\x00" in name:
        raise InputError("ArcShuttle result filename must be nonempty and NUL-free")
    if name == "-":
        stream = stdin
        if stream is None:
            stream = getattr(sys.stdin, "buffer", None)
        if stream is None:
            raise InputError("binary stdin is required for --arcshuttle-results -")
        return load_arcshuttle_results(
            stream,
            on_upstream_error=on_upstream_error,
            windows=windows,
        )

    working_directory = os.fspath(cwd) if cwd is not None else os.getcwd()
    path = Path(name if os.path.isabs(name) else os.path.join(working_directory, name))
    try:
        with path.open("rb") as stream:
            return load_arcshuttle_results(
                stream,
                on_upstream_error=on_upstream_error,
                windows=windows,
            )
    except OSError as error:
        raise InputError(
            f"cannot read ArcShuttle results {str(path)!r}: {error}"
        ) from error


__all__ = [
    "ARCSHUTTLE_OPERATION",
    "ARCSHUTTLE_SCHEMA_VERSION",
    "UPSTREAM_STATUSES",
    "ArcShuttleDiagnostic",
    "ArcShuttleSelection",
    "ArcShuttleUpstreamError",
    "UpstreamErrorPolicy",
    "load_arcshuttle_results",
    "read_arcshuttle_results",
    "validate_arcshuttle_records",
]
