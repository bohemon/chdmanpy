"""Command-line entry point for chdmanpy."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import NoReturn

from chdmanpy import __version__
from chdmanpy.errors import CliUsageError, ExitCode


class _ArgumentParser(argparse.ArgumentParser):
    """Argument parser that maps syntax failures to the public usage exit."""

    def error(self, message: str) -> NoReturn:
        raise CliUsageError(message)


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
        "--output-dir",
        metavar="DIR",
        help="root directory for planned CHD outputs",
    )
    parser.add_argument("--preset", metavar="NAME", help="select a bundled preset")
    parser.add_argument(
        "--config", metavar="FILE", help="read explicit TOML configuration"
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

    convert = commands.add_parser(
        "convert",
        help="plan and execute inputs in one invocation",
        description="Plan and run CHDMAN jobs, emitting results and one summary.",
    )
    _add_planning_arguments(convert)
    return parser


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
    except CliUsageError as error:
        parser.print_usage(file=sys.stderr)
        print(f"{parser.prog}: error: {error}", file=sys.stderr)
        return int(ExitCode.USAGE)

    print(f"{parser.prog}: {parsed.command} is not implemented yet", file=sys.stderr)
    return int(ExitCode.USAGE)


def entrypoint() -> int:
    """Console-script entry point."""

    stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
    if stdout_reconfigure is not None:
        stdout_reconfigure(encoding="utf-8", errors="strict", newline="\n")
    stderr_reconfigure = getattr(sys.stderr, "reconfigure", None)
    if stderr_reconfigure is not None:
        stderr_reconfigure(encoding="utf-8", errors="backslashreplace")
    return main()
