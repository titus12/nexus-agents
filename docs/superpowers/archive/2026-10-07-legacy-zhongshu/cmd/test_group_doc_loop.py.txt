"""Group-document lifecycle scripted test (zero model tokens).

Drives the real OrchestratorApp through the parallel menxia group pipeline
with the full shared-document loop, the path the serial scripted e2e tests
cannot cover because they run with menxia disabled:

1. the freeze entry opens a group Solver wave, one binding per group, each
   carrying the seeded v1 skeleton in its dispatch context;
2. the Solver drafts v2 (version arithmetic + touched_scope enforcement);
3. the Analyst and the Critic each append a P1 suggestion (frozen-section
   rules), the Critic demands a revision (wave folds back to SOLVING);
4. the Solver absorbs both suggestions into v3 (verify_absorption rules);
5. the reviewers converge, the gate agent approves, the run reaches DONE.

Every reply is built by mechanically evolving the dispatched document, so
``verify_group_reply`` is the real gatekeeper on this path — a scripted
reply that breaks the document rules would fold as MENXIA_GROUP_DOC_BREACH
and fail the assertions.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace

from orchestrator.adapters import FakeMulticaAdapter
from orchestrator.app import OrchestratorApp
from orchestrator.domain.context import (
    MenxiaParallelLimits,
    ParallelState,
    ZhongshuParallelLimits,
    ProgressState,
    RequestState,
    TaskIdentity,
    WorkflowContext,
)
from orchestrator.domain.menxia_doc import (
    BODY_PART_TITLES,
    MenxiaGroupDoc,
    changed_body_sections,
)
from orchestrator.transport.external import ExternalMessage


def _plan() -> dict:
    def item(item_id: str, group_id: str, title: str) -> dict:
        return {
            "item_id": item_id,
            "group_id": group_id,
            "title": title,
            "objective": f"do {title}",
            "dependencies": [],
            "source_requirement_ids": ["req-000001"],
            "acceptance_signals": [f"{title} is observable"],
        }

    return {
        "plan_id": "plan-1",
        "items": [
            item("item-000001", "group-000001", "Task A"),
            item("item-000002", "group-000001", "Task B"),
            item("item-000003", "group-000002", "Task C"),
            item("item-000004", "group-000002", "Task D"),
        ],
        "groups": [
            {
                "group_id": "group-000001",
                "item_ids": ["item-000001", "item-000002"],
            },
            {
                "group_id": "group-000002",
                "item_ids": ["item-000003", "item-000004"],
            },
        ],
        "requirements": [
            {
                "requirement_id": "req-000001",
                "statement": "run a review",
                "priority": "must",
                "scope": "in",
                "kind": "task",
            }
        ],
    }


def _acceptance_entries(requirement_markdown: str, items: object) -> tuple[str, ...]:
    """Acceptance-map left column: the group requirement document's §8 lines.

    ``menxia_approval_blockers`` only accepts map rows whose left side the
    requirement document stated verbatim, so the scripted solver derives its
    rows from the dispatched ``requirement_markdown`` (fallback: the member
    items' acceptance signals).
    """

    from orchestrator.domain.zhongshu_doc import (
        SECTION_ACCEPTANCE,
        ZhongshuDocError,
        ZhongshuRequirementDoc,
    )

    if requirement_markdown.strip():
        try:
            parsed = ZhongshuRequirementDoc.parse(requirement_markdown)
        except ZhongshuDocError:
            parsed = None
        if parsed is not None:
            lines = tuple(
                line.strip()
                for line in parsed.section(SECTION_ACCEPTANCE).splitlines()
                if line.strip()
            )
            if lines:
                return lines
    signals: list[str] = []
    for item in items or ():
        for signal in (item.get("acceptance_signals") or ()):
            text = str(signal).strip()
            if text and text not in signals:
                signals.append(text)
    return tuple(signals) or ("基线验收",)


def _six_part_body(group_id: str, entries: tuple[str, ...]) -> str:
    """A plan body in the six fixed parts (requirements §9), map rows closing
    against the given acceptance entries."""

    parts = {
        "目标与设计决策": f"按基线执行 {group_id}，不做额外决策。",
        "现状与目标行为": "当前行为与目标行为如基线所述。",
        "统一约束": "遵循统一约束。",
        "任务分解": f"- {group_id} -> 一次执行。",
        "验收映射": "\n".join(f"- {entry} -> 验收用例覆盖。" for entry in entries),
        "风险与兼容": "风险可控。",
    }
    return "\n\n".join(
        f"### {title}\n{parts[title]}" for title in BODY_PART_TITLES
    )


class GroupWaveScripting:
    """Scripted menxia group-wave replies that evolve the shared document.

    Every reply is built by mechanically evolving the dispatched document, so
    ``verify_group_reply`` is the real gatekeeper on this path — a scripted
    reply that breaks the document rules folds as MENXIA_GROUP_DOC_BREACH.

    Subclasses set ``critic_demands_revision = False`` for the short loop:
    no reviewer suggestions, the critic approves the first document.
    """

    critic_demands_revision: bool = True

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.solver_rounds: dict[str, int] = {}
        self.analyst_rounds: dict[str, int] = {}
        self.critic_rounds: dict[str, int] = {}

    def _reply(self, request, payload: dict) -> None:
        context = request.context
        body = {
            "task_id": request.task_id,
            "request_id": request.request_id,
            "phase": request.phase,
            "role": request.role,
            "revision_id": str(context.get("revision_id") or ""),
            "plan_hash": str(context.get("plan_hash") or ""),
        }
        body.update(payload)
        self.queue_reply(
            request.request_id,
            ExternalMessage(request.agent_id, body, request.request_id),
        )

    def _reply_group_wave(self, request) -> None:
        context = request.context
        group_id = str(context.get("group_id") or "")
        stage = str(context.get("stage") or "")
        doc = MenxiaGroupDoc.parse(str(context.get("doc_markdown") or ""))
        if stage == "SOLVING":
            payload = self._solver_reply(request, group_id, doc)
        elif stage == "ANALYZING":
            payload = self._analyst_reply(group_id, doc)
        else:
            payload = self._critic_reply(group_id, doc)
        self._reply(request, payload)

    def _solver_reply(self, request, group_id: str, doc: MenxiaGroupDoc) -> dict:
        round_index = self.solver_rounds.get(group_id, 0)
        self.solver_rounds[group_id] = round_index + 1
        if round_index == 0:
            # First pass: expand the v1 skeleton into a real v2 draft.  The
            # joiner's previous document is the dispatched skeleton, so the
            # version must move exactly one step and the body must carry the
            # six fixed parts with an acceptance map closed against the
            # requirement document's §8 lines.
            doc = replace(
                doc,
                version=doc.version + 1,
                body_markdown=_six_part_body(
                    group_id,
                    _acceptance_entries(
                        str(request.context.get("requirement_markdown") or ""),
                        request.context.get("items") or (),
                    ),
                ),
            )
            action = "FEASIBLE"
            absorbed: list[str] = []
        else:
            # Second pass: absorb every open suggestion into the body and
            # record them in the ledger.  The follow-up note lands inside the
            # last body part (风险与兼容); touched_scope reports the change.
            absorbed = [
                suggestion.suggestion_id
                for suggestion in doc.suggestions
            ]
            doc = doc.with_version(doc.version + 1)
            for suggestion_id in absorbed:
                doc = doc.absorb(suggestion_id, note="applied to the body")
            doc = replace(
                doc,
                body_markdown=(
                    doc.body_markdown
                    + f"\n{group_id} 评审意见已吸收。"
                ),
            )
            action = "READY_FOR_ANALYST"
        previous = MenxiaGroupDoc.parse(str(request.context.get("doc_markdown") or ""))
        touched = list(changed_body_sections(previous, doc))
        return {
            "action": action,
            "summary": f"group {group_id} document v{doc.version}",
            "group_id": group_id,
            "doc_version": doc.version,
            "doc_markdown": doc.render(),
            "touched_scope": touched,
            "absorbed_ids": absorbed,
            "rejected_ids": [],
        }

    def _analyst_reply(self, group_id: str, doc: MenxiaGroupDoc) -> dict:
        pass_index = self.analyst_rounds.get(group_id, 0)
        self.analyst_rounds[group_id] = pass_index + 1
        if pass_index > 0 or not self.critic_demands_revision:
            # Later passes (or the short loop): nothing new to report.
            return self._reviewer_payload(
                "EVIDENCE_SUFFICIENT", group_id, doc, []
            )
        doc = doc.add_suggestion(
            author="analyst",
            severity="P1",
            body=f"acceptance signal for {group_id} must name an observable check",
        )
        return self._reviewer_payload(
            "EVIDENCE_SUFFICIENT", group_id, doc, [doc.suggestions[-1].suggestion_id]
        )

    def _critic_reply(self, group_id: str, doc: MenxiaGroupDoc) -> dict:
        pass_index = self.critic_rounds.get(group_id, 0)
        self.critic_rounds[group_id] = pass_index + 1
        if pass_index > 0 or not self.critic_demands_revision:
            # Later passes (or the short loop): approve the group.
            return self._reviewer_payload("APPROVE_GROUP", group_id, doc, [])
        doc = doc.add_suggestion(
            author="critic",
            severity="P1",
            body=f"risk register missing for {group_id}",
        )
        return self._reviewer_payload(
            "REQUEST_SOLVER_REVISION",
            group_id,
            doc,
            [doc.suggestions[-1].suggestion_id],
        )

    @staticmethod
    def _reviewer_payload(
        action: str,
        group_id: str,
        doc: MenxiaGroupDoc,
        added_ids: list[str],
    ) -> dict:
        return {
            "action": action,
            "group_id": group_id,
            "doc_markdown": doc.render(),
            "added_suggestion_ids": added_ids,
        }


def _zhongshu_doc(group_id: str, titles: tuple[str, ...]) -> str:
    """A nine-section zhongshu requirement document for one group.

    §8 closes with the group's acceptance_signals verbatim — the closure the
    reply validator (against the submitted plan) and the freeze check (against
    the folded items) both enforce.
    """

    sections = (
        "背景", "目标", "标识与范围", "状态与边界语义", "行为要求",
        "责任边界", "交叉不变量", "验收标准", "非目标",
    )
    lines = [f"# {group_id} 需求文档 [v1]"]
    for number, name in enumerate(sections, start=1):
        lines.append(f"## {number}. {name}")
        if name == "背景":
            lines.append(f"{group_id} 的上下文。")
        elif name == "验收标准":
            lines.extend(f"{title} is observable" for title in titles)
    return "\n".join(lines)


class _GroupLoopMultica(GroupWaveScripting, FakeMulticaAdapter):
    """Answers every menxia group binding by evolving the group document.

    Scripted behaviour per stage:

    - SOLVING   first pass: draft v2 from the v1 skeleton;
    - ANALYZING first pass: append a P1 analyst suggestion;
    - REVIEWING first pass: append a P1 critic suggestion and demand a
      revision (one group-wide revision round);
    - SOLVING   second pass: absorb both suggestions into the next version;
    - ANALYZING later passes: no additions, EVIDENCE_SUFFICIENT;
    - REVIEWING later passes: no additions, APPROVE_GROUP.
    """

    def dispatch(self, request):
        receipt = super().dispatch(request)
        context = request.context
        if (
            context.get("contract_mode")
            or context.get("zhongshu_dispatch_mode") == "requirement_contract"
        ):
            self._reply(
                request,
                {
                    "action": "REQUIREMENT_CONTRACT_READY",
                    "requirements": _plan()["requirements"],
                },
            )
            return receipt
        target = request.target_state
        if target == "ZHONGSHU_ANALYST":
            self._reply(request, {"action": "READY_FOR_SOLVER", "findings": []})
        elif target == "ZHONGSHU_SOLVER":
            self._reply(
                request,
                {
                    "action": "READY_FOR_CRITIC",
                    "plan": _plan(),
                    "plan_hash": "plan-1",
                    "changes": [],
                    # Every group ships its requirement document with the
                    # plan; §8 closes with the member items' signals.
                    "group_docs": [
                        {
                            "group_id": "group-000001",
                            "markdown": _zhongshu_doc(
                                "group-000001", ("Task A", "Task B")
                            ),
                        },
                        {
                            "group_id": "group-000002",
                            "markdown": _zhongshu_doc(
                                "group-000002", ("Task C", "Task D")
                            ),
                        },
                    ],
                },
            )
        elif target == "ZHONGSHU_CRITIC":
            self._reply(
                request,
                {
                    "action": "APPROVE_GROUP",
                    "group_id": str(context.get("group_id") or ""),
                    "reviewed_plan_hash": str(context.get("plan_hash") or ""),
                    "findings": [],
                    "finding_responses": [],
                    "review_checks": {},
                },
            )
        elif target == "ZHONGSHU_FREEZE_CHECK":
            self._reply(request, {"action": "FREEZE_APPROVED"})
        elif target in (
            "MENXIA_GROUP_SOLVER",
            "MENXIA_GROUP_ANALYST",
            "MENXIA_GROUP_CRITIC",
        ):
            self._reply_group_wave(request)
        elif target == "MENXIA_GROUP_GATE":
            self._reply(request, {"action": "APPROVE_GROUP"})
        return receipt


def _context() -> WorkflowContext:
    return WorkflowContext(
        identity=TaskIdentity("task-group-loop", "issue-loop", "", "request-loop"),
        progression=ProgressState("REQUEST_INTAKE", 0, "2026-09-23T00:00:00Z"),
        request=RequestState(
            raw_request="run a review", project_type="python", task_type="review"
        ),
        parallel=ParallelState(
            zhongshu=ZhongshuParallelLimits(
                plan_review_gate=False, mechanical_freeze=False
            ),
            menxia=MenxiaParallelLimits(
                enabled=True,
                max_concurrent_groups=2,
                max_concurrent_items=3,
            )
        ),
    )


class GroupDocumentLoopTests(unittest.TestCase):
    """The shared-document loop through the real app, zero model tokens."""

    def _run(self) -> tuple[object, _GroupLoopMultica]:
        with tempfile.TemporaryDirectory() as directory:
            adapter = _GroupLoopMultica()
            app = OrchestratorApp(
                _context(),
                root=directory,
                multica=adapter,
                poll_interval=0,
                timeout_seconds=15,
            )
            self.assertTrue(app.run())
            snapshot = app.repository.load("task-group-loop")
        return snapshot, adapter

    def test_document_loop_converges_to_done(self) -> None:
        snapshot, _ = self._run()

        self.assertEqual(snapshot.context.progression.state, "DONE")
        review = snapshot.context.review
        self.assertIsNotNone(review)

    def test_group_rows_carry_the_converged_document_chain(self) -> None:
        snapshot, _ = self._run()

        rows = {
            row.group_id: row
            for row in (snapshot.context.review.menxia_groups or ())
        }
        self.assertEqual(set(rows), {"group-000001", "group-000002"})
        for group_id, row in rows.items():
            with self.subTest(group_id=group_id):
                self.assertEqual(row.stage, "APPROVED")
                self.assertEqual(row.blocked_reason, "")
                # v1 seed -> v2 draft -> v3 absorption; reviewers never move
                # the version.
                self.assertEqual(row.doc_version, 3)
                doc = MenxiaGroupDoc.parse(row.doc_markdown)
                self.assertEqual(doc.version, 3)
                # Both P1 suggestions left the section into the ledger.
                self.assertEqual(doc.suggestions, ())
                self.assertEqual(
                    [entry.suggestion_ids for entry in doc.ledger],
                    [("S-001", "S-002")],
                )
                self.assertEqual(doc.open_count(), 0)

    def test_group_waves_dispatch_one_binding_per_group(self) -> None:
        _, adapter = self._run()

        waves = [
            request
            for request in adapter.dispatched
            if request.context.get("menxia_dispatch_mode") == "group_pipeline"
        ]
        # Solver draft + analyst + critic + solver absorb + analyst + critic.
        self.assertEqual(len(waves), 12)
        by_stage: dict[str, int] = {}
        for request in waves:
            stage = str(request.context.get("stage") or "")
            by_stage[stage] = by_stage.get(stage, 0) + 1
        self.assertEqual(
            by_stage,
            {"SOLVING": 4, "ANALYZING": 4, "REVIEWING": 4},
        )
        # Every wave covers both groups: the parallel limits allow 2.
        wave_sizes = {
            request.context.get("group_id") for request in waves
        }
        self.assertEqual(
            wave_sizes, {"group-000001", "group-000002"}
        )

    def test_one_revision_round_and_all_items_complete(self) -> None:
        snapshot, _ = self._run()

        review = snapshot.context.review
        rows = {
            row.group_id: row
            for row in (review.menxia_groups or ())
        }
        for row in rows.values():
            with self.subTest(group_id=row.group_id):
                # Exactly one revision demand (the critic's P1), absorbed on
                # the next solver pass.
                self.assertLessEqual(row.revision_round, 1)
        self.assertEqual(
            set(review.completed_item_ids),
            {
                "item-000001",
                "item-000002",
                "item-000003",
                "item-000004",
            },
        )
        self.assertIsNone(snapshot.context.recovery.blocked_reason)


if __name__ == "__main__":
    unittest.main()
