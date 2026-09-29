"""Prompt bundle metering and slicing scaffolding (2026-09-28 plan Task 3).

T3.1 ships unconditional per-section metering (PROMPT_BUNDLE_SECTIONS,
WORKER_CAPSULE_BYTES, PROMPT_BUNDLE_REPORT); T3.2 ships only the config
scaffolding (parallel.prompt_slicing, default off) until the section data
decides the cut points.
"""

from __future__ import annotations

import os
os.environ.setdefault("NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS", "1")
from dataclasses import replace
import json
import os
import tempfile
import unittest
from pathlib import Path

from orchestrator.domain.context import ParallelState, WorkflowContext
from orchestrator.prompt_bundle import (
    BUNDLE_METER,
    PromptBundleBuilder,
    _context_sections,
    _prompt_sections,
    emit_prompt_bundle_report,
)


class PromptSectionTests(unittest.TestCase):
    def test_prompt_sections_split_on_block_markers(self) -> None:
        prompt = (
            "Task task-1\n"
            "State: ZHONGSHU_SOLVER\n"
            "Request:\nrun a review\n"
            "[Contract rules]\n- rule one\n- rule two\n"
            "[Retry feedback]\nThe orchestrator rejected your reply.\n"
        )
        sections = _prompt_sections(prompt)
        self.assertIn("header", sections)
        self.assertIn("Contract rules", sections)
        self.assertIn("Retry feedback", sections)
        self.assertEqual(
            sum(sections.values()), len(prompt.encode("utf-8"))
        )

    def test_prompt_without_markers_is_all_header(self) -> None:
        sections = _prompt_sections("plain text prompt\n")
        self.assertEqual(list(sections), ["header"])

    def test_context_sections_weights_heavy_keys(self) -> None:
        heavy = {"items": [{"task": "x" * 3000}]}
        sections = _context_sections(
            {
                "task_id": "task-1",
                "plan": heavy,
                "dispatch_context": {"envelope": {"ingredients": ["y" * 2500]}},
            }
        )
        self.assertIn("context#/plan", sections)
        self.assertIn("context#/dispatch_context", sections)
        self.assertIn("context#/dispatch_context.envelope", sections)
        self.assertIn("context#/other", sections)
        self.assertNotIn("context#/task_id", sections)


class PromptBundleMeterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.meter = BUNDLE_METER.__class__()

    def test_report_groups_by_state_with_averages(self) -> None:
        self.meter.record(
            "task-1", "ZHONGSHU_SOLVER", "req-1", {"prompt.txt": 100, "context.json": 300}
        )
        self.meter.record(
            "task-1", "ZHONGSHU_SOLVER", "req-2", {"prompt.txt": 200, "context.json": 600}
        )
        self.meter.record(
            "task-1", "ZHONGSHU_CRITIC", "req-3", {"prompt.txt": 50, "context.json": 950}
        )
        report = self.meter.report("task-1")
        self.assertEqual(report["ZHONGSHU_SOLVER"]["hops"], 2)
        self.assertEqual(report["ZHONGSHU_SOLVER"]["bytes_avg"], 600)
        self.assertEqual(
            report["ZHONGSHU_SOLVER"]["top_sections"], {"context.json": 900, "prompt.txt": 300}
        )
        self.assertEqual(report["ZHONGSHU_CRITIC"]["hops"], 1)

    def test_empty_report_is_suppressed(self) -> None:
        self.assertEqual(self.meter.report("task-unknown"), {})

    def test_report_emits_log_once_recorded(self) -> None:
        BUNDLE_METER.record("task-log", "ZHONGSHU_SOLVER", "req-1", {"prompt.txt": 10})
        try:
            with self.assertLogs(
                "review_orchestrator_fsm", level="INFO"
            ) as captured:
                emit_prompt_bundle_report("task-log", reason="test")
        finally:
            BUNDLE_METER._hops.pop("task-log", None)
        self.assertTrue(
            any("PROMPT_BUNDLE_REPORT" in line for line in captured.output)
        )


class BundleBuildMeteringTests(unittest.TestCase):
    def test_build_logs_sections_and_registers_hop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            builder = PromptBundleBuilder(Path(directory) / "bundles")
            task_id = "task-metering"
            with self.assertLogs("review_orchestrator_fsm", level="INFO") as captured:
                builder.build(
                    task_id=task_id,
                    request_id="req-metering",
                    phase="ZHONGSHU",
                    role="review-critic",
                    state="ZHONGSHU_FREEZE_CHECK",
                    context={"plan": {"items": ["x" * 2000]}},
                    prompt="Header text\n[Contract rules]\n- rule\n",
                )
            section_lines = [
                line for line in captured.output if "PROMPT_BUNDLE_SECTIONS" in line
            ]
            self.assertEqual(len(section_lines), 1)
            self.assertIn("state=ZHONGSHU_FREEZE_CHECK", section_lines[0])
            payload = section_lines[0].split("sections=", 1)[1]
            sections = json.loads(payload)
            self.assertIn("prompt.txt", sections)
            self.assertIn("context.json", sections)
            self.assertIn("prompt#/Contract rules", sections)
            self.assertIn("context#/plan", sections)
            hops = BUNDLE_METER.report(task_id)
            self.assertEqual(hops["ZHONGSHU_FREEZE_CHECK"]["hops"], 1)


class PromptSlicingConfigTests(unittest.TestCase):
    def test_default_off(self) -> None:
        self.assertFalse(ParallelState().prompt_slicing)

    def test_env_override_applies_to_new_and_resumed_contexts(self) -> None:
        from orchestrator.app import _with_parallel_flag_overrides
        from orchestrator.domain.context import ProgressState, TaskIdentity

        context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "project-1", "request-1"),
            progression=ProgressState("REQUEST_INTAKE", 0, "2026-09-28T00:00:00Z"),
            parallel=ParallelState(prompt_slicing=True),
        )
        base = os.environ.get("ZHONGSHU_PROMPT_SLICING")
        os.environ["ZHONGSHU_PROMPT_SLICING"] = "0"
        try:
            overridden = _with_parallel_flag_overrides(context)
        finally:
            if base is None:
                os.environ.pop("ZHONGSHU_PROMPT_SLICING", None)
            else:
                os.environ["ZHONGSHU_PROMPT_SLICING"] = base
        self.assertFalse(overridden.parallel.prompt_slicing)


if __name__ == "__main__":
    unittest.main()
