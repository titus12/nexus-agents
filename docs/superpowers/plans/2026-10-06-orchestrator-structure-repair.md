# Orchestrator structure repair implementation plan

> **For agentic workers:** Execute this approved first phase inline, task by task. User authorization is already recorded; do not create another chat or delegate work.

**Goal:** Stop the RTC failure loop by rejecting oversized plans consistently and giving regrouping repairs authority over group topology while preserving task content and human approval.

**Architecture:** A pure `plan_structure` policy owns blocking structural errors and regrouping eligibility. Solver dispatch selects a dedicated complete-plan regrouping channel before local worker fan-out. Entry, worker joins, preflight, and freeze use the same blocking policy. Existing transport, persistence, review, and approval mechanisms remain in place.

**Tech Stack:** Python dataclasses, current linear FSM, unittest through the notification-safe runner.

---

### Task 1: Incident-shaped regression and shared policy

**Files:** Create `cmd/test_plan_structure_repair.py`, `cmd/orchestrator/domain/policies/plan_structure.py`; modify `cmd/orchestrator/domain/policies/solver_plan.py` and `cmd/orchestrator/runtime/agent_effects.py`.

- [x] Create a self-contained 10-item fixture with `GRP-001` containing eight members and `GRP-002` containing two, matching the saved RTC incident topology. Do not depend on ignored run files or copy prompts, credentials, and workspace paths.
- [x] Assert the blocking gate includes `GROUP_MAX_ITEMS:GRP-001:8`, preflight reports the same error, and a rejected first formalization remains uncommitted and retryable.

```python
self.assertIn("GROUP_MAX_ITEMS:GRP-001:8", structural_integrity_errors(plan))
self.assertTrue(is_retryable_solver_reply_error(
    "SOLVER_PLAN_STRUCTURE_INVALID:GROUP_MAX_ITEMS:GRP-001:8"))
```

- [x] Run `rtk proxy python -X utf8 cmd/run_tests.py test_plan_structure_repair`; expect failing assertions before implementation.
- [x] Move the current blocking prefixes and unknown-group-member check into `blocking_structure_errors(plan)`, adding `GROUP_MAX_ITEMS`. Keep requirement coverage advisory. Keep `structural_integrity_errors` as a compatibility facade.

```python
def structural_integrity_errors(plan):
    return blocking_structure_errors(plan)
```

- [x] Change group/item join and salvage to this facade alone, removing the additional unfiltered structural gate.
- [x] Run the focused suite; expect shared-policy tests to pass.

### Task 2: Regrouping authority and protected content

**Files:** Modify `cmd/orchestrator/domain/zhongshu/solver.py`, `cmd/orchestrator/domain/states.py`, `cmd/orchestrator/domain/transitions.py`; extend `cmd/test_plan_structure_repair.py`.

- [x] Test that a persisted invalid plan with findings only in `GRP-002` dispatches `plan_structure_repair` with complete-plan transport, structural errors, all existing items, and explicit protected fields, rather than a `GRP-002` worker patch.
- [x] Test successful splitting of `GRP-001`, preservation of every task and requirement, rejection of changed objectives/dependencies/deleted tasks, and protection of frozen groups. Test changed-group approval invalidation while an untouched peer retains its document and approval.
- [x] Add `group_repair_errors(plan)` for group size, group ID, and projected group cycles. Evidence-only Analyst packets do not trigger it.
- [x] Add a Solver logic branch that accepts a full plan only for a currently invalid group topology. Compare item maps after removing only `group_id`; compare plan fields after removing only `items` and `groups`. Preserve frozen groups exactly. Require documents only for new/changed groups; carry unchanged documents and rows forward. Remove retired group rows and clear approval for changed rows.
- [x] Build the regrouping dispatch before ordinary revision/fan-out. Include `expected_reply_mode=TASK_GRAPH_FORMALIZATION_READ_ONLY`, `structure_issues`, `current_formal_plan`, requirement contract, authoritative documents, and permission to change group membership only. Preserve human-feedback precedence.
- [x] Add the closed `REQUEST_STRUCTURE_REPAIR` edge from Solver, Critic, and Freeze Check to Solver. For in-flight local failures against an already invalid base, route once into regrouping without consuming the generic node failure streak. Invalid regrouping replies use the existing bounded reply retry budget.
- [x] Run `rtk proxy python -X utf8 cmd/run_tests.py test_plan_structure_repair test_solver_group_doc_scope test_solver_batch_handoff`; expect pass.

### Task 3: Freeze defense and complete validation

**Files:** Modify `cmd/orchestrator/domain/states.py`; extend `cmd/test_plan_structure_repair.py`; add validation results to `docs/multi/runtime/orchestrator-structure-repair-validation-20261006.md`.

- [x] Assert mechanical freeze rejects the eight-member topology even after all groups receive fresh approvals, and freeze routes invalid topology into repair even with a previously frozen group or fast track.
- [x] Validate joined plans again at Solver state entry. Validate group topology before Critic approval and full blocking structure before Freeze approval; preserve the original cycle check for legacy projections.
- [x] Recognize unfit groups using actual IDs rather than the literal `group-` prefix. Preserve the existing document recovery fallback in this phase; do not broaden this repair channel to document-only or arbitrary business-content revisions.
- [x] Run the focused regressions, then `rtk proxy python -X utf8 cmd/run_tests.py` with filesystem approval if TemporaryDirectory permissions require it. Test runner blocks external notifications; no live model invocation or restart of port 8766.
- [x] Run `rtk git diff --check`. Record actual test totals and remaining stages: canonical PlanRevision, artifact-centered workflow, runtime batch separation, and migration. Do not claim model stability or an RTC code-review verdict from structural tests.
- [x] Review only this phase's changed files. Keep unrelated root JSON/log/scratch files excluded from any commit.
