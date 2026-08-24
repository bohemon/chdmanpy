"""Schema-v1 result streams, summaries, and process-exit policy."""

from __future__ import annotations

import copy
import re
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from typing import Any, BinaryIO

from chdmanpy.errors import ContractError, ExitCode
from chdmanpy.jsonl import loads_json_lines
from chdmanpy.manifest import SCHEMA_VERSION, is_absolute_path

RESULT_RECORD_TYPE = "result"
SUMMARY_RECORD_TYPE = "summary"
RESULT_STATUSES = ("success", "warning", "failed", "skipped", "interrupted")

_RESULT_FIELDS = frozenset(
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
_RFC3339_UTC_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z\Z"
)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _exact_object(
    value: object, fields: frozenset[str], location: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{location} must be an object")
    actual = frozenset(value)
    missing = sorted(fields - actual)
    unknown = sorted(actual - fields)
    if missing:
        raise ContractError(f"{location} is missing fields: {', '.join(missing)}")
    if unknown:
        raise ContractError(f"{location} contains unknown fields: {', '.join(unknown)}")
    return value


def _string(value: object, location: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        suffix = " or null" if nullable else ""
        raise ContractError(f"{location} must be a nonempty string{suffix}")
    return value


def _id(value: object, location: str) -> str:
    identifier = _string(value, location)
    assert identifier is not None
    if not _SHORT_HEX_RE.fullmatch(identifier):
        raise ContractError(f"{location} must contain exactly 24 lowercase hex digits")
    return identifier


def _path(
    value: object,
    location: str,
    *,
    windows: bool | None,
    nullable: bool = False,
) -> str | None:
    path = _string(value, location, nullable=nullable)
    if path is None:
        return None
    if "\x00" in path:
        raise ContractError(f"{location} must not contain NUL")
    if not is_absolute_path(path, windows=windows):
        raise ContractError(f"{location} must be an absolute path")
    return path


def _timestamp(value: object, location: str, *, nullable: bool = False) -> str | None:
    timestamp = _string(value, location, nullable=nullable)
    if timestamp is None:
        return None
    if not _RFC3339_UTC_RE.fullmatch(timestamp):
        raise ContractError(
            f"{location} must be an RFC 3339 UTC timestamp in "
            "YYYY-MM-DDTHH:MM:SS(.fraction)?Z form"
        )
    try:
        parsed = datetime.fromisoformat(f"{timestamp[:-1]}+00:00")
    except ValueError as error:
        raise ContractError(f"{location} must be a valid RFC 3339 timestamp") from error
    if parsed.utcoffset() is None:
        raise ContractError(f"{location} must include a UTC offset")
    return timestamp


def _warnings(value: object, location: str) -> list[str]:
    if not isinstance(value, list):
        raise ContractError(f"{location} must be an array")
    for index, warning in enumerate(value):
        if not isinstance(warning, str) or not warning:
            raise ContractError(f"{location}[{index}] must be a nonempty string")
    return value


def validate_result_record(
    record: object, *, windows: bool | None = None
) -> dict[str, Any]:
    """Validate one schema-v1 per-job result record."""

    result = _exact_object(record, _RESULT_FIELDS, "result record")
    if result["schema_version"] != SCHEMA_VERSION or not _is_int(
        result["schema_version"]
    ):
        raise ContractError(
            f"unsupported result schema_version: {result['schema_version']!r}"
        )
    if result["record_type"] != RESULT_RECORD_TYPE:
        raise ContractError(
            f"unsupported result record_type: {result['record_type']!r}"
        )
    _string(result["run_id"], "result.run_id")
    _id(result["job_id"], "result.job_id")
    plan_index = result["plan_index"]
    if not _is_int(plan_index) or plan_index < 0:
        raise ContractError("result.plan_index must be a nonnegative integer")
    if result["status"] not in RESULT_STATUSES:
        raise ContractError(f"unsupported result.status: {result['status']!r}")
    _path(result["source_path"], "result.source_path", windows=windows)
    _path(result["output_path"], "result.output_path", windows=windows)
    _path(result["staging_path"], "result.staging_path", windows=windows, nullable=True)
    _path(result["log_path"], "result.log_path", windows=windows, nullable=True)
    exit_code = result["chdman_exit_code"]
    if exit_code is not None and not _is_int(exit_code):
        raise ContractError("result.chdman_exit_code must be an integer or null")
    started_at = _timestamp(result["started_at"], "result.started_at", nullable=True)
    finished_at = _timestamp(result["finished_at"], "result.finished_at", nullable=True)
    if (started_at is None) != (finished_at is None):
        raise ContractError(
            "result.started_at and result.finished_at must both be set or null"
        )
    duration_ms = result["duration_ms"]
    if not _is_int(duration_ms) or duration_ms < 0:
        raise ContractError("result.duration_ms must be a nonnegative integer")
    _string(result["error"], "result.error", nullable=True)
    _warnings(result["warnings"], "result.warnings")
    return copy.deepcopy(result)


def validate_summary_record(record: object) -> dict[str, Any]:
    """Validate one schema-v1 terminal summary record."""

    summary = _exact_object(record, _SUMMARY_FIELDS, "summary record")
    if summary["schema_version"] != SCHEMA_VERSION or not _is_int(
        summary["schema_version"]
    ):
        raise ContractError(
            f"unsupported summary schema_version: {summary['schema_version']!r}"
        )
    if summary["record_type"] != SUMMARY_RECORD_TYPE:
        raise ContractError(
            f"unsupported summary record_type: {summary['record_type']!r}"
        )
    _string(summary["run_id"], "summary.run_id")
    total = summary["total"]
    if not _is_int(total) or total < 0:
        raise ContractError("summary.total must be a nonnegative integer")
    for status in RESULT_STATUSES:
        count = summary[status]
        if not _is_int(count) or count < 0:
            raise ContractError(f"summary.{status} must be a nonnegative integer")
    if sum(summary[status] for status in RESULT_STATUSES) != total:
        raise ContractError("summary counts must add up to summary.total")
    duration_ms = summary["duration_ms"]
    if not _is_int(duration_ms) or duration_ms < 0:
        raise ContractError("summary.duration_ms must be a nonnegative integer")
    return copy.deepcopy(summary)


def validate_result_stream(
    records: Sequence[object], *, windows: bool | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate a complete ordered result stream and its terminal summary."""

    if not records:
        raise ContractError("result stream is missing its terminal summary")
    if (
        not isinstance(records[-1], dict)
        or records[-1].get("record_type") != SUMMARY_RECORD_TYPE
    ):
        raise ContractError("result stream must end with exactly one summary record")
    for index, record in enumerate(records[:-1]):
        if (
            not isinstance(record, dict)
            or record.get("record_type") != RESULT_RECORD_TYPE
        ):
            raise ContractError(f"result stream record {index + 1} must be a result")

    results = [
        validate_result_record(record, windows=windows) for record in records[:-1]
    ]
    summary = validate_summary_record(records[-1])
    run_id = summary["run_id"]
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    for result in results:
        if result["run_id"] != run_id:
            raise ContractError("all result and summary run_id values must match")
        if result["job_id"] in seen_ids:
            raise ContractError(f"duplicate result job_id: {result['job_id']}")
        if result["plan_index"] in seen_indexes:
            raise ContractError(f"duplicate result plan_index: {result['plan_index']}")
        seen_ids.add(result["job_id"])
        seen_indexes.add(result["plan_index"])

    if [result["plan_index"] for result in results] != list(range(len(results))):
        raise ContractError(
            "result plan_index values must be contiguous and emitted in order "
            "starting at zero"
        )
    actual_counts = Counter(result["status"] for result in results)
    counts_match = all(
        summary[status] == actual_counts[status] for status in RESULT_STATUSES
    )
    if summary["total"] != len(results) or not counts_match:
        raise ContractError("summary counts do not match the result records")
    return results, summary


def load_result_stream(
    stream: BinaryIO, *, windows: bool | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read and validate a complete schema-v1 result stream."""

    return validate_result_stream(loads_json_lines(stream), windows=windows)


def exit_code_for_results(
    results: Sequence[dict[str, Any]], summary: dict[str, Any]
) -> ExitCode:
    """Apply the stable process-exit precedence to a validated run."""

    if summary["interrupted"]:
        return ExitCode.INTERRUPTED
    if summary["failed"]:
        return ExitCode.JOB_FAILURE
    any_warnings = any(result["warnings"] for result in results)
    if summary["warning"] or summary["skipped"] or any_warnings:
        return ExitCode.WARNING
    return ExitCode.SUCCESS


__all__ = [
    "RESULT_RECORD_TYPE",
    "RESULT_STATUSES",
    "SUMMARY_RECORD_TYPE",
    "exit_code_for_results",
    "load_result_stream",
    "validate_result_record",
    "validate_result_stream",
    "validate_summary_record",
]
