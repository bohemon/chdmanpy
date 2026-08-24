"""Explicit direct-input selectors and deterministic path normalization."""

from __future__ import annotations

import ntpath
import os
import posixpath
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO

from chdmanpy.errors import InputError
from chdmanpy.manifest import is_absolute_path, path_key


def _path_module(windows: bool) -> object:
    return ntpath if windows else posixpath


def normalize_user_paths(
    values: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | os.PathLike[str] | None = None,
    windows: bool | None = None,
) -> list[str]:
    """Normalize without resolving symlinks and preserve first-occurrence order."""

    windows_mode = os.name == "nt" if windows is None else windows
    path_module = _path_module(windows_mode)
    working_directory = os.fspath(cwd) if cwd is not None else os.getcwd()
    if not is_absolute_path(working_directory, windows=windows_mode):
        raise InputError("input working directory must be an absolute path")
    working_directory = path_module.normpath(working_directory)  # type: ignore[attr-defined]

    normalized: list[str] = []
    seen: set[str] = set()
    for index, raw_value in enumerate(values):
        value = os.fspath(raw_value)
        if not isinstance(value, str):
            raise InputError(f"input path {index + 1} must be text, not bytes")
        if not value or "\x00" in value:
            raise InputError(f"input path {index + 1} must be nonempty and NUL-free")
        if windows_mode == (os.name == "nt"):
            value = os.path.expanduser(value)
        if not is_absolute_path(value, windows=windows_mode):
            value = path_module.join(working_directory, value)  # type: ignore[attr-defined]
        value = path_module.normpath(value)  # type: ignore[attr-defined]
        if not is_absolute_path(value, windows=windows_mode):
            raise InputError(
                f"input path {index + 1} could not be resolved to an absolute path"
            )
        key = path_key(value, windows=windows_mode)
        if key not in seen:
            normalized.append(value)
            seen.add(key)
    if not normalized:
        raise InputError("the selected input source contains no paths")
    return normalized


def _decode_path_list(data: bytes, *, nul_delimited: bool, source: str) -> list[str]:
    if data.startswith(b"\xef\xbb\xbf"):
        raise InputError(f"{source} must be BOM-free UTF-8")
    try:
        if nul_delimited:
            raw_items = data.split(b"\x00")
            if raw_items and not raw_items[-1]:
                raw_items.pop()
        else:
            raw_items = data.split(b"\n")
            if raw_items and not raw_items[-1]:
                raw_items.pop()
            raw_items = [
                item[:-1] if item.endswith(b"\r") else item for item in raw_items
            ]
        items = [item.decode("utf-8", errors="strict") for item in raw_items]
    except UnicodeDecodeError as error:
        raise InputError(f"{source} must be BOM-free UTF-8: {error}") from error
    if any(not item for item in items):
        delimiter = "NUL" if nul_delimited else "newline"
        raise InputError(f"{source} contains an empty {delimiter}-delimited path")
    if not nul_delimited and any("\r" in item or "\x00" in item for item in items):
        raise InputError(f"{source} contains an invalid newline-delimited path")
    return items


def _read_list_file(
    value: str | os.PathLike[str],
    *,
    stdin: BinaryIO | None,
    cwd: str | os.PathLike[str] | None,
) -> tuple[bytes, str]:
    name = os.fspath(value)
    if not isinstance(name, str):
        raise InputError("path-list filename must be text, not bytes")
    if "\x00" in name:
        raise InputError("path-list filename must not contain NUL")
    if name == "-":
        stream = stdin
        if stream is None:
            stream = getattr(sys.stdin, "buffer", None)
        if stream is None:
            raise InputError("binary stdin is unavailable")
        try:
            return stream.read(), "stdin"
        except OSError as error:
            raise InputError(f"cannot read stdin: {error}") from error
    working_directory = os.fspath(cwd) if cwd is not None else os.getcwd()
    path = name if os.path.isabs(name) else os.path.join(working_directory, name)
    path = os.path.abspath(os.path.normpath(path))
    try:
        return Path(path).read_bytes(), f"path list {path!r}"
    except OSError as error:
        raise InputError(f"cannot read path list {path!r}: {error}") from error


def load_direct_inputs(
    *,
    paths: Sequence[str | os.PathLike[str]] = (),
    files_from: str | os.PathLike[str] | None = None,
    files0_from: str | os.PathLike[str] | None = None,
    stdin: BinaryIO | None = None,
    cwd: str | os.PathLike[str] | None = None,
    windows: bool | None = None,
) -> list[str]:
    """Read exactly one direct selector and return normalized absolute paths.

    ArcShuttle result ingestion is intentionally not part of this function; its
    schema-v2 adapter supplies roots to the same planner in a later integration layer.
    """

    selected = sum((bool(paths), files_from is not None, files0_from is not None))
    if selected != 1:
        raise InputError(
            "select exactly one direct input source: paths, files_from, or files0_from"
        )
    values: Sequence[str | os.PathLike[str]]
    if paths:
        values = paths
    else:
        list_file = files_from if files_from is not None else files0_from
        if list_file is None:  # pragma: no cover - selected above, for type narrowing
            raise AssertionError("unreachable input selector")
        data, source = _read_list_file(list_file, stdin=stdin, cwd=cwd)
        values = _decode_path_list(
            data,
            nul_delimited=files0_from is not None,
            source=source,
        )
    return normalize_user_paths(values, cwd=cwd, windows=windows)


__all__ = ["load_direct_inputs", "normalize_user_paths"]
