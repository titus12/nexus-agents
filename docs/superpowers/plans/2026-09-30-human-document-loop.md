# Human Document Loop Implementation Plan

> **For agentic workers:** Execute inline in this authorized session, one mechanism at a time with offline regressions. Preserve unrelated working-tree changes.

**Goal:** Loop human feedback through LLM revision and review until the user replies exactly `通过`, then hand over the exact reviewed requirements document.

**Architecture:** Persist one immutable versioned review packet in ReviewState. Human feedback uses a dedicated full-plan Solver dispatch; ordinary Critic revisions retain scoped behavior. Hash checks bind approval and Menxia input to the stored document, and marked group chapters support existing acceptance-map checks.

### User correction: Solver-only human revision

The user explicitly requires human-requested revisions to skip Critic because the human will review them. Implement inline in the existing checkout, preserving unrelated changes.

- [x] Add the internal `ZHONGSHU_SOLVER -> PLAN_REVIEW -> HUMAN_GATE` edge; keep ordinary `READY_FOR_CRITIC` unchanged.
- [x] After successful full-plan/group-document materialization with pending human feedback, fold the new graph and exact document bytes, run mechanical dependency/form/version checks, freeze eligible groups without inventing Critic ledger approvals, and reuse canonical packet publication directly.
- [x] Verify every successful human round adds exactly one Solver dispatch and zero Analyst/Critic/freeze-check dispatches, including task addition/regrouping/restart/late feedback and malformed reply retries. Verify initial Critic still runs.
- [x] Update local Solver/Critic instructions and Zhongshu skills to the direct-human path, sync Multica with idle checks and independent read-back, and run notification-isolated full regressions.

**Tech Stack:** Python dataclasses/unittest, scripted Multica and Feishu ports, existing CLI asset synchronizer.

---

### Task 1: Document identity and persistence

Files: `cmd/orchestrator/domain/context.py`, new `cmd/orchestrator/domain/plan_review.py`, `cmd/orchestrator/zhongshu_plan_doc.py`, `cmd/orchestrator/runtime/plan_effects.py`.

- [x] Add `ReviewState.plan_review: dict[str, object]` and matching nullable ReviewUpdate field; preserve through DTO/reducer.
- [x] Build the artifact from exact group chapters, graph hash and task table; persist markdown/hash/version/group hashes. Publish only `zhongshu-requirements.md` with immutable version snapshots.
- [x] Make notification and Menxia document reads use stored bytes. For group acceptance checks, extract the marked group chapter rather than parsing all groups together.

### Task 2: Exact human decision and full feedback

Files: `cmd/orchestrator/app.py`, `cmd/orchestrator/transport/external.py`, `cmd/orchestrator/adapters.py`, `cmd/orchestrator/domain/states.py`, `cmd/orchestrator/domain/plan_review.py`.

- [x] Implement approval as `str(answer or '').strip() == '通过'`. Empty input waits; all other text causes PLAN_REVIEW_REJECTED.
- [x] Add optional external message id to HumanReply. Preserve ordered complete feedback messages and reviewed decision id/version; deduplicate ids.
- [x] Revoke approval and unexecuted Menxia rows, reset machine convergence/ledger, and dispatch a real Solver request immediately from the feedback transition.
- [x] Check current document/graph/group hashes at approval; stale approval does not enter Menxia. A new offered packet replaces the old gate identity.

### Task 3: Solver revision and human handoff

Files: `cmd/orchestrator/domain/zhongshu/solver.py`, `cmd/orchestrator/domain/states.py`, `cmd/orchestrator/domain/menxia_doc.py`, `cmd/orchestrator/notifications.py`.

- [x] Prioritize `human_plan_revision` dispatch over scoped group/item revision while feedback is pending. Supply full prior document, full current graph, authoritative group docs, original requirements and full current feedback.
- [x] Validate its full-plan reply using FormalizeSolverLogic and normal form/structure/projection checks; clear pending only after successful materialization. For human-requested revisions, return directly to the human after mechanical checks, without Critic.
- [x] Freeze dependency-ready groups to closure before publishing the complete human gate. Hold every Menxia dispatch until current document approval, including fast/mechanical paths.
- [x] Feed the stored full artifact to Menxia and expose its version/hash in the worker bundle. Update all gate prompts to the exact `通过` semantics.

### Task 4: Regression and Multica consistency

Files: new `cmd/test_human_document_loop.py`, affected existing gate/transport tests, local runtime instructions/skills as needed.

- [x] Run `rtk proxy python -X utf8 cmd/run_tests.py test_human_document_loop test_plan_review_gate test_human_gate_reply_poll` while developing; expected zero external notifications and real FSM scripted execution.
- [x] Test repeated revisions and final pass with `app.engine.dispatch(event); app.effects.execute_pending(task_id)`, comparing sent packet bodies and Menxia `requirement_markdown` byte-for-byte.
- [x] Run freeze/convergence/entrypoint/group/transport regressions and the full suite through `cmd/run_tests.py`; save results under `runs/maintenance/20260930-human-document-loop/`.
- [x] Run `cmd/sync_multica_review_agents.py --check`; apply and verify any changed agent/skill assets only after preflight, preserving model/runtime bindings. Report new-process activation and current live-run limitations accurately.

## Validation record

- Final full suite: 1261 tests, 47.186 seconds, OK. Log: `runs/maintenance/20260930-human-document-loop/full-suite.log`; execution metadata: `verification.json` in that directory.
- Targeted loop/gate/poll regressions: 30 tests, OK. Covers long full feedback, repeated revision, task addition, regrouping, dependency freeze closure, current approval bytes, restart, late thread supplements, dedup, stale graph/document approval and punctuation preservation.
- Multica apply: `runs/maintenance/multica-agent-sync-20260930T100115Z/verification.json`. Independent check: `runs/maintenance/multica-agent-sync-20260930T100259Z/verification.json`; all three agents have no drift, Zhongshu/Menxia skill bodies match local sources, protected model/runtime/bindings unchanged.
- Production forces the human gate on; explicit gate-off remains only for notification-isolated scripted fixtures. The active 8766 service and existing live workflow process were not restarted or rewound. Code behavior applies to newly started orchestrator processes; remote policies were published and read back.

## Solver-only correction validation

- Final full suite: 1262 tests in 43.487 seconds, OK; log: runs/maintenance/20260930-human-solver-only/full-suite.log.
- Targeted regressions: 38 tests in 17.201 seconds, OK. Successful human revisions add only one Solver dispatch; malformed replies retry Solver with the complete feedback preserved. Initial Critic still runs.
- Human regrouping keeps mechanically frozen new groups without fabricating Critic ledger approvals. Exact pass and canonical document identity remain required.
- Multica apply: runs/maintenance/multica-agent-sync-20260930T102722Z/verification.json. Independent check: runs/maintenance/multica-agent-sync-20260930T102815Z/verification.json. All three agents and bound skills have no drift; protected configuration unchanged.
- Existing live processes and port 8766 were not restarted. Code changes apply to newly started orchestrator processes.
