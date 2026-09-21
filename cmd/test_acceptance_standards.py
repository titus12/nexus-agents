from __future__ import annotations

import unittest

from orchestrator.acceptance_standards import (
    ZHONGSHU_ACCEPTANCE_CHECKLIST,
    acceptance_standard_applies_to,
    acceptance_standard_block,
    acceptance_standard_hint,
)
from orchestrator.domain.context import (
    ProgressState,
    RequestState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.policies.prompts import build_prompt
from orchestrator.domain.states import _task_capsule_text


def _context() -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
        progression=ProgressState("REQUEST_INTAKE", 0, "2026-09-17T00:00:00Z"),
        request=RequestState(raw_request="run a review"),
    )


class AcceptanceStandardBlockTests(unittest.TestCase):
    """The standard is single-sourced and shipped with the producing roles."""

    def test_block_contains_the_eight_distilled_rules(self) -> None:
        lines = acceptance_standard_block().strip().splitlines()

        self.assertEqual(len(lines), len(ZHONGSHU_ACCEPTANCE_CHECKLIST))
        self.assertIn("[Acceptance standard]", lines[0])
        numbered = [line for line in lines[1:] if line[:1].isdigit()]
        self.assertEqual(len(numbered), 8)
        self.assertIn("可测性闸门", acceptance_standard_block())

    def test_producing_states_carry_the_block(self) -> None:
        for state in ("ZHONGSHU_ANALYST", "ZHONGSHU_SOLVER"):
            prompt = build_prompt(_context(), target_state=state)

            self.assertTrue(acceptance_standard_applies_to(state))
            self.assertIn("[Acceptance standard]", prompt.content)
            self.assertIn("度量口径", prompt.content)
            self.assertIn("UNKNOWN", prompt.content)

    def test_judging_states_do_not_carry_the_full_block(self) -> None:
        for state in ("ZHONGSHU_CRITIC", "ZHONGSHU_FREEZE_CHECK", "MENXIA_ITEM_SOLVER"):
            prompt = build_prompt(_context(), target_state=state)

            self.assertFalse(acceptance_standard_applies_to(state))
            self.assertNotIn("[Acceptance standard]", prompt.content)

    def test_block_coexists_with_the_revision_task(self) -> None:
        prompt = build_prompt(_context(), target_state="ZHONGSHU_SOLVER")

        self.assertIn("[Acceptance standard]", prompt.content)


class AcceptanceStandardHintTests(unittest.TestCase):
    """The task-review capsule carries the compact judging hint."""

    def test_capsule_contains_the_hint(self) -> None:
        capsule = _task_capsule_text(
            job=type("Job", (), {"review_job_id": "job-1", "item_id": "item-000001"})(),
            item=None,
            group=None,
        )

        self.assertIn("shared acceptance standard", capsule)
        self.assertIn("source_requirement_ids lineage", capsule)
        self.assertIn("execution phase", capsule)
        self.assertNotIn("five recipe elements", capsule)

    def test_capsule_finding_gate_is_hard_and_language_stable(self) -> None:
        capsule = _task_capsule_text(
            job=type("Job", (), {"review_job_id": "job-1", "item_id": "item-000001"})(),
            item=None,
            group=None,
        )

        self.assertIn("hard output gate", capsule)
        self.assertIn("MUST reuse its finding_id verbatim", capsule)
        self.assertIn("same language as that canonical claim", capsule)
        self.assertIn("Chinese and English", capsule)


if __name__ == "__main__":
    unittest.main()
