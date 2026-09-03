# Orchestrator Context, Reply Correlation, and Incremental Comment Polling

## Status

Approved design for implementation after review of `multica/orchestrator-20260903-13.log`.

## Problem

The last observed run exited as `BLOCKED` after repeatedly cycling between `MENXIA_ITEM_SOLVER` and `MENXIA_ITEM_ANALYST`. The log proves three separate weaknesses:

1. Solver responses were accepted as `FEASIBLE` while all six active Critic findings remained unanswered (`response_field_present=False`, no responded finding IDs).
2. The final Analyst dispatch carried Analyst runtime metadata, but the Agent reported that the target was Solver and returned `BLOCKED`.
3. Polling a shared issue comment stream saw 183 comments, including 130 author mismatches and 50 stale comments, and accepted the final reply through a broad issue/agent/recent fallback.

The fix must prevent semantic context leakage, prevent ambiguous comments from becoming replies, stop non-progressing loops, and reduce normal polling from repeated broad history reads to incremental reads.

## Goals

- Keep every Agent request scoped to its exact task, phase, state, role, group, item, and request ID.
- Accept a reply only when it has an explicit current-request or current-dispatch binding.
- Make Solver progress measurable against the active Critic finding set.
- Detect repeated no-progress revisions with a deterministic terminal reason.
- Read only comments newer than a maintained per-issue/task cursor during normal polling.
- Preserve recovery from process restarts and tolerate comments sharing the same timestamp without loss.
- Keep the active Nexus service on its existing port; integration verification uses port `18766`.

## Non-goals

- Do not modify Codex global configuration or the generated model catalog.
- Do not change the external `multica` CLI in this change; it already supports `--since` but does not expose a numeric `after-index`.
- Do not redesign the Menxia worker concurrency model.
- Do not accept lower-quality replies merely to reduce polling latency.

## Design

### 1. Request-scoped Agent context

`StateContext.request_payload` remains the source of workflow state, but transient rejected replies are stored under a request-scoped history keyed by the full `request_id`. A new request receives only:

- the current task, phase, state, role, group, and item;
- the current proposal/review relevant to that state;
- active Critic findings relevant to the current item;
- the current request's bounded repair feedback, when applicable.

Cross-phase fields such as an old `ZHONGSHU_SOLVER` `last_rejected_reply` are excluded from normal Menxia Analyst and Critic prompts. The transport envelope and the prompt bundle both carry `target_state`, `target_role`, `phase`, and `request_id`. The Agent result must echo those identity fields; a mismatch is a protocol rejection with an explicit diagnostic.

### 2. Strict reply correlation

The normal reply path uses the dispatch trigger comment and `sent_after` cursor. A candidate is accepted only if:

- its payload `request_id` equals the active request ID; or
- its parent/thread binding equals the active dispatch external message ID and the candidate's task, phase, and role are compatible.

The existing `issue+agent+unique_recent_reply` fallback is retained only as a recovery diagnostic. It must not turn an ambiguous or unbound comment into an accepted Agent reply. If explicit binding is absent, the poll records `AMBIGUOUS_REPLY` and remains pending until a bound result file or bound reply arrives.

Result-file recovery remains valid only after checking the expected path, UTF-8/no-BOM contract, SHA-256, task ID, request ID, phase, and role.

### 3. Incremental comment feed cursor

Introduce an issue/task-scoped comment feed with one monotonic cursor:

```text
CommentCursor {
    issue_id: string
    task_id: string
    last_created_at: RFC3339Nano timestamp
    last_comment_id: UUID
}
```

The feed reader is serialized per `(issue_id, task_id)` and stores newly fetched comments in an append-only in-memory cache for active request consumers. Each request keeps its own consumed comment IDs, so one request cannot advance a shared cursor past comments needed by another request.

Normal reads use:

```text
multica issue comment list <issue-id> --since <last_created_at> --output json
```

Because the CLI has no numeric after-index, the reader uses a small timestamp overlap and de-duplicates by comment ID. It advances the cursor only after the response has been parsed and cached. The tie-breaker comment ID is recorded for diagnostics and duplicate suppression. The normal thread path continues to use the active dispatch thread with `--since <sent_after>` and never requests the whole issue history.

The broad `--recent 100` recovery read is removed from normal polling. A bounded bootstrap/recovery read is allowed only when the cursor is absent after process restart or when an operator explicitly requests recovery; it must be marked in logs and must not use fallback acceptance without explicit binding.

### 4. Solver/Analyst progress contract

When active Critic findings exist for an item, a Solver result is valid only when `responses_to_critic` is present and covers every active finding ID. Missing or incomplete responses are rejected as a contract error and returned to the same Solver request for bounded repair.

Each revision records a deterministic progress fingerprint containing the item ID, active finding IDs, proposal hash, response-finding IDs, and action. Repeating the same fingerprint without new evidence increments a no-progress counter. Once the configured item revision budget is exhausted, the FSM enters `BLOCKED` with `NO_PROGRESS` and writes the missing finding IDs, last request ID, and last proposal hash to the lifecycle result.

### 5. Error handling and observability

The implementation logs, without logging reply bodies:

- cursor before/after, fetched count, deduplicated count, and cache count;
- correlation decision and reason for every discarded candidate;
- target identity mismatch fields;
- active finding IDs, responded IDs, and no-progress fingerprint changes;
- whether a read was normal incremental polling or recovery.

No network or file error is converted into `BLOCKED` without preserving the specific error code. Ambiguous comments remain pending; malformed Agent output remains a contract rejection; semantic role mismatch is a distinct `AGENT_TARGET_MISMATCH` rejection.

## Files and responsibilities

- `cmd/orchestrator/states.py`: build state-scoped prompts, identity envelope, and request-scoped repair context.
- `cmd/orchestrator/adapters.py`: strict reply correlation, incremental feed cursor, bounded recovery, and cursor diagnostics.
- `cmd/orchestrator/app.py`: validate Solver finding coverage and record progress fingerprints.
- `cmd/orchestrator/validators.py`: validate echoed request identity and `responses_to_critic` coverage.
- `cmd/orchestrator/transitions.py`: route contract failures and no-progress exhaustion deterministically.
- `cmd/orchestrator/context.py`: store cursor/progress fields needed by persistence and lifecycle output.
- `cmd/test_multica_poll_filter_v31.py` and `cmd/test_reply_fallback_v32.py`: correlation and recovery regressions.
- `cmd/test_solver_contract_fix.py`, `cmd/test_solver_revision_prompt.py`, and new focused tests: finding coverage, context isolation, cursor overlap/deduplication, and no-progress behavior.

## Verification

Unit tests will cover:

- old ZHONGSHU reply present while dispatching MENXIA Analyst;
- explicit current request binding, explicit thread binding, stale comment, wrong author, wrong role, and ambiguous unbound comments;
- same-timestamp comments with overlap and ID de-duplication;
- two active request consumers sharing one issue feed;
- Solver with all, some, or none of the active Critic finding responses;
- repeated identical proposal/finding fingerprints and deterministic `NO_PROGRESS` termination.

Integration verification will start a validation instance on `18766`, replay the relevant final-run fixtures, and confirm that the active service on `8766` is not stopped or rebound.

## Acceptance criteria

- A MENXIA Analyst prompt cannot contain a cross-phase `last_rejected_reply` unless it is explicitly keyed to the current request.
- A reply without current request/thread binding is never accepted through the issue/agent/recent fallback.
- Normal polling does not issue `--recent 100` and reports incremental cursor statistics.
- Solver cannot advance as `FEASIBLE` while active Critic findings remain unaddressed.
- The reproduced scenario terminates with a specific diagnostic rather than an Analyst/Solver loop or role-confusion `BLOCKED` response.
- All existing focused tests and the repository verification gate pass without changing protected Codex files.
