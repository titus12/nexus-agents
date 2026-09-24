# Zhongshu Solver Evidence Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route unresolved ZHONGSHU Solver findings to Analyst instead of allowing `READY_FOR_CRITIC`.

**Architecture:** Normalize a structurally valid Solver revision before plan validation. Preserve the canonical plan, change only the workflow action to `REQUEST_ANALYST_EVIDENCE`, and let the existing transition policy route to Analyst. Store the normalized plan and feedback marker in the request context.

**Tech Stack:** Python 3, unittest, existing FSM and JSON payload contracts.

---

### Task 1: Add regression tests

**Files:**
- Modify: `cmd/test_solver_contract_fix.py`
- Modify: `cmd/test_fsm_core_v31.py`

- [ ] Add a Solver revision test with an `unresolved` finding resolution and assert normalization returns `REQUEST_ANALYST_EVIDENCE` while retaining the current plan.
- [ ] Add a resolved finding test asserting normalization keeps `READY_FOR_CRITIC`.
- [ ] Run the focused tests and confirm the new unresolved test fails before implementation.

### Task 2: Normalize unresolved Solver revisions

**Files:**
- Modify: `cmd/orchestrator/states.py`
- Modify: `cmd/orchestrator/app.py`

- [ ] In `states.py`, detect required finding resolutions whose status is not `resolved`.
- [ ] For a valid revision response, set action to `REQUEST_ANALYST_EVIDENCE` before materializing the canonical plan and attach the finding IDs as routing diagnostics.
- [ ] In `app.py`, preserve the normalized candidate plan for the Analyst route and mark the stored Critic review as `REQUEST_ANALYST_EVIDENCE` so the Analyst prompt includes the evidence gap.

### Task 3: Verify the workflow contract

**Files:**
- No new files.

- [ ] Run the focused Solver/FSM tests.
- [ ] Run `rtk python cmd\run_core_tests.py` using the repository’s safe test entry point.
- [ ] Review the diff and confirm no protected Codex configuration files were touched.
