# Zhongshu Analyst/Solver Hardening Design

**Goal:** Preserve all decision-relevant Analyst evidence across parallel workers and make Solver evidence requests, output bounds, and parallel-stage timing deterministic.

## Design

1. Treat each Analyst worker's `evidence_id` as local to that worker. The merged packet keeps the local ID, source worker IDs, and every conflicting observation; the Solver projection must include compact conflict variants instead of silently dropping them.
2. Keep `questions_for_solver` out of the open-ended planning path, but preserve a bounded structured `evidence_requests` list containing requirement/item scope, missing fact, and blocking reason. A Solver evidence request must carry an explicit scope and is rejected before dispatch when it cannot be routed.
3. Enforce Analyst limits in the orchestrator: at most six updates per worker, at most twelve merged canonical updates, and every update must carry a source and decision relevance. The error is deterministic and repairable.
4. Apply a stage deadline to the sequential requirement-contract call plus the parallel evidence fan-out. Worker timeouts remain 15 minutes by default, while the stage cannot run indefinitely past its configured deadline.
5. Preserve the current role protocols and Solver's single-owner model. No implementation details or task-generation authority are added to Analyst.

## Acceptance criteria

- Same local `ev-001` from different workers never causes one conclusion to hide another.
- A blocking evidence request reaches Solver/Analyst with explicit scope or is rejected with a stable protocol error.
- Excess Analyst output is rejected before Solver prompt construction.
- A stalled Analyst stage exits through the existing FSM error/retry path at the stage deadline.
- Existing state, result-file, request-correlation, and task-graph protocols remain unchanged.
