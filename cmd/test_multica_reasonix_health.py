from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import multica_reasonix_health as health


class ExtractIssueIdTest(unittest.TestCase):
    def test_direct_id(self):
        self.assertEqual(health._extract_issue_id({"id": "abc"}), "abc")

    def test_issue_id_key(self):
        self.assertEqual(health._extract_issue_id({"issue_id": "xyz"}), "xyz")

    def test_nested_issue(self):
        self.assertEqual(health._extract_issue_id({"issue": {"id": "nested"}}), "nested")

    def test_missing(self):
        self.assertEqual(health._extract_issue_id({"title": "no id"}), "")
        self.assertEqual(health._extract_issue_id("not-a-dict"), "")


class FindAgentTest(unittest.TestCase):
    def test_by_substring(self):
        agents = [
            {"id": "a1", "name": "Analyst-reasonix-v4", "archived": False, "status": "idle"},
            {"id": "a2", "name": "Critic-codex", "archived": False, "status": "idle"},
        ]
        with patch.object(health, "run_cli_json", return_value=agents):
            agent_id, name, status = health.find_agent("Analyst-reasonix", "")
        self.assertEqual((agent_id, name, status), ("a1", "Analyst-reasonix-v4", "idle"))

    def test_ambiguous_raises(self):
        agents = [
            {"id": "a1", "name": "Analyst-reasonix-a", "archived": False},
            {"id": "a2", "name": "Analyst-reasonix-b", "archived": False},
        ]
        with patch.object(health, "run_cli_json", return_value=agents):
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                health.find_agent("Analyst-reasonix", "")

    def test_no_match_raises(self):
        with patch.object(health, "run_cli_json", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "no agent"):
                health.find_agent("ghost", "")

    def test_explicit_id_skips_lookup(self):
        agent_id, name, _ = health.find_agent("anything", "given-id")
        self.assertEqual(agent_id, "given-id")
        self.assertEqual(name, "(given via --agent-id)")


class AgentCommentsFilterTest(unittest.TestCase):
    def test_only_agent_authored(self):
        payload = [
            {"author_type": "member", "content": "human"},
            {"author_type": "agent", "content": '{"ok": true}'},
            {"author_type": "agent", "content": "second"},
            "garbage-row",
        ]
        with patch.object(health, "run_cli_json", return_value=payload):
            comments = health.agent_comments("issue-1")
        self.assertEqual(len(comments), 2)
        self.assertEqual(comments[-1]["content"], "second")

    def test_dict_payload_shape(self):
        with patch.object(health, "run_cli_json", return_value={"comments": [{"author_type": "agent", "content": "x"}]}):
            comments = health.agent_comments("issue-1")
        self.assertEqual(len(comments), 1)


class LatestRunsShapeTest(unittest.TestCase):
    def test_list_payload(self):
        with patch.object(health, "run_cli_json", return_value=[{"status": "running"}, {"status": "completed"}]):
            runs = health.latest_runs("issue-1")
        self.assertEqual(len(runs), 2)

    def test_dict_payload_with_runs_key(self):
        with patch.object(health, "run_cli_json", return_value={"runs": [{"status": "queued"}]}):
            runs = health.latest_runs("issue-1")
        self.assertEqual(len(runs), 1)

    def test_drops_non_dict_rows(self):
        with patch.object(health, "run_cli_json", return_value=["nope", {"status": "running"}]):
            runs = health.latest_runs("issue-1")
        self.assertEqual(len(runs), 1)


if __name__ == "__main__":
    unittest.main()
