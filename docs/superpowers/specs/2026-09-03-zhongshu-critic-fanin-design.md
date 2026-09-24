# Zhongshu Critic fan-in and Solver contract recovery

## Problem

The Zhongshu parallel Critic workers returned valid replies containing review
findings and `REQUEST_SOLVER_REVISION`, but parallel fan-in preserved duplicate
findings from all three workers. The resulting 21-entry revision context
expanded the Solver prompt past the compacting threshold. The compact Solver
template then showed `requirements: []`, contradicting its own requirement to
preserve Analyst requirements. Separately, the Solver validator rejected a
Critic-requested new requirement (`REQ-006`) because it required exact equality
with the original Analyst requirement set.

## Design

Update `CriticConflictResolver.aggregate()` to normalize each worker result
before validation and aggregation:

- Accept the existing outer result wrapper and use its `payload` mapping when
  present.
- Read revision from the payload or compatible outer metadata and read the
  plan hash from either `plan_hash` or `reviewed_plan_hash`, while preserving
  the worker id from the outer wrapper.
- Read `findings` and the Critic action from the normalized payload, and retain
  all findings from every accepted worker, including findings that reference
  either formal Zhongshu group.
- Continue rejecting results whose revision does not match the current run or
  whose supplied plan hash does not match the current plan. A missing hash is
  accepted only for the current revision because the production runtime already
  correlates the reply to the current dispatch; the missing hash is logged as a
  protocol warning.
- Deduplicate equivalent findings before building the aggregate review.
  Findings with the same `finding_id` and equivalent semantic content are
  represented once; distinct findings remain separate. The aggregate retains
  worker provenance for diagnostics.

Update the Solver revision prompt and validator:

- The compact prompt example contains a typed, non-empty requirements example
  with `requirement_id`, `statement`, `priority`, and `scope`.
- The prompt explicitly states that Analyst requirements are mandatory and
  Critic-approved additions may be appended.
- Validation requires every Analyst requirement ID, allows explicitly
  requested Critic additions, and still rejects empty, duplicate, or malformed
  requirement IDs.

## Safety behavior

Fan-in must fail closed. If workers are reported as completed but no worker
result can be normalized and accepted, the resolver must not emit an empty
aggregate with `APPROVE_FREEZE`. It should return an explicit non-freeze
decision that the existing orchestration path can persist and surface.

## Menxia audit result

Menxia did not contain the same empty-requirements example, and its item Solver
already deduplicated active Critic finding IDs before checking
`responses_to_critic`. The audit did find two parallel-path contract hazards:

- Parallel item results were indexed by `item_id` without rejecting duplicate,
  unknown, or missing IDs, so a result could be silently overwritten.
- The parallel `AgentRequest` reconstruction dropped `target_state` and
  `target_role`, so the generic parallel validator could apply the wrong action
  set to Menxia replies.

Both hazards are now rejected or preserved at the parallel boundary. Menxia
Solver replies also require an `implementation_proposal`, and Menxia Analyst
and Critic replies require their evidence/findings containers before entering
the FSM.

## Scope

Only `cmd/orchestrator/zhongshu_parallel.py`, `cmd/orchestrator/states.py`, and
focused design/verification records are in scope. No changes are made to global
Codex configuration, model catalog, transport polling, or Menxia scheduling.

## Verification

Perform static verification after the edit by inspecting the normalization and
validation paths, checking the captured worker result shape, and running
focused syntax checks. Automated end-to-end tests are intentionally not run in
this turn; the user will perform manual testing.
