#!/usr/bin/env python3
"""Deterministic stand-in for CHDMAN used by the test suite.

The caller supplies behavior in a JSON file named by ``FAKE_CHDMAN_CONTROL``.
Observations are atomically written to the file named by
``FAKE_CHDMAN_RECORD``.  Only those two narrowly scoped environment variables
are inspected; the fake never records the caller's environment.
"""

from __future__ import annotations

import base64
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

CONTROL_ENV = "FAKE_CHDMAN_CONTROL"
RECORD_ENV = "FAKE_CHDMAN_RECORD"
ASSERTION_EXIT = 97
USAGE_EXIT = 2
INTERRUPTED_EXIT = 130
OPERATIONS = frozenset({"createcd", "createdvd"})


def _load_control() -> dict[str, Any]:
    control_name = os.environ.get(CONTROL_ENV)
    if not control_name:
        return {}
    with Path(control_name).open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise TypeError("fake CHDMAN control must be a JSON object")
    return value


def _write_record(record: dict[str, Any]) -> None:
    record_name = os.environ.get(RECORD_ENV)
    if not record_name:
        return
    destination = Path(record_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)


def _managed_value(arguments: list[str], option: str) -> tuple[str | None, str | None]:
    positions = [index for index, value in enumerate(arguments) if value == option]
    if len(positions) != 1:
        return None, f"expected exactly one {option} argument"
    index = positions[0]
    if index + 1 >= len(arguments):
        return None, f"missing value after {option}"
    return arguments[index + 1], None


def _output_bytes(control: dict[str, Any]) -> bytes:
    encoded = control.get("output_bytes_base64")
    if encoded is not None:
        return base64.b64decode(str(encoded), validate=True)
    if "output_text" in control:
        return str(control["output_text"]).encode("utf-8")
    # Enough of a CHD v5 header for callers to reject empty/truncated output.
    return b"MComprHD" + (124).to_bytes(4, "big") + (5).to_bytes(4, "big") + bytes(108)


def _append_line(current: str, line: object | None) -> str:
    if line is None:
        return current
    separator = "" if not current or current.endswith("\n") else "\n"
    return f"{current}{separator}{line}\n"


def _signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except ValueError:
        return str(signum)


def main() -> int:
    try:
        control = _load_control()
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"fake chdman: invalid control: {error}", file=sys.stderr)
        return USAGE_EXIT

    arguments = sys.argv[1:]
    record: dict[str, Any] = {
        "args": arguments,
        "cwd": str(Path.cwd()),
        "input_path": None,
        "output_exists": False,
        "output_path": None,
        "pid": os.getpid(),
        "received_signal": None,
        "state": "starting",
        "stdin_eof": None,
    }
    mismatches: list[str] = []

    expected_arguments = control.get("expected_args")
    if expected_arguments is not None and arguments != expected_arguments:
        mismatches.append(
            f"args differ: expected {expected_arguments!r}, observed {arguments!r}"
        )
    expected_cwd = control.get("expected_cwd")
    if expected_cwd is not None and record["cwd"] != expected_cwd:
        mismatches.append(
            f"cwd differs: expected {expected_cwd!r}, observed {record['cwd']!r}"
        )

    if control.get("probe_stdin", False):
        observed = sys.stdin.buffer.read(1)
        record["stdin_eof"] = observed == b""
        record["stdin_bytes_read"] = len(observed)
        expected_eof = control.get("expected_stdin_eof")
        if expected_eof is not None and record["stdin_eof"] is not expected_eof:
            mismatches.append(
                "stdin EOF policy differs: "
                f"expected {expected_eof!r}, observed {record['stdin_eof']!r}"
            )

    help_request = arguments == ["-help"]
    operation_help_request = (
        len(arguments) == 2 and arguments[0] == "help" and arguments[1] in OPERATIONS
    )
    operation = None if not arguments else arguments[0]
    invocation_error: str | None = None
    if not help_request and not operation_help_request:
        if operation not in OPERATIONS:
            invocation_error = "expected -help, createcd, or createdvd"
        else:
            input_name, input_error = _managed_value(arguments[1:], "-i")
            output_name, output_error = _managed_value(arguments[1:], "-o")
            invocation_error = input_error or output_error
            record["input_path"] = input_name
            record["output_path"] = output_name
            if (
                invocation_error is None
                and control.get("require_input_exists", True)
                and input_name is not None
                and not Path(input_name).is_file()
            ):
                invocation_error = f"input does not exist: {input_name}"

    output_path = (
        Path(record["output_path"]) if record["output_path"] is not None else None
    )

    def handle_signal(signum: int, _frame: object) -> None:
        record["state"] = "interrupted"
        record["received_signal"] = _signal_name(signum)
        record["output_exists"] = bool(output_path and output_path.exists())
        record["exit_code"] = int(
            control.get("interrupted_exit_code", INTERRUPTED_EXIT)
        )
        _write_record(record)
        raise SystemExit(record["exit_code"])

    handled_signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        handled_signals.append(signal.SIGBREAK)
    for handled_signal in handled_signals:
        signal.signal(handled_signal, handle_signal)

    stdout = str(control.get("stdout", ""))
    stderr = str(control.get("stderr", ""))
    stderr = _append_line(stderr, control.get("warning"))
    default_exit = 1 if help_request or operation_help_request else 0
    configured_exit = int(control.get("exit_code", default_exit))

    if (help_request or operation_help_request) and not stdout:
        stdout = "chdman - MAME Compressed Hunks of Data manager (fake)\n"
    if invocation_error is not None:
        configured_exit = USAGE_EXIT
        stderr = _append_line(stderr, f"fake chdman: {invocation_error}")
    if mismatches:
        configured_exit = ASSERTION_EXIT
        for mismatch in mismatches:
            stderr = _append_line(stderr, f"fake chdman assertion: {mismatch}")

    record["mismatches"] = mismatches
    record["state"] = "running"
    record["exit_code"] = configured_exit

    partial_output = control.get("partial_output_text")
    if output_path is not None and partial_output is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(str(partial_output), encoding="utf-8")
        record["output_exists"] = True

    _write_record(record)
    if stdout:
        sys.stdout.write(stdout)
        sys.stdout.flush()
    if stderr:
        sys.stderr.write(stderr)
        sys.stderr.flush()

    delay = float(control.get("delay_seconds", 0))
    if delay > 0:
        time.sleep(delay)

    create_output = control.get(
        "create_output", output_path is not None and configured_exit == 0
    )
    if create_output and output_path is not None and configured_exit == 0:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(_output_bytes(control))

    record["output_exists"] = bool(output_path and output_path.exists())
    record["state"] = "completed"
    _write_record(record)
    return configured_exit


if __name__ == "__main__":
    raise SystemExit(main())
