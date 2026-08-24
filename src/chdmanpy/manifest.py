"""Schema-v1 chdmanpy job manifests and complete-stream preflight."""

from __future__ import annotations

import copy
import hashlib
import hmac
import ntpath
import os
import posixpath
import re
from collections.abc import Mapping, Sequence
from typing import Any, BinaryIO

from chdmanpy.errors import ContractError
from chdmanpy.jsonl import canonical_json_bytes, loads_json_lines

SCHEMA_VERSION = 1
JOB_RECORD_TYPE = "job"
ALLOWED_OPERATIONS = frozenset({"createcd", "createdvd"})
EXISTING_POLICIES = frozenset({"fail", "skip", "rename"})
EDITABLE_JOB_FIELDS = frozenset({"destination.path", "scheduling.priority", "tags"})

_JOB_FIELDS = frozenset(
    {
        "schema_version",
        "record_type",
        "job_id",
        "plan_index",
        "source",
        "destination",
        "chdman",
        "scheduling",
        "tags",
        "warnings",
        "integrity",
    }
)
_SOURCE_FIELDS = frozenset({"path", "input_root", "size", "mtime_ns", "identity"})
_DESTINATION_FIELDS = frozenset({"path", "existing"})
_CHDMAN_FIELDS = frozenset({"operation", "options"})
_SCHEDULING_FIELDS = frozenset({"priority", "estimated_weight"})
_SHORT_HEX_RE = re.compile(r"[0-9a-f]{24}\Z")
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MANAGED_OPTIONS = frozenset({"-f", "-i", "-o", "--force", "--input", "--output"})


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


def _require_string(value: object, location: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{location} must be a string")
    if nonempty and not value:
        raise ContractError(f"{location} must not be empty")
    return value


def _require_string_list(value: object, location: str) -> list[str]:
    if not isinstance(value, list):
        raise ContractError(f"{location} must be an array")
    for index, item in enumerate(value):
        _require_string(item, f"{location}[{index}]")
    return value


def _windows_mode(windows: bool | None) -> bool:
    return os.name == "nt" if windows is None else windows


def path_key(path: str, *, windows: bool | None = None) -> str:
    """Return the host path comparison key used by job IDs and collisions."""

    if _windows_mode(windows):
        return ntpath.normcase(ntpath.normpath(path)).casefold()
    return posixpath.normpath(path)


def is_absolute_path(path: str, *, windows: bool | None = None) -> bool:
    """Return whether *path* is fully qualified for the selected host syntax."""

    if _windows_mode(windows):
        drive, tail = ntpath.splitdrive(path)
        return bool(drive and tail.startswith(("\\", "/"))) or path.startswith("\\\\")
    return posixpath.isabs(path)


def _validate_absolute_path(
    value: object, location: str, *, windows: bool | None
) -> str:
    path = _require_string(value, location)
    if "\x00" in path:
        raise ContractError(f"{location} must not contain NUL")
    if not is_absolute_path(path, windows=windows):
        raise ContractError(f"{location} must be an absolute path")
    if _windows_mode(windows):
        normalized_separators = path.replace("/", "\\")
        is_device_path = normalized_separators.startswith(("\\\\?\\", "\\\\.\\"))
        if not is_device_path:
            components = (
                part
                for part in normalized_separators.split("\\")
                if part and part not in {".", ".."}
            )
            if any(component.endswith((".", " ")) for component in components):
                raise ContractError(
                    f"{location} must not contain Windows path components ending "
                    "in a period or space"
                )
    return path


def _is_equal_or_beneath(path: str, root: str, *, windows: bool | None = None) -> bool:
    path_module = ntpath if _windows_mode(windows) else posixpath
    try:
        common = path_module.commonpath(
            (path_key(path, windows=windows), path_key(root, windows=windows))
        )
    except ValueError:
        return False
    return common == path_key(root, windows=windows)


def make_job_id(
    source_path: str,
    source_identity: str,
    operation: str,
    options: Sequence[str],
    *,
    windows: bool | None = None,
) -> str:
    """Create the deterministic 24-hex schema-v1 job identifier."""

    value = {
        "schema_version": SCHEMA_VERSION,
        "source_path": path_key(source_path, windows=windows),
        "source_identity": source_identity,
        "operation": operation,
        "options": list(options),
    }
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()[:24]


def _protected_job_value(record: Mapping[str, object]) -> dict[str, object]:
    protected = copy.deepcopy(dict(record))
    protected.pop("integrity", None)
    destination = protected.get("destination")
    scheduling = protected.get("scheduling")
    if not isinstance(destination, dict) or not isinstance(scheduling, dict):
        raise ContractError("job destination and scheduling must be objects")
    destination.pop("path", None)
    scheduling.pop("priority", None)
    protected.pop("tags", None)
    return protected


def compute_job_integrity(record: Mapping[str, object]) -> str:
    """Compute schema-v1 integrity while excluding documented editable fields."""

    digest = hashlib.sha256(
        canonical_json_bytes(_protected_job_value(record))
    ).hexdigest()
    return f"sha256:{digest}"


def add_job_integrity(record: Mapping[str, object]) -> dict[str, object]:
    """Return a deep copy with its schema-v1 integrity value populated."""

    result = copy.deepcopy(dict(record))
    result["integrity"] = compute_job_integrity(result)
    return result


def validate_job_record(
    record: object, *, windows: bool | None = None
) -> dict[str, Any]:
    """Validate one strict job record and return a defensive copy."""

    job = _require_exact_fields(record, _JOB_FIELDS, "job record")
    source = _require_exact_fields(job["source"], _SOURCE_FIELDS, "job.source")
    destination = _require_exact_fields(
        job["destination"], _DESTINATION_FIELDS, "job.destination"
    )
    chdman = _require_exact_fields(job["chdman"], _CHDMAN_FIELDS, "job.chdman")
    scheduling = _require_exact_fields(
        job["scheduling"], _SCHEDULING_FIELDS, "job.scheduling"
    )

    integrity = _require_string(job["integrity"], "job.integrity")
    if not _SHA256_RE.fullmatch(integrity):
        raise ContractError(
            "job.integrity must be 'sha256:' followed by 64 lowercase hex digits"
        )
    expected_integrity = compute_job_integrity(job)
    if not hmac.compare_digest(integrity, expected_integrity):
        raise ContractError("job integrity does not match protected fields")

    if job["schema_version"] != SCHEMA_VERSION or not _is_int(job["schema_version"]):
        raise ContractError(
            f"unsupported job schema_version: {job['schema_version']!r}"
        )
    if job["record_type"] != JOB_RECORD_TYPE:
        raise ContractError(f"unsupported manifest record_type: {job['record_type']!r}")

    job_id = _require_string(job["job_id"], "job.job_id")
    if not _SHORT_HEX_RE.fullmatch(job_id):
        raise ContractError("job.job_id must contain exactly 24 lowercase hex digits")
    plan_index = job["plan_index"]
    if not _is_int(plan_index) or plan_index < 0:
        raise ContractError("job.plan_index must be a nonnegative integer")

    source_path = _validate_absolute_path(
        source["path"], "job.source.path", windows=windows
    )
    input_root = _validate_absolute_path(
        source["input_root"], "job.source.input_root", windows=windows
    )
    if not _is_equal_or_beneath(source_path, input_root, windows=windows):
        raise ContractError(
            "job.source.path must be equal to or beneath job.source.input_root"
        )
    for field in ("size", "mtime_ns"):
        value = source[field]
        if not _is_int(value) or value < 0:
            raise ContractError(f"job.source.{field} must be a nonnegative integer")
    source_identity = _require_string(source["identity"], "job.source.identity")
    if not _SHA256_RE.fullmatch(source_identity):
        raise ContractError(
            "job.source.identity must be 'sha256:' followed by 64 lowercase hex digits"
        )

    _validate_absolute_path(
        destination["path"], "job.destination.path", windows=windows
    )
    existing_policy = _require_string(
        destination["existing"], "job.destination.existing"
    )
    if existing_policy not in EXISTING_POLICIES:
        raise ContractError("job.destination.existing must be fail, skip, or rename")

    operation = _require_string(chdman["operation"], "job.chdman.operation")
    if operation not in ALLOWED_OPERATIONS:
        raise ContractError("job.chdman.operation must be createcd or createdvd")
    options = _require_string_list(chdman["options"], "job.chdman.options")
    for option in options:
        name = option.split("=", maxsplit=1)[0]
        if name in _MANAGED_OPTIONS or option.startswith(("-i=", "-o=", "-f=")):
            raise ContractError(
                f"job.chdman.options must not set managed option {name!r}"
            )
        if "\x00" in option:
            raise ContractError("job.chdman.options must not contain NUL")

    priority = scheduling["priority"]
    if not _is_int(priority) or not -(2**31) <= priority < 2**31:
        raise ContractError("job.scheduling.priority must be a signed 32-bit integer")
    estimated_weight = scheduling["estimated_weight"]
    if not _is_int(estimated_weight) or estimated_weight < 0:
        raise ContractError(
            "job.scheduling.estimated_weight must be a nonnegative integer"
        )

    _require_string_list(job["tags"], "job.tags")
    _require_string_list(job["warnings"], "job.warnings")

    expected_job_id = make_job_id(
        source_path,
        source_identity,
        operation,
        options,
        windows=windows,
    )
    if not hmac.compare_digest(job_id, expected_job_id):
        raise ContractError("job.job_id is not the deterministic ID for this job")

    return copy.deepcopy(job)


def validate_manifest_records(
    records: Sequence[object], *, windows: bool | None = None
) -> list[dict[str, Any]]:
    """Validate a complete manifest before returning any runnable jobs."""

    if not records:
        raise ContractError("job manifest must contain at least one job record")
    jobs = [validate_job_record(record, windows=windows) for record in records]
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    destinations: dict[str, str] = {}
    input_roots = [job["source"]["input_root"] for job in jobs]
    for job in jobs:
        job_id = job["job_id"]
        plan_index = job["plan_index"]
        destination = job["destination"]["path"]
        if job_id in seen_ids:
            raise ContractError(f"duplicate job_id: {job_id}")
        if plan_index in seen_indexes:
            raise ContractError(f"duplicate plan_index: {plan_index}")
        destination_key = path_key(destination, windows=windows)
        if destination_key in destinations:
            raise ContractError(
                "destination collision between "
                f"{destinations[destination_key]!r} and {destination!r}"
            )
        for input_root in input_roots:
            if _is_equal_or_beneath(destination, input_root, windows=windows):
                raise ContractError(
                    f"job destination {destination!r} must not be equal to or beneath "
                    f"any job.source.input_root ({input_root!r})"
                )
        seen_ids.add(job_id)
        seen_indexes.add(plan_index)
        destinations[destination_key] = destination

    expected_indexes = set(range(len(jobs)))
    if seen_indexes != expected_indexes:
        raise ContractError("job plan_index values must be contiguous starting at zero")
    return sorted(jobs, key=lambda job: job["plan_index"])


def load_manifest(
    stream: BinaryIO, *, windows: bool | None = None
) -> list[dict[str, Any]]:
    """Read and completely preflight a schema-v1 job manifest."""

    return validate_manifest_records(loads_json_lines(stream), windows=windows)


__all__ = [
    "ALLOWED_OPERATIONS",
    "EDITABLE_JOB_FIELDS",
    "EXISTING_POLICIES",
    "JOB_RECORD_TYPE",
    "SCHEMA_VERSION",
    "add_job_integrity",
    "compute_job_integrity",
    "is_absolute_path",
    "load_manifest",
    "make_job_id",
    "path_key",
    "validate_job_record",
    "validate_manifest_records",
]
