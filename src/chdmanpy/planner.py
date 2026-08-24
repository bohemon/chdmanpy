"""Deterministic filesystem discovery and schema-v1 job derivation."""

from __future__ import annotations

import hashlib
import ntpath
import os
import posixpath
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from chdmanpy.config import FormatConfig, PlanningConfig
from chdmanpy.errors import ContractError, PlanningError
from chdmanpy.input import normalize_user_paths
from chdmanpy.jsonl import canonical_json_bytes
from chdmanpy.manifest import (
    JOB_RECORD_TYPE,
    SCHEMA_VERSION,
    add_job_integrity,
    make_job_id,
    path_key,
    validate_manifest_records,
)

PRIMARY_CUE_WARNING = (
    "source identity covers only the primary CUE file metadata, not referenced tracks"
)

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_WINDOWS_INVALID_NAME = re.compile(r'[<>:"/\\|?*]')
_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


@dataclass(frozen=True, slots=True)
class DiscoveredSource:
    """One supported primary input and its explicit-root ownership."""

    path: str
    input_root: str
    relative_path: str
    namespace: str
    extension: str
    size: int
    mtime_ns: int


def _is_reparse(value: os.stat_result) -> bool:
    return bool(getattr(value, "st_file_attributes", 0) & _REPARSE_POINT)


def _lstat(path: str, description: str) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as error:
        raise PlanningError(
            f"cannot inspect {description} {path!r}: {error}"
        ) from error


def _existing_components(path: str) -> list[str]:
    value = Path(path)
    return [str(component) for component in reversed(value.parents)] + [str(value)]


def _reject_link_components(path: str, description: str) -> None:
    for component in _existing_components(path):
        try:
            metadata = os.lstat(component)
        except FileNotFoundError:
            continue
        except OSError as error:
            raise PlanningError(
                f"cannot inspect {description} component {component!r}: {error}"
            ) from error
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
            raise PlanningError(
                f"{description} must not traverse a symlink, junction, or reparse point: "
                f"{component!r}"
            )


def _format_for_name(
    name: str, formats: Mapping[str, FormatConfig]
) -> tuple[str, FormatConfig] | None:
    folded = name.casefold()
    for extension in sorted(formats, key=lambda item: (-len(item), item)):
        if folded.endswith(extension):
            return extension, formats[extension]
    return None


def _safe_namespace(value: str, *, windows: bool) -> str:
    if not value:
        return "root"
    if windows:
        value = _WINDOWS_INVALID_NAME.sub("_", value).rstrip(" .")
        if not value:
            value = "root"
        if value.split(".", maxsplit=1)[0].upper() in _WINDOWS_DEVICE_NAMES:
            value = f"_{value}"
    return value


def _namespace_bases(
    paths: Sequence[str], file_roots: frozenset[str], *, windows: bool
) -> list[str]:
    path_module = ntpath if windows else posixpath
    result: list[str] = []
    for path in paths:
        stripped = path.rstrip("\\/")
        leaf = path_module.basename(stripped)
        if not leaf:
            drive, _ = path_module.splitdrive(path)
            leaf = drive.rstrip(":") or "root"
        if path_key(path, windows=windows) in file_roots:
            leaf = path_module.splitext(leaf)[0] or leaf
        result.append(_safe_namespace(leaf, windows=windows))
    return result


def assign_root_namespaces(
    paths: Sequence[str],
    *,
    file_roots: Sequence[str] = (),
    windows: bool | None = None,
) -> list[str]:
    """Return collision-safe namespaces for explicit roots.

    Every member of a same-leaf collision group receives a path-derived suffix, so
    reordering explicit roots cannot change which root owns an unsuffixed name.
    """

    windows_mode = os.name == "nt" if windows is None else windows
    file_keys = frozenset(path_key(value, windows=windows_mode) for value in file_roots)
    bases = _namespace_bases(paths, file_keys, windows=windows_mode)
    groups: dict[str, list[int]] = {}
    for index, base in enumerate(bases):
        groups.setdefault(path_key(base, windows=windows_mode), []).append(index)

    candidates = list(bases)
    suffixed: set[int] = set()
    for indexes in groups.values():
        if len(indexes) > 1:
            suffixed.update(indexes)
    digest_lengths = {index: 12 for index in suffixed}
    while True:
        for index in suffixed:
            digest = hashlib.sha256(
                path_key(paths[index], windows=windows_mode).encode("utf-8")
            ).hexdigest()
            candidates[index] = f"{bases[index]}--{digest[: digest_lengths[index]]}"
        collisions: dict[str, list[int]] = {}
        for index, candidate in enumerate(candidates):
            collisions.setdefault(path_key(candidate, windows=windows_mode), []).append(
                index
            )
        duplicate_groups = [
            indexes for indexes in collisions.values() if len(indexes) > 1
        ]
        if not duplicate_groups:
            return candidates
        changed = False
        for indexes in duplicate_groups:
            for index in indexes:
                suffixed.add(index)
                if digest_lengths.get(index, 12) < 64:
                    digest_lengths[index] = min(64, digest_lengths.get(index, 12) + 8)
                    changed = True
        if not changed:
            # Only an actual SHA-256 collision reaches this deterministic fallback.
            ordered = sorted(
                range(len(paths)),
                key=lambda index: path_key(paths[index], windows=windows_mode),
            )
            ranks = {index: rank for rank, index in enumerate(ordered, start=1)}
            return [
                f"{candidate}--{ranks[index]}" if index in suffixed else candidate
                for index, candidate in enumerate(candidates)
            ]


def _walk_directory(
    root: str, formats: Mapping[str, FormatConfig]
) -> list[tuple[str, str, os.stat_result]]:
    discovered: list[tuple[str, str, os.stat_result]] = []

    def visit(directory: str) -> None:
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(
                    iterator, key=lambda entry: (entry.name.casefold(), entry.name)
                )
        except OSError as error:
            raise PlanningError(
                f"cannot scan input directory {directory!r}: {error}"
            ) from error
        for entry in entries:
            path = os.path.join(directory, entry.name)
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise PlanningError(
                    f"cannot inspect input entry {path!r}: {error}"
                ) from error
            if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
                continue
            if stat.S_ISDIR(metadata.st_mode):
                visit(path)
            elif stat.S_ISREG(metadata.st_mode):
                matched = _format_for_name(entry.name, formats)
                if matched is not None:
                    discovered.append((path, matched[0], metadata))

    visit(root)
    return discovered


def discover_sources(
    input_paths: Sequence[str | os.PathLike[str]],
    formats: Mapping[str, FormatConfig],
    *,
    cwd: str | os.PathLike[str] | None = None,
) -> list[DiscoveredSource]:
    """Discover supported regular files without following special filesystem entries."""

    if not formats:
        raise PlanningError("at least one input extension must be configured")
    paths = normalize_user_paths(input_paths, cwd=cwd)
    root_metadata: list[os.stat_result] = []
    file_roots: list[str] = []
    for path in paths:
        _reject_link_components(path, "input path")
        metadata = _lstat(path, "input path")
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
            raise PlanningError(
                f"input root must not be a symlink or reparse point: {path!r}"
            )
        if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
            raise PlanningError(
                f"input root must be a regular file or directory: {path!r}"
            )
        if stat.S_ISREG(metadata.st_mode):
            file_roots.append(path)
        root_metadata.append(metadata)

    namespaces = assign_root_namespaces(paths, file_roots=file_roots)
    result: list[DiscoveredSource] = []
    seen: set[str] = set()
    for root, metadata, namespace in zip(paths, root_metadata, namespaces, strict=True):
        if stat.S_ISREG(metadata.st_mode):
            matched = _format_for_name(os.path.basename(root), formats)
            candidates = [] if matched is None else [(root, matched[0], metadata)]
        else:
            candidates = _walk_directory(root, formats)
        for path, extension, source_metadata in candidates:
            key = path_key(path)
            if key in seen:
                continue
            relative = (
                os.path.basename(path) if path == root else os.path.relpath(path, root)
            )
            result.append(
                DiscoveredSource(
                    path=path,
                    input_root=root,
                    relative_path=relative,
                    namespace=namespace,
                    extension=extension,
                    size=source_metadata.st_size,
                    mtime_ns=source_metadata.st_mtime_ns,
                )
            )
            seen.add(key)
    if not result:
        raise PlanningError("no supported regular input files were discovered")
    return result


def source_metadata_identity(source: DiscoveredSource) -> str:
    """Identify only the primary file's path, size, and nanosecond mtime metadata."""

    value = {
        "identity_kind": "primary-file-metadata-v1",
        "path": path_key(source.path),
        "size": source.size,
        "mtime_ns": source.mtime_ns,
    }
    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


def _is_equal_or_beneath(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((path_key(path), path_key(root))) == path_key(root)
    except ValueError:
        return False


def _validate_output_root(output_dir: str, input_roots: Sequence[str]) -> None:
    if not os.path.isabs(output_dir):
        raise PlanningError("output directory must be an absolute path")
    _reject_link_components(output_dir, "output path")
    try:
        metadata = os.lstat(output_dir)
    except FileNotFoundError:
        metadata = None
    except OSError as error:
        raise PlanningError(
            f"cannot inspect output directory {output_dir!r}: {error}"
        ) from error
    if metadata is not None and not stat.S_ISDIR(metadata.st_mode):
        raise PlanningError(f"output path must be a directory: {output_dir!r}")

    directory_roots = {
        root for root in input_roots if stat.S_ISDIR(_lstat(root, "input root").st_mode)
    }
    for root in directory_roots:
        if _is_equal_or_beneath(output_dir, root) or _is_equal_or_beneath(
            root, output_dir
        ):
            raise PlanningError(
                "output directory and directory input root must not contain one another: "
                f"{output_dir!r}, {root!r}"
            )


def _destination_for(source: DiscoveredSource, output_dir: str) -> str:
    relative = Path(source.relative_path)
    relative_without_extension = relative.name[: -len(source.extension)]
    destination_name = f"{relative_without_extension}.chd"
    return str(Path(output_dir) / source.namespace / relative.parent / destination_name)


def _validate_destination_parent(destination: str) -> None:
    parent = os.path.dirname(destination)
    _reject_link_components(parent, "destination parent")
    for component in _existing_components(parent):
        try:
            metadata = os.lstat(component)
        except FileNotFoundError:
            continue
        except OSError as error:
            raise PlanningError(
                f"cannot inspect destination parent {component!r}: {error}"
            ) from error
        if not stat.S_ISDIR(metadata.st_mode):
            raise PlanningError(
                f"destination parent component must be a directory: {component!r}"
            )


def _renamed_destination(destination: str, reserved: set[str]) -> str:
    value = Path(destination)
    index = 1
    while True:
        candidate = str(value.with_name(f"{value.stem} ({index}){value.suffix}"))
        if path_key(candidate) not in reserved and not os.path.lexists(candidate):
            return candidate
        index += 1


def plan_jobs(
    input_paths: Sequence[str | os.PathLike[str]],
    config: PlanningConfig,
    *,
    cwd: str | os.PathLike[str] | None = None,
) -> list[dict[str, object]]:
    """Create and fully validate deterministic schema-v1 jobs without invoking CHDMAN."""

    normalized_inputs = normalize_user_paths(input_paths, cwd=cwd)
    sources = discover_sources(normalized_inputs, config.formats)
    output_dir = os.path.abspath(os.path.normpath(config.output_dir))
    _validate_output_root(output_dir, normalized_inputs)

    jobs: list[dict[str, object]] = []
    destinations: dict[str, str] = {}
    for source in sources:
        format_config = config.formats[source.extension]
        identity = source_metadata_identity(source)
        destination = _destination_for(source, output_dir)
        if config.existing == "rename" and (
            os.path.lexists(destination) or path_key(destination) in destinations
        ):
            destination = _renamed_destination(destination, set(destinations))
        _validate_destination_parent(destination)
        destination_key = path_key(destination)
        if destination_key in destinations:
            raise PlanningError(
                "destination collision between "
                f"{destinations[destination_key]!r} and {destination!r}"
            )
        if path_key(destination) == path_key(source.path):
            raise PlanningError(
                f"destination must not replace its source: {destination!r}"
            )
        warnings = [PRIMARY_CUE_WARNING] if source.extension == ".cue" else []
        job: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "record_type": JOB_RECORD_TYPE,
            "job_id": make_job_id(
                source.path,
                identity,
                format_config.operation,
                format_config.options,
            ),
            "plan_index": len(jobs),
            "source": {
                "path": source.path,
                "input_root": source.input_root,
                "size": source.size,
                "mtime_ns": source.mtime_ns,
                "identity": identity,
            },
            "destination": {"path": destination, "existing": config.existing},
            "chdman": {
                "operation": format_config.operation,
                "options": list(format_config.options),
            },
            "scheduling": {
                "priority": config.priority,
                "estimated_weight": source.size,
            },
            "tags": [],
            "warnings": warnings,
        }
        jobs.append(add_job_integrity(job))
        destinations[destination_key] = destination
    # Planner faults use the public usage/configuration failure category.
    try:
        return validate_manifest_records(jobs)
    except ContractError as error:
        raise PlanningError(f"planner produced an invalid manifest: {error}") from error


__all__ = [
    "PRIMARY_CUE_WARNING",
    "DiscoveredSource",
    "assign_root_namespaces",
    "discover_sources",
    "plan_jobs",
    "source_metadata_identity",
]
