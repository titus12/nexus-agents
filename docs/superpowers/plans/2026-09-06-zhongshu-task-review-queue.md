# Zhongshu Task Review Queue Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with checkpoints. Preserve unrelated working-tree changes and do not modify Codex global configuration.

**Goal:** Replace Zhongshu's fixed three-Critic full-plan fan-out with a durable task-level work queue that uses at most six concurrent workers, never leases the same task twice, and requeues only affected tasks during Solver convergence.

**Architecture:** Solver output is frozen into an immutable revision snapshot and flattened into one review job per `(revision_id, group_id, item_id)`. A bounded worker pool repeatedly claims one durable job, builds a task-scoped Critic request, persists the result, and claims the next job. The orchestrator performs only task coverage/status aggregation; it does not invoke an LLM summarizer or global Critic quorum.

**Tech Stack:** Python 3, dataclasses, `ThreadPoolExecutor`, existing `ConcurrencyAdmission`, JSON atomic persistence, existing result-file protocol, unittest-style pure local checks.

---

## Task 1: Add durable task-review job model and deterministic queue helpers

**Files:**
- Create: `cmd/orchestrator/zhongshu_review_queue.py`
- Modify: `cmd/orchestrator/context.py`
- Modify: `cmd/orchestrator/models.py`
- Test: `cmd/test_zhongshu_task_review_queue.py`

- [ ] **Step 1: Add a pure queue regression test**

Create tests for a plan with groups `G1/G2/G3` and ten items. Assert that `build_review_jobs` returns ten stable job keys, that `claim_next` returns unique jobs for six worker slots, and that four additional claims occur only after earlier jobs are completed.

The test must also attempt two claims for the same job through two queue instances sharing the same serialized queue and assert that only one claim succeeds. Test stale completion by submitting the wrong lease ID and asserting that the queue raises `StaleReviewLease`.

- [ ] **Step 2: Implement immutable job identity and queue serialization**

Add these concrete types to `zhongshu_review_queue.py`:

```python
@dataclass
class ReviewJob:
    review_job_id: str
    revision_id: str
    group_id: str
    item_id: str
    status: str = "PENDING"
    lease_id: str = ""
    worker_id: str = ""
    attempt: int = 0
    request_id: str = ""
    task_hash: str = ""
    dependency_hash: str = ""
    result_path: str = ""
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReviewJob":
        return cls(**{field.name: value[field.name] for field in fields(cls) if field.name in value})
```

Implement `build_review_jobs(plan, revision_id)` to read canonical `groups` and `items`, use each group’s ordered `items` when present, fall back to the plan-level `items` plus each item’s `group_id`, and reject duplicate or missing `(group_id, item_id)` identities. Use `sha256(canonical_json(task))` for `task_hash` and `sha256(canonical_json(dependency_refs))` for `dependency_hash`.

Implement `TaskReviewQueue` with a `threading.RLock` and these methods:

```python
class TaskReviewQueue:
    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TaskReviewQueue":
        jobs = [ReviewJob.from_dict(item) for item in value.get("jobs", [])]
        return cls(
            revision_id=str(value.get("revision_id") or ""),
            plan_hash=str(value.get("plan_hash") or ""),
            jobs=jobs,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision_id": self.revision_id,
            "plan_hash": self.plan_hash,
            "jobs": [job.to_dict() for job in self.jobs],
        }

    def claim_next(self, worker_id: str) -> ReviewJob | None: pass

    def complete(self, review_job_id: str, lease_id: str, attempt: int, result_path: str) -> ReviewJob: pass

    def fail(self, review_job_id: str, lease_id: str, attempt: int, error: str, retryable: bool) -> ReviewJob: pass

    def requeue(self, review_job_ids: list[str], revision_id: str) -> None: pass

    def all_terminal(self) -> bool: pass

    def pending_ids(self) -> list[str]: pass
```

`claim_next` must sort by `(group_order, item_order, group_id, item_id)`, generate a cryptographically unique lease token with `uuid.uuid4().hex`, increment `attempt`, and set `RUNNING`. `complete` and `fail` must reject a mismatched lease or attempt.

- [ ] **Step 3: Persist queue state in `StateContext`**

Add `zhongshu_task_review_queue: dict[str, Any]` to `StateContext` with an empty default. Include it in serialized container validation. Keep the queue under `request_payload["zhongshu_task_review_queue"]` as the authoritative copy so existing `state.json` persistence and recovery retain the queue snapshot.

- [ ] **Step 4: Run the pure queue test**

Run:

```powershell
rtk python cmd/test_zhongshu_task_review_queue.py
```

Expected output:

```text
zhongshu task review queue self-test: PASS
```

## Task 2: Add task-scoped Critic capsule and protocol validation

**Files:**
- Modify: `cmd/orchestrator/states.py`
- Modify: `cmd/orchestrator/structured_output.py`
- Modify: `cmd/orchestrator/adapters.py`
- Modify: `cmd/orchestrator/app.py`
- Modify: the active Zhongshu Critic Skill under `multica/`
- Test: `cmd/test_zhongshu_task_review_queue.py`

- [ ] **Step 1: Add capsule construction tests**

Assert that a task capsule contains exactly one `group_id/item_id`, the complete selected task, requirement references, dependency references, historical findings for that task, `task_hash`, and `dependency_hash`. Assert that the capsule does not contain the full plan, unrelated group items, or unrelated findings.

Assert that a valid task result with the matching revision, group, item, and hashes is accepted. Assert that a result for another item, a mismatched hash, or a Finding outside the assigned item is rejected with a stable error code.

- [ ] **Step 2: Implement `_zhongshu_task_critic_prompt`**

Add a function in `states.py` that accepts `ctx`, `plan`, and one `ReviewJob`, then emits one compact JSON prompt with these top-level keys:

```python
{
    "task": ctx.raw_request,
    "role": "ZHONGSHU_CRITIC",
    "mode": "REVIEW_ONE_TASK",
    "review_scope": {
        "scope_type": "item",
        "revision_id": job.revision_id,
        "group_id": job.group_id,
        "item_id": job.item_id,
        "task_hash": job.task_hash,
        "dependency_hash": job.dependency_hash,
    },
    "group_context": group_context,
    "task": task,
    "requirement_refs": requirement_refs,
    "dependency_refs": dependency_refs,
    "historical_findings": historical_findings,
    "instruction": task_review_instruction,
}
```

The instruction must require review of only the assigned task, prohibit implementation design and unrelated Findings, require `group_id`, `item_id`, `reviewed_task_hash`, and `reviewed_dependency_hash`, and require explicit checks for requirement coverage, boundaries, dependencies, acceptance signals, unknowns, and risks.

- [ ] **Step 3: Extend the role protocol for task review**

Keep the Critic role’s stable fields, and add these fields to the structured response:

```text
group_id, item_id, reviewed_task_hash, reviewed_dependency_hash,
review_checks, evidence_ids, unknowns
```

Allow task-review actions only when `zhongshu_dispatch_mode == "task_review"`:

```text
TASK_APPROVED
TASK_CHANGES_REQUIRED
REQUEST_ANALYST_EVIDENCE
HUMAN_GATE
BLOCKED
```

Do not allow `TASK_APPROVED` or `TASK_CHANGES_REQUIRED` in the legacy full-plan Critic path. The task result is translated to the existing FSM-level actions only after queue fan-in.

- [ ] **Step 4: Enforce task scope before fan-in**

Add `validate_zhongshu_task_critic_reply(payload, job, plan)` in `app.py` or a focused validator module. It must reject:

- revision mismatch;
- missing or mismatched `group_id/item_id`;
- task/dependency hash mismatch;
- missing `review_checks` keys;
- Finding with a different `group_id/item_id`;
- dependency Finding without one primary `item_id`;
- `TASK_APPROVED` with an active P0/P1 Finding;
- `TASK_CHANGES_REQUIRED` with no actionable Finding.

- [ ] **Step 5: Run protocol-only checks**

Run the queue self-test from Task 1 with the new capsule and validator cases. Expected output remains `PASS`; no Agent adapter or Feishu adapter may be constructed by the test.

## Task 3: Implement dynamic worker-slot execution

**Files:**
- Modify: `cmd/orchestrator/parallel_runtime.py`
- Modify: `cmd/orchestrator/lifecycle.py`
- Modify: `cmd/orchestrator/app.py`
- Test: `cmd/test_parallel_runtime_correlation.py`
- Test: `cmd/test_zhongshu_task_review_queue.py`

- [ ] **Step 1: Add dynamic queue driver tests**

Test a fake dispatcher/poller with ten jobs and six worker slots. Record `(worker_id, item_id)` pairs and assert every item appears once, no worker has two active jobs, and the maximum simultaneous active jobs is six. Add a slow-job case proving a finished slot claims the seventh and later jobs without waiting for all initial jobs.

Add a lease-expiry case and a late-result case. The late result must be classified as stale and must not change the completed job or its result path.

- [ ] **Step 2: Refactor one-attempt execution into a reusable function**

Extract the current dispatch/poll/validate/persist logic from `ParallelCoordinatorDriver.run` into a private method with this interface:

```python
def _execute_request(
    self,
    *,
    phase: str,
    revision_id: str,
    worker: ParallelWorker,
    validate: Callable[[dict[str, Any], ParallelWorker], None] | None,
    timeout_seconds: float,
    max_attempts: int,
) -> dict[str, Any]:
    return self._execute_request(
        phase=phase,
        revision_id=revision_id,
        worker=worker,
        validate=validate,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
    )
```

Preserve existing request lookup, dispatch idempotency key, polling correlation, rejected reply persistence, timeout, lease refresh, and `find_existing_request` behavior.

- [ ] **Step 3: Add `run_task_review_queue`**

Add a method to `ParallelCoordinatorDriver`:

```python
def run_task_review_queue(
    self,
    *,
    phase: str,
    revision_id: str,
    queue: TaskReviewQueue,
    worker_count: int,
    build_request: Callable[[ReviewJob, str], ParallelWorker],
    validate: Callable[[dict[str, Any], ParallelWorker, ReviewJob], None] | None,
    on_completed: Callable[[ReviewJob, dict[str, Any]], None],
    on_failed: Callable[[ReviewJob, dict[str, Any]], None],
    timeout_seconds: float,
    max_attempts: int,
) -> TaskReviewQueue:
    return queue
```

Create at most `min(worker_count, queue.nonterminal_count())` threads. Each slot loops over `claim_next`, builds one request, executes it, calls the completion callback, and then claims the next job. A slot must never hold two leases.

Use `worker_id = "zhongshu-critic-slot-{index}"`. The logical request ID must include revision, group, item, and job attempt so a reused slot never reuses a prior task’s request identity.

- [ ] **Step 4: Make lifecycle result paths task-specific**

Change Zhongshu result paths from `workers/<worker_id>/result.json` to a task/revision path that cannot be overwritten when a slot is reused:

```text
<task>/zhongshu/<revision>/tasks/<group_id>/<item_id>/attempt-<n>/result.json
```

Keep worker ID and attempt in the envelope. Add a loader that recovers only the latest valid result for the exact review job identity.

- [ ] **Step 5: Run local dynamic-runtime checks**

Run the focused pure tests only. Do not start the Nexus service or use any notification adapter.

## Task 4: Route Zhongshu Critic through the task queue

**Files:**
- Modify: `cmd/orchestrator/app.py`
- Modify: `cmd/orchestrator/zhongshu_review.py`
- Modify: `cmd/orchestrator/transitions.py`
- Modify: `cmd/orchestrator/policies.py`
- Test: `cmd/test_zhongshu_task_review_queue.py`

- [ ] **Step 1: Add task-result fan-in tests**

Test these cases:

- all ten tasks return `TASK_APPROVED` → aggregate action `APPROVE_FREEZE`;
- one task returns `TASK_CHANGES_REQUIRED` with P1 → aggregate action `REQUEST_SOLVER_REVISION`;
- one task requests analyst evidence → aggregate action `REQUEST_ANALYST_EVIDENCE`;
- one task returns `HUMAN_GATE` or `BLOCKED` → aggregate action `HUMAN_GATE`;
- missing, stale, failed, or retryable task → aggregate action is not approval;
- old task result from a prior revision is rejected;
- findings remain bound to their task and are not copied to other tasks.

- [ ] **Step 2: Build and persist the immutable review snapshot**

At the start of `ZHONGSHU_CRITIC`, flatten the current candidate plan into `TaskReviewQueue`, persist the revision, plan hash, task hashes, dependency hashes, and job list before dispatching any external request. On retry or restart, reuse the persisted queue when its revision and plan snapshot match; do not recompute a new queue for the same revision.

- [ ] **Step 3: Add the task-queue branch to `_run_parallel_state`**

Keep the existing three-worker fan-out for `ZHONGSHU_ANALYST`. For `ZHONGSHU_CRITIC`, create six bounded slot workers, load/reuse the persisted queue, build one task capsule per claim, and call `run_task_review_queue`.

Set the default Critic slot count to six, while honoring an explicit lower configured limit. The queue, not the number of groups, determines how many jobs are executed.

- [ ] **Step 4: Replace global Critic quorum fan-in**

Add `aggregate_task_review_results(queue, results, previous_review)` that records one result per task, updates the task-scoped Finding ledger, and computes only:

```text
completed_task_count
total_task_count
failed_task_ids
retryable_task_ids
blocked_task_ids
active_p0_p1_finding_ids
```

It must not compare three whole-review fingerprints or require a Critic quorum. It must return the existing FSM action names so `transitions.py` continues to receive a single deterministic event.

- [ ] **Step 5: Preserve convergence routing**

When the aggregate action requests Solver revision, persist `affected_item_ids` and `affected_group_ids`. When the next Solver plan is accepted, compare each item’s task/dependency hash with the prior snapshot. Requeue only changed items and items at either end of a changed dependency edge. Preserve approved results for unchanged items.

- [ ] **Step 6: Update transition guards**

Make `ZHONGSHU_CRITIC` approval require queue coverage and no unresolved P0/P1 Finding. A queue with any `PENDING`, `RUNNING`, or `RETRYABLE` job must not reach `ZHONGSHU_FREEZE_CHECK`. Keep the existing max revision and no-progress gates.

## Task 5: Align Skill, prompt bundle, persistence, and recovery contracts

**Files:**
- Modify: the active Zhongshu Critic Skill under `multica/`
- Modify: `cmd/orchestrator/prompt_bundle.py`
- Modify: `cmd/orchestrator/persistence.py`
- Modify: `cmd/orchestrator/recovery.py`
- Modify: `cmd/orchestrator/notifications.py` only if task status rendering needs fields
- Test: `cmd/test_prompt_bundle.py`
- Test: `cmd/test_zhongshu_task_review_queue.py`

- [ ] **Step 1: Update the Critic Skill protocol**

Document `REVIEW_ONE_TASK`, the mandatory scope fields, the hash echo rule, the allowed task actions, the Finding ownership rule, and the prohibition on reviewing or creating findings for other tasks. Keep the Skill’s response contract identical for normal execution, retry, and recovery.

- [ ] **Step 2: Add prompt-bundle task scope**

Ensure prompt bundle serialization preserves UTF-8, includes the task capsule exactly once, and does not append the full candidate plan or prior full worker result. Add a prompt budget log containing revision, group, item, task hash, and byte count.

- [ ] **Step 3: Persist queue and recovery metadata atomically**

Save the queue snapshot before dispatch, save each lease transition before the external request, and save completion before fan-in. On recovery, first reuse immutable task results, then call `find_existing_request` for in-flight jobs, and only redispatch when both the result and existing request are absent.

- [ ] **Step 4: Keep notifications quiet**

Do not add per-task Feishu messages. If existing status rendering is updated, keep it to one optional aggregate status and preserve notification dedupe. Pure tests must inject no Feishu adapter.

## Task 6: Self-review and focused verification

**Files:**
- Modify: any files requiring corrections from the review
- Test: `cmd/test_zhongshu_task_review_queue.py`
- Test: `cmd/test_parallel_runtime_correlation.py`

- [ ] **Step 1: Inspect the final diff and search for old full-plan Critic dispatch**

Run:

```powershell
rtk powershell -NoProfile -Command "rg -n 'range\(3\)|critic_quorum|semantic_fingerprint|REVIEW_CURRENT_TASK_GRAPH|zhongshu_task_review_queue|run_task_review_queue' cmd/orchestrator"
```

Confirm that the production Zhongshu Critic branch no longer dispatches three full-plan requests, while legacy review helpers remain unused by that branch only if they are still needed elsewhere.

- [ ] **Step 2: Run only notification-free focused checks**

Run:

```powershell
rtk python cmd/test_zhongshu_task_review_queue.py
rtk python cmd/test_parallel_runtime_correlation.py
```

Use a separate validation port if a test process is required. Do not run external Agent workflows, do not start the active Nexus service, and do not send Feishu notifications.

- [ ] **Step 3: Verify invariants from test output**

The final checks must demonstrate:

- ten tasks are reviewed exactly once in the initial revision;
- six slots are the maximum concurrent external requests;
- the seventh through tenth tasks are claimed only after a slot completes;
- no duplicate task lease exists;
- stale replies cannot overwrite current results;
- only affected tasks are requeued after Solver revision;
- incomplete or retryable queues cannot approve;
- no notification adapter was called.
