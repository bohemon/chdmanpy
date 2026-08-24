from __future__ import annotations

import copy
import unittest

from helpers import result_record, summary_record

from chdmanpy.errors import ContractError, ExitCode
from chdmanpy.results import (
    exit_code_for_results,
    validate_result_record,
    validate_result_stream,
    validate_summary_record,
)


class ResultContractTests(unittest.TestCase):
    def test_all_statuses_validate_and_summary_counts_match(self) -> None:
        statuses = ["success", "warning", "failed", "skipped", "interrupted"]
        results = [
            result_record(plan_index=index, status=status)
            for index, status in enumerate(statuses)
        ]
        summary = summary_record(statuses)
        validated_results, validated_summary = validate_result_stream(
            [*results, summary]
        )
        self.assertEqual(validated_results, results)
        self.assertEqual(validated_summary, summary)

    def test_summary_is_required_terminal_and_unique(self) -> None:
        result = result_record()
        summary = summary_record(["success"])
        invalid_streams = (
            [],
            [result],
            [summary, result],
            [result, summary, summary],
        )
        for stream in invalid_streams:
            with self.subTest(stream=stream), self.assertRaises(ContractError):
                validate_result_stream(stream)

    def test_summary_must_match_results(self) -> None:
        result = result_record()
        summary = summary_record(["warning"])
        with self.assertRaisesRegex(ContractError, "do not match"):
            validate_result_stream([result, summary])

    def test_run_id_duplicates_and_plan_order_are_validated(self) -> None:
        first = result_record(plan_index=0)
        second = result_record(plan_index=1)
        summary = summary_record(["success", "success"])

        mismatched = copy.deepcopy(second)
        mismatched["run_id"] = "another-run"
        with self.assertRaisesRegex(ContractError, "run_id"):
            validate_result_stream([first, mismatched, summary])

        duplicate = copy.deepcopy(second)
        duplicate["job_id"] = first["job_id"]
        with self.assertRaisesRegex(ContractError, "duplicate"):
            validate_result_stream([first, duplicate, summary])

        with self.assertRaisesRegex(ContractError, "contiguous"):
            validate_result_stream([second, first, summary])

        for indexes in ([1], [0, 2]):
            results = [result_record(plan_index=index) for index in indexes]
            summary = summary_record(["success"] * len(results))
            with (
                self.subTest(indexes=indexes),
                self.assertRaisesRegex(ContractError, "contiguous"),
            ):
                validate_result_stream([*results, summary])

    def test_run_id_is_an_opaque_nonempty_string(self) -> None:
        result = result_record()
        result["run_id"] = "release/run 2026-08-24"
        validate_result_record(result)
        result["run_id"] = ""
        with self.assertRaises(ContractError):
            validate_result_record(result)

    def test_unknown_fields_and_bool_counts_are_rejected(self) -> None:
        result = result_record()
        result["unknown"] = True
        with self.assertRaisesRegex(ContractError, "unknown"):
            validate_result_record(result)

        summary = summary_record([])
        summary["success"] = False
        with self.assertRaises(ContractError):
            validate_summary_record(summary)

    def test_timestamps_require_strict_rfc3339_utc_form(self) -> None:
        valid = result_record()
        valid["started_at"] = "2026-08-24T01:02:03.123456789Z"
        validate_result_record(valid)

        invalid_timestamps = (
            "20260824T010203Z",
            "2026-W35-1T01:02:03Z",
            "2026-08-24 01:02:03Z",
            "2026-08-24t01:02:03Z",
            "2026-08-24T01:02Z",
            "2026-08-24T01:02:03+00:00",
        )
        for timestamp in invalid_timestamps:
            result = result_record()
            result["started_at"] = timestamp
            with (
                self.subTest(timestamp=timestamp),
                self.assertRaisesRegex(ContractError, "RFC 3339"),
            ):
                validate_result_record(result)

    def test_exit_precedence_for_every_status_combination(self) -> None:
        cases = (
            (["success"], ExitCode.SUCCESS),
            (["warning"], ExitCode.WARNING),
            (["skipped"], ExitCode.WARNING),
            (["failed"], ExitCode.JOB_FAILURE),
            (["failed", "warning"], ExitCode.JOB_FAILURE),
            (["interrupted"], ExitCode.INTERRUPTED),
            (["interrupted", "failed"], ExitCode.INTERRUPTED),
        )
        for statuses, expected in cases:
            results = [
                result_record(plan_index=index, status=status)
                for index, status in enumerate(statuses)
            ]
            summary = summary_record(statuses)
            with self.subTest(statuses=statuses):
                self.assertEqual(exit_code_for_results(results, summary), expected)

    def test_any_result_warning_text_produces_warning_exit(self) -> None:
        result = result_record(warnings=["CHDMAN reported a warning"])
        summary = summary_record(["success"])
        self.assertEqual(exit_code_for_results([result], summary), ExitCode.WARNING)

        result["warnings"] = [""]
        with self.assertRaises(ContractError):
            validate_result_record(result)


if __name__ == "__main__":
    unittest.main()
