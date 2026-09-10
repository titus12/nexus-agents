# Linear FSM Migration Design

**Date:** 2026-09-10

## Goal

Complete the migration from the legacy mutable FSM to the immutable New workflow runtime, make the New runtime the only production FSM, and remove obsolete legacy FSM design code after all production references and tests have been migrated.

## Scope and constraints

This migration covers the Python orchestrator under `cmd/orchestrator`. It includes the production entrypoint, workflow state behavior, persistence, recovery, effects, worker nodes, transport normalization, and tests that currently depend on the legacy FSM.

The migration must not modify `C:\Users\Administrator\.codex\config.toml` or `C:\Users\Administrator\.codex\nexus-model-catalog.json`. Existing unrelated working-tree changes belong to the user and must be preserved. Validation services must use a separate port such as `18766`; the active Nexus instance on `8766` must remain available.

Necessary compatibility is limited to external boundaries: old Multica/Feishu/result-file message envelopes may be normalized at the adapter boundary. The domain and workflow runtime must not retain legacy FSM types or legacy transport semantics.

## Target architecture

There will be one production FSM:

```text
external message / agent result
        -> transport adapter and ReplyNormalizer
        -> DomainEvent
        -> WorkflowEngine
        -> WorkflowState -> StateDecision
        -> LinearContextReducer
        -> journal-first WorkflowRepository
        -> EffectManager / NodeExecutor
        -> external result -> DomainEvent
```

### Domain layer

`cmd/orchestrator/domain` owns immutable workflow data and pure decisions:

- `WorkflowContext` and its typed subcontexts are the only workflow context model.
- `domain.states` contains concrete state policies. States validate their current context and return `StateDecision`; they do not perform I/O, persistence, transport, locking, or concurrency.
- `domain.transitions` is the closed transition graph. A caller cannot inject an arbitrary target state.
- `domain.events`, `domain.decisions`, `domain.findings`, and `domain.errors` define typed inputs, outputs, finding scope, and failure attribution.

The generic state skeleton must be replaced or extended with the real business decisions currently implemented in the legacy state module. In particular, Analyst, Solver, Critic, Freeze Check, Menxia item/group review, human gate, retry, failure, and persistence-degraded behavior must all return complete typed decisions rather than placeholder transitions.

### Runtime layer

`cmd/orchestrator/runtime` owns execution mechanics:

- `WorkflowEngine` acquires one task lock, loads a snapshot, handles exactly one `DomainEvent`, validates the decision, reduces an immutable next snapshot, commits the transition, and releases the lock.
- `LinearContextReducer` is the only component that produces the next `WorkflowContext` from a `StateDecision`.
- `JsonWorkflowRepository` stores journal-first transitions, snapshots, effect intents, effect results, and failure records.
- `EffectManager` executes persisted effect intents and converts external outcomes into typed result events.
- `NodeExecutor` and its joiner run bounded Menxia workers without exposing the main workflow context to workers.
- Runtime ports isolate agent transport, artifact/result files, notifications, locks, and event input from the domain.

`app.py` becomes the composition root and event loop. It may construct adapters, repositories, ports, policies, the state registry, the engine, and effect manager, but it must not contain legacy state-name dispatch or mutate a workflow context directly.

## Business event flow

The state sequence remains explicit and closed:

```text
REQUEST_INTAKE
  -> ZHONGSHU_ANALYST
  -> ZHONGSHU_SOLVER
  -> ZHONGSHU_CRITIC
  -> ZHONGSHU_FREEZE_CHECK
  -> MENXIA_ITEM_SOLVER
  -> MENXIA_ITEM_ANALYST
  -> MENXIA_ITEM_CRITIC
  -> MENXIA_GROUP_GATE
  -> DONE
```

Each external request is represented by a typed effect and immutable correlation data: `task_id`, `request_id`, `agent_id`, phase/role, and external trigger/run identifiers. A completed or failed external run must produce a terminal result event. `unknown` is a diagnostic correlation state only; it must not be treated as proof that an agent is still running or allow an unbounded wait loop.

System states use explicit recovery metadata:

- `HUMAN_GATE`, `RETRY_WAIT`, `BLOCKED`, and `PERSISTENCE_DEGRADED` require a validated `resume_state`.
- `FAILED`, `CANCELLED`, and `DONE` are terminal.
- Remote failure, timeout, malformed result, missing result artifact, and persistence failure each produce an attributable `FailureRecord` and a defined terminal or recoverable transition.

The Analyst incident that motivated this migration must therefore resolve as:

```text
remote run failed
  -> failure result event
  -> FAILED or bounded recovery transition
  -> durable diagnostic record
```

It must never resolve as repeated `status=unknown` polling until operator interruption.

## Migration and deletion plan

1. Complete New domain state policies and typed effect decisions by moving the business rules out of the legacy state implementation.
2. Add the runtime policy/port implementations needed to construct prompts, validate role replies, aggregate findings, dispatch and poll agents, and deliver notifications without domain I/O.
3. Wire `app.py` to `WorkflowRepository`, `WorkflowEngine`, event input, effect management, and the New state registry.
4. Add a one-time DTO conversion at the persistence boundary for existing legacy snapshots. The conversion must not expose `StateContext` to the New domain or runtime.
5. Port relevant tests to `WorkflowContext`, `DomainEvent`, `StateDecision`, and runtime ports. Remove tests whose only purpose is to preserve the deleted FSM API.
6. Run static reference checks and the complete test suite.
7. Only after the production reference check is clean, delete the legacy FSM modules and old persistence/recovery branches:
   - `cmd/orchestrator/state_machine.py`
   - `cmd/orchestrator/states.py`
   - `cmd/orchestrator/transitions.py`
   - `cmd/orchestrator/context.py`
   - obsolete `StateContext` exports and legacy FSM-only tests/helpers

External adapters remain only where they are needed to communicate with Multica, Feishu, or result files. Their compatibility code must terminate at normalization into a typed `DomainEvent` or port DTO; it must not recreate the old FSM.

## Detailed migration design

### 1. Decomposing the legacy `states.py`

The 4,347-line legacy state module is not migrated as one file. Its responsibilities are separated by behavior before any old state class is removed:

| Legacy area | Current responsibility | New destination | Removal condition |
|---|---|---|---|
| `BaseState` lifecycle (`states.py:213-580`) | enter/update/exit, heartbeat, lease release, reply polling | `WorkflowEngine`, `EffectManager`, `HeartbeatEffectRunner`, and typed state policies | no production caller needs `BaseState` |
| request construction (`states.py:612-920`) | prompt/request creation, idempotency, dispatch and poll | `domain/prompt_policies.py`, `runtime/agent_effects.py`, `AgentTransportPort` | every effect uses `EffectRequest` and a port DTO |
| repair/prompt/reply helpers (`states.py:962-3948`) | prompt projection, Analyst/Solver/Critic validation, repair and finding rules | pure role policies under `domain/policies/` and existing typed contract modules | no helper accepts `StateContext` or mutates a context |
| concrete state classes (`states.py:3975-4274`) | business/system state side effects and event generation | concrete `domain.states` objects plus effect runners | New `StateRegistry` covers every production state |
| registry/action table (`states.py:4277-end`) | state-name dispatch and allowed actions | `domain.states.StateRegistry` and `domain.transitions.TransitionRegistry` | static check finds no old registry or state-name branching |

The extraction order for each state is fixed:

1. Extract pure validation and normalization with typed input/output.
2. Extract prompt creation into a `PromptSpec`/artifact-producing policy.
3. Replace dispatch, poll, file writes, and notifications with typed `EffectRequest` values.
4. Make the New state return the decision and effect intents.
5. Add an event/effect/recovery test before deleting the corresponding legacy branch.

The New state object never receives `MulticaCliAdapter`, `FeishuHttpAdapter`, `LifecyclePaths`, `ConcurrencyAdmission`, or a mutable context. During migration a temporary `CompatibilityEffectRunner` may call the existing external adapter methods, but it is an effect-runner implementation only; it cannot import `StateContext`, `StateMachine`, or any legacy state class and is deleted once the native runner is complete.

### 2. Runtime port contracts

The current ports are incomplete for the migration. They will be expanded with the following stable contracts; concrete adapters remain outside the domain package:

```python
@dataclass(frozen=True)
class AgentDispatchRequest:
    task_id: str
    issue_id: str
    request_id: str
    agent_id: str
    role: str
    phase: str
    prompt_ref: str
    idempotency_key: str

@dataclass(frozen=True)
class RemoteRunStatus:
    request_id: str
    operation_id: str | None
    status: Literal["RUNNING", "COMPLETED", "FAILED", "UNKNOWN"]
    result_ref: str | None = None
    error_code: str | None = None
    error_message: str | None = None

class AgentTransportPort(Protocol):
    def dispatch(self, request: AgentDispatchRequest) -> DispatchReceipt: ...
    def poll(self, request: PollRequest) -> tuple[RawTransportReply, ...]: ...
    def status(self, request: PollRequest) -> RemoteRunStatus: ...

class ArtifactPort(Protocol):
    def write(self, artifact: ArtifactInput) -> ArtifactReceipt: ...
    def read(self, task_id: str, artifact_id: str) -> bytes: ...

class DomainEventInbox(Protocol):
    def next(self, task_id: str) -> DomainEvent | None: ...

class ConcurrencyAdmissionPort(Protocol):
    def acquire(self, key: AdmissionKey, deadline: float) -> LeaseReceipt | None: ...
    def refresh(self, lease_id: str) -> bool: ...
    def release(self, lease_id: str) -> bool: ...
```

`NotificationPort` remains a typed `send(NotificationRequest)` boundary. `LockPort` owns the main workflow task lock; `ConcurrencyAdmissionPort` owns global, per-task, and phase worker capacity. They are separate leases with separate failure semantics.

`RemoteRunStatus.FAILED` is terminal evidence even when no reply comment or result file exists. `UNKNOWN` is returned only when the adapter cannot correlate a run and must be accompanied by a bounded retry deadline; it is never converted into a successful wait state without a deadline.

### 3. Zhongshu and Menxia parallel mapping

The parallel modules are split into pure planning/joining and runtime execution:

| Legacy component | New representation | Ownership |
|---|---|---|
| `ParallelWorker` | `WorkerBinding` plus a stable `AgentDispatchRequest` | domain node plan |
| `ParallelFanIn` | `NodeResult` containing ordered `WorkerResult` and `FailureRecord` values | `NodeJoiner` |
| `ParallelCoordinatorDriver.run()` | `NodeEffectRunner` invoking `NodeExecutor` | `EffectManager` |
| `external_target_parallelism()` | admission key `(issue_id, agent_id)` and `ConcurrencyAdmissionPort` | runtime admission |
| worker dispatch/poll/retry | `AgentWorkerRunner.run()` with one idempotent request attempt | agent effect runner |
| stage result/verdict files | `ArtifactPort.write/read()` using `node_run_id` and `worker_id` | artifact runner |
| recovered worker results | artifact recovery keyed by `(task_id, node_run_id, worker_id, request_id)` | recovery service |
| `zhongshu_parallel` validation/merge helpers | pure `domain` review policies and typed finding updates | domain |
| `ZhongshuFanoutCoordinator` | `ReviewNode` builder plus `NodeJoiner`/conflict policy | domain/runtime boundary |
| `MenxiaParallelCoordinator` | deterministic `ReviewNode` plans and a typed item ledger in `ReviewState` | domain/runtime |
| mutable `MenxiaItemRuntime` | immutable item execution snapshot/artifact records | repository |

One node effect owns a deterministic set of worker bindings. Each worker has its own idempotency key and admission lease. `NodeExecutor` runs workers sequentially or concurrently, reuses terminal worker artifacts on recovery, and always fans in results in binding order. A worker failure cannot mutate the main `WorkflowContext`; the node result becomes a domain event that the engine handles under the normal sequence check.

Zhongshu-specific pure functions such as task-graph canonicalization, requirement binding, evidence merge, critic fingerprinting, and conflict resolution move to dedicated policy modules. Menxia group/item transitions are represented by `ReviewState` plus node results; no mutable coordinator object remains in the FSM.

### 4. Legacy-to-New DTO mapping

The current `StateContext` declaration has 73 dataclass fields; its final field is on line 85. The conversion is field-by-field, with derived fields recomputed and checked. Large or untyped legacy payloads are written once through `ArtifactPort` and referenced by an immutable artifact id; they are not silently dropped or copied as an arbitrary context patch.

| Legacy field | New target | Conversion rule |
|---|---|---|
| `task_id` | `identity.task_id` | required identity |
| `issue_id` | `identity.issue_id` | required identity |
| `workflow_state` | `progression.state` | validate against `ALL_STATES` |
| `sequence` | `progression.sequence` | preserve exactly |
| `raw_request` | `request.raw_request` | preserve exactly |
| `request_payload` | `request.payload_ref` | write canonical artifact and retain digest |
| `zhongshu_parallel` | `parallel.zhongshu_limits` | validate into typed limits |
| `menxia_parallel` | `parallel.menxia_snapshot_ref` | write canonical typed snapshot artifact |
| `state_version` | `WorkflowSnapshot.state_version` | snapshot metadata, not domain context |
| `last_agent_payload` | `review.last_result_ref` | write immutable result artifact |
| `sent_notification_keys` | `audit.sent_notification_keys` | convert list to ordered tuple |
| `heartbeat_count` | `audit.heartbeat_count` | preserve counter |
| `last_heartbeat_epoch` | `audit.last_heartbeat_at` | convert epoch to UTC ISO-8601 |
| `project_type` | `request.project_type` | preserve normalized value |
| `task_type` | `request.task_type` | preserve normalized value |
| `current_phase` | `delivery.phase` | preserve dispatch binding |
| `current_role` | `delivery.role` | preserve dispatch binding |
| `expected_agent_id` | `delivery.expected_agent_id` | preserve author binding |
| `active_request_id` | `delivery.active_request_id` | preserve request correlation |
| `last_sent_at` | `delivery.last_sent_at` | preserve timestamp |
| `active_group_id` | `review.active_group_id` | preserve review scope |
| `active_item_id` | `review.active_item_id` | preserve review scope |
| `group_index` | `parallel.group_index` | preserve ordering index |
| `item_index` | `parallel.item_index` | preserve ordering index |
| `zhongshu_revision_round` | `review.zhongshu_revision_round` | preserve counter |
| `max_zhongshu_revision_rounds` | `review.max_zhongshu_revision_rounds` | preserve limit |
| `freeze_check_attempt` | `review.freeze_check_attempt` | preserve counter |
| `max_freeze_check_attempts` | `review.max_freeze_check_attempts` | preserve limit |
| `item_revision_round` | `review.item_revision_round` | preserve counter |
| `max_item_revision_rounds` | `review.max_item_revision_rounds` | preserve limit |
| `active_decision_id` | `human_gate.decision_id` | null when no active gate |
| `resume_state` | `progression.resume_state` | validate only for recoverable system states |
| `gate_message_id` | `human_gate.message_id` | preserve external message identity |
| `last_artifact_id` | `audit.last_artifact_id` | preserve artifact identity |
| `active_finding_ids` | derived from `review.findings` | recompute and compare during migration |
| `pending_solver_finding_ids` | derived from `review.findings` | recompute and compare during migration |
| `resolved_finding_ids` | derived from `review.findings` | recompute and compare during migration |
| `dispatch_status` | `delivery.status` | normalize to typed status enum |
| `dispatch_operation_id` | `delivery.dispatch_operation_id` | preserve remote operation id |
| `dispatch_external_message_id` | `delivery.external_message_id` | preserve trigger/comment id |
| `dispatch_idempotency_key` | `delivery.idempotency_key` | required for retry/recovery |
| `dispatch_attempt` | `delivery.attempt` | preserve counter |
| `external_retry_count` | `recovery.external_retry_count` | preserve counter |
| `reply_retry_count` | `recovery.reply_retry_count` | preserve counter |
| `max_external_retries` | `recovery.max_external_retries` | preserve limit |
| `max_reply_retries` | `recovery.max_reply_retries` | preserve limit |
| `max_state_write_retries` | `recovery.max_state_write_retries` | preserve limit |
| `timeout_retry_count` | `recovery.timeout_retry_count` | preserve counter |
| `max_timeout_retries` | `recovery.max_timeout_retries` | preserve limit |
| `timeout_phase` | `recovery.timeout_phase` | preserve diagnostic binding |
| `timeout_role` | `recovery.timeout_role` | preserve diagnostic binding |
| `timeout_request_id` | `recovery.timeout_request_id` | preserve diagnostic binding |
| `last_error` | `recovery.last_failure` | convert to `FailureRecord`; reject malformed data |
| `reply_history` | `audit.reply_history_ref` | write immutable history artifact |
| `last_reply_fingerprint` | `review.last_reply_fingerprint` | preserve dedupe identity |
| `no_progress_count` | `recovery.no_progress_count` | preserve counter |
| `max_no_progress` | `recovery.max_no_progress` | preserve limit |
| `recoverable` | `recovery.recoverable` | preserve flag |
| `blocked_reason` | `recovery.blocked_reason` | preserve reason |
| `entered_at` | `progression.entered_at` | preserve timestamp |
| `updated_at` | `audit.updated_at` | preserve timestamp |
| `findings` | `review.findings` | convert every entry to immutable `Finding` |
| `schema_version` | `WorkflowContext.schema_version` | write new schema version and source version |

The new aggregate therefore gains typed `RequestState`, `ParallelState`, `AuditState`, and the missing dispatch/retry/review counters. A migration test must prove DTO round-trip, artifact digest preservation, finding-derived-list equality, invalid-state rejection, and no loss of any source field.

### 5. Concurrency and lease ownership

`ConcurrencyAdmission` is promoted behind `ConcurrencyAdmissionPort`; its existing limits are not discarded:

- `global_max` limits all active external workers.
- `per_task_max` limits one workflow's active workers.
- `analyst_max` and `critic_max` limit phase-specific fan-out.
- logical key `(task_id, phase, worker_id, revision_id)` prevents duplicate worker leases.
- external target key `(issue_id, agent_id)` serializes requests that Multica may coalesce.
- lease refresh is required during dispatch/poll and lease loss stops the worker before another effect is started.

Admission leases are runtime ownership records, not workflow state. A restart expires or reconciles them, then reacquires capacity using the persisted worker idempotency key. The main `LockPort` still protects one workflow transition; admission does not replace it.

### 6. Incremental delivery without two active FSMs

The migration is delivered as vertical slices. The New `WorkflowEngine` becomes the sole owner of workflow state as soon as the first slice is enabled; the old `StateMachine` is never run concurrently for the same task.

| Slice | Production result | Temporary compatibility |
|---|---|---|
| A. Runtime shell | New repository, reducer, engine, event inbox, and lock are live | existing Multica/Feishu methods wrapped by `CompatibilityEffectRunner` |
| B. Intake + Analyst | Analyst prompt, dispatch, terminal status, reply normalization, and failure recovery run through New decisions | adapter implementation may still call existing CLI/HTTP client |
| C. Solver + Critic + Freeze | role policies, findings, revision and freeze events run through New | existing pure validators can be called through typed policy wrappers |
| D. Menxia nodes | Zhongshu/Menxia fan-out, admission, fan-in, artifact recovery and group gate run through New | no mutable coordinator is allowed past this slice |
| E. System states | human gate, retry, block, timeout, failed and persistence-degraded paths are New-native | only external notification transport remains compatible |
| F. Cleanup | old files, exports, state store branches and migration-only runners are deleted | no FSM fallback remains |

Each slice has a passing end-to-end test and can be deployed independently. Compatibility is limited to calling external adapters; it never means converting back to `StateContext` or invoking a legacy state class. A runtime feature switch is allowed only during rollout and is removed with the old path at Slice F.

### 7. Test migration inventory

The test migration is explicit:

**Keep as New runtime contract tests (the existing 12 files):**

`test_linear_fsm_architecture.py`, `test_linear_fsm_direct_transport.py`, `test_linear_fsm_effects.py`, `test_linear_fsm_engine.py`, `test_linear_fsm_failures.py`, `test_linear_fsm_locking.py`, `test_linear_fsm_nodes.py`, `test_linear_fsm_persistence.py`, `test_linear_fsm_reducer.py`, `test_linear_fsm_states.py`, `test_linear_fsm_transitions.py`, and `test_linear_fsm_transport.py`.

**Rewrite against New context/events/ports while preserving behavior coverage:**

`test_fsm_core_v31.py`, `test_full_workflow_v31.py`, `test_review_orchestrator_v2.py`, `test_timeout_loop_v31.py`, `test_process_interruption_v31.py`, `test_runtime_diagnostics_v31.py`, `test_heartbeat_and_timeout_v31.py`, `test_heartbeat_display_v32.py`, `test_human_gate_protocol_v31.py`, `test_human_gate_queue_v31.py`, `test_notifications_and_gate_v31.py`, `test_menxia_item_prompt.py`, `test_menxia_persistence.py`, `test_menxia_parallel.py`, `test_zhongshu_parallel.py`, `test_zhongshu_iterative_convergence.py`, `test_zhongshu_task_graph.py`, `test_zhongshu_task_review_queue.py`, `test_zhongshu_evidence_routing.py`, `test_group_revision.py`, `test_parallel_runtime_correlation.py`, `test_orchestrator_correlation_v32.py`, `test_multica_dispatch_assignment_v31.py`, `test_multica_poll_filter_v31.py`, `test_agent_result_file_poll.py`, `test_final_delivery.py`, `test_notification_dedupe_v31.py`, `test_notification_rendering_v32.py`, `test_reply_retry_scope.py`, `test_unstructured_reply_repair.py`, and `test_repair_mvp.py`.

**Keep as boundary/contract tests and remove only legacy imports:**

`test_agent_result_file.py`, `test_analyst_contract_v31.py`, `test_analyst_feedback.py`, `test_role_phase_contracts.py`, `test_solver_contract_fix.py`, `test_solver_prompt_v31.py`, `test_solver_revision_prompt.py`, `test_reply_fallback_v32.py`, `test_prompt_bundle.py`, `test_prompt_bundle_dispatch.py`, `test_comment_feed_v32.py`, `test_feishu_command_parser_v32.py`, `test_json_bom_v31.py`, `test_lifecycle.py`, `test_core_hardening.py`, `test_deterministic_hardening.py`, `test_hourly_logging_v31.py`, and `test_v31_audit.py`.

**Add dedicated migration and production-cutover tests:**

`test_linear_fsm_dto_migration.py`, `test_linear_fsm_parallel_effects.py`, `test_linear_fsm_concurrency.py`, `test_linear_fsm_terminal_run_status.py`, `test_linear_fsm_entrypoint.py`, and `test_linear_fsm_no_legacy_references.py`.

No test is deleted merely because it imports V2. Its behavior assertion is first moved to a New test. Only a file whose assertions are fully duplicated and whose only remaining purpose is to exercise the deleted API may then be removed. The final test gate runs both the New contract suite and the full `cmd` discovery suite.

## Error handling and recovery

All cross-boundary failures become immutable `FailureRecord` values with at least `failure_id`, `stage`, `owner_component`, `task_id`, `state`, `sequence`, correlation ids, `error_code`, `retryable`, and `cause_type`.

Recovery order is deterministic:

1. Reconcile journal and snapshot.
2. Recover pending/running effect intents idempotently.
3. Recover pending worker/result artifacts.
4. Validate the recorded system-state resume point.
5. Continue through `WorkflowEngine` only after the event and sequence are valid.

No state object may call an external adapter directly, and no recovery branch may redispatch an effect without checking its idempotency key and external operation identity.

## Verification gates

The migration is complete only when all of the following hold:

1. `rg` finds no production import or construction of `StateContext`, `StateMachine`, or the deleted legacy state/transition modules.
2. The production entrypoint constructs and drives `WorkflowEngine`.
3. New domain, reducer, repository, engine, effect, node, transport-normalization, recovery, and system-state tests pass.
4. Existing Analyst, Solver, Critic, Menxia, human-gate, persistence, interruption, and diagnostic behavior is covered by the New API.
5. A failure/timeout/missing-result test proves that an external run reaches a durable failure or bounded recovery state rather than an infinite unknown-status wait.
6. `python -m compileall cmd/orchestrator`, the core tests, and the full unittest discovery pass. Any live validation instance uses `18766`, never `8766`.
