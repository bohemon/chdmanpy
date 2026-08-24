from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
from conftest import FakeChdman

import chdmanpy.cli as cli
from chdmanpy.chdman import ChdmanExecutable
from chdmanpy.config import RuntimeConfig, resolve_config
from chdmanpy.errors import ExitCode
from chdmanpy.jsonl import dump_json_lines
from chdmanpy.planner import plan_jobs
from chdmanpy.results import validate_result_stream
from chdmanpy.runner import RunOutcome

UPSTREAM_STATUSES = ("success", "warning", "failed", "skipped", "interrupted")


class _BinaryInput:
    def __init__(self, data: bytes) -> None:
        self.buffer = io.BytesIO(data)


def _records(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines()]


def _manifest(
    tmp_path: Path, *, count: int = 1
) -> tuple[list[dict[str, object]], Path]:
    source_dir = tmp_path / "input files"
    source_dir.mkdir()
    for index in range(count):
        (source_dir / f"disc {index} 日本語.iso").write_bytes(f"iso-{index}".encode())
    config = resolve_config(
        preset="ps2",
        output_dir=tmp_path / "output files",
        environ={},
    )
    return plan_jobs([source_dir], config), source_dir


def _write_manifest(path: Path, jobs: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        dump_json_lines(stream, jobs)


def _select_fake(
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeChdman,
) -> ChdmanExecutable:
    for name in ("FAKE_CHDMAN_CONTROL", "FAKE_CHDMAN_RECORD"):
        monkeypatch.setenv(name, fake.environment[name])
    executable = ChdmanExecutable(
        command=fake.command,
        source="explicit",
        description="chdman - fake test runtime",
    )
    monkeypatch.setattr(cli, "discover_chdman", Mock(return_value=executable))
    return executable


def _arc_result(
    tmp_path: Path,
    index: int,
    *,
    status: str,
    output_path: Path,
) -> dict[str, Any]:
    staging = (
        None
        if status in {"success", "skipped"}
        else str(tmp_path / f"arc-stage-{index}.failed")
    )
    exit_code = {
        "success": 0,
        "warning": 1,
        "failed": 2,
        "skipped": None,
        "interrupted": None,
    }[status]
    return {
        "schema_version": 2,
        "record_type": "result",
        "run_id": "20260824T064152Z-cli-test",
        "job_id": f"{index + 1:024x}",
        "path": str(tmp_path / f"archive-{index}.zip"),
        "status": status,
        "exit_code": exit_code,
        "started_at": "2026-08-24T06:41:52.011Z",
        "finished_at": "2026-08-24T06:41:52.012Z",
        "duration_ms": 1,
        "assigned_cpu_tokens": 1,
        "assigned_threads": 1,
        "output_dir": str(output_path),
        "staging_dir": staging,
        "log_path": str(tmp_path / "arc-logs" / f"job-{index}"),
        "warnings": [] if status == "success" else [f"upstream {status}"],
        "operation": "extract",
        "output_path": str(output_path),
        "staging_path": staging,
    }


def _write_arc_stream(path: Path, results: list[dict[str, Any]]) -> None:
    summary = {
        "schema_version": 2,
        "record_type": "summary",
        "run_id": "20260824T064152Z-cli-test",
        "total": len(results),
        **{
            status: sum(result["status"] == status for result in results)
            for status in UPSTREAM_STATUSES
        },
        "duration_ms": 2,
    }
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        dump_json_lines(stream, [*results, summary])


def test_plan_emits_only_valid_jobs_and_never_discovers_chdman(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "disc.iso"
    source.write_bytes(b"iso")
    discovery = Mock(side_effect=AssertionError("plan must not discover CHDMAN"))
    monkeypatch.setattr(cli, "discover_chdman", discovery)
    monkeypatch.setenv("CHDMANPY_CHDMAN", "missing-chdman-is-ignored-by-plan")

    exit_code = cli.main(
        [
            "plan",
            str(source),
            "--output-dir",
            str(tmp_path / "output"),
            "--preset",
            "ps2",
            "--existing",
            "skip",
            "--priority",
            "-7",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    assert captured.err == ""
    jobs = _records(captured.out)
    assert len(jobs) == 1
    assert jobs[0]["record_type"] == "job"
    assert jobs[0]["source"]["path"] == str(source)
    assert jobs[0]["destination"]["existing"] == "skip"
    assert jobs[0]["scheduling"]["priority"] == -7
    discovery.assert_not_called()


@pytest.mark.parametrize("selector", ["positional", "files-from"])
def test_plan_with_job_warning_emits_manifest_and_returns_warning_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    selector: str,
) -> None:
    source = tmp_path / "disc.cue"
    source.write_text('FILE "track.bin" BINARY\n', encoding="utf-8")
    selected_input = [str(source)]
    if selector == "files-from":
        path_list = tmp_path / "paths.list"
        path_list.write_text(f"{source}\n", encoding="utf-8")
        selected_input = ["--files-from", str(path_list)]
    discovery = Mock(side_effect=AssertionError("plan must not discover CHDMAN"))
    monkeypatch.setattr(cli, "discover_chdman", discovery)

    exit_code = cli.main(
        [
            "plan",
            *selected_input,
            "--output-dir",
            str(tmp_path / "output"),
            "--preset",
            "others",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == ExitCode.WARNING
    jobs = _records(captured.out)
    assert len(jobs) == 1
    assert jobs[0]["warnings"]
    assert captured.err == ""
    discovery.assert_not_called()


@pytest.mark.parametrize(
    ("selector", "delimiter"), [("--files-from", "\n"), ("--files0-from", "\0")]
)
def test_plan_accepts_explicit_path_list_selectors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    selector: str,
    delimiter: str,
) -> None:
    source = tmp_path / "disc.iso"
    source.write_bytes(b"iso")
    path_list = tmp_path / "paths.list"
    path_list.write_bytes((str(source) + delimiter).encode())

    exit_code = cli.main(
        [
            "plan",
            selector,
            str(path_list),
            "--preset",
            "ps2",
            "--output-dir",
            str(tmp_path / "output"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    assert captured.err == ""
    assert _records(captured.out)[0]["source"]["path"] == str(source)


def test_run_preflights_manifest_then_emits_ordered_results_and_summary(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    jobs, _ = _manifest(tmp_path, count=2)
    manifest = tmp_path / "jobs.jsonl"
    _write_manifest(manifest, jobs)
    fake = fake_chdman_factory({})
    _select_fake(monkeypatch, fake)

    exit_code = cli.main(
        [
            "run",
            "--manifest",
            str(manifest),
            "--workers",
            "2",
            "--log-dir",
            str(tmp_path / "logs"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    records = _records(captured.out)
    results, summary = validate_result_stream(records)
    assert [result["plan_index"] for result in results] == [0, 1]
    assert summary["success"] == 2
    assert "CHDMAN selected from explicit" in captured.err
    assert "CHDMAN version: chdman - fake test runtime" in captured.err
    assert "run log:" in captured.err
    assert "MAME Compressed" not in captured.out


def test_cli_chdman_beats_environment_and_toml_runtime(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    jobs, _ = _manifest(tmp_path)
    manifest = tmp_path / "jobs.jsonl"
    _write_manifest(manifest, jobs)
    config = tmp_path / "config.toml"
    config.write_text(
        '[runtime]\nchdman = "toml-chdman"\n',
        encoding="utf-8",
    )
    fake = fake_chdman_factory({})
    for name in ("FAKE_CHDMAN_CONTROL", "FAKE_CHDMAN_RECORD"):
        monkeypatch.setenv(name, fake.environment[name])
    monkeypatch.setenv("CHDMANPY_CHDMAN", "environment-chdman")
    executable = ChdmanExecutable(
        command=fake.command,
        source="explicit",
        description="chdman - fake test runtime",
    )

    def discover(*, explicit: object, runtime: RuntimeConfig) -> ChdmanExecutable:
        assert explicit == "cli-chdman"
        assert runtime.chdman == "environment-chdman"
        return executable

    discovery = Mock(side_effect=discover)
    monkeypatch.setattr(cli, "discover_chdman", discovery)

    exit_code = cli.main(
        [
            "run",
            "--manifest",
            str(manifest),
            "--config",
            str(config),
            "--chdman",
            "cli-chdman",
            "--log-dir",
            str(tmp_path / "logs"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    validate_result_stream(_records(captured.out))
    discovery.assert_called_once()
    assert "selected from explicit" in captured.err


@pytest.mark.parametrize(
    ("behavior", "expected_exit", "expected_status"),
    [
        ({"warning": "CHDMAN warning: test"}, ExitCode.WARNING, "warning"),
        (
            {"exit_code": 23, "partial_output_text": "partial"},
            ExitCode.JOB_FAILURE,
            "failed",
        ),
    ],
)
def test_convert_maps_warning_and_job_failure_exits(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    behavior: dict[str, object],
    expected_exit: ExitCode,
    expected_status: str,
) -> None:
    source = tmp_path / "disc 日本語.iso"
    source.write_bytes(b"iso")
    fake = fake_chdman_factory({"by_input": {str(source): behavior}})
    _select_fake(monkeypatch, fake)

    exit_code = cli.main(
        [
            "convert",
            str(source),
            "--preset",
            "ps2",
            "--output-dir",
            str(tmp_path / "output"),
            "--workers",
            "1",
            "--log-dir",
            str(tmp_path / "logs"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == expected_exit
    results, summary = validate_result_stream(_records(captured.out))
    assert results[0]["status"] == expected_status
    assert summary[expected_status] == 1
    assert all(record["record_type"] != "job" for record in _records(captured.out))


def test_arcshuttle_skip_uses_only_success_roots_and_forces_warning_exit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    extracted = tmp_path / "extracted" / "good"
    extracted.mkdir(parents=True)
    (extracted / "disc.iso").write_bytes(b"iso")
    clean = _arc_result(tmp_path, 0, status="success", output_path=extracted)
    failed = _arc_result(
        tmp_path,
        1,
        status="failed",
        output_path=tmp_path / "extracted" / "failed",
    )
    arc_stream = tmp_path / "arc-results.jsonl"
    _write_arc_stream(arc_stream, [clean, failed])

    exit_code = cli.main(
        [
            "plan",
            "--arcshuttle-results",
            str(arc_stream),
            "--on-upstream-error",
            "skip",
            "--preset",
            "ps2",
            "--output-dir",
            str(tmp_path / "chd"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == ExitCode.WARNING
    jobs = _records(captured.out)
    assert len(jobs) == 1
    assert jobs[0]["source"]["input_root"] == str(extracted)
    assert failed["output_path"] not in captured.out
    assert f"job {failed['job_id']} status=failed omitted" in captured.err


def test_primary_arcshuttle_stdin_pipeline_converts_without_job_records(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    extracted = tmp_path / "extracted" / "pipeline game"
    extracted.mkdir(parents=True)
    source = extracted / "disc.iso"
    source.write_bytes(b"iso")
    clean = _arc_result(tmp_path, 0, status="success", output_path=extracted)
    arc_stream = tmp_path / "arc-results.jsonl"
    _write_arc_stream(arc_stream, [clean])
    monkeypatch.setattr(cli.sys, "stdin", _BinaryInput(arc_stream.read_bytes()))
    fake = fake_chdman_factory({})
    _select_fake(monkeypatch, fake)

    exit_code = cli.main(
        [
            "convert",
            "--arcshuttle-results",
            "-",
            "--preset",
            "ps2",
            "--output-dir",
            str(tmp_path / "chd"),
            "--log-dir",
            str(tmp_path / "logs"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == ExitCode.SUCCESS
    results, summary = validate_result_stream(_records(captured.out))
    assert len(results) == 1
    assert results[0]["source_path"] == str(source)
    assert summary["success"] == 1
    assert '"record_type":"job"' not in captured.out


def test_arcshuttle_default_rejects_partial_run_without_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    failed = _arc_result(
        tmp_path,
        0,
        status="failed",
        output_path=tmp_path / "extracted" / "failed",
    )
    arc_stream = tmp_path / "arc-results.jsonl"
    _write_arc_stream(arc_stream, [failed])

    exit_code = cli.main(
        [
            "plan",
            "--arcshuttle-results",
            str(arc_stream),
            "--output-dir",
            str(tmp_path / "chd"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == ExitCode.USAGE
    assert captured.out == ""
    assert "ArcShuttle run is not clean" in captured.err


def test_arcshuttle_skip_diagnostic_survives_downstream_planning_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    extracted = tmp_path / "extracted" / "unsupported"
    extracted.mkdir(parents=True)
    (extracted / "readme.txt").write_text("not a disc", encoding="utf-8")
    clean = _arc_result(tmp_path, 0, status="success", output_path=extracted)
    failed = _arc_result(
        tmp_path,
        1,
        status="failed",
        output_path=tmp_path / "extracted" / "failed",
    )
    arc_stream = tmp_path / "arc-results.jsonl"
    _write_arc_stream(arc_stream, [clean, failed])

    exit_code = cli.main(
        [
            "plan",
            "--arcshuttle-results",
            str(arc_stream),
            "--on-upstream-error",
            "skip",
            "--preset",
            "ps2",
            "--output-dir",
            str(tmp_path / "chd"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == ExitCode.USAGE
    assert captured.out == ""
    assert f"job {failed['job_id']} status=failed omitted" in captured.err
    assert "no supported regular input files" in captured.err


def test_malformed_manifest_is_rejected_before_chdman_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = tmp_path / "bad.jsonl"
    manifest.write_text('{"schema_version":1}\n', encoding="utf-8")
    discovery = Mock(side_effect=AssertionError("discovery must follow preflight"))
    monkeypatch.setattr(cli, "discover_chdman", discovery)

    exit_code = cli.main(["run", "--manifest", str(manifest)])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.USAGE
    assert captured.out == ""
    assert "missing fields" in captured.err
    discovery.assert_not_called()


def test_invalid_worker_budget_is_rejected_before_input_or_discovery(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    discovery = Mock(side_effect=AssertionError("invalid CLI must not discover CHDMAN"))
    monkeypatch.setattr(cli, "discover_chdman", discovery)

    exit_code = cli.main(["run", "--manifest", "missing.jsonl", "--workers", "0"])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.USAGE
    assert captured.out == ""
    assert "positive integer" in captured.err
    discovery.assert_not_called()


def test_keyboard_interrupt_before_execution_maps_to_130_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    jobs, _ = _manifest(tmp_path)
    manifest = tmp_path / "jobs.jsonl"
    _write_manifest(manifest, jobs)
    monkeypatch.setattr(cli, "_run", Mock(side_effect=KeyboardInterrupt))

    exit_code = cli.main(["run", "--manifest", str(manifest)])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.INTERRUPTED
    assert captured.out == ""
    assert captured.err == "chdmanpy: interrupted\n"
    assert "Traceback" not in captured.err


def test_interrupted_run_emits_complete_result_stream_with_exit_130(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    jobs, _ = _manifest(tmp_path)
    manifest = tmp_path / "jobs.jsonl"
    _write_manifest(manifest, jobs)
    job = jobs[0]
    run_id = "run-cli-interrupted"
    result = {
        "schema_version": 1,
        "record_type": "result",
        "run_id": run_id,
        "job_id": job["job_id"],
        "plan_index": job["plan_index"],
        "status": "interrupted",
        "source_path": job["source"]["path"],
        "output_path": job["destination"]["path"],
        "staging_path": None,
        "log_path": None,
        "chdman_exit_code": None,
        "started_at": None,
        "finished_at": None,
        "duration_ms": 0,
        "error": "job was not started because the run was interrupted",
        "warnings": [],
    }
    summary = {
        "schema_version": 1,
        "record_type": "summary",
        "run_id": run_id,
        "total": 1,
        "success": 0,
        "warning": 0,
        "failed": 0,
        "skipped": 0,
        "interrupted": 1,
        "duration_ms": 0,
    }
    results, validated_summary = validate_result_stream([result, summary])
    outcome = RunOutcome(
        results=results,
        summary=validated_summary,
        exit_code=ExitCode.INTERRUPTED,
        executable=ChdmanExecutable(("fake",), "explicit", "fake"),
        run_log_path=str(tmp_path / "run.log"),
    )
    monkeypatch.setattr(cli, "_run", Mock(return_value=outcome))

    exit_code = cli.main(["run", "--manifest", str(manifest)])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.INTERRUPTED
    emitted_results, emitted_summary = validate_result_stream(_records(captured.out))
    assert emitted_results[0]["status"] == "interrupted"
    assert emitted_summary["interrupted"] == 1
    assert captured.err == ""


def test_full_fake_child_interruption_emits_results_and_returns_130(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    jobs, _ = _manifest(tmp_path)
    source = jobs[0]["source"]["path"]
    manifest = tmp_path / "jobs.jsonl"
    _write_manifest(manifest, jobs)
    fake = fake_chdman_factory(
        {"by_input": {source: {"delay_seconds": 30, "partial_output_text": "partial"}}}
    )
    _select_fake(monkeypatch, fake)

    def interrupt_after_child_start(*args: object, **kwargs: object) -> None:
        fake.wait_until_running(timeout=10, expected_input_path=source)
        raise KeyboardInterrupt

    with patch("chdmanpy.runner.wait", side_effect=interrupt_after_child_start):
        exit_code = cli.main(
            [
                "run",
                "--manifest",
                str(manifest),
                "--workers",
                "1",
                "--log-dir",
                str(tmp_path / "logs"),
            ]
        )

    captured = capsys.readouterr()
    assert exit_code == ExitCode.INTERRUPTED
    results, summary = validate_result_stream(_records(captured.out))
    assert results[0]["status"] == "interrupted"
    assert summary["interrupted"] == 1
    assert fake.read_record()["state"] == "interrupted"
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("command", ["run", "convert"])
def test_execution_commands_reject_missing_chdman_before_work(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    jobs, source_dir = _manifest(tmp_path)
    missing = tmp_path / "missing-tools" / "chdman"
    log_dir = tmp_path / f"{command}-logs"
    if command == "run":
        manifest = tmp_path / "jobs.jsonl"
        _write_manifest(manifest, jobs)
        arguments = ["run", "--manifest", str(manifest)]
    else:
        arguments = [
            "convert",
            str(source_dir),
            "--preset",
            "ps2",
            "--output-dir",
            str(tmp_path / "convert-output"),
        ]

    exit_code = cli.main(
        [*arguments, "--chdman", str(missing), "--log-dir", str(log_dir)]
    )

    captured = capsys.readouterr()
    assert exit_code == ExitCode.USAGE
    assert captured.out == ""
    assert "was not found or is not executable" in captured.err
    assert not log_dir.exists()


def test_equivalent_direct_manifest_and_arcshuttle_workflows(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_dir = tmp_path / "equivalent root"
    source_dir.mkdir()
    source = source_dir / "disc 日本語.iso"
    source.write_bytes(b"iso")
    outputs = {
        "direct": tmp_path / "direct-output",
        "manifest": tmp_path / "manifest-output",
        "arc": tmp_path / "arc-output",
    }

    def select_fresh_fake() -> None:
        _select_fake(monkeypatch, fake_chdman_factory({}))

    select_fresh_fake()
    direct_exit = cli.main(
        [
            "convert",
            str(source_dir),
            "--preset",
            "ps2",
            "--output-dir",
            str(outputs["direct"]),
            "--log-dir",
            str(tmp_path / "direct-logs"),
        ]
    )
    direct_capture = capsys.readouterr()

    plan_exit = cli.main(
        [
            "plan",
            str(source_dir),
            "--preset",
            "ps2",
            "--output-dir",
            str(outputs["manifest"]),
        ]
    )
    plan_capture = capsys.readouterr()
    manifest = tmp_path / "planned.jsonl"
    manifest.write_text(plan_capture.out, encoding="utf-8", newline="\n")
    select_fresh_fake()
    run_exit = cli.main(
        [
            "run",
            "--manifest",
            str(manifest),
            "--log-dir",
            str(tmp_path / "manifest-logs"),
        ]
    )
    run_capture = capsys.readouterr()

    arc_stream = tmp_path / "arc-results.jsonl"
    _write_arc_stream(
        arc_stream,
        [_arc_result(tmp_path, 0, status="success", output_path=source_dir)],
    )
    select_fresh_fake()
    arc_exit = cli.main(
        [
            "convert",
            "--arcshuttle-results",
            str(arc_stream),
            "--preset",
            "ps2",
            "--output-dir",
            str(outputs["arc"]),
            "--log-dir",
            str(tmp_path / "arc-logs"),
        ]
    )
    arc_capture = capsys.readouterr()

    assert [direct_exit, plan_exit, run_exit, arc_exit] == [
        ExitCode.SUCCESS,
        ExitCode.SUCCESS,
        ExitCode.SUCCESS,
        ExitCode.SUCCESS,
    ]
    workflow_records = [
        validate_result_stream(_records(capture.out))
        for capture in (direct_capture, run_capture, arc_capture)
    ]
    semantics = []
    for name, (results, summary) in zip(outputs, workflow_records, strict=True):
        semantics.append(
            (
                results[0]["source_path"],
                Path(results[0]["output_path"]).relative_to(outputs[name]),
                results[0]["status"],
                summary["success"],
                summary["failed"],
            )
        )
    assert semantics[0] == semantics[1] == semantics[2]
