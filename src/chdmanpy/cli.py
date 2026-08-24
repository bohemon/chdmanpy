"""Pipeline-oriented command-line entry point for chdmanpy."""

from __future__ import annotations

import argparse
import os
import signal
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, NoReturn

from chdmanpy import __version__
from chdmanpy.arcshuttle import ArcShuttleSelection, read_arcshuttle_results
from chdmanpy.chdman import ChdmanExecutable, discover_chdman
from chdmanpy.config import PRESET_NAMES, resolve_config, resolve_runtime_config
from chdmanpy.errors import ChdmanpyError, CliUsageError, ExitCode, InputError
from chdmanpy.input import load_direct_inputs
from chdmanpy.jsonl import dump_json_lines
from chdmanpy.manifest import EXISTING_POLICIES, load_manifest
from chdmanpy.planner import plan_jobs
from chdmanpy.runner import RunnerOptions, RunOutcome, run_jobs


class _ArgumentParser(argparse.ArgumentParser):
    """Argument parser that maps syntax failures to the public usage exit."""

    def error(self, message: str) -> NoReturn:
        raise CliUsageError(message)


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


@dataclass(frozen=True, slots=True)
class _PlanOutcome:
    jobs: list[dict[str, object]]
    upstream: ArcShuttleSelection | None = None


def _add_input_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "paths",
        metavar="PATH",
        nargs="*",
        help="explicit input file or directory (one or more may be supplied)",
    )
    parser.add_argument(
        "--files-from",
        metavar="FILE",
        help="read newline-delimited input paths from FILE; use - for stdin",
    )
    parser.add_argument(
        "--files0-from",
        metavar="FILE",
        help="read NUL-delimited input paths from FILE; use - for stdin",
    )
    parser.add_argument(
        "--arcshuttle-results",
        metavar="FILE",
        help="read ArcShuttle schema-v2 extract results from FILE; use - for stdin",
    )


def _add_planning_arguments(parser: argparse.ArgumentParser) -> None:
    _add_input_arguments(parser)
    parser.add_argument(
        "--on-upstream-error",
        choices=("fail", "skip"),
        default="fail",
        help=(
            "ArcShuttle policy: reject the complete run, or retain only finalized "
            "successful roots and require a warning exit"
        ),
    )
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        help="root directory for planned CHD outputs",
    )
    parser.add_argument(
        "--preset",
        metavar="NAME",
        choices=PRESET_NAMES,
        help="select a bundled preset",
    )
    parser.add_argument(
        "--config", metavar="FILE", help="read explicit TOML configuration"
    )
    parser.add_argument(
        "--existing",
        choices=tuple(sorted(EXISTING_POLICIES)),
        help="existing-output policy (default: fail)",
    )
    parser.add_argument(
        "--priority",
        type=int,
        metavar="INTEGER",
        help="signed 32-bit scheduling priority recorded in each job",
    )


def _add_runtime_arguments(
    parser: argparse.ArgumentParser, *, include_config: bool
) -> None:
    if include_config:
        parser.add_argument(
            "--config", metavar="FILE", help="read explicit TOML configuration"
        )
    parser.add_argument(
        "--chdman",
        metavar="COMMAND",
        help="explicit CHDMAN executable (otherwise use configuration or PATH)",
    )
    parser.add_argument(
        "--workers",
        type=_positive_integer,
        metavar="COUNT",
        help="maximum number of concurrent CHDMAN processes",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="stop starting new jobs after the first job failure",
    )
    parser.add_argument(
        "--allow-changed",
        action="store_true",
        help="run changed primary inputs with a warning instead of failing them",
    )
    parser.add_argument(
        "--log-dir",
        metavar="DIR",
        help="root directory for per-run and per-job logs",
    )


def _validate_input_selector(arguments: argparse.Namespace) -> None:
    selected = sum(
        (
            bool(arguments.paths),
            arguments.files_from is not None,
            arguments.files0_from is not None,
            arguments.arcshuttle_results is not None,
        )
    )
    if selected != 1:
        raise CliUsageError(
            "select exactly one input source: PATH..., --files-from, "
            "--files0-from, or --arcshuttle-results"
        )
    if arguments.arcshuttle_results is None and arguments.on_upstream_error != "fail":
        raise CliUsageError(
            "--on-upstream-error is valid only with --arcshuttle-results"
        )


def build_parser() -> argparse.ArgumentParser:
    """Build the public plan/run/convert command-line parser."""

    parser = _ArgumentParser(
        prog="chdmanpy",
        description="A pipeline-friendly command-line frontend for CHDMAN.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    plan = commands.add_parser(
        "plan",
        help="validate inputs and emit a schema-v1 job manifest",
        description="Plan CHDMAN jobs and write JSON Lines job records to stdout.",
    )
    _add_planning_arguments(plan)

    run = commands.add_parser(
        "run",
        help="preflight and execute a schema-v1 job manifest",
        description="Run a fully validated chdmanpy job manifest.",
    )
    run.add_argument(
        "--manifest",
        metavar="FILE",
        required=True,
        help="read a chdmanpy schema-v1 job manifest from FILE; use - for stdin",
    )
    _add_runtime_arguments(run, include_config=True)

    convert = commands.add_parser(
        "convert",
        help="plan and execute inputs in one invocation",
        description="Plan and run CHDMAN jobs, emitting results and one summary.",
    )
    _add_planning_arguments(convert)
    _add_runtime_arguments(convert, include_config=False)
    return parser


def _binary_stdin() -> BinaryIO:
    stream = getattr(sys.stdin, "buffer", None)
    if stream is None:
        raise InputError("binary stdin is unavailable")
    return stream


def _read_manifest(value: str, *, cwd: str) -> list[dict[str, object]]:
    if not value or "\x00" in value:
        raise InputError("manifest filename must be nonempty and NUL-free")
    if value == "-":
        try:
            return load_manifest(_binary_stdin())
        except OSError as error:
            raise InputError(f"cannot read manifest from stdin: {error}") from error

    raw_path = os.path.expanduser(value)
    if not os.path.isabs(raw_path):
        raw_path = os.path.join(cwd, raw_path)
    path = Path(os.path.abspath(os.path.normpath(raw_path)))
    try:
        with path.open("rb") as stream:
            return load_manifest(stream)
    except OSError as error:
        raise InputError(f"cannot read manifest {str(path)!r}: {error}") from error


def _plan(arguments: argparse.Namespace, *, cwd: str) -> _PlanOutcome:
    upstream: ArcShuttleSelection | None = None
    if arguments.arcshuttle_results is not None:
        upstream = read_arcshuttle_results(
            arguments.arcshuttle_results,
            stdin=_binary_stdin() if arguments.arcshuttle_results == "-" else None,
            cwd=cwd,
            on_upstream_error=arguments.on_upstream_error,
        )
        _report_upstream(upstream)
        inputs = list(upstream.roots)
    else:
        stdin = (
            _binary_stdin()
            if arguments.files_from == "-" or arguments.files0_from == "-"
            else None
        )
        inputs = load_direct_inputs(
            paths=arguments.paths,
            files_from=arguments.files_from,
            files0_from=arguments.files0_from,
            stdin=stdin,
            cwd=cwd,
        )

    config = resolve_config(
        preset=arguments.preset,
        config_path=arguments.config,
        output_dir=arguments.output_dir,
        existing=arguments.existing,
        priority=arguments.priority,
        cwd=cwd,
    )
    return _PlanOutcome(jobs=plan_jobs(inputs, config, cwd=cwd), upstream=upstream)


def _report_upstream(selection: ArcShuttleSelection | None) -> None:
    if selection is None:
        return
    for diagnostic in selection.diagnostics:
        disposition = "omitted" if diagnostic.omitted else "retained"
        messages = "; ".join(diagnostic.messages)
        print(
            "chdmanpy: ArcShuttle "
            f"job {diagnostic.job_id} status={diagnostic.status} "
            f"{disposition}: {messages}",
            file=sys.stderr,
        )


def _discover_runtime(arguments: argparse.Namespace, *, cwd: str) -> ChdmanExecutable:
    runtime = resolve_runtime_config(config_path=arguments.config, cwd=cwd)
    executable = discover_chdman(explicit=arguments.chdman, runtime=runtime)
    command = " ".join(repr(part) for part in executable.command)
    print(
        f"chdmanpy: CHDMAN selected from {executable.source}: {command}",
        file=sys.stderr,
    )
    print(f"chdmanpy: CHDMAN version: {executable.description}", file=sys.stderr)
    return executable


def _run(
    jobs: list[dict[str, object]],
    arguments: argparse.Namespace,
    *,
    cwd: str,
) -> RunOutcome:
    executable = _discover_runtime(arguments, cwd=cwd)
    options = RunnerOptions(
        workers=arguments.workers,
        fail_fast=arguments.fail_fast,
        allow_changed=arguments.allow_changed,
        log_dir=arguments.log_dir,
    )
    outcome = run_jobs(jobs, chdman=executable, options=options)
    print(f"chdmanpy: run log: {outcome.run_log_path}", file=sys.stderr)
    return outcome


def _dispatch(arguments: argparse.Namespace, *, cwd: str) -> ExitCode:
    if arguments.command == "plan":
        planned = _plan(arguments, cwd=cwd)
        dump_json_lines(sys.stdout, planned.jobs)
        has_job_warnings = any(job["warnings"] for job in planned.jobs)
        return (
            ExitCode.WARNING
            if has_job_warnings
            or (planned.upstream is not None and planned.upstream.requires_warning_exit)
            else ExitCode.SUCCESS
        )

    if arguments.command == "run":
        jobs = _read_manifest(arguments.manifest, cwd=cwd)
        outcome = _run(jobs, arguments, cwd=cwd)
        dump_json_lines(sys.stdout, outcome.records)
        return outcome.exit_code

    if arguments.command == "convert":
        planned = _plan(arguments, cwd=cwd)
        outcome = _run(planned.jobs, arguments, cwd=cwd)
        dump_json_lines(sys.stdout, outcome.records)
        if (
            outcome.exit_code == ExitCode.SUCCESS
            and planned.upstream is not None
            and planned.upstream.requires_warning_exit
        ):
            return ExitCode.WARNING
        return outcome.exit_code

    raise CliUsageError("a command is required")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface and return its process exit code."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    if not arguments:
        parser.print_help()
        return int(ExitCode.SUCCESS)

    try:
        parsed = parser.parse_args(arguments)
        if parsed.command in {"plan", "convert"}:
            _validate_input_selector(parsed)
        return int(_dispatch(parsed, cwd=os.path.abspath(os.getcwd())))
    except CliUsageError as error:
        parser.print_usage(file=sys.stderr)
        print(f"{parser.prog}: error: {error}", file=sys.stderr)
        return int(ExitCode.USAGE)
    except ChdmanpyError as error:
        print(f"{parser.prog}: error: {error}", file=sys.stderr)
        return int(error.exit_code)
    except KeyboardInterrupt:
        print(f"{parser.prog}: interrupted", file=sys.stderr)
        return int(ExitCode.INTERRUPTED)


def _configure_interrupt_handling() -> None:
    if os.name == "nt":
        signal.signal(signal.SIGBREAK, signal.default_int_handler)


def entrypoint() -> int:
    """Console-script entry point."""

    _configure_interrupt_handling()
    stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
    if stdout_reconfigure is not None:
        stdout_reconfigure(encoding="utf-8", errors="strict", newline="\n")
    stderr_reconfigure = getattr(sys.stderr, "reconfigure", None)
    if stderr_reconfigure is not None:
        stderr_reconfigure(encoding="utf-8", errors="backslashreplace")
    return main()
