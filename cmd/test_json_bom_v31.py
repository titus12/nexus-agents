from __future__ import annotations

import unittest

from orchestrator.adapters import _extract_json, _json_parse_diagnostic
from orchestrator.runtime.agent_effects import _rejected_reply_code
from orchestrator.runtime.agent_effects import WorkerResult
from orchestrator.domain.errors import UNSTRUCTURED_REPLY_EVENT


class JsonBomTests(unittest.TestCase):
    def test_agent_json_with_utf8_bom_is_parsed(self):
        value = _extract_json("\ufeff{\n  \"action\": \"HUMAN_GATE\"\n}")
        self.assertEqual(value, {"action": "HUMAN_GATE"})

    def test_single_extra_closing_brace_is_repaired(self):
        value = _extract_json('{"action":"READY_FOR_CRITIC"}}')
        self.assertEqual(value, {"action": "READY_FOR_CRITIC"})

    def test_concatenated_json_documents_are_rejected(self):
        value = _extract_json('{"action":"A"}{"action":"B"}')
        self.assertIsNone(value)

    def test_arbitrary_trailing_text_is_rejected(self):
        value = _extract_json('{"action":"A"} trailing text')
        self.assertIsNone(value)


class InnerQuoteRepairTests(unittest.TestCase):
    """task-20260930-1965b5: prose quotes inside ``summary`` broke both replies."""

    BROKEN = (
        '{"action":"REQUIREMENT_CONTRACT_ONLY","summary":"对已实施的"整体时长优化"'
        '进行审查","items":[{"a":"x"},{"b":"他说"好"了"}],"n":1}'
    )

    def test_unescaped_prose_quotes_are_escaped(self):
        value = _extract_json(self.BROKEN)

        self.assertEqual(value["summary"], '对已实施的"整体时长优化"进行审查')
        self.assertEqual(value["items"][1]["b"], '他说"好"了')
        self.assertEqual(value["n"], 1)

    def test_fenced_reply_with_inner_quotes_is_repaired(self):
        value = _extract_json("```json\n" + self.BROKEN + "\n```")

        self.assertEqual(value["action"], "REQUIREMENT_CONTRACT_ONLY")

    def test_valid_json_is_left_untouched(self):
        body = '{"a":"say \\"hi\\"","b":[1,2]}'

        self.assertEqual(_extract_json(body), {"a": 'say "hi"', "b": [1, 2]})

    def test_unrepairable_body_is_still_rejected(self):
        self.assertIsNone(_extract_json('{"a":"unterminated'))
        self.assertIsNone(_extract_json('{"a":"x"} trailing text'))


class EscapeRepairTests(unittest.TestCase):
    """Invalid backslash escapes and raw control characters inside strings."""

    def test_windows_path_backslashes_are_preserved(self):
        value = _extract_json('{"a":"D:\\workspace\\src\\Users\\x","n":1}')

        self.assertEqual(value["a"], "D:\\workspace\\src\\Users\\x")
        self.assertEqual(value["n"], 1)

    def test_raw_newline_and_tab_in_string_are_escaped(self):
        value = _extract_json('{"a":"line1\nline2\tend","n":1}')

        self.assertEqual(value, {"a": "line1\nline2\tend", "n": 1})

    def test_valid_escapes_are_untouched(self):
        body = '{"a":"q\\"uote \\\\ \\u4e2d \\n","n":1}'

        self.assertEqual(_extract_json(body), {"a": 'q"uote \\ 中 \n', "n": 1})

    def test_inner_quote_and_bad_escape_together(self):
        value = _extract_json('{"a":"路径 "D:\\src\\w" 说明","n":1}')

        self.assertEqual(value["n"], 1)
        self.assertIn("说明", value["a"])

    def test_repair_is_reported(self):
        meta = _json_parse_diagnostic('{"a":"x\ty"}')

        self.assertEqual(meta["json_status"], "repaired")

    def test_structural_damage_is_still_rejected(self):
        self.assertIsNone(_extract_json('{"a":"x\n" "b": 1}'))


class ParseDiagnosticTests(unittest.TestCase):
    def test_fenced_body_is_diagnosed_after_unfencing(self):
        body = '```json\n{"a": 1 "b": 2}\n```'

        meta = _json_parse_diagnostic(body)

        self.assertEqual(meta["json_status"], "invalid")
        self.assertIn("delimiter", meta["json_error"])
        self.assertIn('1 "b"', meta["json_error_context"])

    def test_valid_body_has_no_context(self):
        meta = _json_parse_diagnostic('{"a": 1}')

        self.assertEqual(meta["json_status"], "valid")
        self.assertEqual(meta["json_error_context"], "")

    def test_rejection_reason_names_position_and_cause(self):
        meta = _json_parse_diagnostic('{"a": 1 "b": 2}')
        result = WorkerResult(
            "w-1",
            "SUCCEEDED",
            "artifact-1",
            result_payload={"action": UNSTRUCTURED_REPLY_EVENT, **meta},
        )

        code, message = _rejected_reply_code(result)

        self.assertEqual(code, "AGENT_REPLY_UNSTRUCTURED")
        self.assertIn("JSON parse error", message)
        self.assertIn("at char", message)
        self.assertIn('Escape every double quote', message)


if __name__ == "__main__":
    unittest.main()
