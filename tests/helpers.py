"""Contract fixture builders."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from chdmanpy.manifest import add_job_integrity, make_job_id


def job_record(
    *,
    plan_index: int = 0,
    source_path: str = "/input/Game One/disc.cue",
    destination_path: str = "/output/Game One/disc.chd",
    identity_digit: str = "1",
    options: Sequence[str] = ("-c", "zstd"),
) -> dict[str, Any]:
    identity = f"sha256:{identity_digit * 64}"
    record: dict[str, Any] = {
        "schema_version": 1,
        "record_type": "job",
        "job_id": make_job_id(source_path, identity, "createcd", options),
        "plan_index": plan_index,
        "source": {
            "path": source_path,
            "input_root": "/input/Game One",
            "size": 1234,
            "mtime_ns": 1_700_000_000_000_000_000,
            "identity": identity,
        },
        "destination": {"path": destination_path, "existing": "fail"},
        "chdman": {"operation": "createcd", "options": list(options)},
        "scheduling": {"priority": 0, "estimated_weight": 1234},
        "tags": ["fixture"],
        "warnings": [],
    }
    return add_job_integrity(record)


def result_record(
    *,
    plan_index: int = 0,
    status: str = "success",
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "record_type": "result",
        "run_id": "run-fixture",
        "job_id": f"{plan_index + 1:024x}",
        "plan_index": plan_index,
        "status": status,
        "source_path": f"/input/disc-{plan_index}.cue",
        "output_path": f"/output/disc-{plan_index}.chd",
        "staging_path": None,
        "log_path": f"/logs/{plan_index}.log",
        "chdman_exit_code": 0 if status in {"success", "warning"} else None,
        "started_at": "2026-08-24T01:02:03Z",
        "finished_at": "2026-08-24T01:02:04.123Z",
        "duration_ms": 1123,
        "error": "conversion failed" if status == "failed" else None,
        "warnings": [] if warnings is None else warnings,
    }


def summary_record(statuses: Sequence[str]) -> dict[str, Any]:
    counts = {
        status: sum(value == status for value in statuses)
        for status in ("success", "warning", "failed", "skipped", "interrupted")
    }
    return {
        "schema_version": 1,
        "record_type": "summary",
        "run_id": "run-fixture",
        "total": len(statuses),
        **counts,
        "duration_ms": 1123 if statuses else 0,
    }
