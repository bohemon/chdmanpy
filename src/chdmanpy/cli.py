"""Command-line entry point for chdmanpy."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from chdmanpy import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the package-foundation command-line parser."""
    return argparse.ArgumentParser(
        prog="chdmanpy",
        description="A pipeline-friendly command-line frontend for CHDMAN.",
        epilog="The plan, run, and convert commands are not available yet.",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface and return its process exit code."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    if not arguments:
        parser.print_help()
        return 0

    parser.parse_args(arguments)
    return 0


def entrypoint() -> int:
    """Console-script entry point."""
    return main()
