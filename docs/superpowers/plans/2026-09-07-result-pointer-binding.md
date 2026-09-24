# Result Pointer Binding Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make concurrent result-file replies fail closed when their request identity and result path do not belong to the same request.

**Architecture:** Keep `PromptBundleBuilder` as the source of the canonical request-scoped result path. Add a path-equivalence guard in `MulticaCliAdapter.poll()` before `read_agent_result_file()` is called. The existing result-file identity, hash, schema, state, and role checks remain the second validation layer.

**Tech Stack:** Python 3, `pathlib`, `unittest`, existing Orchestrator transport adapter.

---

### Task 1: Add canonical pointer-path validation

**Files:**
- Modify: `cmd/orchestrator/adapters.py` in `MulticaCliAdapter.poll()`'s result-pointer branch.
- Test: `cmd/test_agent_result_file_poll.py`.

- [x] **Step 1: Add a path-equivalence helper in the adapter.**

Normalize both paths with `Path.expanduser().resolve()` and `os.path.normcase(str(path))` so Windows path casing and separator differences do not create false mismatches.

- [x] **Step 2: Validate the pointer path before file I/O.**

Derive the expected path with `self.prompt_bundle_builder.result_path(request.task_id, request.request_id)`. If the pointer path is not equivalent, increment `request_mismatch`, log `STALE_RESULT_POINTER` with expected and actual paths, and continue without calling `read_agent_result_file()`.

- [x] **Step 3: Keep the existing identity and hash checks.**

Only the canonical path may reach `read_agent_result_file()`. Do not rewrite a bad pointer path and do not weaken the embedded `request_id` or `result_sha256` checks.

- [x] **Step 4: Add the cross-worker regression test.**

Create a valid result under `task-1/request-3/result.json`, send a pointer claiming `request_id=req-1` while naming that path, and assert that polling returns no message. Also assert that the current request's canonical path is not silently substituted.

- [x] **Step 5: Add the positive-path regression test.**

Create the result under the canonical path for `req-1`, send a pointer naming that path, and assert the result is accepted.

### Task 2: Verify path isolation at the bundle boundary

**Files:**
- Test: `cmd/test_prompt_bundle.py`.

- [x] **Step 1: Add a distinct-request path test.**

Build or query canonical paths for two request IDs under the same task and assert that the paths are different and each ends in its own request directory.

- [x] **Step 2: Run focused static/test verification.**

Run the focused Python unit tests for prompt bundles and result-file polling. Do not start the service or send external notifications.

### Task 3: Review the patch for scope and fail-closed behavior

**Files:**
- Review only: `cmd/orchestrator/adapters.py`, `cmd/test_agent_result_file_poll.py`, `cmd/test_prompt_bundle.py`.

- [x] **Step 1: Confirm no other working-tree changes are overwritten.**

Inspect the diff limited to the files above and the two new design documents.

- [x] **Step 2: Confirm the rejected pointer is never read.**

Review the control flow so the path mismatch branch precedes `read_agent_result_file()` and returns to the next comment.
