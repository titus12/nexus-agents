# Zhongshu evidence-driven task convergence

## Goal

Make Zhongshu converge deterministically: Analysts collect evidence only, one Solver builds the candidate task graph, Critics review each task exactly once per pass through a leased item queue, and evidence requests return only to the affected task before Solver revision.

## Design constraints

- Preserve the existing finite-state-machine stages and each role's own response protocol.
- Do not merge the three role protocols into one schema.
- Keep task identity stable across Solver revisions; only the Solver may create, remove, regroup, or semantically merge tasks.
- Critic fan-in is task coverage, not cross-worker quorum. A task is reviewed by one leased worker at a time.
- Analyst evidence fan-in is affected-item coverage. No fixed global three-way quorum is used for evidence supplementation.
- Never silently merge semantic duplicates after Critic approval. Exact canonicalization is safe; semantic duplicates must return to Solver and be reviewed again.
- No service start, external Agent dispatch, or Feishu notification is part of implementation verification.

## Implementation tasks

### 1. Stabilize the Analyst contract as evidence-only

Files: `cmd/orchestrator/states.py`, `cmd/orchestrator/zhongshu_review.py`, `cmd/orchestrator/transitions.py`, `cmd/orchestrator/zhongshu_parallel.py`, `cmd/orchestrator/app.py`.

- Add an explicit initial evidence-collection action and protocol mode.
- Remove task proposal fields from the Analyst initial response contract and prompt.
- Merge Analyst outputs into an evidence packet containing requirements, facts, observations, risks, unknowns, constraints, and provenance, without candidate tasks or groups.
- Keep requirement-contract validation authoritative and preserve all evidence source identities.
- Route the evidence packet to Solver, which becomes the only task-graph producer.

### 2. Make evidence supplementation item-scoped

Files: `cmd/orchestrator/app.py`, `cmd/orchestrator/states.py`, `cmd/orchestrator/zhongshu_parallel.py`, `cmd/orchestrator/zhongshu_review.py`.

- Carry `affected_item_ids`, `finding_ids`, and the evidence questions from Critic into the Analyst request.
- Dispatch one evidence job per affected item/finding bundle, bounded by the existing parallel worker limit.
- Validate that every supplement result is for its leased item and cannot mutate tasks/groups.
- Fan in by complete item coverage; missing or malformed coverage cannot be approved.
- Merge updates into the Analyst evidence packet and return to Solver without rebuilding or multiplying the task graph.

### 3. Keep Solver and Critic convergence bounded

Files: `cmd/orchestrator/states.py`, `cmd/orchestrator/app.py`, `cmd/orchestrator/zhongshu_review.py`.

- Make Solver prompts consume the evidence packet and explicitly own task creation and revision.
- Preserve the full current plan during revision, while requiring finding resolutions for every affected item.
- Reject out-of-scope Solver changes when a revision is item-scoped unless the response explicitly requests regrouping.
- Keep the existing unique task-review queue and ensure Critic evidence requests identify their item scope.

### 4. Add safe final canonicalization before Menxia

Files: `cmd/orchestrator/app.py`, `cmd/orchestrator/states.py`, `cmd/orchestrator/zhongshu_parallel.py`.

- Canonicalize ordering, duplicate references, and exact duplicate records before freeze.
- Detect semantic duplicate task items without silently merging them.
- Route detected semantic duplicates back to Solver for an explicit merge decision and re-review; only a fully reviewed canonical graph can reach Menxia.

### 5. Update notification-free regression tests

Files: `cmd/test_zhongshu_task_graph.py`, `cmd/test_analyst_contract_v31.py`, `cmd/test_solver_revision_prompt.py`, plus a focused evidence-routing test if needed.

- Replace Analyst task-proposal expectations with evidence-only expectations.
- Add tests for item-scoped supplement dispatch and coverage validation.
- Add tests proving one Solver owns task creation and exact/semantic canonicalization behavior.
- Run only pure local tests; do not start the service and do not exercise Feishu notification paths.

## Verification

Run the focused notification-free unit tests with the repository's test runner. Inspect the diff and run static compilation/import checks only; no network, service, or Feishu calls.
