from __future__ import annotations

import io
import json
import unittest

from chdmanpy.errors import ContractError
from chdmanpy.jsonl import canonical_json_bytes, dump_json_line, loads_json_lines


class JsonLinesTests(unittest.TestCase):
    def test_reads_utf8_crlf_and_final_line_ending(self) -> None:
        records = loads_json_lines(io.BytesIO('{"name":"日本語"}\r\n'.encode()))
        self.assertEqual(records, [{"name": "日本語"}])

    def test_empty_stream_is_valid(self) -> None:
        self.assertEqual(loads_json_lines(io.BytesIO(b"")), [])

    def test_rejects_bom_invalid_utf8_and_blank_records(self) -> None:
        invalid_streams = (
            b"\xef\xbb\xbf{}\n",
            b'{"value":"\xff"}\n',
            b"{}\n\n",
            b"{}\n  \n",
        )
        for value in invalid_streams:
            with self.subTest(value=value), self.assertRaises(ContractError):
                loads_json_lines(io.BytesIO(value))

    def test_rejects_malformed_non_object_duplicate_keys_and_non_finite_numbers(
        self,
    ) -> None:
        invalid_streams = (
            b"{\n",
            b"[]\n",
            b'{"x":1,"x":2}\n',
            b'{"x":NaN}\n',
        )
        for value in invalid_streams:
            with self.subTest(value=value), self.assertRaises(ContractError):
                loads_json_lines(io.BytesIO(value))

    def test_parser_resource_errors_are_contract_errors(self) -> None:
        huge_integer = b'{"value":' + (b"9" * 5_000) + b"}\n"
        deeply_nested = b'{"value":' + (b"[" * 10_000) + b"0" + (b"]" * 10_000) + b"}\n"
        for value in (huge_integer, deeply_nested):
            with (
                self.subTest(length=len(value)),
                self.assertRaisesRegex(ContractError, "invalid JSON at line 1"),
            ):
                loads_json_lines(io.BytesIO(value))

    def test_output_round_trips_special_paths_without_diagnostics(self) -> None:
        record = {"path": '/tmp/日本語/space "quote"\\backslash\nline'}
        output = io.StringIO()
        dump_json_line(output, record)
        raw = output.getvalue()
        self.assertFalse(raw.startswith("\ufeff"))
        self.assertEqual(raw.count("\n"), 1)
        self.assertEqual(json.loads(raw), record)

    def test_canonical_json_is_stable_and_rejects_nan(self) -> None:
        self.assertEqual(canonical_json_bytes({"b": 2, "a": 1}), b'{"a":1,"b":2}')
        with self.assertRaises(ContractError):
            canonical_json_bytes({"number": float("nan")})


if __name__ == "__main__":
    unittest.main()
