# Orchestrator Deterministic Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Execute the tasks inline with focused regression checks.

**Goal:** Harden the orchestrator's deterministic recovery, state-transition, result-integrity, path-boundary, comment-cursor, and lease behaviors without changing the pending human-approver policy.

**Architecture:** Keep the existing FSM and parallel coordinator interfaces. Add validation at the transition boundary, route recovery through the App-owned dispatcher, preserve immutable finding severity, and make transport/cursor/lease behavior deterministic. Each fix is covered by a focused regression test.

**Tech Stack:** Python 3, `unittest`, JSON persistence, `ThreadPoolExecutor`, Multica/Feishu adapters.

---

### Task 1: Recovery and transition safety

**Files:**
- Modify: `cmd/orchestrator/app.py`
- Modify: `cmd/orchestrator/transitions.py`
- Modify: `cmd/orchestrator/state_machine.py`
- Test: `cmd/test_fsm_core_v31.py`

- [ ] Route `_ensure_started()` through the App dispatcher so resumed parallel states retain fan-out behavior.
- [ ] Reject dynamic transition targets that are not registered or not allowed for the source state before mutating/persisting the context.
- [ ] Add tests for restart dispatch routing and invalid target rejection.

### Task 2: Critic result integrity

**Files:**
- Modify: `cmd/orchestrator/models.py`
- Modify: `cmd/orchestrator/states.py`
- Modify: `cmd/orchestrator/app.py`
- Test: `cmd/test_solver_contract_fix.py`

- [ ] Require stable finding identity and decision/severity fields for approving Critic results.
- [ ] Preserve an existing finding's severity when an update omits it; never default an existing P0/P1 to P2.
- [ ] Add tests for missing finding identity and severity downgrade attempts.

### Task 3: CLI and transport correctness

**Files:**
- Modify: `cmd/orchestrator/app.py`
- Modify: `cmd/orchestrator/adapters.py`
- Modify: `cmd/orchestrator/comment_feed.py`
- Test: `cmd/test_comment_feed_v32.py`
- Test: `cmd/test_fsm_core_v31.py`

- [ ] Constrain task selectors to the configured runs root.
- [ ] Select the newest valid reply after a batch poll instead of consuming only the oldest reply.
- [ ] Compare reply timestamps chronologically and retain deterministic cursor ordering when pruning IDs.

### Task 4: Lease lifetime

**Files:**
- Modify: `cmd/orchestrator/parallel_runtime.py`
- Modify: `cmd/orchestrator/concurrency.py`
- Test: `cmd/test_parallel_runtime_correlation.py`

- [ ] Add lease refresh support and refresh while a worker remains within its total deadline.
- [ ] Ensure an expired lease cannot overlap an active worker execution.

### Task 5: Verification

- [ ] Run focused regression tests.
- [ ] Run Python compilation and `git diff --check`.
