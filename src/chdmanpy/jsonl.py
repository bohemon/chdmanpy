"""Strict UTF-8 JSON Lines input and output helpers."""

from __future__ import annotations

import codecs
import json
from collections.abc import Iterable, Mapping
from typing import Any, BinaryIO, TextIO

from chdmanpy.errors import ContractError


def canonical_json_bytes(value: object) -> bytes:
    """Serialize *value* using the canonical form used by contract hashes."""

    try:
        serialized = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise ContractError(
            f"value cannot be encoded as canonical JSON: {error}"
        ) from error
    return serialized.encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ContractError(f"non-finite JSON number is not supported: {value}")


def loads_json_lines(stream: BinaryIO) -> list[dict[str, Any]]:
    """Read a complete BOM-free UTF-8 JSON Lines stream.

    A final line ending is permitted, but every logical record line must contain
    exactly one JSON object. Empty and whitespace-only record lines are errors.
    The whole byte stream is decoded and parsed before any records are returned.
    """

    raw = stream.read()
    if not isinstance(raw, bytes):
        raise TypeError("JSON Lines input must be opened in binary mode")
    if raw.startswith(codecs.BOM_UTF8):
        raise ContractError("UTF-8 BOM is not permitted in JSON Lines input")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ContractError(
            f"JSON Lines input is not valid UTF-8 at byte {error.start}"
        ) from error

    if not text:
        return []

    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()

    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        line = line.removesuffix("\r")
        if not line.strip():
            raise ContractError(f"blank JSON Lines record at line {line_number}")
        try:
            value = json.loads(
                line,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
            )
        except ContractError as error:
            raise ContractError(
                f"invalid JSON at line {line_number}: {error}"
            ) from error
        except json.JSONDecodeError as error:
            raise ContractError(
                f"invalid JSON at line {line_number}, column {error.colno}: {error.msg}"
            ) from error
        except (RecursionError, ValueError) as error:
            raise ContractError(
                f"invalid JSON at line {line_number}: {error}"
            ) from error
        if not isinstance(value, dict):
            raise ContractError(
                f"JSON Lines record at line {line_number} must be an object"
            )
        records.append(value)
    return records


def dump_json_line(stream: TextIO, record: Mapping[str, object]) -> None:
    """Write one BOM-free JSON record and a line feed to a text stream."""

    try:
        line = json.dumps(
            record,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ContractError(f"record cannot be encoded as JSON: {error}") from error
    stream.write(line)
    stream.write("\n")


def dump_json_lines(stream: TextIO, records: Iterable[Mapping[str, object]]) -> None:
    """Write records as BOM-free JSON Lines."""

    for record in records:
        dump_json_line(stream, record)


__all__ = [
    "canonical_json_bytes",
    "dump_json_line",
    "dump_json_lines",
    "loads_json_lines",
]
