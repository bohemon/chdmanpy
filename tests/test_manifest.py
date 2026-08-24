from __future__ import annotations

import copy
import io
import json
import unittest
from pathlib import Path
from typing import Any

from helpers import job_record

from chdmanpy.errors import ContractError
from chdmanpy.manifest import (
    EDITABLE_JOB_FIELDS,
    add_job_integrity,
    compute_job_integrity,
    load_manifest,
    make_job_id,
    path_key,
    validate_job_record,
    validate_manifest_records,
)


class JobRecordTests(unittest.TestCase):
    def test_validates_fixture_and_deterministic_job_id(self) -> None:
        job = job_record()
        validated = validate_job_record(job)
        self.assertEqual(validated, job)
        self.assertEqual(
            job["job_id"],
            make_job_id(
                job["source"]["path"],
                job["source"]["identity"],
                job["chdman"]["operation"],
                job["chdman"]["options"],
            ),
        )
        self.assertEqual(
            EDITABLE_JOB_FIELDS,
            {"destination.path", "scheduling.priority", "tags"},
        )

    def test_editable_fields_do_not_change_integrity_but_are_still_validated(
        self,
    ) -> None:
        job = job_record()
        original = job["integrity"]
        job["destination"]["path"] = "/different/output.chd"
        job["scheduling"]["priority"] = -(2**31)
        job["tags"] = ["user-edited"]
        self.assertEqual(compute_job_integrity(job), original)
        validate_job_record(job)

        job["destination"]["path"] = "relative.chd"
        with self.assertRaisesRegex(ContractError, "absolute"):
            validate_job_record(job)

    def test_every_other_field_is_integrity_protected(self) -> None:
        mutations = (
            lambda value: value.update(plan_index=2),
            lambda value: value["source"].update(size=999),
            lambda value: value["destination"].update(existing="skip"),
            lambda value: value["chdman"].update(options=["-c", "none"]),
            lambda value: value["scheduling"].update(estimated_weight=999),
            lambda value: value.update(warnings=["changed"]),
        )
        for mutate in mutations:
            job = job_record()
            mutate(job)
            with (
                self.subTest(job=job),
                self.assertRaisesRegex(ContractError, "integrity"),
            ):
                validate_job_record(job)

    def test_unknown_and_missing_fields_are_rejected_at_every_level(self) -> None:
        containers = ((), ("source",), ("destination",), ("chdman",), ("scheduling",))
        for path in containers:
            job = job_record()
            container = job
            for part in path:
                container = container[part]
            container["unknown"] = True
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(ContractError, "unknown"),
            ):
                validate_job_record(job)

        job = job_record()
        del job["warnings"]
        with self.assertRaisesRegex(ContractError, "missing"):
            validate_job_record(job)

    def test_unknown_schema_and_record_type_are_rejected(self) -> None:
        for field, value in (("schema_version", 2), ("record_type", "result")):
            job = job_record()
            job[field] = value
            job = add_job_integrity(job)
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ContractError, "unsupported"),
            ):
                validate_job_record(job)

    def test_rejects_managed_force_empty_and_nul_options(self) -> None:
        invalid = (
            "-i",
            "-i=/input",
            "--input=/input",
            "-o",
            "--output",
            "-f",
            "--force",
            "",
            "value\x00tail",
        )
        for option in invalid:
            job = job_record(options=(option,))
            with self.subTest(option=option), self.assertRaises(ContractError):
                validate_job_record(job)

        validate_job_record(
            job_record(options=("--inputstartbyte", "0", "--outputparent", "x"))
        )

        for field in ("tags", "warnings"):
            job = job_record()
            job[field] = [""]
            if field == "warnings":
                job = add_job_integrity(job)
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_job_record(job)

    def test_rejects_bool_and_invalid_resource_values(self) -> None:
        for path, value in (
            (("plan_index",), True),
            (("source", "size"), True),
            (("source", "mtime_ns"), -1),
            (("scheduling", "priority"), 2**31),
            (("scheduling", "estimated_weight"), -1),
        ):
            job = job_record()
            target = job
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value
            job = add_job_integrity(job)
            with self.subTest(path=path), self.assertRaises(ContractError):
                validate_job_record(job)

    def test_source_must_be_equal_to_or_beneath_input_root(self) -> None:
        equal = job_record(source_path="/input/Game One")
        equal["source"]["input_root"] = "/input/Game One"
        equal = add_job_integrity(equal)
        validate_job_record(equal)

        outside = job_record(source_path="/input/Game Two/disc.cue")
        outside["source"]["input_root"] = "/input/Game One"
        outside = add_job_integrity(outside)
        with self.assertRaisesRegex(ContractError, "beneath"):
            validate_job_record(outside)

    def test_windows_absolute_paths_and_collision_keys(self) -> None:
        first = job_record(
            source_path=r"C:\Input\Game\disc.cue",
            destination_path=r"C:\Output\Game.chd",
        )
        first["source"]["input_root"] = r"C:\Input\Game"
        first["job_id"] = make_job_id(
            first["source"]["path"],
            first["source"]["identity"],
            first["chdman"]["operation"],
            first["chdman"]["options"],
            windows=True,
        )
        first = add_job_integrity(first)
        validate_job_record(first, windows=True)
        self.assertEqual(
            path_key(r"C:\OUT\A.chd", windows=True),
            path_key(r"c:/out/a.CHD", windows=True),
        )

        second = copy.deepcopy(first)
        second["plan_index"] = 1
        second["source"]["path"] = r"C:\Input\Game\disc-2.cue"
        second["source"]["identity"] = f"sha256:{'2' * 64}"
        second["destination"]["path"] = r"c:/output/game.CHD"
        second["job_id"] = make_job_id(
            second["source"]["path"],
            second["source"]["identity"],
            second["chdman"]["operation"],
            second["chdman"]["options"],
            windows=True,
        )
        second = add_job_integrity(second)
        with self.assertRaisesRegex(ContractError, "collision"):
            validate_manifest_records([first, second], windows=True)

    def test_windows_non_device_paths_reject_trimmed_components(self) -> None:
        def make_windows_job(destination: str) -> dict[str, Any]:
            job = job_record(
                source_path=r"C:\Input\Game\disc.cue",
                destination_path=destination,
            )
            job["source"]["input_root"] = r"C:\Input\Game"
            job["job_id"] = make_job_id(
                job["source"]["path"],
                job["source"]["identity"],
                job["chdman"]["operation"],
                job["chdman"]["options"],
                windows=True,
            )
            return add_job_integrity(job)

        for destination in (
            "C:\\Output\\Game.chd.",
            "C:\\Output\\Game.chd ",
            "C:\\Output.\\Game.chd",
            "C:\\Output.\\..\\Game.chd",
            "\\\\server.\\share\\Game.chd",
        ):
            job = make_windows_job(destination)
            with (
                self.subTest(destination=destination),
                self.assertRaisesRegex(ContractError, "period or space"),
            ):
                validate_job_record(job, windows=True)

        device_job = make_windows_job(r"\\?\C:\Output\Game.chd.")
        validate_job_record(device_job, windows=True)

    def test_complete_preflight_rejects_duplicates_and_sorts_plan_order(self) -> None:
        first = job_record(plan_index=0)
        second = job_record(
            plan_index=1,
            source_path="/input/Game One/disc-2.cue",
            destination_path="/output/Game One/disc-2.chd",
            identity_digit="2",
        )
        self.assertEqual(
            [job["plan_index"] for job in validate_manifest_records([second, first])],
            [0, 1],
        )

        duplicate = copy.deepcopy(first)
        duplicate["plan_index"] = 1
        duplicate["destination"]["path"] = "/output/duplicate.chd"
        duplicate = add_job_integrity(duplicate)
        with self.assertRaisesRegex(ContractError, "duplicate job_id"):
            validate_manifest_records([first, duplicate])

    def test_complete_preflight_rejects_editable_destination_in_input_roots(
        self,
    ) -> None:
        first = job_record(plan_index=0)
        first_integrity = first["integrity"]
        first["destination"]["path"] = "/input/Game One/generated.chd"
        self.assertEqual(first["integrity"], first_integrity)
        with self.assertRaisesRegex(ContractError, "input_root"):
            validate_manifest_records([first])

        first = job_record(plan_index=0)
        second = job_record(
            plan_index=1,
            source_path="/input/Game Two/disc.cue",
            destination_path="/output/Game Two/disc.chd",
            identity_digit="2",
        )
        second["source"]["input_root"] = "/input/Game Two"
        second = add_job_integrity(second)
        first["destination"]["path"] = "/input/Game Two/generated.chd"
        with self.assertRaisesRegex(ContractError, "input_root"):
            validate_manifest_records([first, second])

    def test_complete_preflight_protects_windows_input_roots(self) -> None:
        job = job_record(
            source_path=r"C:\Input\Game\disc.cue",
            destination_path=r"C:\Output\Game.chd",
        )
        job["source"]["input_root"] = r"C:\Input\Game"
        job["job_id"] = make_job_id(
            job["source"]["path"],
            job["source"]["identity"],
            job["chdman"]["operation"],
            job["chdman"]["options"],
            windows=True,
        )
        job = add_job_integrity(job)
        job["destination"]["path"] = r"c:/INPUT/game/generated.chd"
        with self.assertRaisesRegex(ContractError, "input_root"):
            validate_manifest_records([job], windows=True)

    def test_load_manifest_reads_jsonl_through_eof(self) -> None:
        job = job_record()
        raw = (json.dumps(job, ensure_ascii=False) + "\n").encode()
        self.assertEqual(load_manifest(io.BytesIO(raw)), [job])
        with self.assertRaises(ContractError):
            load_manifest(io.BytesIO(raw + b"not-json\n"))
        with self.assertRaisesRegex(ContractError, "at least one"):
            load_manifest(io.BytesIO(b""))

    def test_normative_fixture_remains_valid(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "job-v1.jsonl"
        with fixture.open("rb") as stream:
            jobs = load_manifest(stream)
        self.assertEqual(len(jobs), 1)
        self.assertIn("\n", jobs[0]["source"]["path"])


if __name__ == "__main__":
    unittest.main()
