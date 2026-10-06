# Orchestrator Stabilization Implementation Plan

> **For agentic workers:** Execute this approved plan inline in the current session, task by task. User authorization covers implementation; preserve pre-existing changes.

**Goal:** Complete one bounded request-to-executable-plan workflow with correct accounting, local recovery and preserved human approval.

**Architecture:** Repair the existing immutable FSM and pure policies. Derive progress from validated results and durable problem identities. Use scripted adapters to exercise the real application without model tokens or external notifications.

**Tech Stack:** Python standard library, unittest, OrchestratorApp, existing Multica scripted adapters.

## Task 1: Correct Zhongshu opportunity accounting

Files: `cmd/orchestrator/domain/states.py`, new `cmd/test_orchestrator_stabilization.py`.

- [x] Write regressions using `ZhongshuCriticState.handle` and `LinearContextReducer`: evidence-only rounds preserve `stuck_rounds`, the first review after Solver counts the selected finding, repeated evidence reviews do not consume that batch again, genuine evidence no-progress remains bounded.
- [x] Run `rtk proxy python -X utf8 cmd/run_tests.py test_orchestrator_stabilization` and confirm the accounting failures.
- [x] Compute the batch before `_merge_and_close_findings`; clear consumed `attempted_finding_ids` in Critic review updates. Evidence-only requests with no batch use `()` rather than legacy `None`.
- [x] Re-run the new module plus existing convergence, evidence, finding and stall suites.

## Task 2: Preserve independent group progress

Files: `cmd/orchestrator/domain/states.py`, `cmd/orchestrator/domain/transitions.py`, `cmd/orchestrator/domain/policies/zhongshu_group.py`, new regression module.

- [x] Add tests with one parked group and another ready or pending group, production approval enabled, and absent finding ownership.
- [x] Add `("ZHONGSHU_CRITIC", "CONTINUE_GROUP_REVIEW", "ZHONGSHU_CRITIC")` to the closed graph.
- [x] Park only owned groups; build the next dispatch from the fully folded review; exclude parked groups from group review bindings. Continue pending groups and collect a final actionable group gate when runnable work is exhausted. Preserve scripted incremental freeze and full production approval.
- [x] Test recovery, directed resets, group hashes, and no empty review redispatch.

## Task 3: Track Menxia blocker identities

Files: `cmd/orchestrator/domain/context.py`, `cmd/orchestrator/domain/policies/menxia_group.py`, `cmd/orchestrator/runtime/agent_effects.py`, new regression module.

- [x] Add tests for closed old IDs plus new equal-count IDs, unchanged IDs with changed document text, DTO round-trip, legacy recovery and budget exhaustion.
- [x] Persist `last_open_suggestion_ids: tuple[str, ...] | None`; derive current IDs from `doc.open_blocking()` and `doc.pending_rejections()` in the validated joiner.
- [x] Reset stall count only for a shrinking count or removed prior blocker identity; retain the independent maximum revision budget.
- [x] Run Menxia pipeline, group join, document and alignment tests.

## Task 4: Bound requirement extraction and run the complete workflow

Files: `cmd/orchestrator/zhongshu_parallel.py`, `cmd/orchestrator/contracts/zhongshu_analyst.py`, `docs/multi/runtime/zhongshu-analyst-skill.md`, new regression module and acceptance script.

- [x] Reproduce invented “至少3个” acceptance against an original request with no count.
- [x] Validate explicit bounded quantities against the original request; add extraction rules that keep assumptions and scoped conclusions out of user requirements. Test Chinese/English supported quantities and unchanged valid contracts.
- [x] Exercise a fixed small request through the real app with a scripted malformed reply, corrected retry, explicit test approval, Menxia revision and final documents; repeat three times in isolated run roots and export a JSON report.
- [x] Run `rtk proxy python -X utf8 cmd/run_tests.py`, inspect the acceptance artifacts, run `rtk git diff --check`, and document results and remaining real-model validation boundary.

## Completion record

The fixed request reached DONE in three independent scripted runs with a real initial evidence packet, persisted approval-gate restart, a rejected malformed draft and a substantive Menxia revision. Initial evidence-packet routing without Solver mode was also repaired after the acceptance run reproduced it. Results and limitations: docs/multi/runtime/orchestrator-stabilization-20261006.md.
