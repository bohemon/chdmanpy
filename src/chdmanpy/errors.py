"""Shared exceptions and process exit codes for chdmanpy."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Stable public process exit codes."""

    SUCCESS = 0
    WARNING = 1
    JOB_FAILURE = 2
    USAGE = 64
    INTERRUPTED = 130


class ChdmanpyError(Exception):
    """Base class for errors that may be reported without a traceback."""

    exit_code = ExitCode.USAGE


class ContractError(ChdmanpyError, ValueError):
    """Raised when JSON Lines data violates a public stream contract."""


class CliUsageError(ChdmanpyError):
    """Raised for invalid command-line syntax or command selection."""


class ConfigurationError(ChdmanpyError, ValueError):
    """Raised when configuration is missing, malformed, or unsafe."""


class InputError(ChdmanpyError, ValueError):
    """Raised when a direct input selector or path list is invalid."""


class PlanningError(ChdmanpyError, ValueError):
    """Raised when filesystem inputs cannot produce a safe job plan."""


__all__ = [
    "ChdmanpyError",
    "CliUsageError",
    "ConfigurationError",
    "ContractError",
    "ExitCode",
    "InputError",
    "PlanningError",
]
