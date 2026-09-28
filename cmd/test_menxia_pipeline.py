"""Menxia stage-barrier pipeline tests (design 2026-09-21, sections 4/6).

Covers: group-fair wave scheduling, per-item verdict folding (attempt-scoped
budget, escalation, gate un-parking), wave action reduction, the joiner's
mixed-outcome reduction, the FSM dispatch/fold contract, the group gate's
local routing, and the transition edges the wave reducer relies on.
"""

from __future__ import annotations

import unittest

from orchestrator.domain.context import (
    MenxiaGroupState,
    MenxiaItemState,
    MenxiaParallelLimits,
    ParallelState,
    ZhongshuParallelLimits,
    ProgressState,
    ReviewState,
    ReviewTaskGroup,
    ReviewTaskItem,
    TaskIdentity,
    WorkflowContext,
    ZhongshuGroupState,
)
from orchestrator.domain.decisions import StateDecision
from orchestrator.domain.errors import FailureRecord, InvariantViolation
from orchestrator.domain.events import DomainEvent
from orchestrator.domain.policies.menxia import (
    MENXIA_ACTIVE_STAGES,
    MENXIA_ITEM_TARGETS,
    MENXIA_STAGE_BY_ACTION,
    MENXIA_STAGE_BY_TARGET,
    apply_menxia_item_results,
    menxia_has_blockers,
    menxia_item_next_stage,
    menxia_stage_census,
    menxia_wave_action,
    ready_stage_items,
    remove_plan_item,
)
from orchestrator.domain.states import (
    MenxiaGroupGateState,
    StateRegistry,
    ZhongshuFreezeCheckState,
)
from orchestrator.domain.transitions import TransitionRegistry
from orchestrator.runtime.agent_effects import AgentNodeJoiner
from orchestrator.runtime.nodes import WorkerResult

TASK = "task-menxia-pipeline"


def make_review(**overrides) -> ReviewState:
    items = (
        ReviewTaskItem("item-001", "group-001", order=0),
        ReviewTaskItem("item-002", "group-001", order=1),
        ReviewTaskItem("item-003", "group-002", order=2),
    )
    groups = (
        ReviewTaskGroup("group-001", ("item-001", "item-002"), order=0),
        ReviewTaskGroup("group-002", ("item-003",), order=1),
    )
    base = dict(
        revision_id="rev-1",
        task_items=items,
        task_groups=groups,
        plan={
            "items": [
                {"item_id": "item-001"},
                {"item_id": "item-002"},
                {"item_id": "item-003"},
            ]
        },
        max_item_revision_rounds=5,
    )
    base.update(overrides)
    return ReviewState(**base)


def make_context(
    review: ReviewState,
    state: str = "ZHONGSHU_FREEZE_CHECK",
    sequence: int = 3,
    *,
    menxia_enabled: bool = True,
) -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity(TASK, "issue-1", "proj", "req-1"),
        progression=ProgressState(state, sequence, "2026-09-21T00:00:00Z"),
        review=review,
        parallel=ParallelState(
            zhongshu=ZhongshuParallelLimits(plan_review_gate=False),
            menxia=MenxiaParallelLimits(
                enabled=menxia_enabled,
                max_concurrent_groups=2,
                max_concurrent_items=3,
            )
        ),
    )


def make_event(
    payload: object,
    sequence: int = 3,
    name: str = "NODE_COMPLETED",
) -> DomainEvent:
    return DomainEvent(
        name=name,
        task_id=TASK,
        sequence=sequence,
        payload=payload,
        occurred_at="2026-09-21T00:00:01Z",
        event_id="evt-1",
    )


def wave_payload(
    aggregate_action: str,
    rows: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "aggregate": {"action": aggregate_action},
        "menxia_item_results": rows,
        "node_run_id": "node:1",
    }


ZHONGSHU_DOC_SECTIONS = (
    ("背景", "任务来自 issue 需求。"),
    ("目标", "输出 A 与 B。"),
    ("标识与范围", "group 覆盖 item。"),
    ("状态与边界语义", "仅在依赖组冻结后生效。"),
    ("行为要求", "必须返回结构化结果。"),
    ("责任边界", "Solver 撰写，Critic 裁决。"),
    ("交叉不变量", "依赖组冻结前本组不得冻结。"),
    ("验收标准", ""),
    ("非目标", "不包含部署。"),
)


def zhongshu_doc(group_id: str, version: int = 1) -> str:
    lines = [f"# {group_id} 需求文档 [v{version}]"]
    for number, (name, body) in enumerate(ZHONGSHU_DOC_SECTIONS, start=1):
        lines.append(f"## {number}. {name}")
        if body:
            lines.append(body)
    return "\n".join(lines)


class MenxiaSchedulingTests(unittest.TestCase):
    """ready_stage_items: group-fair, capped, dependency-gated selection."""

    def test_wave_selection_is_group_fair(self):
        review = make_review(
            menxia_items=(
                MenxiaItemState("item-001", "group-001"),
                MenxiaItemState("item-002", "group-001"),
                MenxiaItemState("item-003", "group-002"),
            )
        )
        limits = MenxiaParallelLimits(enabled=True, max_concurrent_groups=2, max_concurrent_items=3)
        rows = ready_stage_items(review, target="MENXIA_ITEM_SOLVER", limits=limits)
        # Round-robin between groups: group-001 first item, then group-002,
        # then group-001's second item.
        self.assertEqual(
            [row.item_id for row in rows],
            ["item-001", "item-003", "item-002"],
        )

    def test_caps_bound_groups_and_items(self):
        review = make_review(
            menxia_items=(
                MenxiaItemState("item-001", "group-001"),
                MenxiaItemState("item-002", "group-001"),
                MenxiaItemState("item-003", "group-002"),
            )
        )
        limits = MenxiaParallelLimits(enabled=True, max_concurrent_groups=1, max_concurrent_items=2)
        rows = ready_stage_items(review, target="MENXIA_ITEM_SOLVER", limits=limits)
        self.assertEqual(
            [row.item_id for row in rows],
            ["item-001", "item-002"],
        )

    def test_disabled_limits_keep_legacy_serial_pick(self):
        review = make_review()
        limits = MenxiaParallelLimits(enabled=False)
        rows = ready_stage_items(review, target="MENXIA_ITEM_SOLVER", limits=limits)
        self.assertEqual([row.item_id for row in rows], ["item-001"])

    def test_dependencies_gate_the_wave(self):
        review = make_review(
            task_items=(
                ReviewTaskItem("item-001", "group-001", order=0),
                ReviewTaskItem("item-002", "group-001", ("item-001",), 1),
            ),
            task_groups=(ReviewTaskGroup("group-001", ("item-001", "item-002"), 0),),
            menxia_items=(
                MenxiaItemState("item-001", "group-001", stage="ANALYZING"),
                MenxiaItemState("item-002", "group-001", stage="SOLVING"),
            ),
        )
        limits = MenxiaParallelLimits(enabled=True, max_concurrent_groups=2, max_concurrent_items=3)
        rows = ready_stage_items(review, target="MENXIA_ITEM_SOLVER", limits=limits)
        # item-002 depends on item-001 which is not completed yet.
        self.assertEqual([row.item_id for row in rows], [])

    def test_terminal_rows_are_not_redispatched(self):
        review = make_review(
            menxia_items=(
                MenxiaItemState("item-001", "group-001", stage="APPROVED"),
                MenxiaItemState("item-002", "group-001", stage="SOLVING"),
                MenxiaItemState("item-003", "group-002", stage="REMOVED"),
            )
        )
        limits = MenxiaParallelLimits(enabled=True, max_concurrent_groups=2, max_concurrent_items=3)
        rows = ready_stage_items(review, target="MENXIA_ITEM_SOLVER", limits=limits)
        self.assertEqual([row.item_id for row in rows], ["item-002"])

    def test_unknown_target_state_is_rejected(self):
        with self.assertRaises(InvariantViolation):
            ready_stage_items(make_review(), target="ZHONGSHU_SOLVER")


class MenxiaFoldTests(unittest.TestCase):
    """apply_menxia_item_results: verdict folding, budget, un-parking."""

    def test_forward_actions_advance_stage(self):
        review = make_review()
        post = apply_menxia_item_results(
            review,
            [
                {"item_id": "item-001", "action": "READY_FOR_ANALYST"},
                {"item_id": "item-002", "action": "READY_FOR_CRITIC"},
                {"item_id": "item-003", "action": "APPROVE_ITEM"},
            ],
            max_rounds=5,
        )
        stages = {row.item_id: row.stage for row in post}
        self.assertEqual(
            stages,
            {"item-001": "ANALYZING", "item-002": "REVIEWING", "item-003": "APPROVED"},
        )

    def test_revision_demand_bumps_round_until_cap(self):
        review = make_review(
            menxia_items=(MenxiaItemState("item-001", "group-001", stage="SOLVING", revision_round=3),)
        )
        post = apply_menxia_item_results(
            review,
            [{"item_id": "item-001", "action": "REVISE_ITEM"}],
            max_rounds=5,
        )
        self.assertEqual(post[0].revision_round, 4)
        self.assertEqual(post[0].stage, "SOLVING")

        post = apply_menxia_item_results(
            review,
            [{"item_id": "item-001", "action": "REVISE_ITEM"}],
            max_rounds=4,
        )
        self.assertEqual(post[0].revision_round, 4)
        self.assertEqual(post[0].stage, "ESCALATED")
        self.assertEqual(post[0].blocked_reason, "MENXIA_ITEM_STALLED")

    def test_terminal_rows_ignore_late_results(self):
        review = make_review(
            menxia_items=(MenxiaItemState("item-001", "group-001", stage="APPROVED"),)
        )
        post = apply_menxia_item_results(
            review,
            [{"item_id": "item-001", "action": "REVISE_ITEM"}],
            max_rounds=5,
        )
        self.assertEqual(post[0].stage, "APPROVED")

    def test_budget_reset_unparks_with_fresh_round(self):
        review = make_review(
            menxia_items=(
                MenxiaItemState(
                    "item-001", "group-001", stage="ESCALATED",
                    revision_round=5, blocked_reason="MENXIA_ITEM_STALLED",
                ),
            )
        )
        post = apply_menxia_item_results(
            review,
            [{
                "item_id": "item-001",
                "action": "REQUEST_SOLVER_REVISION",
                "budget_reset": True,
            }],
            max_rounds=5,
        )
        self.assertEqual(post[0].stage, "SOLVING")
        self.assertEqual(post[0].revision_round, 0)
        self.assertEqual(post[0].blocked_reason, "")

    def test_removed_rows_cannot_restart(self):
        review = make_review(
            menxia_items=(MenxiaItemState("item-001", "group-001", stage="REMOVED"),)
        )
        post = apply_menxia_item_results(
            review,
            [{
                "item_id": "item-001",
                "action": "REQUEST_SOLVER_REVISION",
                "budget_reset": True,
            }],
            max_rounds=5,
        )
        self.assertEqual(post[0].stage, "REMOVED")

    def test_unknown_item_result_raises(self):
        with self.assertRaises(InvariantViolation):
            apply_menxia_item_results(
                make_review(),
                [{"item_id": "item-999", "action": "REVISE_ITEM"}],
                max_rounds=5,
            )

    def test_human_gate_parks_in_place(self):
        review = make_review(
            menxia_items=(MenxiaItemState("item-001", "group-001", stage="SOLVING"),)
        )
        post = apply_menxia_item_results(
            review,
            [{"item_id": "item-001", "action": "HUMAN_GATE"}],
            max_rounds=5,
        )
        self.assertEqual(post[0].stage, "SOLVING")
        self.assertEqual(post[0].last_verdict, "HUMAN_GATE")

    def test_blocked_result_carries_reason(self):
        review = make_review(
            menxia_items=(MenxiaItemState("item-001", "group-001", stage="SOLVING"),)
        )
        post = apply_menxia_item_results(
            review,
            [{
                "item_id": "item-001",
                "action": "BLOCKED",
                "blocked_reason": "AGENT_REPLY_UNSTRUCTURED",
            }],
            max_rounds=5,
        )
        self.assertEqual(post[0].stage, "BLOCKED")
        self.assertEqual(post[0].blocked_reason, "AGENT_REPLY_UNSTRUCTURED")


class MenxiaWaveReductionTests(unittest.TestCase):
    """menxia_wave_action: the design section 6.3 reduction table."""

    def test_solver_routes_to_earliest_undrained_stage(self):
        self.assertEqual(
            menxia_wave_action(
                "MENXIA_ITEM_SOLVER",
                result_actions={"item-001": "READY_FOR_ANALYST"},
                census={"ANALYZING": 1},
            ),
            "READY_FOR_ANALYST",
        )
        self.assertEqual(
            menxia_wave_action(
                "MENXIA_ITEM_SOLVER",
                result_actions={"item-001": "READY_FOR_CRITIC"},
                census={"REVIEWING": 1},
            ),
            "READY_FOR_CRITIC",
        )

    def test_analyst_loops_back_while_solving(self):
        self.assertEqual(
            menxia_wave_action(
                "MENXIA_ITEM_ANALYST",
                result_actions={"item-001": "NEEDS_MORE_EVIDENCE"},
                census={"SOLVING": 1, "ANALYZING": 1},
            ),
            "NEEDS_MORE_EVIDENCE",
        )
        self.assertEqual(
            menxia_wave_action(
                "MENXIA_ITEM_ANALYST",
                result_actions={"item-001": "EVIDENCE_SUFFICIENT"},
                census={"REVIEWING": 1},
            ),
            "EVIDENCE_SUFFICIENT",
        )

    def test_critic_loops_back_while_solving(self):
        self.assertEqual(
            menxia_wave_action(
                "MENXIA_ITEM_CRITIC",
                result_actions={"item-001": "REQUEST_SOLVER_REVISION"},
                census={"SOLVING": 1, "REVIEWING": 1},
            ),
            "REQUEST_SOLVER_REVISION",
        )
        self.assertEqual(
            menxia_wave_action(
                "MENXIA_ITEM_CRITIC",
                result_actions={"item-001": "APPROVE_ITEM"},
                census={"APPROVED": 1},
            ),
            "APPROVE_ITEM",
        )

    def test_per_item_human_gate_wins(self):
        for state in MENXIA_ITEM_TARGETS:
            with self.subTest(state=state):
                self.assertEqual(
                    menxia_wave_action(
                        state,
                        result_actions={"item-001": "HUMAN_GATE", "item-002": "READY_FOR_ANALYST"},
                        census={"ANALYZING": 1, "SOLVING": 1},
                    ),
                    "HUMAN_GATE",
                )

    def test_unknown_state_is_rejected(self):
        with self.assertRaises(InvariantViolation):
            menxia_wave_action("DONE", result_actions={}, census={})


class MenxiaWaveDispatchTests(unittest.TestCase):
    """FSM dispatch: wave payload shape and legacy serial compatibility."""

    def test_freeze_check_dispatches_solver_wave(self):
        registry = StateRegistry.default()
        context = make_context(make_review(), state="ZHONGSHU_FREEZE_CHECK")
        decision = registry.get("ZHONGSHU_FREEZE_CHECK").handle(
            context, make_event({"aggregate": {"action": "FREEZE_APPROVED"}})
        )
        self.assertEqual(decision.transition.action, "FREEZE_APPROVED")
        # The approval also sinks the plan-review document (thin-doc, 2026-09-27);
        # the wave dispatch is the one node_dispatch effect.
        dispatches = [
            effect
            for effect in decision.effects
            if effect.effect_type == "node_dispatch"
        ]
        self.assertEqual(len(dispatches), 1)
        effect = dispatches[0]
        self.assertEqual(effect.effect_type, "node_dispatch")
        payload = effect.payload
        # The freeze entry now opens the shared-document group pipeline.
        self.assertEqual(payload["dispatch_mode"], "menxia_group_pipeline")
        self.assertEqual(
            [binding["group_id"] for binding in payload["bindings"]],
            ["group-001", "group-002"],
        )
        self.assertEqual(
            payload["menxia_stage_census"],
            {"group-001": "SOLVING", "group-002": "SOLVING"},
        )
        binding_context = payload["bindings"][0]["dispatch_context"]
        self.assertEqual(binding_context["menxia_dispatch_mode"], "group_pipeline")
        self.assertEqual(binding_context["group_id"], "group-001")
        self.assertIn("nexus.menxia.group_solver.v1", str(binding_context["envelope"]))

    def test_disabled_machinery_blocks_the_freeze_entry(self):
        registry = StateRegistry.default()
        context = make_context(
            make_review(), state="ZHONGSHU_FREEZE_CHECK", menxia_enabled=False
        )
        decision = registry.get("ZHONGSHU_FREEZE_CHECK").handle(
            context, make_event({"aggregate": {"action": "FREEZE_APPROVED"}})
        )
        # With the menxia machinery disabled the group pipeline cannot run
        # (no bindings, no document join) and the legacy item serial chain is
        # retired, so the freeze approval must stop loudly instead of
        # silently shipping a review with no documents.
        self.assertEqual(decision.transition.action, "BLOCKED")
        self.assertEqual(
            decision.update.recovery.blocked_reason, "MENXIA_PARALLEL_DISABLED"
        )
        self.assertEqual(decision.effects, ())

    def test_enabled_machinery_still_opens_the_group_pipeline(self):
        registry = StateRegistry.default()
        context = make_context(
            make_review(), state="ZHONGSHU_FREEZE_CHECK", menxia_enabled=True
        )
        decision = registry.get("ZHONGSHU_FREEZE_CHECK").handle(
            context, make_event({"aggregate": {"action": "APPROVE_FREEZE"}})
        )
        self.assertEqual(decision.transition.action, "APPROVE_FREEZE")
        effect = decision.effects[0]
        self.assertEqual(effect.payload["state"], "MENXIA_GROUP_SOLVER")
        self.assertEqual(effect.payload["dispatch_mode"], "menxia_group_pipeline")


class MenxiaJoinerTests(unittest.TestCase):
    """AgentNodeJoiner._join_menxia: mixed outcomes reduce without conflict."""

    def _joiner(self, payload: dict[str, object]) -> AgentNodeJoiner:
        return AgentNodeJoiner(
            "node:1",
            task_id=TASK,
            state="MENXIA_ITEM_SOLVER",
            sequence=3,
            revision_id="rev-1",
            dispatch_mode="menxia_item_pipeline",
            menxia_stage_census=payload["menxia_stage_census"],
            binding_contexts={
                binding["worker_id"]: dict(binding["dispatch_context"])
                for binding in payload["bindings"]
            },
        )

    def _solver_wave(self):
        # The item pipeline no longer receives the freeze entry, so the item
        # wave is built by calling the item dispatch directly (the item
        # branch stays registered until the pipeline is retired).
        from orchestrator.domain.states import _ConcreteWorkflowState

        context = make_context(make_review(), state="MENXIA_ITEM_SOLVER")
        effect = _ConcreteWorkflowState._dispatch_effect(
            context, "MENXIA_ITEM_SOLVER", revision_id="rev-1", plan_hash=""
        )
        return effect.payload

    def _content_failure(self) -> FailureRecord:
        return FailureRecord(
            failure_id="f-1",
            stage="node",
            owner_component="runner",
            task_id=TASK,
            state="MENXIA_ITEM_SOLVER",
            sequence=3,
            node_run_id="node:1",
            worker_id=None,
            effect_id=None,
            error_code="AGENT_REPLY_UNSTRUCTURED",
            retryable=True,
            message="bad reply",
            cause_type="WorkerResult",
        )

    def test_mixed_outcomes_reduce_without_action_conflict(self):
        payload = self._solver_wave()
        joiner = self._joiner(payload)
        results = (
            WorkerResult(
                payload["bindings"][0]["worker_id"], "SUCCEEDED", None,
                result_payload={"action": "READY_FOR_ANALYST"},
            ),
            WorkerResult(
                payload["bindings"][1]["worker_id"], "SUCCEEDED", None,
                result_payload={"action": "HUMAN_GATE"},
            ),
            WorkerResult(
                payload["bindings"][2]["worker_id"], "FAILED", None,
                failure=self._content_failure(),
            ),
        )
        node_result = joiner.join(results)
        self.assertIsNone(node_result.failure)
        self.assertEqual(node_result.status, "SUCCEEDED")
        aggregate = node_result.aggregate
        # A per-item HUMAN_GATE verdict pauses the wave (design L72).
        self.assertEqual(aggregate["action"], "HUMAN_GATE")
        rows = {row["item_id"]: row["action"] for row in aggregate["menxia_item_results"]}
        self.assertEqual(
            rows,
            {
                "item-001": "READY_FOR_ANALYST",
                "item-003": "HUMAN_GATE",
                "item-002": "BLOCKED",
            },
        )

    def test_clean_wave_routes_to_next_stage(self):
        payload = self._solver_wave()
        joiner = self._joiner(payload)
        results = tuple(
            WorkerResult(
                binding["worker_id"], "SUCCEEDED", None,
                result_payload={"action": "READY_FOR_ANALYST"},
            )
            for binding in payload["bindings"]
        )
        node_result = joiner.join(results)
        self.assertEqual(node_result.aggregate["action"], "READY_FOR_ANALYST")

    def test_content_failure_blocks_only_that_item(self):
        payload = self._solver_wave()
        joiner = self._joiner(payload)
        results = (
            WorkerResult(
                payload["bindings"][0]["worker_id"], "FAILED", None,
                failure=self._content_failure(),
            ),
            WorkerResult(
                payload["bindings"][1]["worker_id"], "SUCCEEDED", None,
                result_payload={"action": "READY_FOR_ANALYST"},
            ),
            WorkerResult(
                payload["bindings"][2]["worker_id"], "SUCCEEDED", None,
                result_payload={"action": "READY_FOR_ANALYST"},
            ),
        )
        node_result = joiner.join(results)
        rows = {row["item_id"]: row for row in node_result.aggregate["menxia_item_results"]}
        self.assertEqual(rows["item-001"]["action"], "BLOCKED")
        self.assertEqual(rows["item-001"]["blocked_reason"], "AGENT_REPLY_UNSTRUCTURED")
        self.assertEqual(node_result.aggregate["action"], "READY_FOR_ANALYST")


class MenxiaFsmFoldTests(unittest.TestCase):
    """FSM folding: stage rows, drain-out escalation, gate routing."""

    def setUp(self):
        self.registry = StateRegistry.default()

    def test_solver_wave_fold_pauses_on_human_gate(self):
        context = make_context(make_review(), state="MENXIA_ITEM_SOLVER")
        rows = [
            {"item_id": "item-001", "action": "READY_FOR_ANALYST"},
            {"item_id": "item-002", "action": "BLOCKED", "blocked_reason": "X"},
            {"item_id": "item-003", "action": "HUMAN_GATE"},
        ]
        decision = self.registry.get("MENXIA_ITEM_SOLVER").handle(
            context, make_event(wave_payload("HUMAN_GATE", rows))
        )
        self.assertEqual(decision.transition.action, "HUMAN_GATE")
        stages = {
            row.item_id: row.stage for row in decision.update.review.menxia_items
        }
        self.assertEqual(
            stages,
            {"item-001": "ANALYZING", "item-002": "BLOCKED", "item-003": "SOLVING"},
        )

    def test_budget_exhaustion_drains_out_to_group_gate(self):
        context = make_context(
            make_review(
                menxia_items=(
                    MenxiaItemState("item-001", "group-001", stage="SOLVING", revision_round=4),
                    MenxiaItemState("item-002", "group-001", stage="APPROVED"),
                    MenxiaItemState("item-003", "group-002", stage="APPROVED"),
                ),
            ),
            state="MENXIA_ITEM_SOLVER",
            sequence=9,
        )
        decision = self.registry.get("MENXIA_ITEM_SOLVER").handle(
            context,
            make_event(
                wave_payload(
                    "READY_FOR_ANALYST",
                    [{"item_id": "item-001", "action": "REVISE_ITEM"}],
                ),
                sequence=9,
            ),
        )
        self.assertEqual(decision.transition.action, "OPEN_HUMAN_GATE")
        self.assertEqual(decision.transition.reason_code, "MENXIA_GROUP_BLOCKED")
        self.assertEqual(
            decision.update.human_gate.resume_state, "MENXIA_GROUP_GATE"
        )
        stages = {
            row.item_id: row.stage for row in decision.update.review.menxia_items
        }
        self.assertEqual(stages["item-001"], "ESCALATED")

    def test_critic_remove_item_closes_every_ledger(self):
        context = make_context(
            make_review(
                menxia_items=(
                    MenxiaItemState("item-001", "group-001", stage="REVIEWING"),
                    MenxiaItemState("item-002", "group-001", stage="REVIEWING"),
                    MenxiaItemState("item-003", "group-002", stage="APPROVED"),
                ),
                completed_item_ids=("item-003",),
            ),
            state="MENXIA_ITEM_CRITIC",
            sequence=7,
        )
        decision = self.registry.get("MENXIA_ITEM_CRITIC").handle(
            context,
            make_event(
                wave_payload(
                    "APPROVE_ITEM",
                    [
                        {"item_id": "item-001", "action": "REMOVE_ITEM"},
                        {"item_id": "item-002", "action": "APPROVE_ITEM"},
                    ],
                ),
                sequence=7,
            ),
        )
        self.assertEqual(decision.transition.action, "APPROVE_ITEM")
        update = decision.update.review
        stages = {row.item_id: row.stage for row in update.menxia_items}
        self.assertEqual(stages["item-001"], "REMOVED")
        self.assertIn("item-002", update.completed_item_ids)
        self.assertNotIn("item-001", [item.item_id for item in update.task_items])
        self.assertNotIn(
            "item-001", [entry.get("item_id") for entry in update.plan["items"]]
        )

    def test_group_gate_approve_completes_with_parked_items(self):
        context = make_context(
            make_review(
                task_items=(
                    ReviewTaskItem("item-001", "group-001", order=0),
                    ReviewTaskItem("item-002", "group-001", order=1),
                ),
                task_groups=(ReviewTaskGroup("group-001", ("item-001", "item-002"), 0),),
                plan={"items": [{"item_id": "item-001"}, {"item_id": "item-002"}]},
                menxia_items=(
                    MenxiaItemState("item-001", "group-001", stage="ESCALATED"),
                    MenxiaItemState("item-002", "group-001", stage="APPROVED"),
                ),
                completed_item_ids=("item-002",),
            ),
            state="MENXIA_GROUP_GATE",
            sequence=11,
        )
        decision = self.registry.get("MENXIA_GROUP_GATE").handle(
            context,
            make_event({"aggregate": {"action": "APPROVE_GROUP"}}, sequence=11),
        )
        # Parked (escalated) items do not block the operator-backed approval.
        self.assertEqual(decision.transition.action, "APPROVE_GROUP")
        self.assertEqual(decision.effects, ())

    def test_group_gate_revision_unparks_named_group(self):
        review = make_review(
            menxia_groups=(
                MenxiaGroupState(
                    "group-001",
                    stage="ESCALATED",
                    revision_round=5,
                    blocked_reason="MENXIA_GROUP_STALLED",
                ),
                MenxiaGroupState("group-002", stage="APPROVED"),
            ),
        )
        context = make_context(review, state="MENXIA_GROUP_GATE", sequence=11)
        decision = self.registry.get("MENXIA_GROUP_GATE").handle(
            context,
            make_event(
                {
                    "aggregate": {"action": "REQUEST_GROUP_REVISION"},
                    "required_changes": [
                        {"item_id": "item-001", "claim": "fix scoping"}
                    ],
                },
                sequence=11,
            ),
        )
        self.assertEqual(decision.transition.action, "REQUEST_GROUP_REVISION")
        stages = {
            row.group_id: row.stage
            for row in decision.update.review.menxia_groups
        }
        self.assertEqual(stages["group-001"], "SOLVING")
        self.assertEqual(stages["group-002"], "APPROVED")
        row = next(
            row
            for row in decision.update.review.menxia_groups
            if row.group_id == "group-001"
        )
        self.assertEqual(row.revision_round, 0)
        self.assertEqual(
            decision.effects[0].payload["bindings"][0]["group_id"], "group-001"
        )

    def test_group_gate_revision_parses_target_and_item_decisions(self):
        """Real gate verdicts name targets via required_changes[].target and
        group_consistency.item_decisions[]; both must restart the group
        Solver wave instead of escalating to the human gate (2026-09-22
        live bug, ported to the shared-document pipeline)."""
        review = make_review(
            menxia_groups=(
                MenxiaGroupState(
                    "group-001",
                    stage="ESCALATED",
                    revision_round=5,
                    blocked_reason="MENXIA_GROUP_STALLED",
                ),
                MenxiaGroupState("group-002", stage="APPROVED"),
            ),
        )
        context = make_context(review, state="MENXIA_GROUP_GATE", sequence=11)
        decision = self.registry.get("MENXIA_GROUP_GATE").handle(
            context,
            make_event(
                {
                    "aggregate": {"action": "REQUEST_GROUP_REVISION"},
                    "required_changes": [
                        {"target": "item-001", "claim": "fix snapshot"},
                        {"target": "group-001", "claim": "shared contract"},
                    ],
                    "group_consistency": {
                        "item_decisions": [
                            {
                                "item_id": "item-001",
                                "action": "REVISE_ITEM",
                                "blockers": ["finding-000001"],
                            }
                        ]
                    },
                },
                sequence=11,
            ),
        )
        self.assertEqual(decision.transition.action, "REQUEST_GROUP_REVISION")
        stages = {
            row.group_id: row.stage
            for row in decision.update.review.menxia_groups
        }
        self.assertEqual(stages["group-001"], "SOLVING")
        self.assertEqual(stages["group-002"], "APPROVED")
        self.assertEqual(
            decision.effects[0].payload["bindings"][0]["group_id"], "group-001"
        )

    def test_group_gate_revision_without_items_escalates(self):
        review = make_review(
            menxia_items=(
                MenxiaItemState("item-001", "group-001", stage="APPROVED"),
                MenxiaItemState("item-002", "group-001", stage="APPROVED"),
                MenxiaItemState("item-003", "group-002", stage="APPROVED"),
            ),
            completed_item_ids=("item-001", "item-002", "item-003"),
        )
        context = make_context(review, state="MENXIA_GROUP_GATE", sequence=11)
        decision = self.registry.get("MENXIA_GROUP_GATE").handle(
            context,
            make_event(
                {"aggregate": {"action": "REQUEST_GROUP_REVISION"}}, sequence=11
            ),
        )
        self.assertEqual(decision.transition.action, "HUMAN_GATE")
        self.assertEqual(
            decision.transition.reason_code, "MENXIA_GROUP_BLOCKED"
        )


class ZhongshuMenxiaPingPongTests(unittest.TestCase):
    """Early hand-over: FROZEN groups enter Menxia while Zhongshu keeps
    grinding the rest, and the group gate bounces back (implementation
    plan Task 8)."""

    def setUp(self) -> None:
        self.registry = StateRegistry.default()

    def test_first_frozen_group_enters_menxia_while_others_reviewing(self):
        review = make_review(
            zhongshu_groups=(
                ZhongshuGroupState(
                    "group-001",
                    stage="CONVERGED",
                    doc_version=1,
                    doc_markdown=zhongshu_doc("group-001"),
                ),
                ZhongshuGroupState("group-002", stage="REVIEWING"),
            ),
        )
        context = make_context(review, state="ZHONGSHU_FREEZE_CHECK")
        decision = self.registry.get("ZHONGSHU_FREEZE_CHECK").handle(
            context, make_event({"aggregate": {"action": "FREEZE_APPROVED"}})
        )
        # Partial release: group-001 freezes and enters Menxia in the same
        # decision while group-002 keeps its Zhongshu row untouched.
        self.assertEqual(decision.transition.action, "FREEZE_APPROVED")
        stages = {
            row.group_id: row.stage
            for row in decision.update.review.zhongshu_groups
        }
        self.assertEqual(
            stages, {"group-001": "FROZEN", "group-002": "REVIEWING"}
        )
        effect = decision.effects[0]
        self.assertEqual(effect.effect_type, "node_dispatch")
        self.assertEqual(effect.payload["dispatch_mode"], "menxia_group_pipeline")
        self.assertEqual(
            [binding["group_id"] for binding in effect.payload["bindings"]],
            ["group-001"],
        )

    def test_unfrozen_groups_not_seeded(self):
        review = make_review(
            zhongshu_groups=(
                ZhongshuGroupState("group-001", stage="FROZEN"),
                ZhongshuGroupState("group-002", stage="REVIEWING"),
            ),
        )
        rows = review.seed_menxia_groups()
        self.assertEqual([row.group_id for row in rows], ["group-001"])

    def test_gate_routes_back_when_zhongshu_work_remains(self):
        review = make_review(
            menxia_groups=(MenxiaGroupState("group-001", stage="APPROVED"),),
            zhongshu_groups=(
                ZhongshuGroupState("group-001", stage="FROZEN"),
                ZhongshuGroupState("group-002", stage="REVIEWING"),
            ),
        )
        context = make_context(review, state="MENXIA_GROUP_GATE", sequence=11)
        decision = self.registry.get("MENXIA_GROUP_GATE").handle(
            context,
            make_event({"aggregate": {"action": "APPROVE_GROUP"}}, sequence=11),
        )
        # The menxia wave for group-001 wrapped up, but group-002 is still
        # unfrozen: the gate must bounce the run back to the Zhongshu Critic
        # instead of dispatching another menxia solver wave or finishing.
        self.assertEqual(decision.transition.action, "REQUEST_NEXT_GROUP")
        registry = TransitionRegistry.default()
        self.assertEqual(
            registry.target_for("MENXIA_GROUP_GATE", "REQUEST_NEXT_GROUP"),
            "ZHONGSHU_CRITIC",
        )
        effect = decision.effects[0]
        self.assertEqual(effect.effect_type, "node_dispatch")
        self.assertEqual(
            [binding["group_id"] for binding in effect.payload["bindings"]],
            ["group-002"],
        )
        self.assertEqual(
            effect.payload["dispatch_mode"], "group_review"
        )

    def test_critic_wave_skips_frozen_group_items(self):
        from orchestrator.domain.states import _task_review_bindings

        review = make_review(
            zhongshu_groups=(
                ZhongshuGroupState("group-001", stage="FROZEN"),
                ZhongshuGroupState("group-002", stage="REVIEWING"),
            ),
        )
        context = make_context(review, state="ZHONGSHU_CRITIC")
        bindings = _task_review_bindings(context, "rev-1", "")
        self.assertIsNotNone(bindings)
        self.assertEqual(
            [binding["item_id"] for binding in bindings], ["item-003"]
        )

    def test_gate_approves_done_only_when_all_groups_complete(self):
        review = make_review(
            menxia_groups=(
                MenxiaGroupState("group-001", stage="APPROVED"),
                MenxiaGroupState("group-002", stage="APPROVED"),
            ),
            zhongshu_groups=(
                ZhongshuGroupState("group-001", stage="FROZEN"),
                ZhongshuGroupState("group-002", stage="FROZEN"),
            ),
            completed_item_ids=("item-001", "item-002", "item-003"),
        )
        context = make_context(review, state="MENXIA_GROUP_GATE", sequence=11)
        decision = self.registry.get("MENXIA_GROUP_GATE").handle(
            context,
            make_event({"aggregate": {"action": "APPROVE_GROUP"}}, sequence=11),
        )
        # Every group is FROZEN and menxia-approved: nothing routes back and
        # the gate approval finishes the run.
        self.assertEqual(decision.transition.action, "APPROVE_GROUP")


class MenxiaTransitionContractTests(unittest.TestCase):
    """Every wave-reducer action must resolve to a legal graph target."""

    def test_wave_aggregate_actions_have_edges(self):
        registry = TransitionRegistry.default()
        expected = {
            # The freeze entry now opens the group pipeline; the item edges
            # remain registered until the item pipeline is retired.
            ("ZHONGSHU_FREEZE_CHECK", "FREEZE_APPROVED"): "MENXIA_GROUP_SOLVER",
            ("MENXIA_ITEM_SOLVER", "READY_FOR_ANALYST"): "MENXIA_ITEM_ANALYST",
            ("MENXIA_ITEM_SOLVER", "READY_FOR_CRITIC"): "MENXIA_ITEM_CRITIC",
            ("MENXIA_ITEM_SOLVER", "HUMAN_GATE"): "HUMAN_GATE",
            ("MENXIA_ITEM_SOLVER", "OPEN_HUMAN_GATE"): "HUMAN_GATE",
            ("MENXIA_ITEM_ANALYST", "EVIDENCE_SUFFICIENT"): "MENXIA_ITEM_CRITIC",
            ("MENXIA_ITEM_ANALYST", "NEEDS_MORE_EVIDENCE"): "MENXIA_ITEM_SOLVER",
            ("MENXIA_ITEM_ANALYST", "REQUEST_SOLVER_REVISION"): "MENXIA_ITEM_SOLVER",
            ("MENXIA_ITEM_CRITIC", "APPROVE_ITEM"): "MENXIA_GROUP_GATE",
            ("MENXIA_ITEM_CRITIC", "REVISE_ITEM"): "MENXIA_ITEM_SOLVER",
            ("MENXIA_ITEM_CRITIC", "REMOVE_ITEM"): "MENXIA_ITEM_SOLVER",
            ("MENXIA_ITEM_CRITIC", "REQUEST_SOLVER_REVISION"): "MENXIA_ITEM_SOLVER",
            ("MENXIA_GROUP_SOLVER", "READY_FOR_ANALYST"): "MENXIA_GROUP_ANALYST",
            ("MENXIA_GROUP_SOLVER", "READY_FOR_CRITIC"): "MENXIA_GROUP_CRITIC",
            ("MENXIA_GROUP_ANALYST", "EVIDENCE_SUFFICIENT"): "MENXIA_GROUP_CRITIC",
            ("MENXIA_GROUP_ANALYST", "NEEDS_MORE_EVIDENCE"): "MENXIA_GROUP_SOLVER",
            ("MENXIA_GROUP_ANALYST", "REQUEST_SOLVER_REVISION"): "MENXIA_GROUP_SOLVER",
            ("MENXIA_GROUP_CRITIC", "APPROVE_GROUP"): "MENXIA_GROUP_GATE",
            ("MENXIA_GROUP_CRITIC", "REQUEST_SOLVER_REVISION"): "MENXIA_GROUP_SOLVER",
            ("MENXIA_GROUP_CRITIC", "REQUEST_EVIDENCE"): "MENXIA_GROUP_ANALYST",
            ("MENXIA_GROUP_GATE", "APPROVE_GROUP"): "DONE",
            ("MENXIA_GROUP_GATE", "NEXT_ITEM"): "MENXIA_GROUP_SOLVER",
            ("MENXIA_GROUP_GATE", "REQUEST_GROUP_REVISION"): "MENXIA_GROUP_SOLVER",
            # Early hand-over: the gate bounces the run back to the Zhongshu
            # Critic while unfrozen groups still need convergence (Task 8).
            ("MENXIA_GROUP_GATE", "REQUEST_NEXT_GROUP"): "ZHONGSHU_CRITIC",
        }
        for (source, action), target in expected.items():
            with self.subTest(source=source, action=action):
                self.assertEqual(registry.target_for(source, action), target)

    def test_stage_maps_are_consistent(self):
        for target, stage in MENXIA_STAGE_BY_TARGET.items():
            self.assertIn(target, MENXIA_ITEM_TARGETS)
            self.assertIn(stage, MENXIA_ACTIVE_STAGES)
        for action, stage in MENXIA_STAGE_BY_ACTION.items():
            mapped = menxia_item_next_stage(action)
            self.assertEqual(mapped, stage)
        # Every non-parking action must send the item somewhere the graph
        # can resume from; parking actions (HUMAN_GATE) keep the stage.
        self.assertIsNone(menxia_item_next_stage("HUMAN_GATE"))


class MenxiaPlanRemovalTests(unittest.TestCase):
    def test_remove_plan_item_edits_plan_in_place_copy(self):
        plan = {
            "items": [
                {"item_id": "item-001", "title": "a"},
                {"item_id": "item-002", "title": "b"},
            ]
        }
        updated = remove_plan_item(plan, "item-001")
        self.assertEqual(
            [entry["item_id"] for entry in updated["items"]], ["item-002"]
        )
        self.assertEqual(
            [entry["item_id"] for entry in plan["items"]], ["item-001", "item-002"]
        )

    def test_remove_plan_item_keeps_plan_without_match(self):
        plan = {"items": [{"item_id": "item-009"}]}
        self.assertIsNone(remove_plan_item(plan, "item-001"))


class MenxiaBlockerHelperTests(unittest.TestCase):
    def test_menxia_has_blockers(self):
        self.assertFalse(menxia_has_blockers(()))
        self.assertFalse(
            menxia_has_blockers((MenxiaItemState("i", "g"),))
        )
        self.assertTrue(
            menxia_has_blockers((MenxiaItemState("i", "g", stage="ESCALATED"),))
        )
        self.assertTrue(
            menxia_has_blockers((MenxiaItemState("i", "g", stage="BLOCKED"),))
        )

    def test_census_counts_stages(self):
        census = menxia_stage_census(
            (
                MenxiaItemState("i1", "g", stage="SOLVING"),
                MenxiaItemState("i2", "g", stage="SOLVING"),
                MenxiaItemState("i3", "g", stage="APPROVED"),
            )
        )
        self.assertEqual(census, {"SOLVING": 2, "APPROVED": 1})


class ParallelFlagOverrideTests(unittest.TestCase):
    """Env flags override persisted parallel limits for new and resumed tasks."""

    def _apply(self, **env: str | None):
        import os

        from orchestrator.app import _with_parallel_flag_overrides

        saved = {name: os.environ.get(name) for name in env}
        try:
            for name, value in env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            context = make_context(make_review(), menxia_enabled=False)
            return _with_parallel_flag_overrides(context)
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_unset_env_defaults_menxia_on(self):
        # Menxia is on by default now: an unset env flag flips even a
        # persisted disabled task to enabled.  NEXUS_MENXIA_ENABLED=0 opts
        # out explicitly (test_explicit_falsy_flag_disables).
        result = self._apply(
            ZHONGSHU_ITEM_WORKFLOW_ENABLED=None, NEXUS_MENXIA_ENABLED=None
        )
        self.assertFalse(result.parallel.zhongshu.item_workflow_enabled)
        self.assertTrue(result.parallel.menxia.enabled)

    def test_menxia_flag_enables_resumed_task(self):
        result = self._apply(NEXUS_MENXIA_ENABLED="1")
        self.assertTrue(result.parallel.menxia.enabled)
        self.assertFalse(result.parallel.zhongshu.item_workflow_enabled)

    def test_explicit_falsy_flag_disables(self):
        result = self._apply(
            NEXUS_MENXIA_ENABLED="0", ZHONGSHU_ITEM_WORKFLOW_ENABLED="true"
        )
        self.assertFalse(result.parallel.menxia.enabled)
        self.assertTrue(result.parallel.zhongshu.item_workflow_enabled)


class RepositoryPatchContextTests(unittest.TestCase):
    def test_patch_context_persists_flag_correction(self):
        import tempfile
        from dataclasses import replace as dc_replace
        from pathlib import Path

        from orchestrator.runtime.repository import (
            JsonWorkflowRepository,
            WorkflowSnapshot,
        )

        with tempfile.TemporaryDirectory() as tmp:
            repository = JsonWorkflowRepository(Path(tmp))
            context = make_context(make_review(), menxia_enabled=False)
            repository.initialize(WorkflowSnapshot(TASK, context, 0))
            corrected = dc_replace(
                context,
                parallel=dc_replace(
                    context.parallel,
                    menxia=dc_replace(context.parallel.menxia, enabled=True),
                ),
            )
            repository.patch_context(TASK, corrected)
            reloaded = repository.load(TASK).context
            self.assertTrue(reloaded.parallel.menxia.enabled)
            self.assertFalse(reloaded.parallel.zhongshu.item_workflow_enabled)


class MenxiaGateRevisionTargetTests(unittest.TestCase):
    """The gate's revision demand must reach the groups it actually names."""

    def _extract(self, payload: dict[str, object]) -> list[str]:
        from orchestrator.domain.states import _menxia_gate_revision_group_ids

        return _menxia_gate_revision_group_ids(payload, make_review())

    def test_required_changes_group_targets_are_extracted(self):
        payload = {
            "required_changes": [
                {"target": "group-002", "change": "redo the approach"},
                "group-001",
                {"target": "item-003", "change": "rework this item"},
                {"target": "req-000001", "change": "not a group target"},
            ],
        }
        # item-003 maps back to group-002, which the object target already
        # named; the dedup keeps the first mention's order.
        self.assertEqual(self._extract(payload), ["group-002", "group-001"])

    def test_explicit_group_ids_still_win(self):
        payload = {
            "group_ids": ["group-001"],
            "required_changes": [{"target": "group-002", "change": "redo"}],
        }
        self.assertEqual(self._extract(payload), ["group-001", "group-002"])

    def test_unactionable_demands_name_nothing(self):
        payload = {
            "required_changes": [
                {"target": "item-001", "change": "rework"},
                {"change": "no target field"},
                42,
            ],
        }
        # item-001 belongs to group-001, so the demand is still actionable
        # through the item mapping; a verdict with no mappable target at all
        # returns an empty list and the gate hands the run to an operator.
        self.assertEqual(self._extract(payload), ["group-001"])
        self.assertEqual(self._extract({"required_changes": [{"change": "x"}]}), [])


if __name__ == "__main__":
    unittest.main()
