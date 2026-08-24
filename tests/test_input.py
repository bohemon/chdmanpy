from __future__ import annotations

import io
from pathlib import Path

import pytest

from chdmanpy.errors import InputError
from chdmanpy.input import load_direct_inputs, normalize_user_paths


def test_positional_paths_are_normalized_and_deduplicated(tmp_path: Path) -> None:
    assert load_direct_inputs(
        paths=["games/../disc.iso", "./disc.iso", "second.cue"],
        cwd=tmp_path,
    ) == [str(tmp_path / "disc.iso"), str(tmp_path / "second.cue")]


def test_windows_normalization_uses_case_insensitive_first_occurrence() -> None:
    assert normalize_user_paths(
        [r"Games\DISC.ISO", r"games\disc.iso", r"Other\disc.iso"],
        cwd=r"C:\Input",
        windows=True,
    ) == [r"C:\Input\Games\DISC.ISO", r"C:\Input\Other\disc.iso"]


def test_windows_rejects_drive_relative_input_on_a_different_drive() -> None:
    with pytest.raises(InputError, match="absolute"):
        normalize_user_paths(
            [r"C:disc.iso"],
            cwd=r"D:\Input",
            windows=True,
        )


@pytest.mark.parametrize(
    "keywords",
    [
        {},
        {"paths": ["one"], "files_from": "paths.txt"},
        {"files_from": "a", "files0_from": "b"},
    ],
)
def test_requires_exactly_one_direct_selector(keywords: dict[str, object]) -> None:
    with pytest.raises(InputError, match="exactly one"):
        load_direct_inputs(**keywords)  # type: ignore[arg-type]


def test_newline_list_supports_utf8_crlf_and_stdin(tmp_path: Path) -> None:
    stream = io.BytesIO("日本語.iso\r\nspace name.cue\r\n".encode())
    assert load_direct_inputs(files_from="-", stdin=stream, cwd=tmp_path) == [
        str(tmp_path / "日本語.iso"),
        str(tmp_path / "space name.cue"),
    ]


def test_nul_list_preserves_newlines_in_paths(tmp_path: Path) -> None:
    stream = io.BytesIO(b"line\nbreak.cue\0plain.cue\0")
    assert load_direct_inputs(files0_from="-", stdin=stream, cwd=tmp_path) == [
        str(tmp_path / "line\nbreak.cue"),
        str(tmp_path / "plain.cue"),
    ]


def test_relative_list_filename_is_resolved_from_cwd(tmp_path: Path) -> None:
    (tmp_path / "paths.txt").write_text("one.iso\ntwo.iso\n", encoding="utf-8")
    assert load_direct_inputs(files_from="paths.txt", cwd=tmp_path) == [
        str(tmp_path / "one.iso"),
        str(tmp_path / "two.iso"),
    ]


@pytest.mark.parametrize(
    "data, nul_delimited, message",
    [
        (b"\xef\xbb\xbfone.iso\n", False, "BOM-free"),
        (b"\xff\n", False, "UTF-8"),
        (b"one\n\ntwo\n", False, "empty"),
        (b"one\x00\x00two\x00", True, "empty"),
        (b"one\x00two\n", False, "invalid"),
        (b"", False, "no paths"),
    ],
)
def test_rejects_invalid_path_list_streams(
    tmp_path: Path, data: bytes, nul_delimited: bool, message: str
) -> None:
    selector = "files0_from" if nul_delimited else "files_from"
    with pytest.raises(InputError, match=message):
        load_direct_inputs(
            **{selector: "-"},
            stdin=io.BytesIO(data),
            cwd=tmp_path,
        )


def test_does_not_resolve_symlinks_during_normalization(tmp_path: Path) -> None:
    link = tmp_path / "linked"
    assert normalize_user_paths([link / "disc.iso"], cwd=tmp_path) == [
        str(link / "disc.iso")
    ]


def test_rejects_bytes_paths() -> None:
    with pytest.raises(InputError, match="text, not bytes"):
        load_direct_inputs(paths=[b"disc.iso"])  # type: ignore[list-item]
