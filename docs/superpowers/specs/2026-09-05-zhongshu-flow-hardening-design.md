# Zhongshu Flow Hardening Design

**Goal:** Make the Zhongshu requirement-to-task workflow deterministic, recoverable, and semantically safe under parallel Analyst/Critic execution.

**Scope:** `cmd/orchestrator` only. The existing three-role model remains: Analyst discovers requirements and candidate tasks, the single Solver owns the formal task graph, and parallel Critics independently challenge that graph.

## Confirmed failure modes

- The Analyst worker prompt uses `TASK_PROPOSALS_READY` and the contract worker uses `REQUIREMENT_CONTRACT_READY`, while the transport response contract advertises only `READY_FOR_SOLVER`.
- Parallel fan-in rejection reasons are returned as FSM events without being persisted as repair feedback.
- Parallel Critic quorum is counted before strict Zhongshu Critic validation and does not require independent semantic results.
- Parallel Analyst prompts discard Critic feedback and repair context.
- Solver validation preserves requirement IDs but does not preserve the complete Analyst candidate-item set.
- Explicit unknowns are described as acceptable in prompts but are not represented in coverage evaluation.
- Findings omitted by a later Critic response remain active, and any active finding currently blocks freeze regardless of severity.
- Zhongshu stage results are written but not loaded before a restart redispatch.

## Design

### 1. One runtime contract per dispatch mode

Extend the transport contract builder with explicit Zhongshu Analyst modes:

- `contract_mode`: `REQUIREMENT_CONTRACT_READY`, with `requirements` and `kind` declared.
- `task_discovery_mode`: `TASK_PROPOSALS_READY`, with `lens`, `requirements`, `task_proposals`, `constraints`, `conflicts`, and `unknowns` declared.
- canonical full-plan mode: `READY_FOR_SOLVER`, retained only for non-parallel legacy/full-plan replies.

Declare `reviewed_plan_hash` and `findings` as required for successful parallel Zhongshu Critic replies. The local validator and transport contract must use the same action/field set.

### 2. Semantic fan-in and repair feedback

Add an app-owned helper for parallel failures. It will persist a bounded error record containing the fan-in reason, phase/state, worker IDs, and rejected worker payloads into `last_error` and `reply_history`, then return the normal retry event. Repair-prompt construction will accept this fan-in error class.

Critic fan-in will validate each result against the current plan revision and canonical plan hash before it becomes quorum-eligible. Quorum will require at least two valid results and at least two distinct semantic fingerprints after transport metadata is removed. The aggregate remains conservative: any unresolved material disagreement or P1 finding prevents freeze.

Malformed Critic fields will be rejected as worker failures; resolver exceptions will be converted into the same structured fan-in failure path instead of escaping the FSM.

### 3. Preserve context and task coverage

Parallel Analyst prompts will retain the current Critic review, repair feedback, active skill metadata, and requirement contract while adding the worker lens. The prompt will not silently replace the authoritative context.

Solver validation will require every Analyst candidate item ID to appear in the formal Solver plan, while still allowing the Solver to enrich task fields and regroup items. An executable requirement with an explicit unknown will be represented as an evidence gap and routed to a bounded evidence/human decision path; it will not be misreported as an ordinary missing task.

### 4. Safe Finding lifecycle and freeze gate

Critic results will be treated as complete snapshots for the current Zhongshu review. Existing findings omitted from a snapshot will not be silently closed; the reply will be rejected as incomplete and the prior finding list will be supplied for explicit resolution.

Freeze blocking will use only active P0/P1 findings. P2/P3 findings remain durable follow-up risk unless the Critic explicitly escalates them.

### 5. Restart reuse and Skill binding

Before dispatching Zhongshu workers, load the matching durable stage result for the same phase, revision, worker ID, request identity, and plan hash, and revalidate it. Only missing or invalid results are dispatched again.

Prompt bundles will carry the active composite Skill identity, source/version/hash metadata, and runtime contract metadata. A missing or mismatched binding will fail closed as a structured dispatch error.

## Non-goals

- No rewrite of the parallel coordinator.
- No parallelization of the single Zhongshu Solver; its single formalization pass remains the quality boundary.
- No automatic closure of findings without explicit evidence or resolution.
- No Feishu notification changes and no external message sending as part of this implementation.

## Verification policy

The implementation will be reviewed statically in this turn. No automated tests, service startup, or external notification will be run unless separately requested.
