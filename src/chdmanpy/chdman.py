"""Validated CHDMAN executable discovery and subprocess adaptation."""

from __future__ import annotations

import math
import os
import shutil
import signal
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from chdmanpy.config import RuntimeConfig
from chdmanpy.errors import ChdmanError
from chdmanpy.manifest import ALLOWED_OPERATIONS

_MANAGED_OPTIONS = frozenset({"-f", "-i", "-o", "--force", "--input", "--output"})
_SIGNATURES = (b"chdman", b"compressed hunks", b"mame")

CommandPart = str | os.PathLike[str]
Command = CommandPart | Sequence[CommandPart]


@dataclass(frozen=True, slots=True)
class ChdmanExecutable:
    """One probed executable command and its discovery source."""

    command: tuple[str, ...]
    source: str
    description: str


def validate_operation_options(
    operation: object, options: Sequence[object]
) -> tuple[str, tuple[str, ...]]:
    """Validate a CHDMAN creation operation and all user-controlled arguments."""

    if not isinstance(operation, str) or operation not in ALLOWED_OPERATIONS:
        raise ChdmanError("CHDMAN operation must be createcd or createdvd")
    if isinstance(options, (str, bytes)) or not isinstance(options, Sequence):
        raise ChdmanError("CHDMAN options must be a sequence of argument strings")
    validated: list[str] = []
    for index, value in enumerate(options):
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ChdmanError(
                f"CHDMAN option {index + 1} must be a nonempty, NUL-free string"
            )
        name = value.split("=", maxsplit=1)[0]
        if name in _MANAGED_OPTIONS or value.startswith(("-i=", "-o=", "-f=")):
            raise ChdmanError(f"CHDMAN options must not set managed option {name!r}")
        validated.append(value)
    return operation, tuple(validated)


def _command_parts(value: Command) -> tuple[str, ...]:
    if isinstance(value, (str, os.PathLike)):
        raw_parts = [value]
    elif isinstance(value, Sequence) and not isinstance(value, bytes):
        raw_parts = list(value)
    else:
        raise ChdmanError("CHDMAN command must be a path or argument sequence")
    if not raw_parts:
        raise ChdmanError("CHDMAN command must not be empty")
    parts: list[str] = []
    for index, raw_part in enumerate(raw_parts):
        try:
            part = os.fspath(raw_part)
        except TypeError as error:
            raise ChdmanError(
                f"CHDMAN command part {index + 1} must be path-like text"
            ) from error
        if not isinstance(part, str) or not part or "\x00" in part:
            raise ChdmanError(
                f"CHDMAN command part {index + 1} must be nonempty and NUL-free"
            )
        parts.append(part)
    return tuple(parts)


def _resolve_program(command: tuple[str, ...], path: str | None) -> tuple[str, ...]:
    program = command[0]
    resolved = shutil.which(program, path=path)
    if resolved is None:
        candidate = Path(program)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            resolved = str(candidate.absolute())
    if resolved is None:
        raise ChdmanError(
            f"CHDMAN executable was not found or is not executable: {program!r}"
        )
    if not os.path.isabs(resolved):
        resolved = os.path.abspath(resolved)
    return (resolved, *command[1:])


def _probe(
    command: tuple[str, ...],
    *,
    environment: Mapping[str, str] | None,
    timeout: float,
) -> str:
    try:
        completed = subprocess.run(
            [*command, "-help"],
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=None if environment is None else dict(environment),
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ChdmanError(f"cannot execute CHDMAN help probe: {error}") from error
    output = completed.stdout + b"\n" + completed.stderr
    folded = output.lower()
    if b"chdman" not in folded or not any(
        signature in folded for signature in _SIGNATURES[1:]
    ):
        raise ChdmanError(
            "CHDMAN help probe did not contain the expected CHDMAN/MAME signature"
        )
    first_line = next(
        (
            line.strip()
            for line in output.decode("utf-8", "replace").splitlines()
            if line.strip()
        ),
        "CHDMAN",
    )
    # Real CHDMAN commonly returns 1 for -help; signature, not exit zero, is decisive.
    return first_line[:256]


def discover_chdman(
    *,
    explicit: Command | None = None,
    runtime: RuntimeConfig | str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
    path: str | None = None,
    probe_timeout: float = 15.0,
) -> ChdmanExecutable:
    """Discover and probe CHDMAN using the documented precedence."""

    if (
        isinstance(probe_timeout, bool)
        or not isinstance(probe_timeout, (int, float))
        or not math.isfinite(probe_timeout)
        or probe_timeout <= 0
    ):
        raise ChdmanError("CHDMAN probe timeout must be a positive finite number")
    effective_environment = os.environ if environment is None else environment
    source: str
    selected: Command
    if explicit is not None:
        selected = explicit
        source = "explicit"
    elif "CHDMANPY_CHDMAN" in effective_environment:
        selected = effective_environment["CHDMANPY_CHDMAN"]
        source = "environment"
    else:
        runtime_value = (
            runtime.chdman if isinstance(runtime, RuntimeConfig) else runtime
        )
        if runtime_value is not None:
            selected = runtime_value
            source = "configuration"
        else:
            selected = "chdman"
            source = "PATH"
    search_path = path
    if search_path is None:
        search_path = effective_environment.get("PATH")
    command = _resolve_program(_command_parts(selected), search_path)
    description = _probe(
        command,
        environment=effective_environment,
        timeout=probe_timeout,
    )
    return ChdmanExecutable(command=command, source=source, description=description)


def build_argv(
    executable: ChdmanExecutable,
    operation: object,
    options: Sequence[object],
    source_path: str | os.PathLike[str],
    staging_path: str | os.PathLike[str],
) -> list[str]:
    """Build the only supported safe CHDMAN creation argument order."""

    validated_operation, validated_options = validate_operation_options(
        operation, options
    )
    source = os.fspath(source_path)
    staging = os.fspath(staging_path)
    if not isinstance(source, str) or not source or "\x00" in source:
        raise ChdmanError("CHDMAN source path must be nonempty and NUL-free")
    if not isinstance(staging, str) or not staging or "\x00" in staging:
        raise ChdmanError("CHDMAN staging path must be nonempty and NUL-free")
    return [
        *executable.command,
        validated_operation,
        *validated_options,
        "-i",
        source,
        "-o",
        staging,
    ]


def spawn_chdman(
    executable: ChdmanExecutable,
    operation: object,
    options: Sequence[object],
    source_path: str | os.PathLike[str],
    staging_path: str | os.PathLike[str],
    *,
    log: BinaryIO,
    environment: Mapping[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> subprocess.Popen[bytes]:
    """Start one isolated owned child with closed stdin and captured output."""

    argv = build_argv(executable, operation, options, source_path, staging_path)
    kwargs: dict[str, object] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(
        argv,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=None if environment is None else dict(environment),
        cwd=None if cwd is None else os.fspath(cwd),
        **kwargs,
    )


def terminate_owned_process(
    process: subprocess.Popen[bytes], *, timeout: float = 5.0
) -> None:
    """Terminate only the supplied owned child process/group, then escalate."""

    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=timeout)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    if os.name == "nt" and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=timeout)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    if process.poll() is None:
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()


__all__ = [
    "ChdmanExecutable",
    "build_argv",
    "discover_chdman",
    "spawn_chdman",
    "terminate_owned_process",
    "validate_operation_options",
]
