# Canonical PlanRevision implementation plan

> **For agentic workers:** Execute the already approved second stage inline. The user requested continuation and a separate self-review after implementation.

**Goal:** Introduce an immutable structured plan revision and one commit boundary for opt-in new tasks, with derived documents and strict version/projection consistency.

**Architecture:** Keep the current transport, state machine, review ledger, and human approval policy. Add `domain/planning/model.py`, `compiler.py`, and `render.py`. `apply_review_update` is the sole canonical commit boundary; both speculative next-dispatch views and the reducer use it. Existing snapshots default to `legacy-v1`, and new tasks explicitly select `canonical-v1` through the CLI. Canonical Solver uses one writer so legacy document-producing worker joins cannot write new plans.

**Tech Stack:** Python immutable dataclasses, canonical JSON/SHA-256, current typed ReviewUpdate, unittest and scripted OrchestratorApp adapters.

### Task 1: Immutable model and compiler

Files: create `cmd/orchestrator/domain/planning/{__init__,model,compiler}.py`, `cmd/test_canonical_plan_revision.py`.

- [x] Write failing tests for isolated data ownership, stable content identity, stale base rejection, unchanged-plan idempotency, duplicate IDs, immutable requirements, frozen work, and common structural gates.

```python
revision = commit_plan(None, draft, expected_ref="")
copy = revision.plan
copy["items"][0]["objective"] = "tampered"
assert revision.plan["items"][0]["objective"] != "tampered"
assert commit_plan(revision, revision.plan, expected_ref=revision.reference) == revision
```

- [x] Run `rtk proxy python -X utf8 cmd/run_tests.py test_canonical_plan_revision`; expect unavailable model/compiler imports before implementation.
- [x] Implement `PlanRevision` with schema/policy versions, ordinal, parent reference, canonical serialized structured data, hash, and a validated DTO. Hash excludes revision metadata; the reference binds ordinal and content.
- [x] Implement `compile_plan` to validate arrays/IDs/membership, fill derived group ownership, require explicit structured acceptance signals, preserve all extra structured business fields, and apply phase-one blocking rules.
- [x] Implement `commit_plan(current, draft, expected_ref, requirements=None, frozen_group_ids=())`: reject stale/missing base, changed requirement baseline or frozen work, validate the full candidate, reuse unchanged revisions, otherwise increment ordinal and bind the parent reference.

### Task 2: Derived documents and one write boundary

Files: create `cmd/orchestrator/domain/planning/render.py`; modify `cmd/orchestrator/domain/context.py` and `cmd/orchestrator/domain/zhongshu/solver.py`.

- [x] Test that supplied Markdown cannot change canonical acceptance signals and canonical first formalization does not require group documents.
- [x] Render the current nine-section requirement documents from structured group/task/requirement records. §8 uses the canonical member signals verbatim. Document source hashes use only relevant group inputs, allowing unchanged peers to retain document bytes and approval.
- [x] Add persisted `plan_schema` and `plan_revision`, plus the internal expected base reference on ReviewUpdate. At `apply_review_update`, canonical plan writes call `commit_plan` and regenerate plan/hash/task projections/documents together. Reject projection-only business changes. Keep legacy paths unchanged.
- [x] Round-trip canonical revision DTOs and reject mismatched mirrors/hashes or unknown schema/policy versions. Old DTOs without schema remain legacy and are not converted.

### Task 3: Dispatch, result binding, and approvals

Files: modify `cmd/orchestrator/app.py`, `cmd/orchestrator/domain/states.py`, `cmd/orchestrator/runtime/agent_effects.py`, `cmd/orchestrator/domain/plan_review.py`, `cmd/orchestrator/contracts/zhongshu_solver.py`.

- [x] Add `--plan-schema {legacy-v1,canonical-v1}` for new tasks only. Persist the selection; resuming tasks uses saved policy. Reject attempts to change saved schema.
- [x] Route canonical Solver through single-writer dispatch. State the canonical document policy in context and prompt; send the current revision reference. Transport stamps the trusted dispatched base onto normal and recovered results, overriding model-supplied identity claims.
- [x] Validate base identity before processing canonical Solver replies. Scope existing-plan revisions to the dictated findings; maintain separate human-feedback and regrouping authority. Treat invalid reply shape/version as bounded retries, preserving current canonical state.
- [x] Bind human review packets to the canonical revision reference as well as their exact Markdown/hash. Preserve existing approval placement and checks.

### Task 4: Application validation and final self-review

Files: extend `cmd/test_canonical_plan_revision.py`; create `docs/multi/runtime/canonical-plan-revision-validation-20261006.md`.

- [x] Drive the actual OrchestratorApp from request intake through canonical formalization, derived documents, review, human gate, and final plan. Drive revision/restart and stale-result cases with zero models and no external notifications. Bound application pumps so a broken test fails rather than waiting indefinitely.
- [x] Run focused canonical/legacy/FSM regressions, then `rtk proxy python -X utf8 cmd/run_tests.py` with approved filesystem access for Windows temporary directories. Do not restart Nexus 8766 or run live models.
- [x] Self-review the final diff separately: enumerate every write path; test evidence-only updates after a formal plan; check version bypasses, immutable ownership, peer approval reuse, human-feedback precedence, saved-schema recovery, and artifact/dispatch identities. Fix findings and rerun affected checks before committing.
- [x] Run `rtk git diff --check`; save concrete findings, repairs, test totals, and remaining phase-three boundary. Commit only this stage's code/tests/docs; exclude root scratch files, logs, run data, and protected Codex configuration.
