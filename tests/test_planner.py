from __future__ import annotations

import os
from pathlib import Path
from types import MappingProxyType

import pytest

from chdmanpy.config import FormatConfig, PlanningConfig, resolve_config
from chdmanpy.errors import PlanningError
from chdmanpy.manifest import validate_manifest_records
from chdmanpy.planner import (
    PRIMARY_CUE_WARNING,
    assign_root_namespaces,
    discover_sources,
    plan_jobs,
    source_metadata_identity,
)


def planning_config(
    output_dir: Path,
    *,
    formats: dict[str, FormatConfig] | None = None,
    existing: str = "fail",
) -> PlanningConfig:
    return PlanningConfig(
        output_dir=str(output_dir),
        formats=MappingProxyType(
            formats or {".iso": FormatConfig("createdvd", ("-c", "zlib"))}
        ),
        existing=existing,
    )


def test_plan_is_deterministic_namespaced_and_manifest_valid(tmp_path: Path) -> None:
    input_dir = tmp_path / "Input 日本語"
    (input_dir / "B folder").mkdir(parents=True)
    (input_dir / "B folder" / "second.ISO").write_bytes(b"22")
    (input_dir / "first.iso").write_bytes(b"1")
    (input_dir / "ignored.txt").write_text("ignored", encoding="utf-8")
    output_dir = tmp_path / "Output"
    config = planning_config(output_dir)

    first = plan_jobs([input_dir], config)
    second = plan_jobs([input_dir], config)

    assert first == second
    assert validate_manifest_records(first) == first
    assert [job["source"]["path"] for job in first] == [
        str(input_dir / "B folder" / "second.ISO"),
        str(input_dir / "first.iso"),
    ]
    assert [job["destination"]["path"] for job in first] == [
        str(output_dir / "Input 日本語" / "B folder" / "second.chd"),
        str(output_dir / "Input 日本語" / "first.chd"),
    ]
    assert [job["plan_index"] for job in first] == [0, 1]
    assert first[0]["scheduling"]["estimated_weight"] == 2


def test_same_leaf_roots_are_all_stably_disambiguated(tmp_path: Path) -> None:
    first = tmp_path / "one" / "games"
    second = tmp_path / "two" / "games"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "disc.iso").write_bytes(b"1")
    (second / "disc.iso").write_bytes(b"2")

    paths = [str(first), str(second)]
    namespaces = assign_root_namespaces(paths)
    reversed_namespaces = assign_root_namespaces(list(reversed(paths)))
    assert all(namespace.startswith("games--") for namespace in namespaces)
    assert len(set(namespaces)) == 2
    assert dict(zip(paths, namespaces, strict=True)) == dict(
        zip(reversed(paths), reversed_namespaces, strict=True)
    )

    jobs = plan_jobs(paths, planning_config(tmp_path / "out"))
    assert len({job["destination"]["path"] for job in jobs}) == 2


def test_windows_namespace_collisions_and_reserved_names_are_safe() -> None:
    paths = [r"C:\One\Game", r"D:\Two\game", r"D:\CON"]
    namespaces = assign_root_namespaces(paths, windows=True)
    assert namespaces[0].casefold() != namespaces[1].casefold()
    assert namespaces[0].startswith("Game--")
    assert namespaces[1].startswith("game--")
    assert namespaces[2] == "_CON"


def test_overlapping_roots_preserve_first_source_ownership(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    source = child / "disc.iso"
    source.write_bytes(b"disc")

    discovered = discover_sources(
        [parent, child], {".iso": FormatConfig("createdvd", ())}
    )

    assert len(discovered) == 1
    assert discovered[0].input_root == str(parent)
    assert discovered[0].relative_path == os.path.join("child", "disc.iso")


def test_symlinks_are_skipped_during_walk_and_rejected_as_roots(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "disc.iso").write_bytes(b"disc")
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
        (real / "loop").symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available")

    jobs = plan_jobs([real], planning_config(tmp_path / "out"))
    assert len(jobs) == 1
    with pytest.raises(PlanningError, match="symlink|reparse"):
        plan_jobs([link], planning_config(tmp_path / "out"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX non-regular entry coverage")
def test_nonregular_entries_are_skipped(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "disc.iso").write_bytes(b"disc")
    os.mkfifo(input_dir / "named-pipe.iso")
    jobs = plan_jobs([input_dir], planning_config(tmp_path / "out"))
    assert len(jobs) == 1


@pytest.mark.parametrize("direction", ["output-in-input", "input-in-output"])
def test_rejects_unsafe_directory_output_relationships(
    tmp_path: Path, direction: str
) -> None:
    container = tmp_path / "container"
    input_dir = container / "input" if direction == "input-in-output" else container
    input_dir.mkdir(parents=True)
    (input_dir / "disc.iso").write_bytes(b"disc")
    output_dir = (
        input_dir / "generated" if direction == "output-in-input" else container
    )
    with pytest.raises(PlanningError, match="must not contain"):
        plan_jobs([input_dir], planning_config(output_dir))


def test_rejects_output_symlink_and_destination_collisions(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "disc.iso").write_bytes(b"iso")
    (input_dir / "disc.cue").write_bytes(b"cue")
    formats = {
        ".iso": FormatConfig("createdvd", ()),
        ".cue": FormatConfig("createcd", ()),
    }
    with pytest.raises(PlanningError, match="collision"):
        plan_jobs([input_dir], planning_config(tmp_path / "out", formats=formats))

    real_output = tmp_path / "real-output"
    real_output.mkdir()
    output_link = tmp_path / "output-link"
    try:
        output_link.symlink_to(real_output, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available")
    with pytest.raises(PlanningError, match="symlink|reparse"):
        plan_jobs([input_dir / "disc.iso"], planning_config(output_link))


def test_rejects_existing_symlink_or_file_destination_parent(tmp_path: Path) -> None:
    source = tmp_path / "game.iso"
    source.write_bytes(b"disc")
    output = tmp_path / "out"
    output.mkdir()
    namespace = output / "game"
    namespace.write_bytes(b"not a directory")
    with pytest.raises(PlanningError, match="must be a directory"):
        plan_jobs([source], planning_config(output))

    namespace.unlink()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    try:
        namespace.symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available")
    with pytest.raises(PlanningError, match="symlink|reparse"):
        plan_jobs([source], planning_config(output))


@pytest.mark.parametrize("policy", ["fail", "skip"])
def test_fail_and_skip_preserve_existing_destination_policy(
    tmp_path: Path, policy: str
) -> None:
    source = tmp_path / "game.iso"
    source.write_bytes(b"disc")
    destination = tmp_path / "out" / "game" / "game.chd"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"existing")

    jobs = plan_jobs([source], planning_config(tmp_path / "out", existing=policy))
    assert jobs[0]["destination"] == {"path": str(destination), "existing": policy}
    assert destination.read_bytes() == b"existing"


def test_rename_selects_first_available_destination_without_writes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "game.iso"
    source.write_bytes(b"disc")
    output = tmp_path / "out"
    destination = output / "game" / "game.chd"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"existing")
    (destination.parent / "game (1).chd").write_bytes(b"existing-one")

    jobs = plan_jobs([source], planning_config(output, existing="rename"))

    assert jobs[0]["destination"] == {
        "path": str(destination.parent / "game (2).chd"),
        "existing": "rename",
    }
    assert not (destination.parent / "game (2).chd").exists()


def test_rename_resolves_collisions_between_planned_destinations(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "disc.cue").write_bytes(b"cue")
    (input_dir / "disc.iso").write_bytes(b"iso")
    formats = {
        ".cue": FormatConfig("createcd", ()),
        ".iso": FormatConfig("createdvd", ()),
    }

    jobs = plan_jobs(
        [input_dir],
        planning_config(tmp_path / "out", formats=formats, existing="rename"),
    )

    assert [Path(job["destination"]["path"]).name for job in jobs] == [
        "disc.chd",
        "disc (1).chd",
    ]


def test_cue_identity_is_explicitly_primary_only_and_changes_with_metadata(
    tmp_path: Path,
) -> None:
    source = tmp_path / "disc.cue"
    source.write_bytes(b'FILE "track.bin" BINARY')
    config = resolve_config(preset="others", output_dir=tmp_path / "out", environ={})
    first = plan_jobs([source], config)[0]
    assert first["warnings"] == [PRIMARY_CUE_WARNING]

    discovered = discover_sources([source], config.formats)[0]
    assert source_metadata_identity(discovered) == first["source"]["identity"]
    source.write_bytes(b'FILE "different.bin" BINARY')
    second = plan_jobs([source], config)[0]
    assert second["source"]["identity"] != first["source"]["identity"]
    assert second["job_id"] != first["job_id"]


def test_rejects_missing_unsupported_and_empty_inputs(tmp_path: Path) -> None:
    config = planning_config(tmp_path / "out")
    with pytest.raises(PlanningError, match="cannot inspect"):
        plan_jobs([tmp_path / "missing"], config)
    unsupported = tmp_path / "unsupported.txt"
    unsupported.write_text("x", encoding="utf-8")
    with pytest.raises(PlanningError, match="no supported"):
        plan_jobs([unsupported], config)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(PlanningError, match="no supported"):
        plan_jobs([empty], config)
