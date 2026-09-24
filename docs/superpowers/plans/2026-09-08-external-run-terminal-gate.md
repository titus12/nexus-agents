# External Run Terminal Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require remote Multica completion before accepting structured result files and prevent duplicate external targets from being dispatched concurrently.

**Architecture:** Add a narrow run-status lookup to `MulticaCliAdapter`, gate only result-file transport on that status, and serialize fan-out workers sharing the same `(issue_id, agent_id)` target. Keep legacy transport and role contracts unchanged.

**Tech Stack:** Python 3, `unittest`, Multica CLI adapter, existing `ParallelCoordinatorDriver`.

---

### Task 1: Add remote lifecycle status lookup

**Files:**
- Modify: `cmd/orchestrator/adapters.py`
- Modify: `cmd/test_agent_result_file_poll.py`

- [x] Add `get_run_status(request)` to the adapter protocol and production adapter. Match the request dispatch comment against `trigger_comment_id`, `coalesced_comment_ids`, and `delivered_comment_ids`; return `completed`, `running`, `failed`, or `unknown`.
- [x] Keep `FakeMulticaAdapter` compatible by returning `completed` for dispatched requests unless a test overrides the status.
- [x] Add tests proving a coalesced run is matched and status is returned.

### Task 2: Gate result-file acceptance on remote completion

**Files:**
- Modify: `cmd/orchestrator/adapters.py`
- Modify: `cmd/test_agent_result_file_poll.py`

- [x] For `structured_output.mode == "result_file"`, query remote status before accepting comment pointers or directory-scan recovery.
- [x] Return no reply while status is `running` or `unknown`, and emit `AGENT_REPLY_WAITING_REMOTE_TERMINAL`.
- [x] Accept the same valid result once status is `completed`.
- [x] Leave non-structured legacy reply parsing unchanged.

### Task 3: Serialize duplicate external targets

**Files:**
- Modify: `cmd/orchestrator/parallel_runtime.py`
- Modify: `cmd/orchestrator/app.py`
- Modify: `cmd/test_parallel_runtime_correlation.py`

- [x] Add an optional coordinator worker-count override.
- [x] In `run_parallel_fanout`, detect duplicate `(issue_id, agent_id)` pairs and set the override to `1`, logging `PARALLEL_SHARED_EXTERNAL_TARGET_SERIALIZED`.
- [x] Add a test that two workers with one external target are dispatched serially while distinct targets remain eligible for parallel execution.

### Task 4: Run focused verification and self-review

**Files:**
- No production files beyond Tasks 1–3.

- [x] Run the result-file polling, adapter correlation, and parallel-runtime test modules.
- [x] Run `compileall` on touched modules and `git diff --check`.
- [x] Inspect the final diff for unintended changes, confirm no skill/contract files were modified, and report any pre-existing unrelated failures separately.
