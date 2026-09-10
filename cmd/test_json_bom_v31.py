from __future__ import annotations

import unittest

from orchestrator.adapters import _extract_json


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


if __name__ == "__main__":
    unittest.main()
