from __future__ import annotations

import contextlib
import io
import unittest

from chdmanpy.cli import build_parser, main
from chdmanpy.errors import ExitCode


class CliContractTests(unittest.TestCase):
    def test_public_command_surfaces_parse(self) -> None:
        parser = build_parser()
        plan = parser.parse_args(["plan", "/input", "--output-dir", "/output"])
        self.assertEqual(plan.command, "plan")
        run = parser.parse_args(["run", "--manifest", "-"])
        self.assertEqual(run.manifest, "-")
        convert = parser.parse_args(
            [
                "convert",
                "--arcshuttle-results",
                "-",
                "--on-upstream-error",
                "skip",
                "--preset",
                "ps2",
            ]
        )
        self.assertEqual(convert.arcshuttle_results, "-")
        self.assertEqual(convert.on_upstream_error, "skip")

    def test_no_command_prints_help_without_reading_stdin(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main([])
        self.assertEqual(exit_code, ExitCode.SUCCESS)
        self.assertIn("plan", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_no_input_selector_and_multiple_selectors_are_usage_errors(self) -> None:
        cases = (
            ["plan"],
            ["convert", "/input", "--files-from", "paths.txt"],
            ["plan", "--files-from", "a", "--files0-from", "b"],
        )
        for arguments in cases:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                self.subTest(arguments=arguments),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                self.assertEqual(main(arguments), ExitCode.USAGE)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("select exactly one", stderr.getvalue())

    def test_upstream_policy_is_only_valid_for_arcshuttle_input(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(["plan", "/input", "--on-upstream-error", "skip"])
        self.assertEqual(exit_code, ExitCode.USAGE)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("valid only with --arcshuttle-results", stderr.getvalue())

    def test_diagnostics_never_pollute_stdout(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(["run", "--manifest", "-"])
        self.assertEqual(exit_code, ExitCode.USAGE)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("not implemented", stderr.getvalue())

    def test_unknown_command_returns_usage_exit(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(["unknown"])
        self.assertEqual(exit_code, ExitCode.USAGE)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("invalid choice", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
