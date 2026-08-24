from __future__ import annotations

import builtins
import copy
import importlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

import chdmanpy.arcshuttle as adapter
from chdmanpy.config import resolve_config
from chdmanpy.errors import ContractError, InputError
from chdmanpy.planner import plan_jobs

FIXTURE = Path(__file__).parent / "fixtures" / "arcshuttle-v0.3.2-success.jsonl"
STATUSES = ("success", "warning", "failed", "skipped", "interrupted")


def encode(records: list[dict[str, Any]]) -> io.BytesIO:
    payload = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    )
    return io.BytesIO(payload.encode("utf-8"))


def result_record(
    root: Path,
    index: int = 0,
    *,
    status: str = "success",
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    output = root / f"output-{index}"
    exit_codes = {
        "success": 0,
        "warning": 1,
        "failed": 2,
        "skipped": None,
        "interrupted": None,
    }
    staging = (
        None if status in {"success", "skipped"} else root / f"stage-{index}.failed"
    )
    return {
        "schema_version": 2,
        "record_type": "result",
        "run_id": "20260824T064152Z-796729f7",
        "job_id": f"{index + 1:024x}",
        "path": str(root / f"archive-{index}.zip"),
        "status": status,
        "exit_code": exit_codes[status],
        "started_at": "2026-08-24T06:41:52.011Z",
        "finished_at": "2026-08-24T06:41:52.012Z",
        "duration_ms": 1,
        "assigned_cpu_tokens": 1,
        "assigned_threads": 1,
        "output_dir": str(output),
        "staging_dir": None if staging is None else str(staging),
        "log_path": str(root / "logs" / f"job-{index}"),
        "warnings": [] if warnings is None else warnings,
        "operation": "extract",
        "output_path": str(output),
        "staging_path": None if staging is None else str(staging),
    }


def summary_record(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "record_type": "summary",
        "run_id": "20260824T064152Z-796729f7",
        "total": len(results),
        **{
            status: sum(result["status"] == status for result in results)
            for status in STATUSES
        },
        "duration_ms": 3,
    }


def stream_records(*results: dict[str, Any]) -> list[dict[str, Any]]:
    values = list(results)
    return [*values, summary_record(values)]


def finalized(result: dict[str, Any]) -> Path:
    path = Path(result["output_path"])
    path.mkdir(parents=True)
    return path


def captured_fixture_records(tmp_path: Path) -> list[dict[str, Any]]:
    records = [
        json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()
    ]
    result = records[0]
    output = tmp_path / "extracted" / "空 白"
    output.mkdir(parents=True)
    result["path"] = str(tmp_path / "archives" / "空 白.zip")
    result["output_dir"] = result["output_path"] = str(output)
    result["log_path"] = str(tmp_path / "logs" / result["run_id"] / result["job_id"])
    return records


def test_v032_fixture_is_a_clean_finalized_selection(tmp_path: Path) -> None:
    records = captured_fixture_records(tmp_path)

    selection = adapter.load_arcshuttle_results(encode(records))

    assert selection.run_id == records[-1]["run_id"]
    assert selection.roots == (records[0]["output_path"],)
    assert selection.diagnostics == ()
    assert selection.result_count == 1
    assert selection.requires_warning_exit is False


def test_arcshuttle_roots_plan_exactly_like_direct_directories(tmp_path: Path) -> None:
    extracted = tmp_path / "extracted" / "Game One"
    extracted.mkdir(parents=True)
    (extracted / "disc.iso").write_bytes(b"disc")
    result = result_record(tmp_path)
    result["output_dir"] = result["output_path"] = str(extracted)
    selection = adapter.validate_arcshuttle_records(stream_records(result))
    config = resolve_config(preset="ps2", output_dir=tmp_path / "chd", environ={})

    assert plan_jobs(selection.roots, config) == plan_jobs([extracted], config)


@pytest.mark.parametrize("status", ["warning", "failed", "skipped", "interrupted"])
def test_default_policy_rejects_every_non_success_status(
    tmp_path: Path, status: str
) -> None:
    result = result_record(tmp_path, status=status)

    with pytest.raises(adapter.ArcShuttleUpstreamError) as caught:
        adapter.validate_arcshuttle_records(stream_records(result))

    assert caught.value.diagnostics[0].status == status
    assert caught.value.diagnostics[0].omitted is True


def test_skip_policy_returns_only_finalized_success_subset_and_diagnostics(
    tmp_path: Path,
) -> None:
    clean = result_record(tmp_path, 0)
    finalized(clean)
    warning = result_record(tmp_path, 1, status="warning", warnings=["partial output"])
    warned_success = result_record(
        tmp_path, 2, status="success", warnings=["inspection warning"]
    )
    finalized(warned_success)

    selection = adapter.validate_arcshuttle_records(
        stream_records(clean, warning, warned_success),
        on_upstream_error="skip",
    )

    assert selection.roots == (clean["output_path"], warned_success["output_path"])
    assert selection.requires_warning_exit is True
    assert [(item.status, item.omitted) for item in selection.diagnostics] == [
        ("warning", True),
        ("success", False),
    ]
    assert warning["staging_path"] not in selection.roots


def test_success_output_must_not_substitute_cross_record_failed_staging(
    tmp_path: Path,
) -> None:
    failed = result_record(tmp_path, 0, status="failed")
    retained = Path(failed["staging_path"])
    retained.mkdir()
    (retained / ".arcshuttle-owned").write_text(
        failed["job_id"] + "\n", encoding="utf-8"
    )
    success = result_record(tmp_path, 1)
    success["output_dir"] = success["output_path"] = str(retained)

    with pytest.raises(ContractError, match="collides with retained staging_path"):
        adapter.validate_arcshuttle_records(
            stream_records(failed, success), on_upstream_error="skip"
        )


def test_windows_success_output_must_not_case_alias_retained_staging(
    tmp_path: Path,
) -> None:
    failed = result_record(tmp_path, 0, status="failed")
    failed["path"] = r"C:\Archives\failed.zip"
    failed["output_dir"] = failed["output_path"] = r"C:\Extracted\failed"
    failed["staging_dir"] = failed["staging_path"] = (
        r"C:\Stages\.arcshuttle-000000000000-deadbeef.failed"
    )
    failed["log_path"] = r"C:\Logs\failed"
    success = result_record(tmp_path, 1)
    success["path"] = r"C:\Archives\success.zip"
    success["output_dir"] = success["output_path"] = (
        r"c:\stages\.ARCSHUTTLE-000000000000-DEADBEEF.FAILED"
    )
    success["log_path"] = r"C:\Logs\success"

    with pytest.raises(ContractError, match="collides with retained staging_path"):
        adapter.validate_arcshuttle_records(
            stream_records(failed, success),
            on_upstream_error="skip",
            windows=True,
        )


@pytest.mark.parametrize("marker_kind", ["regular", "symlink"])
def test_success_output_rejects_arcshuttle_ownership_marker(
    tmp_path: Path, marker_kind: str
) -> None:
    result = result_record(tmp_path)
    output = finalized(result)
    marker = output / ".arcshuttle-owned"
    if marker_kind == "regular":
        marker.write_text(result["job_id"] + "\n", encoding="utf-8")
    else:
        try:
            marker.symlink_to(output / "missing-owner")
        except OSError:
            pytest.skip("symlink creation is unavailable")

    with pytest.raises(ContractError, match="ownership marker"):
        adapter.validate_arcshuttle_records(stream_records(result))


def test_success_result_warning_fails_clean_default_after_full_preflight(
    tmp_path: Path,
) -> None:
    result = result_record(tmp_path, warnings=["source changed"])
    finalized(result)

    with pytest.raises(adapter.ArcShuttleUpstreamError, match="source changed"):
        adapter.validate_arcshuttle_records(stream_records(result))


@pytest.mark.parametrize(
    ("status", "exit_code", "staging", "message"),
    [
        ("warning", 1, None, "retain a staging"),
        ("failed", 1, "failed", "must not have exit_code 1"),
        ("skipped", None, "failed", "null staging"),
    ],
)
def test_rejects_impossible_v032_status_exit_and_staging_combinations(
    tmp_path: Path,
    status: str,
    exit_code: int | None,
    staging: str | None,
    message: str,
) -> None:
    result = result_record(tmp_path, status=status)
    result["exit_code"] = exit_code
    staging_path = None if staging is None else str(tmp_path / "retained.failed")
    result["staging_dir"] = result["staging_path"] = staging_path

    with pytest.raises(ContractError, match=message):
        adapter.validate_arcshuttle_records(
            stream_records(result), on_upstream_error="skip"
        )


@pytest.mark.parametrize(
    ("exit_code", "has_staging"),
    [(None, False), (0, True), (2, True), (-15, True)],
)
def test_interrupted_v032_results_allow_started_and_unstarted_shapes(
    tmp_path: Path, exit_code: int | None, has_staging: bool
) -> None:
    result = result_record(tmp_path, status="interrupted")
    result["exit_code"] = exit_code
    staging_path = str(tmp_path / "retained.failed") if has_staging else None
    result["staging_dir"] = result["staging_path"] = staging_path

    selection = adapter.validate_arcshuttle_records(
        stream_records(result), on_upstream_error="skip"
    )

    assert selection.roots == ()
    assert selection.requires_warning_exit is True


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda records: records[-1].update(total=2), "total"),
        (lambda records: records[-1].update(success=0, failed=1), "count"),
        (lambda records: records[0].update(run_id="another-run"), "run_id"),
        (lambda records: records[0].update(schema_version=1), "schema_version"),
        (lambda records: records[0].update(operation="create"), "operation"),
        (lambda records: records[0].update(output_path="relative/output"), "absolute"),
        (
            lambda records: records[0].update(
                staging_path="/retained.failed", staging_dir="/retained.failed"
            ),
            "staging",
        ),
        (
            lambda records: records[0].update(
                output_dir=str(Path(records[0]["output_path"]).with_name("different"))
            ),
            "aliases",
        ),
        (
            lambda records: records[0].update(assigned_threads=2),
            "must not exceed assigned_cpu_tokens",
        ),
    ],
)
def test_rejects_inconsistent_or_unsupported_streams(
    tmp_path: Path, mutation: Any, message: str
) -> None:
    result = result_record(tmp_path)
    finalized(result)
    records = stream_records(result)
    mutation(records)

    with pytest.raises(ContractError, match=message):
        adapter.validate_arcshuttle_records(records)


def test_rejects_duplicate_job_ids_and_output_paths(tmp_path: Path) -> None:
    first = result_record(tmp_path, 0)
    second = result_record(tmp_path, 1)
    finalized(first)
    finalized(second)
    second["job_id"] = first["job_id"]
    with pytest.raises(ContractError, match="duplicate ArcShuttle job_id"):
        adapter.validate_arcshuttle_records(stream_records(first, second))

    second["job_id"] = f"{2:024x}"
    second["output_dir"] = second["output_path"] = first["output_path"]
    with pytest.raises(ContractError, match="duplicate ArcShuttle output_path"):
        adapter.validate_arcshuttle_records(stream_records(first, second))


def test_windows_output_collisions_and_unsafe_aliases_are_rejected(
    tmp_path: Path,
) -> None:
    first = result_record(tmp_path, 0, status="failed")
    second = result_record(tmp_path, 1, status="failed")
    for result, output in zip(
        (first, second), (r"C:\Extracted\GAME", r"c:\extracted\game"), strict=True
    ):
        result["path"] = rf"C:\Archives\{result['job_id']}.zip"
        result["output_dir"] = result["output_path"] = output
        result["staging_dir"] = result["staging_path"] = (
            rf"C:\Stages\{result['job_id']}.failed"
        )
        result["log_path"] = rf"C:\Logs\{result['job_id']}"

    with pytest.raises(ContractError, match="duplicate ArcShuttle output_path"):
        adapter.validate_arcshuttle_records(
            stream_records(first, second), on_upstream_error="skip", windows=True
        )

    first["output_dir"] = first["output_path"] = r"C:\Extracted\game."
    with pytest.raises(ContractError, match="period or space"):
        adapter.validate_arcshuttle_records(
            stream_records(first), on_upstream_error="skip", windows=True
        )


@pytest.mark.parametrize("kind", ["missing", "file", "symlink"])
def test_success_requires_existing_non_link_directory(
    tmp_path: Path, kind: str
) -> None:
    result = result_record(tmp_path)
    output = Path(result["output_path"])
    if kind == "file":
        output.write_bytes(b"not a directory")
    elif kind == "symlink":
        target = tmp_path / "real-output"
        target.mkdir()
        try:
            output.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation is unavailable")

    with pytest.raises(ContractError, match="does not exist|directory|symlink"):
        adapter.validate_arcshuttle_records(stream_records(result))


def test_rejects_trailing_records_invalid_json_and_bom(tmp_path: Path) -> None:
    result = result_record(tmp_path)
    finalized(result)
    complete = stream_records(result)
    with pytest.raises(ContractError, match="end with exactly one summary"):
        adapter.validate_arcshuttle_records([*complete, copy.deepcopy(result)])
    with pytest.raises(ContractError, match="invalid JSON"):
        adapter.load_arcshuttle_results(io.BytesIO(b"{not-json}\n"))
    with pytest.raises(ContractError, match="BOM"):
        adapter.load_arcshuttle_results(io.BytesIO(b"\xef\xbb\xbf{}\n"))


def test_reads_explicit_file_or_stdin_but_rejects_unknown_policy(
    tmp_path: Path,
) -> None:
    records = captured_fixture_records(tmp_path)
    filename = tmp_path / "results.jsonl"
    filename.write_bytes(encode(records).getvalue())

    assert adapter.read_arcshuttle_results("results.jsonl", cwd=tmp_path).roots == (
        records[0]["output_path"],
    )
    assert adapter.read_arcshuttle_results("-", stdin=encode(records)).roots == (
        records[0]["output_path"],
    )
    with pytest.raises(InputError, match="fail or skip"):
        adapter.validate_arcshuttle_records(
            records,
            on_upstream_error="ignore",  # type: ignore[arg-type]
        )


def test_stream_read_failure_is_an_input_error() -> None:
    class Unreadable(io.BytesIO):
        def read(self, *args: Any, **kwargs: Any) -> bytes:
            raise OSError("fixture read failure")

    with pytest.raises(InputError, match="fixture read failure"):
        adapter.load_arcshuttle_results(Unreadable())


def test_adapter_has_no_arcshuttle_runtime_or_command_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "arcshuttle" or name.startswith("arcshuttle."):
            raise AssertionError("ArcShuttle must not be imported")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    reloaded = importlib.reload(adapter)
    assert not hasattr(reloaded, "subprocess")
