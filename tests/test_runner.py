from __future__ import annotations

import contextlib
import io
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import MappingProxyType
from typing import Any
from unittest.mock import patch

import pytest
from conftest import FakeChdman

from chdmanpy.config import FormatConfig, PlanningConfig
from chdmanpy.errors import ContractError, ExitCode, RunnerError
from chdmanpy.jsonl import dump_json_lines
from chdmanpy.planner import plan_jobs
from chdmanpy.runner import RunnerOptions, run_jobs, run_manifest, staging_path_for


def _jobs(
    tmp_path: Path,
    count: int = 1,
    *,
    existing: str = "fail",
) -> tuple[list[dict[str, object]], list[Path], Path]:
    source_dir = tmp_path / "入力 sources"
    source_dir.mkdir(parents=True)
    sources: list[Path] = []
    for index in range(count):
        source = source_dir / f"disc {index} 日本語.iso"
        source.write_bytes(f"iso-{index}".encode())
        sources.append(source)
    output = tmp_path / "出力 results"
    config = PlanningConfig(
        output_dir=str(output),
        formats=MappingProxyType({".iso": FormatConfig("createdvd", ("-c", "zlib"))}),
        existing=existing,
    )
    return plan_jobs([source_dir], config), sources, output


def _options(
    fake: FakeChdman,
    tmp_path: Path,
    **overrides: object,
) -> RunnerOptions:
    values: dict[str, object] = {
        "workers": 1,
        "log_dir": tmp_path / "logs",
        "run_id": "test-run",
        "environment": fake.environment,
    }
    values.update(overrides)
    return RunnerOptions(**values)  # type: ignore[arg-type]


def _retained_output(result: dict[str, Any]) -> Path:
    staging = Path(result["staging_path"])
    return staging / "output.chd"


def test_success_warning_logs_unicode_and_stdout_purity(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    jobs, sources, _ = _jobs(tmp_path)
    fake = fake_chdman_factory(
        {
            "by_input": {
                str(sources[0]): {
                    "stdout": "compression complete\n",
                    "warning": "CHDMAN warning: synthetic\n",
                }
            }
        }
    )
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        outcome = run_jobs(
            jobs,
            chdman=fake.command,
            options=_options(fake, tmp_path),
        )

    assert stdout.getvalue() == ""
    assert stderr.getvalue() == ""
    assert outcome.exit_code == ExitCode.WARNING
    assert outcome.summary["warning"] == 1
    result = outcome.results[0]
    assert result["status"] == "warning"
    assert Path(result["output_path"]).read_bytes().startswith(b"MComprHD")
    assert result["staging_path"] is None
    staging = Path(
        staging_path_for(result["output_path"], "test-run", result["job_id"])
    )
    assert not staging.exists()
    assert "CHDMAN reported a warning" in result["warnings"][-1]
    job_log = Path(result["log_path"])
    assert "compression complete" in job_log.read_text(encoding="utf-8")
    assert "synthetic" in job_log.read_text(encoding="utf-8")
    assert "run started" in Path(outcome.run_log_path).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "behavior, error_fragment",
    [
        ({"create_output": False}, "did not produce"),
        ({"output_text": "short"}, "truncated"),
        ({"output_kind": "directory"}, "not a regular"),
    ],
)
def test_rejects_missing_truncated_and_nonregular_outputs(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
    behavior: dict[str, object],
    error_fragment: str,
) -> None:
    jobs, sources, _ = _jobs(tmp_path)
    fake = fake_chdman_factory({"by_input": {str(sources[0]): behavior}})
    outcome = run_jobs(jobs, chdman=fake.command, options=_options(fake, tmp_path))

    result = outcome.results[0]
    assert outcome.exit_code == ExitCode.JOB_FAILURE
    assert result["status"] == "failed"
    assert error_fragment in result["error"]
    staging = Path(result["staging_path"])
    assert staging.is_dir()
    assert staging.name.endswith(".failed")
    assert (staging / ".chdmanpy-owner").is_file()


@pytest.mark.skipif(os.name == "nt", reason="test symlink creation needs privileges")
def test_rejects_symlink_staging_output_without_following_or_publishing_it(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    jobs, sources, _ = _jobs(tmp_path)
    destination = Path(jobs[0]["destination"]["path"])
    fake = fake_chdman_factory(
        {"by_input": {str(sources[0]): {"output_kind": "symlink"}}}
    )
    outcome = run_jobs(jobs, chdman=fake.command, options=_options(fake, tmp_path))

    result = outcome.results[0]
    assert result["status"] == "failed"
    assert "symlink" in result["error"]
    assert not destination.exists()
    assert _retained_output(result).is_symlink()


def test_nonzero_failure_retains_owned_partial_staging(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    jobs, sources, _ = _jobs(tmp_path)
    fake = fake_chdman_factory(
        {
            "by_input": {
                str(sources[0]): {
                    "exit_code": 23,
                    "partial_output_text": "partial",
                }
            }
        }
    )
    outcome = run_jobs(jobs, chdman=fake.command, options=_options(fake, tmp_path))
    result = outcome.results[0]
    assert result["status"] == "failed"
    assert result["chdman_exit_code"] == 23
    assert _retained_output(result).read_text(encoding="utf-8") == "partial"
    assert (Path(result["staging_path"]) / ".chdmanpy-owner").is_file()
    assert sources[0].read_bytes() == b"iso-0"


def test_missing_and_changed_sources_are_checked_before_spawn(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    missing_jobs, missing_sources, _ = _jobs(tmp_path / "missing")
    missing_sources[0].unlink()
    missing_fake = fake_chdman_factory({})
    missing = run_jobs(
        missing_jobs,
        chdman=missing_fake.command,
        options=_options(missing_fake, tmp_path / "missing"),
    )
    assert missing.results[0]["status"] == "failed"
    assert "cannot inspect source" in missing.results[0]["error"]
    # The executable probe runs, but no job log means no CHDMAN conversion was started.
    assert missing.results[0]["log_path"] is None

    changed_jobs, changed_sources, _ = _jobs(tmp_path / "changed")
    changed_sources[0].write_bytes(b"changed-size-and-content")
    changed_fake = fake_chdman_factory({})
    changed = run_jobs(
        changed_jobs,
        chdman=changed_fake.command,
        options=_options(changed_fake, tmp_path / "changed"),
    )
    assert changed.results[0]["status"] == "failed"
    assert "metadata changed" in changed.results[0]["error"]


def test_allow_changed_runs_with_warning_status(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    jobs, sources, _ = _jobs(tmp_path)
    sources[0].write_bytes(b"changed-size-and-content")
    fake = fake_chdman_factory({})
    outcome = run_jobs(
        jobs,
        chdman=fake.command,
        options=_options(fake, tmp_path, allow_changed=True),
    )
    assert outcome.results[0]["status"] == "warning"
    assert "metadata changed" in outcome.results[0]["warnings"][-1]
    assert Path(outcome.results[0]["output_path"]).is_file()


@pytest.mark.parametrize(
    "policy, expected_status, renamed",
    [
        ("fail", "failed", False),
        ("skip", "skipped", False),
        ("rename", "success", True),
    ],
)
def test_existing_output_policies_never_overwrite(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
    policy: str,
    expected_status: str,
    renamed: bool,
) -> None:
    jobs, sources, _ = _jobs(tmp_path, existing=policy)
    destination = Path(jobs[0]["destination"]["path"])
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"existing-sentinel")
    fake = fake_chdman_factory({})
    outcome = run_jobs(jobs, chdman=fake.command, options=_options(fake, tmp_path))

    result = outcome.results[0]
    assert result["status"] == expected_status
    assert sources[0].read_bytes() == b"iso-0"
    assert destination.read_bytes() == b"existing-sentinel"
    if renamed:
        assert result["output_path"] == str(
            destination.with_name(f"{destination.stem} (1).chd")
        )
        assert Path(result["output_path"]).is_file()
    else:
        assert result["output_path"] == str(destination)


@pytest.mark.parametrize(
    "policy, expected_status, renamed",
    [
        ("fail", "failed", False),
        ("skip", "skipped", False),
        ("rename", "success", True),
    ],
)
def test_atomic_publish_race_obeys_policy_without_clobber(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
    policy: str,
    expected_status: str,
    renamed: bool,
) -> None:
    jobs, sources, _ = _jobs(tmp_path, existing=policy)
    destination = Path(jobs[0]["destination"]["path"])
    fake = fake_chdman_factory(
        {
            "by_input": {
                str(sources[0]): {
                    "race_destination": str(destination),
                    "race_bytes": "racer-sentinel",
                }
            }
        }
    )
    outcome = run_jobs(jobs, chdman=fake.command, options=_options(fake, tmp_path))
    result = outcome.results[0]
    assert result["status"] == expected_status
    assert destination.read_bytes() == b"racer-sentinel"
    if renamed:
        assert result["output_path"] != str(destination)
        assert Path(result["output_path"]).is_file()
    else:
        assert result["staging_path"] is not None
        staging = Path(result["staging_path"])
        assert staging.is_dir()
        assert _retained_output(result).is_file()
        assert (staging / ".chdmanpy-owner").is_file()


def test_refuses_unowned_staging_without_modifying_it(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    jobs, _, _ = _jobs(tmp_path)
    destination = jobs[0]["destination"]["path"]
    staging = Path(staging_path_for(destination, "test-run", jobs[0]["job_id"]))
    staging.mkdir(parents=True)
    sentinel = staging / "user-owned.txt"
    sentinel.write_bytes(b"unowned-sentinel")
    fake = fake_chdman_factory({})
    outcome = run_jobs(jobs, chdman=fake.command, options=_options(fake, tmp_path))
    assert outcome.results[0]["status"] == "failed"
    assert "unowned staging" in outcome.results[0]["error"]
    assert sentinel.read_bytes() == b"unowned-sentinel"
    assert not (staging / ".chdmanpy-owner").exists()


def test_tampered_owner_marker_is_never_published_or_cleaned(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    jobs, sources, _ = _jobs(tmp_path)
    destination = Path(jobs[0]["destination"]["path"])
    fake = fake_chdman_factory(
        {"by_input": {str(sources[0]): {"tamper_owner_text": "different owner\n"}}}
    )
    outcome = run_jobs(jobs, chdman=fake.command, options=_options(fake, tmp_path))

    result = outcome.results[0]
    staging = Path(result["staging_path"])
    assert result["status"] == "failed"
    assert "unverified staging" in result["error"]
    assert not destination.exists()
    assert (staging / ".chdmanpy-owner").read_text(encoding="utf-8") == (
        "different owner\n"
    )
    assert _retained_output(result).is_file()


def test_owner_marker_short_writes_are_completed(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    jobs, _, _ = _jobs(tmp_path)
    fake = fake_chdman_factory({})
    real_write = os.write

    def short_write(descriptor: int, contents: bytes) -> int:
        return real_write(descriptor, contents[: max(1, len(contents) // 2)])

    with patch("chdmanpy.runner.os.write", side_effect=short_write):
        outcome = run_jobs(jobs, chdman=fake.command, options=_options(fake, tmp_path))

    assert outcome.results[0]["status"] == "success"
    assert outcome.results[0]["staging_path"] is None


def test_owner_marker_initialization_failure_reports_retained_private_directory(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    jobs, _, _ = _jobs(tmp_path)
    destination = jobs[0]["destination"]["path"]
    staging = Path(staging_path_for(destination, "test-run", jobs[0]["job_id"]))
    fake = fake_chdman_factory({})
    with patch("chdmanpy.runner.os.fsync", side_effect=OSError("synthetic fsync")):
        outcome = run_jobs(jobs, chdman=fake.command, options=_options(fake, tmp_path))

    result = outcome.results[0]
    assert result["status"] == "failed"
    assert "cannot create staging ownership marker" in result["error"]
    assert result["staging_path"] == str(staging)
    assert staging.is_dir()
    assert (staging / ".chdmanpy-owner").is_file()
    # Only the executable probe ran; marker initialization failed before conversion.
    assert fake.read_record()["args"] == ["-help"]


def test_new_stage_inspection_failure_reports_retained_private_directory(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    jobs, _, _ = _jobs(tmp_path)
    destination = jobs[0]["destination"]["path"]
    staging = Path(staging_path_for(destination, "test-run", jobs[0]["job_id"]))
    fake = fake_chdman_factory({})
    real_lstat = os.lstat

    def fail_new_stage(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if os.fspath(path) == str(staging) and staging.is_dir():
            raise OSError("synthetic staging lstat failure")
        return real_lstat(path, *args, **kwargs)

    with patch("chdmanpy.runner.os.lstat", side_effect=fail_new_stage):
        outcome = run_jobs(jobs, chdman=fake.command, options=_options(fake, tmp_path))

    result = outcome.results[0]
    assert result["status"] == "failed"
    assert "cannot inspect newly created" in result["error"]
    assert result["staging_path"] == str(staging)
    assert staging.is_dir()
    assert list(staging.iterdir()) == []
    assert fake.read_record()["args"] == ["-help"]


def test_fail_fast_uses_bounded_submission_and_marks_pending_jobs(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    jobs, sources, _ = _jobs(tmp_path, count=4)
    fake = fake_chdman_factory({"by_input": {str(sources[0]): {"exit_code": 17}}})
    outcome = run_jobs(
        jobs,
        chdman=fake.command,
        options=_options(fake, tmp_path, fail_fast=True, workers=1),
    )
    assert [result["status"] for result in outcome.results] == [
        "failed",
        "skipped",
        "skipped",
        "skipped",
    ]
    assert outcome.summary["total"] == 4
    assert outcome.exit_code == ExitCode.JOB_FAILURE


def test_one_process_budget_and_plan_order_when_completion_is_out_of_order(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    jobs, sources, _ = _jobs(tmp_path, count=4)
    behaviors = {
        str(source): {"delay_seconds": 0.25 if index == 0 else 0.1}
        for index, source in enumerate(sources)
    }
    fake = fake_chdman_factory({"by_input": behaviors})
    started = time.monotonic()
    outcome = run_jobs(
        jobs,
        chdman=fake.command,
        options=_options(fake, tmp_path, workers=2),
    )
    elapsed = time.monotonic() - started
    assert 0.25 <= elapsed < 1.2
    assert [result["plan_index"] for result in outcome.results] == [0, 1, 2, 3]
    assert all(result["status"] == "success" for result in outcome.results)


def test_cancel_event_interrupts_owned_child_and_pending_jobs(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    jobs, sources, _ = _jobs(tmp_path, count=3)
    fake = fake_chdman_factory(
        {
            "by_input": {
                str(sources[0]): {
                    "delay_seconds": 30,
                    "partial_output_text": "partial",
                }
            }
        }
    )
    cancel = threading.Event()
    captured: dict[str, object] = {}

    def invoke() -> None:
        captured["outcome"] = run_jobs(
            jobs,
            chdman=fake.command,
            options=_options(fake, tmp_path, cancel_event=cancel, workers=1),
        )

    thread = threading.Thread(target=invoke)
    thread.start()
    fake.wait_until_running(timeout=10, expected_input_path=str(sources[0]))
    cancel.set()
    thread.join(timeout=15)
    assert not thread.is_alive()
    outcome = captured["outcome"]
    assert [result["status"] for result in outcome.results] == [  # type: ignore[union-attr]
        "interrupted",
        "interrupted",
        "interrupted",
    ]
    assert outcome.exit_code == ExitCode.INTERRUPTED  # type: ignore[union-attr]
    assert fake.read_record()["state"] == "interrupted"
    first_result = outcome.results[0]  # type: ignore[union-attr]
    retained = Path(first_result["staging_path"])
    assert retained.name.endswith(".failed")
    assert _retained_output(first_result).read_text(encoding="utf-8") == "partial"


def test_cancel_observed_by_submitted_worker_prevents_process_start(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    jobs, _, _ = _jobs(tmp_path)
    fake = fake_chdman_factory({})
    cancel = threading.Event()
    original_validate = __import__(
        "chdmanpy.runner", fromlist=["_validate_regular_source"]
    )._validate_regular_source

    def cancel_during_worker(job: dict[str, object]) -> tuple[list[str], str | None]:
        result = original_validate(job)
        cancel.set()
        return result

    with patch(
        "chdmanpy.runner._validate_regular_source", side_effect=cancel_during_worker
    ):
        outcome = run_jobs(
            jobs,
            chdman=fake.command,
            options=_options(fake, tmp_path, cancel_event=cancel),
        )

    assert outcome.results[0]["status"] == "interrupted"
    assert outcome.exit_code == ExitCode.INTERRUPTED
    assert fake.read_record()["args"] == ["-help"]


def test_rejects_log_directory_inside_input_root_before_creating_it(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    jobs, _, _ = _jobs(tmp_path)
    input_root = Path(jobs[0]["source"]["input_root"])
    log_dir = input_root / "runner logs"
    fake = fake_chdman_factory({})

    with pytest.raises(RunnerError, match="input root"):
        run_jobs(
            jobs,
            chdman=fake.command,
            options=_options(fake, tmp_path, log_dir=log_dir),
        )

    assert not log_dir.exists()
    assert fake.read_record()["args"] == ["-help"]


def test_run_manifest_preflights_complete_stream_before_probe_or_logs(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
) -> None:
    jobs, _, _ = _jobs(tmp_path)
    binary = io.BytesIO()
    text = io.TextIOWrapper(binary, encoding="utf-8", write_through=True)
    dump_json_lines(text, jobs)
    binary.seek(0, os.SEEK_END)
    binary.write(b"not-json\n")
    binary.seek(0)
    fake = fake_chdman_factory({})
    with pytest.raises(ContractError):
        run_manifest(binary, chdman=fake.command, options=_options(fake, tmp_path))
    assert not fake.record_path.exists()
    assert not (tmp_path / "logs").exists()


@pytest.mark.parametrize("workers", [0, -1, True, 1.5])
def test_rejects_invalid_worker_budget_before_start(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
    workers: object,
) -> None:
    jobs, _, _ = _jobs(tmp_path)
    fake = fake_chdman_factory({})
    with pytest.raises(RunnerError, match="workers"):
        run_jobs(
            jobs,
            chdman=fake.command,
            options=_options(fake, tmp_path, workers=workers),
        )


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf")])
def test_rejects_invalid_timeouts_before_start(
    tmp_path: Path,
    fake_chdman_factory: Callable[[dict[str, Any]], FakeChdman],
    timeout: object,
) -> None:
    jobs, _, _ = _jobs(tmp_path)
    fake = fake_chdman_factory({})
    with pytest.raises(RunnerError, match="timeouts"):
        run_jobs(
            jobs,
            chdman=fake.command,
            options=_options(fake, tmp_path, probe_timeout=timeout),
        )
