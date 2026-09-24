# Orchestrator Context, Correlation, and Comment Cursor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Menxia review runs request-safe and convergent while replacing broad issue-comment polling with an incremental, deduplicated cursor.

**Architecture:** Keep the existing FSM and single orchestrator owner. Scope transient reply context and identity to the active request, make correlation strict on request/thread bindings, and put a serialized per-issue/task feed in front of poll consumers. Add a progress contract so Solver cannot claim `FEASIBLE` while active Critic findings remain unanswered.

**Tech Stack:** Python 3, `unittest`, dataclasses, existing `StateContext` persistence, `multica issue comment list --since`, and the repository's existing orchestrator adapters/FSM.

---

## Working-tree safety

The worktree already contains user changes. Do not reset, checkout, clean, or stage unrelated files. Only stage files explicitly listed in each task. Do not modify `C:\Users\Administrator\.codex\config.toml` or `C:\Users\Administrator\.codex\nexus-model-catalog.json`.

### Task 1: Add failing regressions for request identity and context isolation

**Files:**
- Create: `cmd/test_request_context_isolation.py`
- Modify: `cmd/test_multica_poll_filter_v31.py`
- Test: `cmd/test_request_context_isolation.py`, `cmd/test_multica_poll_filter_v31.py`

- [ ] **Step 1: Add a test that an Analyst request excludes an old cross-phase rejected reply.**

Build a `StateContext` containing `last_rejected_reply` with `phase="ZHONGSHU"`, `role="review-solver"`, and an old `ZHONGSHU_SOLVER` request ID. Instantiate the existing `MenxiaItemAnalystState`, call its request-building path, decode the generated prompt JSON, and assert that the prompt does not contain the old reply while the request context still contains `MENXIA`, `MENXIA_ITEM_ANALYST`, and `review-analyst`.

- [ ] **Step 2: Add a test that the transport request declares its target state and role.**

Construct an Analyst request and assert that the serialized dispatch payload contains:

```python
{
    "target_state": "MENXIA_ITEM_ANALYST",
    "target_role": "review-analyst",
    "phase": "MENXIA",
    "request_id": request.request_id,
}
```

- [ ] **Step 3: Add a test that an explicit target mismatch is rejected.**

Pass a reply whose `request_id` matches but whose `target_state` is `MENXIA_ITEM_SOLVER` to `validate_agent_reply`. Assert the result is a rejection with reason `TARGET_STATE_MISMATCH`, and assert that a reply with matching target identity remains valid.

- [ ] **Step 4: Run the new failing tests.**

Run: `rtk python -m unittest cmd.test_request_context_isolation cmd.test_multica_poll_filter_v31`

Expected: the new tests fail because target identity is not yet included/validated and the Analyst prompt still serializes the cross-phase transient field.

### Task 2: Implement request-scoped context and target identity

**Files:**
- Modify: `cmd/orchestrator/models.py`
- Modify: `cmd/orchestrator/context.py`
- Modify: `cmd/orchestrator/states.py`
- Modify: `cmd/orchestrator/adapters.py`
- Modify: `cmd/orchestrator/validators.py`
- Test: `cmd/test_request_context_isolation.py`

- [ ] **Step 1: Add persisted request-scoped transient state.**

Add a `reply_history: dict[str, dict[str, Any]]` field to `StateContext`. When a reply is rejected, store the bounded payload under its full active request ID. Keep the existing lifecycle fields for compatibility, but stop using the unkeyed `request_payload["last_rejected_reply"]` as prompt input for normal requests.

- [ ] **Step 2: Build state-scoped prompts.**

In `BaseState.request`, construct a filtered context for `MENXIA_ITEM_ANALYST` and `MENXIA_ITEM_CRITIC` containing only the current task/group/item, current proposal/review, active findings, and current request-scoped repair feedback. Exclude `last_rejected_reply`, `notification_payload`, and other cross-phase payloads unless they are nested under the active request ID and needed for a repair turn.

- [ ] **Step 3: Add target identity to the Agent request and transport envelope.**

Add `target_state` and `target_role` to `AgentRequest`, populate them from the FSM state and `ROLE_BY_STATE`, and include them in both the prompt-bundle context and the dispatch transport metadata. Preserve existing `phase`, `role`, and `request_id` fields.

- [ ] **Step 4: Validate echoed target identity.**

Extend `validate_agent_reply` and the state payload validation path so an explicitly present `target_state` or `target_role` must equal the active request. Return `TARGET_STATE_MISMATCH` or `TARGET_ROLE_MISMATCH`; missing identity remains inferable only for legacy replies that already have a strong current request/thread binding.

- [ ] **Step 5: Run the context and existing validation tests.**

Run: `rtk python -m unittest cmd.test_request_context_isolation cmd.test_multica_poll_filter_v31 cmd.test_solver_contract_fix`

Expected: PASS, with no change to existing valid reply inference behavior.

### Task 3: Add failing tests for strict correlation and incremental comment polling

**Files:**
- Create: `cmd/test_comment_cursor.py`
- Modify: `cmd/test_multica_poll_filter_v31.py`
- Modify: `cmd/test_reply_fallback_v32.py`
- Test: `cmd/test_comment_cursor.py`, `cmd/test_multica_poll_filter_v31.py`, `cmd/test_reply_fallback_v32.py`

- [ ] **Step 1: Add a test that an unbound root reply is never accepted.**

Return an empty active thread, an unbound root reply from the expected agent, and an execution record with no matching delivered comment. Assert `adapter.poll(request) == []`, assert the fallback result is not accepted, and assert the command invocation is not `--recent 100`.

- [ ] **Step 2: Add cursor tests for timestamp overlap and comment ID deduplication.**

Use two successive comment-list responses containing one repeated comment ID and two new comments with the same `created_at`. Assert that the consumer sees each ID once and that the cursor advances to the greatest timestamp only after the response is parsed.

- [ ] **Step 3: Add a test for independent consumers of one issue feed.**

Create two requests for the same `(issue_id, task_id)` with different dispatch IDs. Feed one combined ordered response. Assert that advancing consumer A does not remove the comment needed by consumer B.

- [ ] **Step 4: Add a test for explicit binding under noisy history.**

Return old comments, wrong-author comments, stale comments, an unbound structured reply, and one current reply carrying the exact request ID. Assert only the current reply is returned and the poll statistics record the discarded categories.

- [ ] **Step 5: Run the new failing tests.**

Run: `rtk python -m unittest cmd.test_comment_cursor cmd.test_multica_poll_filter_v31 cmd.test_reply_fallback_v32`

Expected: the cursor tests fail because no feed cursor exists, and the strict fallback test fails because current recovery can accept a unique unbound candidate.

### Task 4: Implement the incremental per-issue/task comment feed

**Files:**
- Create: `cmd/orchestrator/comment_feed.py`
- Modify: `cmd/orchestrator/adapters.py`
- Modify: `cmd/orchestrator/context.py`
- Modify: `cmd/orchestrator/persistence.py`
- Test: `cmd/test_comment_cursor.py`, `cmd/test_multica_poll_filter_v31.py`, `cmd/test_reply_fallback_v32.py`

- [ ] **Step 1: Implement the cursor and feed cache.**

Create dataclasses with these fields and behavior:

```python
@dataclass
class CommentCursor:
    issue_id: str
    task_id: str
    last_created_at: str = ""
    last_comment_id: str = ""

class IncrementalCommentFeed:
    def read_since(self, issue_id: str, task_id: str, since: str) -> list[dict[str, Any]]: ...
    def consume(self, issue_id: str, task_id: str, consumer_id: str) -> list[dict[str, Any]]: ...
```

Guard each `(issue_id, task_id)` feed with a lock. Keep an append-only bounded cache keyed by comment ID, de-duplicate overlap responses, and keep per-consumer consumed IDs. Advance `last_created_at` and `last_comment_id` only after parsing succeeds.

- [ ] **Step 2: Persist active cursors with the existing state.**

Add `comment_cursors` and bounded consumer state to `StateContext` serialization. Loading an older state with absent fields must create empty defaults. Do not persist full comment bodies in `StateContext`; retain only cursor and IDs needed for deduplication.

- [ ] **Step 3: Use `--since` for normal issue reads.**

Replace broad fallback reads in `MulticaCliAdapter.poll` with the feed. Normal issue reads must use:

```text
issue comment list <issue-id> --since <cursor.last_created_at> --output json
```

The existing active-thread read remains bounded by `--thread`, `--tail 30`, and `--since <request.sent_after>`. The normal path must never call `--recent 100`.

- [ ] **Step 4: Handle the CLI's timestamp-only forward cursor safely.**

When a cursor exists, request a small timestamp overlap and de-duplicate by comment ID. Preserve comments with equal timestamps until every consumer has consumed them. When no cursor exists after restart, perform one explicitly logged bounded bootstrap read; it may use a small `--recent` value but must not accept unbound replies.

- [ ] **Step 5: Remove acceptance through broad fallback.**

Keep execution-record/result-file recovery, but require matching trigger/delivered IDs plus request identity. If root history contains one unbound structured candidate, record `AMBIGUOUS_REPLY` and return no reply instead of rewriting its request ID and accepting it.

- [ ] **Step 6: Add cursor and correlation diagnostics.**

Log only metadata: read mode, cursor before/after, fetched count, deduplicated count, consumer count, correlation decision, and discard reason. Do not log reply bodies or credentials.

- [ ] **Step 7: Run cursor and correlation tests.**

Run: `rtk python -m unittest cmd.test_comment_cursor cmd.test_multica_poll_filter_v31 cmd.test_reply_fallback_v32 cmd.test_parallel_runtime_correlation`

Expected: PASS; normal polling command assertions contain `--since` and no `--recent 100`.

### Task 5: Add failing tests for Solver finding coverage and no-progress termination

**Files:**
- Create: `cmd/test_menxia_progress_guard.py`
- Modify: `cmd/test_solver_contract_fix.py`
- Modify: `cmd/test_solver_revision_prompt.py`
- Test: `cmd/test_menxia_progress_guard.py`, `cmd/test_solver_contract_fix.py`, `cmd/test_solver_revision_prompt.py`

- [ ] **Step 1: Test that missing Critic responses reject Solver `FEASIBLE`.**

Create a context with active findings `finding-1` and `finding-2`. Submit a Solver payload with `action="FEASIBLE"` and no `responses_to_critic`. Assert the state payload validation returns a contract error naming the missing IDs.

- [ ] **Step 2: Test partial response coverage.**

Submit a payload responding only to `finding-1`. Assert it is rejected and the error names `finding-2`; submit both responses and assert validation succeeds.

- [ ] **Step 3: Test deterministic no-progress detection.**

Submit the same item ID, active finding IDs, proposal hash, response IDs, and action for the configured no-progress budget. Assert the FSM emits `BLOCKED` with reason `NO_PROGRESS`, including the last request ID and missing finding IDs.

- [ ] **Step 4: Run the new failing tests.**

Run: `rtk python -m unittest cmd.test_menxia_progress_guard cmd.test_solver_contract_fix cmd.test_solver_revision_prompt`

Expected: the missing/partial coverage tests fail because `FEASIBLE` is currently accepted without `responses_to_critic`.

### Task 6: Implement Solver coverage validation and no-progress guard

**Files:**
- Modify: `cmd/orchestrator/app.py`
- Modify: `cmd/orchestrator/validators.py`
- Modify: `cmd/orchestrator/transitions.py`
- Modify: `cmd/orchestrator/context.py`
- Modify: `cmd/orchestrator/states.py`
- Test: `cmd/test_menxia_progress_guard.py`, `cmd/test_solver_contract_fix.py`, `cmd/test_solver_revision_prompt.py`

- [ ] **Step 1: Validate `responses_to_critic` against active finding IDs.**

In the Menxia Solver contract path, derive active finding IDs from the current item review. For `FEASIBLE`, require a list of response objects whose `finding_id` set covers every active ID. Return a bounded contract error with sorted missing IDs; do not accept a proposal that merely contains `implementation_proposal`.

- [ ] **Step 2: Preserve missing-finding feedback for bounded repair.**

Store the contract error under the current request ID and add only the missing IDs plus the original validated Solver payload to the next Solver repair prompt. Do not append the old cross-phase reply to the next Analyst prompt.

- [ ] **Step 3: Record a progress fingerprint.**

Add fields for the last fingerprint and repeat count to `StateContext`. Hash a canonical JSON object containing `item_id`, sorted active finding IDs, proposal hash, sorted response finding IDs, and action. Reset the repeat count when any component changes.

- [ ] **Step 4: Make no-progress exhaustion deterministic.**

When the repeat count reaches `max_item_revision_rounds`, set `blocked_reason="NO_PROGRESS"`, preserve the last request ID/proposal hash, and transition to `BLOCKED`. Do not ask the Agent to decide whether the loop should continue.

- [ ] **Step 5: Run all FSM/progress tests.**

Run: `rtk python -m unittest cmd.test_menxia_progress_guard cmd.test_solver_contract_fix cmd.test_solver_revision_prompt cmd.test_fsm_core_v31 cmd.test_group_revision`

Expected: PASS, with existing valid Solver proposals still accepted when all active findings are answered.

### Task 7: Add reproduction coverage and run the repository verification gate

**Files:**
- Create: `cmd/test_final_run_regression.py`
- Modify: `cmd/test_full_workflow_v31.py`
- Modify: `cmd/test_menxia_parallel.py`
- Test: `cmd/test_final_run_regression.py`, `cmd/test_full_workflow_v31.py`, `cmd/test_menxia_parallel.py`

- [ ] **Step 1: Add a deterministic fixture for the observed run shape.**

Replay one Solver `FEASIBLE` payload with six unanswered findings, one Analyst request carrying the stale ZHONGSHU reply, a noisy comment feed, and a final correctly bound Analyst reply. Assert the fixed pipeline rejects the incomplete Solver proposal, ignores stale/unbound comments, and never returns the role-confusion `BLOCKED` result.

- [ ] **Step 2: Add a parallel-feed regression.**

Run two request consumers against one ordered issue feed and assert each receives only its own bound reply while the underlying feed performs one incremental read per cursor advance.

- [ ] **Step 3: Run focused tests and inspect command traces.**

Run: `rtk python -m unittest cmd.test_final_run_regression cmd.test_full_workflow_v31 cmd.test_menxia_parallel`

Expected: PASS; test doubles show incremental `--since` calls and no normal `--recent 100` calls.

- [ ] **Step 4: Run the broad verification gate without touching the active service.**

Run the repository gate. Its smoke script allocates and owns an isolated random port in the `8800-8999` range; it does not attach to the active service on `8766`:

```powershell
rtk powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_all.ps1
```

For any live manual API check outside the gate, start the validation service explicitly with `rtk powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_dev.ps1 -Port 18766 -SkipWebBuild` and stop only that validation process after the check. Expected: the verification gate passes, the active service remains available on `8766`, and no protected Codex file changes appear in `rtk git status --short`.

- [ ] **Step 5: Review the final diff.**

Run: `rtk git diff --check -- cmd/orchestrator cmd/test_*.py docs/superpowers`; `rtk git status --short`

Expected: no whitespace errors; only the explicitly implemented files and the already-existing user changes are present.

## Commit checkpoints

Create focused commits only if requested by the user or repository workflow. If committing, stage only the files from the completed task; never stage the pre-existing worktree changes. The design spec is already committed as `4c13008`.
