"""Strict planning configuration and bundled preset resolution."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any

from chdmanpy.errors import ConfigurationError
from chdmanpy.manifest import ALLOWED_OPERATIONS, EXISTING_POLICIES

DEFAULT_PRESET = "others"
PRESET_NAMES = ("others", "ps2", "psp")

_TOP_LEVEL_KEYS = frozenset({"options", "planning"})
_PLANNING_KEYS = frozenset({"output_dir", "existing", "priority"})
_ENVIRONMENT_KEYS = frozenset(
    {
        "CHDMANPY_EXISTING",
        "CHDMANPY_OUTPUT_DIR",
        "CHDMANPY_PRESET",
        "CHDMANPY_PRIORITY",
    }
)
_MANAGED_OPTIONS = frozenset({"-f", "-i", "-o", "--force", "--input", "--output"})
_EXTENSION_RE = re.compile(r"\.[^/\\\x00]+\Z")


@dataclass(frozen=True, slots=True)
class FormatConfig:
    """One input extension's validated CHDMAN creation arguments."""

    operation: str
    options: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanningConfig:
    """Fully resolved, immutable planner configuration."""

    output_dir: str
    formats: Mapping[str, FormatConfig]
    existing: str = "fail"
    priority: int = 0
    preset: str = DEFAULT_PRESET


def _unknown_keys(
    value: Mapping[str, object], allowed: frozenset[str], name: str
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigurationError(f"{name} contains unknown keys: {', '.join(unknown)}")


def _validate_extension(value: object) -> str:
    if not isinstance(value, str):
        raise ConfigurationError("option extension keys must be strings")
    extension = value.casefold()
    if not _EXTENSION_RE.fullmatch(extension) or extension in {".", ".."}:
        raise ConfigurationError(
            f"invalid option extension {value!r}; expected a suffix beginning with '.'"
        )
    return extension


def _validate_arguments(extension: str, value: object) -> FormatConfig:
    if not isinstance(value, list) or not value:
        raise ConfigurationError(
            f"options.{extension!r} must be a nonempty array of strings"
        )
    if any(not isinstance(item, str) or not item or "\x00" in item for item in value):
        raise ConfigurationError(
            f"options.{extension!r} must contain nonempty, NUL-free strings"
        )
    operation, *options = value
    if operation not in ALLOWED_OPERATIONS:
        raise ConfigurationError(
            f"options.{extension!r} operation must be createcd or createdvd"
        )
    for option in options:
        option_name = option.split("=", maxsplit=1)[0]
        if option_name in _MANAGED_OPTIONS or option.startswith(("-i=", "-o=", "-f=")):
            raise ConfigurationError(
                f"options.{extension!r} must not set managed option {option_name!r}"
            )
    return FormatConfig(operation=operation, options=tuple(options))


def _validate_options(value: object, name: str = "options") -> dict[str, FormatConfig]:
    if not isinstance(value, dict) or not value:
        raise ConfigurationError(f"{name} must be a nonempty table")
    result: dict[str, FormatConfig] = {}
    original_keys: dict[str, str] = {}
    for raw_extension, arguments in value.items():
        extension = _validate_extension(raw_extension)
        if extension in result:
            raise ConfigurationError(
                f"{name} contains duplicate normalized extensions "
                f"{original_keys[extension]!r} and {raw_extension!r}"
            )
        result[extension] = _validate_arguments(extension, arguments)
        original_keys[extension] = raw_extension
    return result


def _decode_toml(data: bytes, source: str) -> dict[str, Any]:
    if data.startswith(b"\xef\xbb\xbf"):
        raise ConfigurationError(f"{source} must be BOM-free UTF-8 TOML")
    try:
        text = data.decode("utf-8", errors="strict")
        value = tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(f"invalid TOML in {source}: {error}") from error
    _unknown_keys(value, _TOP_LEVEL_KEYS, source)
    if "options" in value:
        _validate_options(value["options"])
    planning = value.get("planning", {})
    if not isinstance(planning, dict):
        raise ConfigurationError(f"{source}.planning must be a table")
    _unknown_keys(planning, _PLANNING_KEYS, f"{source}.planning")
    if "output_dir" in planning:
        _string(planning["output_dir"], f"{source}.planning.output_dir")
    if "existing" in planning:
        _existing(planning["existing"], f"{source}.planning.existing")
    if "priority" in planning:
        priority_value = planning["priority"]
        if isinstance(priority_value, bool) or not isinstance(priority_value, int):
            raise ConfigurationError(
                f"{source}.planning.priority must be a signed 32-bit integer"
            )
        _priority(priority_value, f"{source}.planning.priority")
    return value


def load_preset(name: str) -> Mapping[str, FormatConfig]:
    """Load and validate a named bundled preset."""

    if name not in PRESET_NAMES:
        raise ConfigurationError(
            f"unknown preset {name!r}; expected one of {', '.join(PRESET_NAMES)}"
        )
    resource = resources.files("chdmanpy.presets").joinpath(f"{name}.toml")
    value = _decode_toml(resource.read_bytes(), f"bundled preset {name!r}")
    return MappingProxyType(_validate_options(value["options"], "preset.options"))


def _config_file(path: str | os.PathLike[str], cwd: str) -> dict[str, Any]:
    raw_path = os.fspath(path)
    config_path = raw_path if os.path.isabs(raw_path) else os.path.join(cwd, raw_path)
    config_path = os.path.abspath(os.path.normpath(config_path))
    try:
        data = Path(config_path).read_bytes()
    except OSError as error:
        raise ConfigurationError(
            f"cannot read configuration {config_path!r}: {error}"
        ) from error
    return _decode_toml(data, f"configuration {config_path!r}")


def _string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ConfigurationError(f"{location} must be a nonempty, NUL-free string")
    return value


def _priority(value: object, location: str) -> int:
    if isinstance(value, bool):
        raise ConfigurationError(f"{location} must be a signed 32-bit integer")
    if isinstance(value, str):
        try:
            parsed = int(value, 10)
        except ValueError as error:
            raise ConfigurationError(
                f"{location} must be a signed 32-bit integer"
            ) from error
    elif isinstance(value, int):
        parsed = value
    else:
        raise ConfigurationError(f"{location} must be a signed 32-bit integer")
    if not -(2**31) <= parsed < 2**31:
        raise ConfigurationError(f"{location} must be a signed 32-bit integer")
    return parsed


def _existing(value: object, location: str) -> str:
    policy = _string(value, location)
    if policy not in EXISTING_POLICIES:
        raise ConfigurationError(f"{location} must be fail, skip, or rename")
    return policy


def _output_dir(value: object, cwd: str, location: str) -> str:
    path = os.path.expanduser(_string(value, location))
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    return os.path.abspath(os.path.normpath(path))


def resolve_config(
    *,
    preset: str | None = None,
    config_path: str | os.PathLike[str] | None = None,
    output_dir: str | os.PathLike[str] | None = None,
    existing: str | None = None,
    priority: int | None = None,
    options: Mapping[str, Sequence[str]] | None = None,
    environ: Mapping[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> PlanningConfig:
    """Resolve CLI, environment, TOML, and preset/default layers in that order.

    Named arguments represent CLI values. ``options`` is an internal/programmatic CLI
    layer; the public 0.1.0 parser does not expose arbitrary CHDMAN fragments.
    """

    environment = dict(os.environ if environ is None else environ)
    unknown_environment = sorted(
        key
        for key in environment
        if key.startswith("CHDMANPY_") and key not in _ENVIRONMENT_KEYS
    )
    if unknown_environment:
        raise ConfigurationError(
            "environment contains unknown chdmanpy keys: "
            + ", ".join(unknown_environment)
        )
    working_directory = os.path.abspath(
        os.fspath(cwd) if cwd is not None else os.getcwd()
    )

    preset_name = preset if preset is not None else environment.get("CHDMANPY_PRESET")
    if preset_name is None:
        preset_name = DEFAULT_PRESET
    preset_name = _string(preset_name, "preset")
    formats = dict(load_preset(preset_name))

    file_value: dict[str, Any] = {}
    if config_path is not None:
        file_value = _config_file(config_path, working_directory)
        if "options" in file_value:
            # Historical [options] is a complete mapping, not a merge with a preset.
            formats = _validate_options(file_value["options"])
    file_planning = file_value.get("planning", {})

    selected_output = file_planning.get("output_dir")
    selected_existing: object = file_planning.get("existing", "fail")
    selected_priority: object = file_planning.get("priority", 0)

    if "CHDMANPY_OUTPUT_DIR" in environment:
        selected_output = environment["CHDMANPY_OUTPUT_DIR"]
    if "CHDMANPY_EXISTING" in environment:
        selected_existing = environment["CHDMANPY_EXISTING"]
    if "CHDMANPY_PRIORITY" in environment:
        selected_priority = environment["CHDMANPY_PRIORITY"]

    if output_dir is not None:
        selected_output = os.fspath(output_dir)
    if existing is not None:
        selected_existing = existing
    if priority is not None:
        selected_priority = priority
    if options is not None:
        formats = _validate_options(
            {extension: list(arguments) for extension, arguments in options.items()},
            "CLI options",
        )

    if selected_output is None:
        raise ConfigurationError(
            "output directory is required via CLI, CHDMANPY_OUTPUT_DIR, or [planning]"
        )
    return PlanningConfig(
        output_dir=_output_dir(selected_output, working_directory, "output directory"),
        formats=MappingProxyType(formats),
        existing=_existing(selected_existing, "existing-output policy"),
        priority=_priority(selected_priority, "priority"),
        preset=preset_name,
    )


__all__ = [
    "DEFAULT_PRESET",
    "PRESET_NAMES",
    "FormatConfig",
    "PlanningConfig",
    "load_preset",
    "resolve_config",
]
