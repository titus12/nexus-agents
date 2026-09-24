# Zhongshu Requirement-to-Task Graph Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor Zhongshu from producing long competing plans into producing one bounded, evidence-linked task graph that hands implementation design to Menxia.

**Architecture:** Three Zhongshu Analyst workers become bounded task-discovery lenses. Their compact proposals are normalized and deterministically merged by the orchestrator; the merged result is the only canonical `analyst_plan`. The Zhongshu Solver formalizes that task graph without implementation details, and Zhongshu Critic reviews task coverage, boundaries, dependencies, and acceptance criteria. Menxia remains responsible for task-to-implementation proposals.

**Tech Stack:** Python 3, existing `StateContext`, `StateMachine`, `ParallelCoordinatorDriver`, `ThreadPoolExecutor`, JSON protocol payloads, `unittest`, existing Multica transport and lifecycle persistence.

---

## File map and boundaries

- Modify `cmd/orchestrator/zhongshu_parallel.py`: add compact Analyst proposal normalization, deterministic task deduplication, provenance tracking, and task-graph fan-in. Keep Critic finding aggregation in the same module.
- Modify `cmd/orchestrator/states.py`: replace the full-plan Analyst prompt with a bounded task-discovery prompt; define the canonical task-graph contract; change Solver instructions and validation so Solver formalizes tasks rather than implementation plans.
- Modify `cmd/orchestrator/app.py`: fan-in all three Analyst outputs instead of selecting `completed[0]`; pass the merged task graph into the existing state machine; add separate Zhongshu worker timeout/attempt budgets without changing Menxia defaults.
- Modify `cmd/orchestrator/parallel_runtime.py`: support a per-run timeout override so Zhongshu can use a shorter worker budget while Menxia retains its existing timeout.
- Modify `cmd/test_zhongshu_parallel.py`: test deterministic Analyst merge, duplicate handling, provenance, and quorum behavior.
- Modify `cmd/test_analyst_contract_v31.py`: test the compact worker contract and the merged canonical Analyst plan contract.
- Modify `cmd/test_solver_prompt_v31.py`: test that Solver receives a task graph, is forbidden from emitting implementation proposals, and preserves task coverage.
- Add `cmd/test_zhongshu_task_graph.py`: focused pure tests for task identity, dependency validation, acceptance criteria, and bounded output behavior.
- Do not modify `cmd/orchestrator/menxia_parallel.py` or the Menxia implementation-proposal contract except where existing shared types require compatibility.

## Task 1: Define the task-discovery and canonical task-graph contracts

**Files:**
- Modify: `cmd/orchestrator/zhongshu_parallel.py`
- Modify: `cmd/orchestrator/states.py`
- Test: `cmd/test_zhongshu_task_graph.py`

- [ ] **Step 1: Write failing pure contract tests**

Add tests for these exact behaviors:

```python
def test_merge_analyst_outputs_keeps_all_unique_tasks_and_worker_provenance():
    result = merge_analyst_outputs(
        "task-1", "revision-1", [
            worker_payload("analyst-1", "requirements", "T-1"),
            worker_payload("analyst-2", "boundaries", "T-2"),
            worker_payload("analyst-3", "risk", "T-1"),
        ],
    )
    tasks = result["plan"]["candidate_items"]
    self.assertEqual([item["item_id"] for item in tasks], ["T-1", "T-2"])
    self.assertEqual(tasks[0]["source_workers"], ["analyst-1", "analyst-3"])

def test_merge_analyst_outputs_rejects_missing_requirement_coverage():
    with self.assertRaisesRegex(ValueError, "requirement coverage"):
        merge_analyst_outputs("task-1", "revision-1", [worker_payload_without_req("analyst-1")])

def test_task_graph_validator_rejects_implementation_details():
    reason = validate_zhongshu_task_graph({
        "action": "READY_FOR_CRITIC",
        "plan": {"items": [{"item_id": "T-1", "implementation_proposal": {}}]},
    })
    self.assertEqual(reason, "ZHONGSHU_TASK_IMPLEMENTATION_DETAIL_FORBIDDEN")
```

Use `unittest` assertions to match the repository's current test style; the snippets above define the required cases and expected values.

- [ ] **Step 2: Run the focused test to verify it fails**

Run:

```powershell
rtk python -m unittest cmd.test_zhongshu_task_graph -v
```

Expected: FAIL because the task-graph merger and validator do not exist.

- [ ] **Step 3: Implement the minimum pure contracts**

Implement in `zhongshu_parallel.py`:

```python
def merge_analyst_outputs(
    task_id: str,
    revision_id: str,
    worker_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge bounded task proposals into one deterministic Analyst plan."""
```

The function must:

1. Accept only current `revision_id` results.
2. Read `task_proposals`, `requirements`, `constraints`, `unknowns`, and `conflicts` from each worker.
3. Use a normalized task identity in this order: explicit `task_key`, normalized `(objective, scope)`, then `item_id`.
4. Merge equivalent proposals, retain the strongest evidence, and append sorted unique `source_workers` and `evidence_ids`.
5. Reject a missing `item_id`/`task_key`, duplicate conflicting task identities, malformed dependencies, or a result set that cannot cover every `must` requirement.
6. Emit one canonical payload with `action=READY_FOR_SOLVER`, `plan.candidate_items`, `plan.candidate_groups`, `plan.requirements`, `plan.dependencies`, `plan.scope`, `plan.unknowns`, `plan.risks`, and `plan.candidate_verification_questions`.
7. Never emit `implementation_proposal`, `file_changes`, `code_changes`, or function-level design fields.

Implement in `states.py`:

```python
def validate_zhongshu_task_graph(payload: dict[str, Any]) -> str:
    """Validate a Zhongshu task graph without validating Menxia implementation design."""
```

The validator must require unique task IDs, non-empty task objectives, requirement coverage, explicit acceptance signals, known dependency IDs, and `phase=ZHONGSHU`; it must reject implementation-detail fields with a stable reason.

- [ ] **Step 4: Run the focused test to verify it passes**

Run:

```powershell
rtk python -m unittest cmd.test_zhongshu_task_graph -v
```

Expected: PASS.

## Task 2: Make Analyst workers bounded task-discovery lenses

**Files:**
- Modify: `cmd/orchestrator/states.py`
- Modify: `cmd/orchestrator/app.py`
- Modify: `cmd/orchestrator/zhongshu_parallel.py`
- Test: `cmd/test_analyst_contract_v31.py`

- [ ] **Step 1: Add failing prompt-contract tests**

Assert that the Analyst prompt contains `task_proposals`, `task_key`, `acceptance_signals`, `source_requirement_ids`, and explicit prohibitions for implementation proposals and file-level changes. Assert that the prompt does not require a complete `candidate_groups`/`candidate_items` plan from each worker.

- [ ] **Step 2: Run the prompt tests to verify the old contract fails them**

Run:

```powershell
rtk python -m unittest cmd.test_analyst_contract_v31 -v
```

Expected: FAIL against the current full-plan worker prompt.

- [ ] **Step 3: Replace the Analyst worker prompt**

Change `_zhongshu_analyst_prompt()` so each worker returns one compact JSON object:

```json
{
  "action": "TASK_PROPOSALS_READY",
  "lens": "requirements|boundaries|risk",
  "requirements": [],
  "task_proposals": [
    {
      "task_key": "stable-normalized-key",
      "title": "string",
      "objective": "one independently implementable outcome",
      "source_requirement_ids": ["REQ-001"],
      "dependencies": [],
      "acceptance_signals": ["observable result"],
      "evidence_ids": [],
      "unknowns": [],
      "risks": [],
      "parallelizable": true
    }
  ],
  "constraints": [],
  "conflicts": [],
  "unknowns": []
}
```

Set explicit limits in the prompt: maximum 8 task proposals, maximum 5 evidence IDs per proposal, no exhaustive repository scan, and stop when all hard requirements have a task candidate or an explicit unknown.

In `_run_parallel_state()`, append only the worker lens instruction to the bounded base prompt. For `ZHONGSHU_ANALYST`, call `merge_analyst_outputs()` with all completed worker results; do not use `values[0]`.

- [ ] **Step 4: Update the parallel worker validation**

Allow `TASK_PROPOSALS_READY` only for Analyst worker requests. Validate its compact fields before fan-in, but validate the merged `READY_FOR_SOLVER` payload with `validate_zhongshu_task_graph()` before the FSM transition.

- [ ] **Step 5: Run the focused Analyst tests**

Run:

```powershell
rtk python -m unittest cmd.test_analyst_contract_v31 cmd.test_zhongshu_task_graph -v
```

Expected: PASS.

## Task 3: Make Solver formalize the task graph, not design implementation

**Files:**
- Modify: `cmd/orchestrator/states.py`
- Modify: `cmd/test_solver_prompt_v31.py`

- [ ] **Step 1: Add failing Solver boundary tests**

Add assertions that the Solver prompt says `requirements -> tasks`, requires complete task coverage, and forbids `implementation_proposal`, code edits, file changes, and function-level design. Add a valid payload using `plan.items` as task objects and an invalid payload containing `implementation_proposal`.

- [ ] **Step 2: Implement the task-only Solver contract**

Change `_zhongshu_solver_prompt()` and `_zhongshu_solver_resume_prompt()` so the Solver receives the canonical Analyst task graph and returns exactly one formal task graph. Preserve `plan.items` and `plan.groups` for Menxia compatibility, but define `items` as tasks and `groups` as task groups. Keep `requirements` equal to the Analyst requirements; Critic may request task changes, not invented implementation requirements.

Update `_validate_solver_plan()` to call the task-graph validator and to reject implementation-detail fields. Remove the rule that permits Solver to invent new requirements merely because a Critic revision exists.

- [ ] **Step 3: Run Solver-focused tests**

Run:

```powershell
rtk python -m unittest cmd.test_solver_prompt_v31 cmd.test_solver_contract_fix -v
```

Expected: PASS with the new task-only contract.

## Task 4: Add bounded Zhongshu time budgets and fail-fast quorum behavior

**Files:**
- Modify: `cmd/orchestrator/parallel_runtime.py`
- Modify: `cmd/orchestrator/app.py`
- Modify: `cmd/orchestrator/context.py`
- Test: `cmd/test_zhongshu_parallel.py`

- [ ] **Step 1: Add timeout and attempt override tests**

Test that `ParallelCoordinatorDriver.run(..., timeout_seconds=..., max_attempts=...)` uses the per-run timeout and attempt count, and that constructor values remain the defaults when no override is supplied.

- [ ] **Step 2: Implement per-run timeout override**

Add optional `timeout_seconds: float | None = None` and `max_attempts: int | None = None` arguments to `ParallelCoordinatorDriver.run()`. Compute `worker_timeout = self.timeout_seconds if timeout_seconds is None else max(1.0, timeout_seconds)` and `worker_attempts = self.max_attempts if max_attempts is None else max(1, max_attempts)`. Use those local values for the worker deadline and attempt loop only; do not mutate driver defaults.

- [ ] **Step 3: Configure Zhongshu-specific budgets**

Add environment overrides:

```text
ZHONGSHU_WORKER_TIMEOUT_SEC=240
ZHONGSHU_MAX_ATTEMPTS=1
```

Pass the worker timeout and attempt count only from `_run_parallel_state()` for Zhongshu. Leave the existing Menxia timeout and attempt count unchanged.

- [ ] **Step 4: Implement quorum and local repair rules**

For Analyst fan-in, accept two of three completed workers if all `must` requirements are covered and no unresolved conflict exists. A malformed or timed-out worker is recorded as failed and is not retried with the same long prompt. If quorum or hard-requirement coverage fails, transition to `HUMAN_GATE`/`BLOCKED` with a diagnostic instead of spending another full 15-minute cycle.

- [ ] **Step 5: Run concurrency and timeout tests**

Run:

```powershell
rtk python -m unittest cmd.test_zhongshu_parallel cmd.test_timeout_loop_v31 cmd.test_parallel_runtime_correlation -v
```

Expected: PASS.

## Task 5: Make Zhongshu Critic review the task graph

**Files:**
- Modify: `cmd/orchestrator/states.py`
- Modify: `cmd/orchestrator/zhongshu_parallel.py`
- Modify: `cmd/test_zhongshu_parallel.py`

- [ ] **Step 1: Add task-graph Critic tests**

Test that Critic findings can target a requirement, task, group, dependency, or acceptance signal; duplicate equivalent findings retain all worker provenance; and an unresolved P0/P1 task-graph conflict prevents freeze.

- [ ] **Step 2: Narrow the Critic prompt**

Change `_zhongshu_critic_prompt()` to state that `review_target.plan` is a task graph. Require findings to cite `requirement_id`, `item_id`, `group_id`, or a dependency edge, and prohibit proposing implementation code or Menxia implementation details.

- [ ] **Step 3: Preserve deterministic fan-in**

Keep `CriticConflictResolver` as the sole Critic reducer. Ensure the aggregate action is based on the normalized current task graph and that equivalent findings are deduplicated before revision requirements are generated.

- [ ] **Step 4: Run Critic regression tests**

Run:

```powershell
rtk python -m unittest cmd.test_zhongshu_parallel cmd.test_solver_revision_prompt -v
```

Expected: PASS.

## Task 6: End-to-end contract and regression verification

**Files:**
- Test: `cmd/test_zhongshu_task_graph.py`
- Test: `cmd/test_analyst_contract_v31.py`
- Test: `cmd/test_solver_prompt_v31.py`
- Test: `cmd/test_zhongshu_parallel.py`
- Test: `cmd/test_full_workflow_v31.py`

- [ ] **Step 1: Add an end-to-end in-memory flow test**

Use three compact Analyst payloads, merge them, validate the canonical task graph, pass it to Solver validation, then pass the resulting task groups to the existing Menxia preparation path. Assert that implementation proposals are absent before Menxia and required after Menxia Solver.

- [ ] **Step 2: Run the focused Zhongshu gate**

Run:

```powershell
rtk python -m unittest cmd.test_zhongshu_task_graph cmd.test_zhongshu_parallel cmd.test_analyst_contract_v31 cmd.test_solver_prompt_v31 cmd.test_solver_contract_fix -v
```

Expected: PASS.

- [ ] **Step 3: Run the broader orchestrator regression suite**

Run:

```powershell
rtk python -m unittest discover -s cmd -p "test_*.py" -v
```

Expected: all existing tests pass, with no tests requiring the active Nexus service to stop or rebind its normal port. Any integration validation that starts a service must use port `18766`.

- [ ] **Step 4: Perform static hygiene checks**

Run:

```powershell
rtk powershell -NoProfile -Command "python -m compileall -q cmd/orchestrator"
rtk powershell -NoProfile -Command "git diff --check"
```

Expected: both commands complete successfully.

- [ ] **Step 5: Review the final diff**

Run:

```powershell
rtk powershell -NoProfile -Command "git diff -- cmd/orchestrator/app.py cmd/orchestrator/context.py cmd/orchestrator/parallel_runtime.py cmd/orchestrator/states.py cmd/orchestrator/zhongshu_parallel.py cmd/test_zhongshu_task_graph.py cmd/test_analyst_contract_v31.py cmd/test_solver_prompt_v31.py cmd/test_zhongshu_parallel.py"
```

Confirm that the diff does not modify global Codex configuration, the model catalog, or Menxia implementation logic.
