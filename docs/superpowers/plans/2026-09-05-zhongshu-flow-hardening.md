# Zhongshu Flow Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the Zhongshu Analyst/Solver/Critic workflow so parallel results use one protocol, quorum counts only valid independent reviews, retries receive the real failure, and restart/finding/task coverage remain safe.

**Architecture:** Keep the current FSM and `ParallelCoordinatorDriver`. Add validation and repair information at the application boundary, make the transport contract mode-aware, and make durable stage results a validated recovery source. The Solver remains a single formal task-graph owner; only Analyst and Critic remain parallel.

**Tech Stack:** Python 3, dataclasses, JSON, SHA-256, existing FSM/persistence/lifecycle adapters.

---

### Task 1: Unify Zhongshu transport contracts

**Files:**
- Modify: `cmd/orchestrator/adapters.py:1390-1563`
- Modify: `cmd/orchestrator/app.py:683-860`
- Modify: `cmd/orchestrator/states.py:3151-3173`
- Modify: `cmd/orchestrator/transitions.py:128-147`

- [ ] **Step 1: Add explicit dispatch-mode metadata.**

  Contract worker requests must carry `context.contract_mode=True` and `context.zhongshu_dispatch_mode="requirement_contract"`. Analyst task workers must carry `context.zhongshu_dispatch_mode="task_discovery"`. Critic workers must carry `context.zhongshu_dispatch_mode="critic_review"`.

- [ ] **Step 2: Make `_response_contract_for` mode-aware.**

  Return these exact contracts:

  ```python
  # requirement contract worker
  allowed_actions = ["REQUIREMENT_CONTRACT_READY", "HUMAN_GATE", "BLOCKED"]
  optional += ["requirements", "conflicts", "unknowns"]
  required_by_action = {"REQUIREMENT_CONTRACT_READY": ["action", "requirements"]}

  # task discovery worker
  allowed_actions = ["TASK_PROPOSALS_READY", "HUMAN_GATE", "BLOCKED"]
  optional += ["lens", "requirements", "task_proposals", "constraints", "conflicts", "unknowns"]
  required_by_action = {"TASK_PROPOSALS_READY": ["action", "task_proposals"]}

  # Zhongshu Critic worker
  required_by_action = {
      "APPROVE_FREEZE": ["action", "reviewed_plan_hash", "findings"],
      "REQUEST_SOLVER_REVISION": ["action", "reviewed_plan_hash", "findings"],
      "REQUEST_ANALYST_EVIDENCE": ["action", "reviewed_plan_hash", "findings"],
  }
  optional += ["reviewed_plan_hash", "plan_hash", "requirement_coverage", "evidence_alignment"]
  ```

  Keep canonical full-plan Analyst replies available only when the dispatch mode is absent; do not advertise `READY_FOR_SOLVER` for a task-discovery worker.

- [ ] **Step 3: Align local action routing.**

  Add `NEEDS_MORE_EVIDENCE` only if the runtime action is represented by a concrete transition; otherwise keep it as an internal skill status and require the Agent to return `HUMAN_GATE` or `BLOCKED`. Ensure every action advertised in the transport contract is accepted by the local validator and has a legal transition.

- [ ] **Step 4: Review the resulting contract paths statically.**

  Confirm contract, task-discovery, direct Analyst, Solver, and Critic requests no longer advertise a contradictory action or omit a required field. Do not run tests.

### Task 2: Preserve and persist parallel failure feedback

**Files:**
- Modify: `cmd/orchestrator/app.py:756-928,1031-1068`
- Modify: `cmd/orchestrator/state_machine.py:72-116`
- Modify: `cmd/orchestrator/states.py:673-713`
- Modify: `cmd/orchestrator/parallel_runtime.py:283-310`

- [ ] **Step 1: Add an app helper for fan-in rejection.**

  The helper must set:

  ```python
  ctx.last_error = {
      "code": "AGENT_REPLY_FANIN_REJECTED",
      "reason": reason,
      "state": state_name,
      "phase": "ZHONGSHU",
      "worker_ids": worker_ids,
  }
  ```

  It must append a bounded `reply_history` entry containing the reason and only bounded worker error/payload metadata, then return `AGENT_REPLY_REJECTED` with the current state and retry limit.

- [ ] **Step 2: Route every Analyst/Critic fan-in failure through the helper.**

  Replace the direct rejection returns for quorum failure, merge validation failure, and contract failure. Preserve the exact validation message such as `requirement coverage missing: REQ-008`.

- [ ] **Step 3: Teach repair prompts to consume fan-in errors.**

  Extend `_attach_repair_feedback` to accept `AGENT_REPLY_FANIN_REJECTED`, include the exact reason, and preserve the original rejected worker response when available. Do not treat a fan-in error as a fresh investigation.

- [ ] **Step 4: Convert resolver exceptions into structured fan-in failures.**

  Wrap Critic resolver execution so malformed `confidence`, finding fields, or unexpected payload types become a worker rejection/fan-in error instead of escaping `OrchestratorApp.run`.

### Task 3: Make Critic quorum semantically valid and independent

**Files:**
- Modify: `cmd/orchestrator/app.py:863-891,939-1030`
- Modify: `cmd/orchestrator/zhongshu_parallel.py:968-1105`
- Modify: `cmd/orchestrator/states.py:1483-1616`

- [ ] **Step 1: Run full Zhongshu Critic validation before quorum accounting.**

  Pass the current `StateContext` or an equivalent expected plan/hash binding into the parallel validator. Require a valid action, `reviewed_plan_hash` matching `canonical_plan_hash(candidate_plan)`, a findings list, unique finding IDs, valid severity, claim, decision, and bounded numeric confidence.

- [ ] **Step 2: Define a stable semantic fingerprint.**

  Hash the Critic action, reviewed plan hash, and normalized findings while excluding `worker_id`, request IDs, timestamps, result paths, and transport metadata. Preserve the worker lens separately for audit.

- [ ] **Step 3: Enforce quorum over valid distinct results.**

  For three configured Critic workers, require at least two valid results and at least two distinct semantic fingerprints. Rejected, hash-mismatched, malformed, and duplicate-semantic results do not count toward quorum. Return a repairable fan-in rejection when the condition is not met.

- [ ] **Step 4: Keep aggregate decisions conservative.**

  Any unresolved conflict or active P1 finding must prevent freeze. A standalone P0/P1 must not produce `APPROVE_FREEZE`.

### Task 4: Preserve Analyst context and repair unknown coverage

**Files:**
- Modify: `cmd/orchestrator/app.py:771-829`
- Modify: `cmd/orchestrator/zhongshu_parallel.py:132-204,249-378`
- Modify: `cmd/orchestrator/states.py:1143-1219`

- [ ] **Step 1: Build Analyst worker prompts from authoritative context.**

  Include `critic_feedback`, `repair_feedback`, active Skill metadata, the canonical requirement contract, and the worker lens in the task-discovery payload. Do not use a replacement prompt that silently drops the base state context.

- [ ] **Step 2: Add requirement-specific unknown representation.**

  Accept `unknown_requirements` as a list of requirement IDs or unknown objects with `requirement_id`. Normalize these IDs during merge. Preserve the complete unknown object in the resulting plan.

- [ ] **Step 3: Route unresolved executable requirements safely.**

  If a must executable requirement has no task but has an explicit unknown, return a structured evidence-gap result that routes to `HUMAN_GATE` or `BLOCKED`; never label it as a completed task graph for Solver.

- [ ] **Step 4: Ensure constraints remain preserved.**

  Keep non-task contract entries in `constraints`/`non_goals` and exclude them from executable task coverage. Do not weaken must-task coverage.

### Task 5: Enforce Solver task preservation

**Files:**
- Modify: `cmd/orchestrator/states.py:1666-1801`
- Modify: `cmd/orchestrator/states.py:1959-2167`

- [ ] **Step 1: Extract Analyst candidate item IDs and source mapping.**

  Treat `analyst_plan.candidate_items[*].item_id` as the authoritative candidate task set. Reject duplicate or empty candidate IDs before comparison.

- [ ] **Step 2: Validate Solver item coverage.**

  Require every Analyst candidate item ID to appear in `plan.items`. Keep the existing requirement, dependency, group, acceptance, unknown, risk, and forbidden implementation-field checks.

- [ ] **Step 3: Preserve coverage across normalized revision output.**

  When materializing `current_plan + changes`, re-run the same candidate-item coverage check after applying changes. A revision cannot delete or silently rename a candidate item.

- [ ] **Step 4: Review all Solver early-return paths.**

  Ensure compact group encoding and full-plan normalization cannot bypass candidate-item coverage. Do not run tests.

### Task 6: Harden Finding lifecycle and freeze semantics

**Files:**
- Modify: `cmd/orchestrator/app.py:1560-1606`
- Modify: `cmd/orchestrator/states.py:1483-1508,1594-1616`
- Modify: `cmd/orchestrator/transitions.py:149-162`

- [ ] **Step 1: Require a complete Critic finding snapshot when prior findings exist.**

  Compare the current Critic review's finding IDs against the prior Zhongshu review. If a prior finding is omitted, reject the reply with a repairable snapshot-incomplete error and include the prior IDs in feedback.

- [ ] **Step 2: Require explicit resolution for closing findings.**

  Keep omitted findings active; only `RESOLVED`, `WONT_FIX`, `DEFERRED` with the appropriate evidence or human decision can close them. Never auto-close by absence.

- [ ] **Step 3: Limit freeze blocking to active P0/P1 findings.**

  Add a severity-filtered helper and use it in the `APPROVE_FREEZE` transition. P2/P3 findings remain durable follow-up information and do not automatically send the flow back to Solver.

- [ ] **Step 4: Handle duplicate finding IDs across Critic workers safely.**

  Reject conflicting same-ID findings in a single aggregate instead of allowing the last update to overwrite the first. Identical same-ID findings may be merged with worker provenance.

### Task 7: Reuse durable Zhongshu stage results after restart

**Files:**
- Modify: `cmd/orchestrator/app.py:646-937`
- Modify: `cmd/orchestrator/parallel_runtime.py:103-311`
- Modify: `cmd/orchestrator/lifecycle.py:40-63,102-133`

- [ ] **Step 1: Load per-worker Zhongshu stage results before dispatch.**

  Resolve `zhongshu/<revision>/workers/<worker_id>/result.json`, check phase, revision, worker ID, request identity, and payload metadata, and revalidate it with the same validator used for fresh replies.

- [ ] **Step 2: Dispatch only missing or invalid workers.**

  Keep recovered results in the current fan-in set. Preserve admission accounting for only newly dispatched workers and make the result ordering deterministic by worker ID.

- [ ] **Step 3: Keep external idempotency as a second recovery layer.**

  Continue using `find_existing_request` for requests without a valid durable result. Do not redispatch a worker whose validated stage result already exists.

### Task 8: Bind runtime Skill metadata and perform final static review

**Files:**
- Modify: `cmd/orchestrator/states.py:51-76,484-548`
- Modify: `cmd/orchestrator/prompt_bundle.py:77-200`
- Modify: `cmd/orchestrator/adapters.py:418-465`
- Modify: `docs/multi/phase-skill-bundle-matrix.md` only if the runtime contract wording must be synchronized

- [ ] **Step 1: Add one active Skill lock to every Zhongshu request context.**

  Include Skill name, phase, role, source/version metadata, and content hash in the prompt-bundle context. Do not combine multiple phase Skills.

- [ ] **Step 2: Include contract mode and Skill lock in the bundle manifest input.**

  Ensure the Agent can identify the active Skill and exact response contract for the request. Fail closed when a required active Skill binding is missing rather than silently falling back to another phase.

- [ ] **Step 3: Inspect changed code paths.**

  Review imports, exception paths, state transitions, persistence writes, and prompt fields. Confirm no changes touch Codex global configuration, no notification call is added, and no automated test or service is run.
