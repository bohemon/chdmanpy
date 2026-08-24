"""Bounded, interruption-safe execution of fully validated CHDMAN manifests."""

from __future__ import annotations

import errno
import hashlib
import math
import os
import stat
import subprocess
import threading
import time
import uuid
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from chdmanpy.chdman import (
    ChdmanExecutable,
    Command,
    discover_chdman,
    spawn_chdman,
    terminate_owned_process,
)
from chdmanpy.config import RuntimeConfig
from chdmanpy.errors import ExitCode, RunnerError
from chdmanpy.jsonl import canonical_json_bytes
from chdmanpy.manifest import (
    SCHEMA_VERSION,
    load_manifest,
    path_key,
    validate_manifest_records,
)
from chdmanpy.results import (
    RESULT_RECORD_TYPE,
    RESULT_STATUSES,
    SUMMARY_RECORD_TYPE,
    exit_code_for_results,
    validate_result_stream,
)

_CHD_MAGIC = b"MComprHD"
_CHD_V5_HEADER_LENGTH = 124
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_DESTINATION_APPEARED = "destination appeared before atomic publish"


@dataclass(frozen=True, slots=True)
class RunnerOptions:
    """Explicit invocation-wide execution policy."""

    workers: int | None = None
    fail_fast: bool = False
    allow_changed: bool = False
    log_dir: str | os.PathLike[str] | None = None
    run_id: str | None = None
    environment: Mapping[str, str] | None = None
    cancel_event: threading.Event | None = None
    probe_timeout: float = 15.0
    termination_timeout: float = 5.0


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Validated ordered results, terminal summary, and process exit policy."""

    results: list[dict[str, Any]]
    summary: dict[str, Any]
    exit_code: ExitCode
    executable: ChdmanExecutable
    run_log_path: str

    @property
    def records(self) -> list[dict[str, Any]]:
        return [*self.results, self.summary]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _duration_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT)


def _components(path: str) -> list[str]:
    value = Path(path)
    return [str(component) for component in reversed(value.parents)] + [str(value)]


def _validate_regular_source(job: Mapping[str, Any]) -> tuple[list[str], str | None]:
    source = job["source"]
    path = source["path"]
    try:
        for component in _components(path):
            metadata = os.lstat(component)
            if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
                return (
                    [],
                    f"source traverses a symlink, junction, or reparse point: {component!r}",
                )
        metadata = os.lstat(path)
    except OSError as error:
        return [], f"cannot inspect source {path!r}: {error}"
    if not stat.S_ISREG(metadata.st_mode):
        return [], f"source is not a regular file: {path!r}"
    identity_value = {
        "identity_kind": "primary-file-metadata-v1",
        "path": path_key(path),
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
    }
    identity = (
        f"sha256:{hashlib.sha256(canonical_json_bytes(identity_value)).hexdigest()}"
    )
    changed_fields: list[str] = []
    if metadata.st_size != source["size"]:
        changed_fields.append("size")
    if metadata.st_mtime_ns != source["mtime_ns"]:
        changed_fields.append("mtime_ns")
    if identity != source["identity"]:
        changed_fields.append("identity")
    if not changed_fields:
        return [], None
    warning = "source primary-file metadata changed after planning: " + ", ".join(
        changed_fields
    )
    return [warning], warning


def _validate_directory_components(path: str, description: str) -> None:
    for component in _components(path):
        try:
            metadata = os.lstat(component)
        except FileNotFoundError:
            continue
        except OSError as error:
            raise RunnerError(
                f"cannot inspect {description} {component!r}: {error}"
            ) from error
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
            raise RunnerError(
                f"{description} must not traverse a symlink, junction, or reparse point: "
                f"{component!r}"
            )
        if not stat.S_ISDIR(metadata.st_mode):
            raise RunnerError(
                f"{description} component is not a directory: {component!r}"
            )


def _ensure_directory(path: str, description: str) -> None:
    _validate_directory_components(path, description)
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise RunnerError(f"cannot create {description} {path!r}: {error}") from error
    _validate_directory_components(path, description)


def staging_path_for(destination: str, run_id: str, job_id: str) -> str:
    """Return the private sibling ``.failed`` directory used for one job."""

    run_token = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
    value = Path(destination)
    return str(value.with_name(f".chdmanpy-{run_token}-{job_id}.failed"))


def _owner_bytes(run_id: str, job_id: str) -> bytes:
    run_digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    return f"chdmanpy-stage-v1\nrun_sha256={run_digest}\njob_id={job_id}\n".encode()


@dataclass(frozen=True, slots=True)
class _StageOwnership:
    directory: str
    marker: str
    directory_device: int
    directory_inode: int
    marker_device: int
    marker_inode: int


class _StageClaimError(RunnerError):
    def __init__(self, message: str, *, retained_path: str | None = None) -> None:
        super().__init__(message)
        self.retained_path = retained_path


def _same_file(metadata: os.stat_result, device: int, inode: int) -> bool:
    return (metadata.st_dev, metadata.st_ino) == (device, inode)


def _claim_staging(staging: str, run_id: str, job_id: str) -> _StageOwnership:
    if os.path.lexists(staging):
        raise RunnerError(f"refusing to use unowned staging directory: {staging!r}")
    try:
        os.mkdir(staging, 0o700)
    except FileExistsError as error:
        raise RunnerError(f"staging directory already exists: {staging!r}") from error
    except OSError as error:
        raise RunnerError(
            f"cannot create private staging directory: {error}"
        ) from error
    try:
        directory_metadata = os.lstat(staging)
    except OSError as error:
        raise _StageClaimError(
            "cannot inspect newly created private staging directory; "
            "it was retained unchanged: "
            f"{error}",
            retained_path=staging,
        ) from error
    marker = str(Path(staging) / ".chdmanpy-owner")
    descriptor = -1
    marker_metadata: os.stat_result | None = None
    try:
        descriptor = os.open(
            marker,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        marker_metadata = os.fstat(descriptor)
        contents = _owner_bytes(run_id, job_id)
        offset = 0
        while offset < len(contents):
            written = os.write(descriptor, contents[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "short write to staging ownership marker")
            offset += written
        os.fsync(descriptor)
        try:
            os.close(descriptor)
        finally:
            descriptor = -1
        current_marker = os.lstat(marker)
        if not _same_file(
            current_marker, marker_metadata.st_dev, marker_metadata.st_ino
        ):
            raise OSError(errno.ESTALE, "staging ownership marker identity changed")
        if not stat.S_ISREG(current_marker.st_mode) or _is_reparse(current_marker):
            raise OSError(errno.EINVAL, "staging ownership marker is not regular")
        descriptor = -1
    except OSError as error:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
        retained_path = None
        with suppress(OSError):
            current_directory = os.lstat(staging)
            if (
                stat.S_ISDIR(current_directory.st_mode)
                and not stat.S_ISLNK(current_directory.st_mode)
                and not _is_reparse(current_directory)
                and _same_file(
                    current_directory,
                    directory_metadata.st_dev,
                    directory_metadata.st_ino,
                )
            ):
                retained_path = staging
        detail = f"cannot create staging ownership marker: {error}"
        if retained_path is not None:
            detail += "; incomplete private staging was retained unchanged"
        raise _StageClaimError(detail, retained_path=retained_path) from error
    assert marker_metadata is not None
    return _StageOwnership(
        directory=staging,
        marker=marker,
        directory_device=directory_metadata.st_dev,
        directory_inode=directory_metadata.st_ino,
        marker_device=marker_metadata.st_dev,
        marker_inode=marker_metadata.st_ino,
    )


def _verify_stage_ownership(
    ownership: _StageOwnership, run_id: str, job_id: str
) -> str | None:
    try:
        directory_metadata = os.lstat(ownership.directory)
        marker_metadata = os.lstat(ownership.marker)
    except OSError as error:
        return f"cannot verify owned .failed staging directory: {error}"
    if stat.S_ISLNK(directory_metadata.st_mode) or _is_reparse(directory_metadata):
        return "owned .failed staging path became a symlink or reparse point"
    if not stat.S_ISDIR(directory_metadata.st_mode):
        return "owned .failed staging path is no longer a directory"
    if (directory_metadata.st_dev, directory_metadata.st_ino) != (
        ownership.directory_device,
        ownership.directory_inode,
    ):
        return "owned .failed staging directory identity changed"
    if stat.S_ISLNK(marker_metadata.st_mode) or _is_reparse(marker_metadata):
        return "staging ownership marker became a symlink or reparse point"
    if not stat.S_ISREG(marker_metadata.st_mode):
        return "staging ownership marker is not a regular file"
    if (marker_metadata.st_dev, marker_metadata.st_ino) != (
        ownership.marker_device,
        ownership.marker_inode,
    ):
        return "staging ownership marker identity changed"
    try:
        with open(ownership.marker, "rb") as stream:
            contents = stream.read(len(_owner_bytes(run_id, job_id)) + 1)
    except OSError as error:
        return f"cannot read staging ownership marker: {error}"
    if contents != _owner_bytes(run_id, job_id):
        return "staging ownership marker does not match this run and job"
    return None


def _cleanup_owned_stage(
    ownership: _StageOwnership, run_id: str, job_id: str
) -> str | None:
    verification_error = _verify_stage_ownership(ownership, run_id, job_id)
    if verification_error is not None:
        return f"refused to clean owned staging directory: {verification_error}"
    try:
        entries = os.listdir(ownership.directory)
    except OSError as error:
        return f"cannot list owned staging directory for cleanup: {error}"
    if entries != [Path(ownership.marker).name]:
        return "owned staging directory contains unexpected retained entries"
    try:
        current_marker = os.lstat(ownership.marker)
        if (current_marker.st_dev, current_marker.st_ino) != (
            ownership.marker_device,
            ownership.marker_inode,
        ):
            return "staging ownership marker changed immediately before cleanup"
        os.unlink(ownership.marker)
        current_directory = os.lstat(ownership.directory)
        if (current_directory.st_dev, current_directory.st_ino) != (
            ownership.directory_device,
            ownership.directory_inode,
        ):
            return "staging directory changed immediately before cleanup"
        os.rmdir(ownership.directory)
    except OSError as error:
        return f"could not remove verified owned staging directory: {error}"
    return None


def _validate_chd_v5(path: str) -> str | None:
    try:
        metadata = os.lstat(path)
    except OSError as error:
        return f"CHDMAN did not produce a readable staging file: {error}"
    if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
        return "CHDMAN staging output is a symlink or reparse point"
    if not stat.S_ISREG(metadata.st_mode):
        return "CHDMAN staging output is not a regular file"
    if metadata.st_size < _CHD_V5_HEADER_LENGTH:
        return "CHDMAN staging output is missing or has a truncated CHD v5 header"
    try:
        with open(path, "rb") as stream:
            header = stream.read(16)
    except OSError as error:
        return f"cannot read CHDMAN staging output: {error}"
    if header[:8] != _CHD_MAGIC:
        return "CHDMAN staging output has an invalid CHD magic"
    if int.from_bytes(header[8:12], "big") != _CHD_V5_HEADER_LENGTH:
        return "CHDMAN staging output has an invalid CHD v5 header length"
    if int.from_bytes(header[12:16], "big") != 5:
        return "CHDMAN staging output is not CHD version 5"
    return None


def _log_reports_warning(path: str) -> bool:
    try:
        with open(path, "rb") as stream:
            trailing = b""
            while chunk := stream.read(64 * 1024):
                combined = trailing + chunk.lower()
                if b"warning" in combined:
                    return True
                trailing = combined[-6:]
    except OSError:
        return False
    return False


def _result(
    job: Mapping[str, Any],
    run_id: str,
    *,
    status: str,
    output_path: str | None = None,
    staging_path: str | None = None,
    log_path: str | None = None,
    chdman_exit_code: int | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    duration_ms: int = 0,
    error: str | None = None,
    warnings: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": RESULT_RECORD_TYPE,
        "run_id": run_id,
        "job_id": job["job_id"],
        "plan_index": job["plan_index"],
        "status": status,
        "source_path": job["source"]["path"],
        "output_path": output_path or job["destination"]["path"],
        "staging_path": staging_path,
        "log_path": log_path,
        "chdman_exit_code": chdman_exit_code,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "error": error,
        "warnings": [*job["warnings"], *warnings],
    }


class _RunContext:
    def __init__(
        self,
        *,
        executable: ChdmanExecutable,
        options: RunnerOptions,
        run_id: str,
        run_log_path: str,
    ) -> None:
        self.executable = executable
        self.options = options
        self.run_id = run_id
        self.run_log_path = run_log_path
        self.cancel_event = options.cancel_event or threading.Event()
        self.interrupted = False
        self.lock = threading.Lock()
        self.start_lock = threading.Lock()
        self.active: set[subprocess.Popen[bytes]] = set()
        self.reserved: set[str] = set()

    def interruption_requested(self) -> bool:
        """Observe cancellation through the same gate used for process starts."""

        with self.start_lock:
            if self.cancel_event.is_set():
                self.interrupted = True
            return self.interrupted

    def request_interruption(self) -> None:
        """Close the process-start gate before terminating registered children."""

        with self.start_lock:
            self.interrupted = True

    def log(self, message: str) -> None:
        with (
            self.lock,
            open(self.run_log_path, "a", encoding="utf-8", newline="\n") as stream,
        ):
            stream.write(message)
            stream.write("\n")

    def register(self, process: subprocess.Popen[bytes]) -> None:
        with self.lock:
            self.active.add(process)

    def unregister(self, process: subprocess.Popen[bytes]) -> None:
        with self.lock:
            self.active.discard(process)

    def terminate_all(self) -> None:
        with self.lock:
            active = list(self.active)
        for process in active:
            terminate_owned_process(process, timeout=self.options.termination_timeout)

    def reserve_destination(self, destination: str, policy: str) -> tuple[str, bool]:
        with self.lock:
            candidate = destination
            if policy == "rename":
                value = Path(destination)
                index = 1
                while path_key(candidate) in self.reserved or os.path.lexists(
                    candidate
                ):
                    candidate = str(
                        value.with_name(f"{value.stem} ({index}){value.suffix}")
                    )
                    index += 1
            key = path_key(candidate)
            collision = key in self.reserved or os.path.lexists(candidate)
            self.reserved.add(key)
            return candidate, collision


def _publish_no_clobber(
    staging: str, destination: str
) -> tuple[str | None, str | None]:
    try:
        if os.name == "nt":
            os.rename(staging, destination)
        else:
            os.link(staging, destination, follow_symlinks=False)
    except FileExistsError:
        return _DESTINATION_APPEARED, None
    except OSError as error:
        if error.errno == errno.EEXIST:
            return _DESTINATION_APPEARED, None
        return f"cannot atomically publish CHD without overwrite: {error}", None
    if os.name != "nt":
        try:
            os.unlink(staging)
        except OSError as error:
            return None, f"published output but could not remove owned staging: {error}"
    return None, None


def _execute_job(
    job: Mapping[str, Any], context: _RunContext, log_directory: str
) -> dict[str, Any]:
    started_clock = time.monotonic()
    started_at = _utc_now()
    warnings, changed_error = _validate_regular_source(job)
    if changed_error is not None and (
        not context.options.allow_changed or not warnings
    ):
        return _result(
            job,
            context.run_id,
            status="failed",
            started_at=started_at,
            finished_at=_utc_now(),
            duration_ms=_duration_ms(started_clock),
            error=changed_error,
        )
    if context.interruption_requested():
        return _result(
            job,
            context.run_id,
            status="interrupted",
            started_at=started_at,
            finished_at=_utc_now(),
            duration_ms=_duration_ms(started_clock),
            error="job was not started because the run was interrupted",
            warnings=warnings,
        )

    policy = job["destination"]["existing"]
    destination, collision = context.reserve_destination(
        job["destination"]["path"], policy
    )
    if collision and policy == "fail":
        return _result(
            job,
            context.run_id,
            status="failed",
            output_path=destination,
            started_at=started_at,
            finished_at=_utc_now(),
            duration_ms=_duration_ms(started_clock),
            error="destination already exists",
            warnings=warnings,
        )
    if collision and policy == "skip":
        return _result(
            job,
            context.run_id,
            status="skipped",
            output_path=destination,
            started_at=started_at,
            finished_at=_utc_now(),
            duration_ms=_duration_ms(started_clock),
            warnings=[*warnings, "destination already exists; job skipped"],
        )

    staging = staging_path_for(destination, context.run_id, job["job_id"])
    staged_output = str(Path(staging) / "output.chd")
    log_path = str(Path(log_directory) / f"{job['plan_index']:06d}-{job['job_id']}.log")
    process: subprocess.Popen[bytes] | None = None
    ownership: _StageOwnership | None = None

    def retained_stage() -> str | None:
        if ownership is None:
            return None
        verification_error = _verify_stage_ownership(
            ownership, context.run_id, job["job_id"]
        )
        if verification_error is not None:
            warnings.append(
                "owned staging verification failed; left unchanged: "
                f"{verification_error}"
            )
        return staging if os.path.lexists(staging) else None

    try:
        _ensure_directory(str(Path(destination).parent), "destination directory")
        if context.interruption_requested():
            return _result(
                job,
                context.run_id,
                status="interrupted",
                output_path=destination,
                started_at=started_at,
                finished_at=_utc_now(),
                duration_ms=_duration_ms(started_clock),
                error="job was not started because the run was interrupted",
                warnings=warnings,
            )
        ownership = _claim_staging(staging, context.run_id, job["job_id"])
        with open(log_path, "xb") as log_stream:
            with context.start_lock:
                if context.interrupted or context.cancel_event.is_set():
                    context.interrupted = True
                else:
                    process = spawn_chdman(
                        context.executable,
                        job["chdman"]["operation"],
                        job["chdman"]["options"],
                        job["source"]["path"],
                        staged_output,
                        log=log_stream,
                        environment=context.options.environment,
                        cwd=str(Path(destination).parent),
                    )
                    context.register(process)
            if process is None:
                return _result(
                    job,
                    context.run_id,
                    status="interrupted",
                    output_path=destination,
                    staging_path=retained_stage(),
                    log_path=log_path,
                    started_at=started_at,
                    finished_at=_utc_now(),
                    duration_ms=_duration_ms(started_clock),
                    error="job was not started because the run was interrupted",
                    warnings=warnings,
                )
            if context.cancel_event.is_set():
                context.request_interruption()
                terminate_owned_process(
                    process, timeout=context.options.termination_timeout
                )
            exit_code = process.wait()
            context.unregister(process)
        if context.interruption_requested():
            return _result(
                job,
                context.run_id,
                status="interrupted",
                output_path=destination,
                staging_path=retained_stage(),
                log_path=log_path,
                chdman_exit_code=exit_code,
                started_at=started_at,
                finished_at=_utc_now(),
                duration_ms=_duration_ms(started_clock),
                error="job interrupted",
                warnings=warnings,
            )
        if exit_code != 0:
            return _result(
                job,
                context.run_id,
                status="failed",
                output_path=destination,
                staging_path=retained_stage(),
                log_path=log_path,
                chdman_exit_code=exit_code,
                started_at=started_at,
                finished_at=_utc_now(),
                duration_ms=_duration_ms(started_clock),
                error=f"CHDMAN exited with status {exit_code}",
                warnings=warnings,
            )
        ownership_error = _verify_stage_ownership(
            ownership, context.run_id, job["job_id"]
        )
        if ownership_error is not None:
            return _result(
                job,
                context.run_id,
                status="failed",
                output_path=destination,
                staging_path=retained_stage(),
                log_path=log_path,
                chdman_exit_code=exit_code,
                started_at=started_at,
                finished_at=_utc_now(),
                duration_ms=_duration_ms(started_clock),
                error=f"refusing to publish unverified staging: {ownership_error}",
                warnings=warnings,
            )
        validation_error = _validate_chd_v5(staged_output)
        if validation_error is not None:
            return _result(
                job,
                context.run_id,
                status="failed",
                output_path=destination,
                staging_path=retained_stage(),
                log_path=log_path,
                chdman_exit_code=exit_code,
                started_at=started_at,
                finished_at=_utc_now(),
                duration_ms=_duration_ms(started_clock),
                error=validation_error,
                warnings=warnings,
            )
        _validate_directory_components(
            str(Path(destination).parent), "destination directory before publish"
        )
        ownership_error = _verify_stage_ownership(
            ownership, context.run_id, job["job_id"]
        )
        if ownership_error is not None:
            raise RunnerError(
                f"refusing to publish unverified staging: {ownership_error}"
            )
        validation_error = _validate_chd_v5(staged_output)
        if validation_error is not None:
            raise RunnerError(
                f"staging output changed immediately before publish: {validation_error}"
            )
        publish_error, publish_warning = _publish_no_clobber(staged_output, destination)
        rename_attempts = 0
        while (
            publish_error == _DESTINATION_APPEARED
            and policy == "rename"
            and rename_attempts < 1_000
        ):
            rename_attempts += 1
            destination, _ = context.reserve_destination(
                job["destination"]["path"], policy
            )
            publish_error, publish_warning = _publish_no_clobber(
                staged_output, destination
            )
        if publish_error == _DESTINATION_APPEARED and policy == "rename":
            publish_error = "too many destination races while applying rename policy"
        if publish_error == _DESTINATION_APPEARED and policy == "skip":
            return _result(
                job,
                context.run_id,
                status="skipped",
                output_path=destination,
                staging_path=retained_stage(),
                log_path=log_path,
                chdman_exit_code=exit_code,
                started_at=started_at,
                finished_at=_utc_now(),
                duration_ms=_duration_ms(started_clock),
                warnings=[
                    *warnings,
                    "destination appeared before publish; completed staging was retained",
                ],
            )
        if publish_error is not None:
            return _result(
                job,
                context.run_id,
                status="failed",
                output_path=destination,
                staging_path=retained_stage(),
                log_path=log_path,
                chdman_exit_code=exit_code,
                started_at=started_at,
                finished_at=_utc_now(),
                duration_ms=_duration_ms(started_clock),
                error=publish_error,
                warnings=warnings,
            )
        if publish_warning is not None:
            warnings.append(publish_warning)
        cleanup_warning = _cleanup_owned_stage(ownership, context.run_id, job["job_id"])
        if cleanup_warning is not None:
            warnings.append(cleanup_warning)
        if _log_reports_warning(log_path):
            warnings.append("CHDMAN reported a warning; inspect the job log")
        retained_staging = staging if os.path.lexists(staging) else None
        status = "warning" if warnings else "success"
        return _result(
            job,
            context.run_id,
            status=status,
            output_path=destination,
            staging_path=retained_staging,
            log_path=log_path,
            chdman_exit_code=exit_code,
            started_at=started_at,
            finished_at=_utc_now(),
            duration_ms=_duration_ms(started_clock),
            warnings=warnings,
        )
    except Exception as error:
        if process is not None:
            context.unregister(process)
            if process.poll() is None:
                terminate_owned_process(
                    process, timeout=context.options.termination_timeout
                )
        return _result(
            job,
            context.run_id,
            status="failed",
            output_path=destination,
            staging_path=(
                error.retained_path
                if isinstance(error, _StageClaimError)
                else retained_stage()
            ),
            log_path=log_path if os.path.lexists(log_path) else None,
            chdman_exit_code=None if process is None else process.returncode,
            started_at=started_at,
            finished_at=_utc_now(),
            duration_ms=_duration_ms(started_clock),
            error=str(error) or type(error).__name__,
            warnings=warnings,
        )


def _not_started_result(
    job: Mapping[str, Any], run_id: str, *, interrupted: bool
) -> dict[str, Any]:
    if interrupted:
        return _result(
            job,
            run_id,
            status="interrupted",
            error="job was not started because the run was interrupted",
        )
    return _result(
        job,
        run_id,
        status="skipped",
        warnings=["job was not started because fail-fast stopped scheduling"],
    )


def _run_log_directory(
    jobs: Sequence[Mapping[str, Any]], options: RunnerOptions, run_id: str
) -> tuple[str, str]:
    base = (
        os.fspath(options.log_dir)
        if options.log_dir is not None
        else str(Path(jobs[0]["destination"]["path"]).parent / ".chdmanpy-logs")
    )
    if not os.path.isabs(base):
        base = os.path.abspath(base)
    base_key = path_key(base)
    for job in jobs:
        input_root_key = path_key(job["source"]["input_root"])
        try:
            common = os.path.commonpath((base_key, input_root_key))
        except ValueError:
            continue
        if common == input_root_key:
            raise RunnerError(
                f"log directory must not be equal to or beneath an input root: {base!r}"
            )
    for job in jobs:
        destination_key = path_key(job["destination"]["path"])
        try:
            common = os.path.commonpath((destination_key, base_key))
        except ValueError:
            continue
        if common == destination_key:
            raise RunnerError(
                "log directory must not be equal to or beneath a destination file: "
                f"{base!r}"
            )
    token = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
    directory = str(Path(base) / f"run-{token}")
    _validate_directory_components(str(Path(directory).parent), "log directory")
    try:
        os.mkdir(directory)
    except FileExistsError as error:
        raise RunnerError(f"run log directory already exists: {directory!r}") from error
    except FileNotFoundError:
        _ensure_directory(str(Path(directory).parent), "log directory")
        try:
            os.mkdir(directory)
        except OSError as error:
            raise RunnerError(
                f"cannot create run log directory {directory!r}: {error}"
            ) from error
    except OSError as error:
        raise RunnerError(
            f"cannot create run log directory {directory!r}: {error}"
        ) from error
    _validate_directory_components(directory, "run log directory")
    run_log = str(Path(directory) / "run.log")
    try:
        Path(run_log).write_text("", encoding="utf-8", newline="\n")
    except OSError as error:
        raise RunnerError(f"cannot create run log {run_log!r}: {error}") from error
    return directory, run_log


def run_jobs(
    records: Sequence[object],
    *,
    chdman: ChdmanExecutable | Command | None = None,
    runtime: RuntimeConfig | str | os.PathLike[str] | None = None,
    options: RunnerOptions | None = None,
) -> RunOutcome:
    """Fully preflight, execute with a bounded budget, and return ordered records."""

    jobs = validate_manifest_records(records)
    selected_options = options or RunnerOptions()
    if not isinstance(selected_options.fail_fast, bool):
        raise RunnerError("fail_fast must be a boolean")
    if not isinstance(selected_options.allow_changed, bool):
        raise RunnerError("allow_changed must be a boolean")
    workers = (
        os.cpu_count() or 1
        if selected_options.workers is None
        else selected_options.workers
    )
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise RunnerError("workers must be a positive integer")
    if (
        isinstance(selected_options.probe_timeout, bool)
        or not isinstance(selected_options.probe_timeout, (int, float))
        or not math.isfinite(selected_options.probe_timeout)
        or selected_options.probe_timeout <= 0
        or isinstance(selected_options.termination_timeout, bool)
        or not isinstance(selected_options.termination_timeout, (int, float))
        or not math.isfinite(selected_options.termination_timeout)
        or selected_options.termination_timeout <= 0
    ):
        raise RunnerError("probe and termination timeouts must be positive")
    executable = (
        chdman
        if isinstance(chdman, ChdmanExecutable)
        else discover_chdman(
            explicit=chdman,
            runtime=runtime,
            environment=selected_options.environment,
            probe_timeout=selected_options.probe_timeout,
        )
    )
    run_id = selected_options.run_id or f"run-{uuid.uuid4().hex}"
    if not isinstance(run_id, str) or not run_id or "\x00" in run_id:
        raise RunnerError("run_id must be a nonempty, NUL-free string")
    if selected_options.log_dir is not None:
        raw_log_dir = os.fspath(selected_options.log_dir)
        if not isinstance(raw_log_dir, str) or not raw_log_dir or "\x00" in raw_log_dir:
            raise RunnerError("log_dir must be a nonempty, NUL-free text path")
    log_directory, run_log_path = _run_log_directory(jobs, selected_options, run_id)
    context = _RunContext(
        executable=executable,
        options=selected_options,
        run_id=run_id,
        run_log_path=run_log_path,
    )
    context.log(
        f"run started: jobs={len(jobs)} workers={workers} executable_source={executable.source}"
    )
    run_started = time.monotonic()
    pending = deque(jobs)
    futures: dict[Future[dict[str, Any]], Mapping[str, Any]] = {}
    results_by_index: dict[int, dict[str, Any]] = {}
    stop_scheduling = False
    interrupted = context.cancel_event.is_set()
    if interrupted:
        context.request_interruption()

    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="chdmanpy")
    try:
        while pending or futures:
            if context.cancel_event.is_set() and not interrupted:
                interrupted = True
                context.request_interruption()
                stop_scheduling = True
                context.terminate_all()
            while (
                pending
                and len(futures) < workers
                and not stop_scheduling
                and not interrupted
            ):
                job = pending.popleft()
                future = executor.submit(_execute_job, job, context, log_directory)
                futures[future] = job
            if not futures:
                break
            completed, _ = wait(futures, timeout=0.05, return_when=FIRST_COMPLETED)
            for future in completed:
                job = futures.pop(future)
                try:
                    result = future.result()
                except Exception as error:  # defensive boundary around worker futures
                    result = _result(
                        job,
                        run_id,
                        status="failed",
                        error=str(error) or type(error).__name__,
                    )
                results_by_index[job["plan_index"]] = result
                context.log(
                    f"job completed: plan_index={job['plan_index']} status={result['status']}"
                )
                if selected_options.fail_fast and result["status"] == "failed":
                    stop_scheduling = True
    except KeyboardInterrupt:
        interrupted = True
        context.request_interruption()
        stop_scheduling = True
        context.cancel_event.set()
        context.terminate_all()
        for future, job in list(futures.items()):
            try:
                results_by_index[job["plan_index"]] = future.result(
                    timeout=selected_options.termination_timeout * 3
                )
            except Exception:
                results_by_index[job["plan_index"]] = _not_started_result(
                    job, run_id, interrupted=True
                )
        futures.clear()
    finally:
        executor.shutdown(wait=True, cancel_futures=False)

    for job in pending:
        results_by_index[job["plan_index"]] = _not_started_result(
            job, run_id, interrupted=interrupted
        )
    results = [results_by_index[index] for index in range(len(jobs))]
    counts = Counter(result["status"] for result in results)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "record_type": SUMMARY_RECORD_TYPE,
        "run_id": run_id,
        "total": len(results),
        **{status: counts[status] for status in RESULT_STATUSES},
        "duration_ms": _duration_ms(run_started),
    }
    validated_results, validated_summary = validate_result_stream([*results, summary])
    exit_code = exit_code_for_results(validated_results, validated_summary)
    context.log(f"run finished: exit_code={int(exit_code)}")
    return RunOutcome(
        results=validated_results,
        summary=validated_summary,
        exit_code=exit_code,
        executable=executable,
        run_log_path=run_log_path,
    )


def run_manifest(
    stream: BinaryIO,
    *,
    chdman: ChdmanExecutable | Command | None = None,
    runtime: RuntimeConfig | str | os.PathLike[str] | None = None,
    options: RunnerOptions | None = None,
) -> RunOutcome:
    """Load a complete binary JSON Lines manifest before delegating to ``run_jobs``."""

    jobs = load_manifest(stream)
    return run_jobs(jobs, chdman=chdman, runtime=runtime, options=options)


__all__ = [
    "RunOutcome",
    "RunnerOptions",
    "run_jobs",
    "run_manifest",
    "staging_path_for",
]
