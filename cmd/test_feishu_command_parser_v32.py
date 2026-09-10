from __future__ import annotations

import unittest

from orchestrator.feishu_command_parser import (
    extract_task_request,
    parse_feishu_instruction,
)


class FeishuCommandParserTests(unittest.TestCase):
    def test_extracts_only_new_value_from_full_launcher_command(self):
        text = (
            "@方案设计师-opencode-v4-high - Multica 启动本机上这个任务，"
            "python D:\\workspace\\src\\nexus-agents\\cmd\\review_orchestrator_v2.py "
            '--new "帮我检查前端功能，在点击初始化Proposal后，知识库功能描述覆盖不全的问题。" '
            "--project 56dd9c04-da60-4905-a7ca-d243f1f0f304 "
            "--runs-root D:\\workspace\\src\\nexus-agents\\runs"
        )
        self.assertEqual(
            extract_task_request(text),
            "帮我检查前端功能，在点击初始化Proposal后，知识库功能描述覆盖不全的问题。",
        )

    def test_uses_first_matching_command(self):
        text = (
            "python D:\\a\\review_orchestrator_v2.py --new \"第一个需求\" "
            "然后 python D:\\b\\review_orchestrator_v2.py --new \"第二个需求\""
        )
        self.assertEqual(extract_task_request(text), "第一个需求")

    def test_supports_explicit_task_request_format(self):
        self.assertEqual(
            extract_task_request("@机器人\n任务需求：检查门下省流程"),
            "检查门下省流程",
        )

    def test_rejects_command_without_new(self):
        result = parse_feishu_instruction(
            "python D:\\workspace\\review_orchestrator_v2.py --project demo"
        )
        self.assertFalse(result.is_valid)
        self.assertIn("--new", result.error or "")

    def test_does_not_execute_or_return_launcher_tokens(self):
        result = parse_feishu_instruction(
            'python D:\\workspace\\review_orchestrator_v2.py --new "检查流程" '
            "--timeout 900"
        )
        self.assertEqual(result.task_request, "检查流程")
        self.assertNotIn("python", result.task_request or "")
        self.assertNotIn("--timeout", result.task_request or "")


if __name__ == "__main__":
    unittest.main()
