# Linear FSM Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `WorkflowEngine` and immutable `WorkflowContext` the only production FSM, migrate all business and parallel behavior, and delete the obsolete mutable FSM implementation.

**Architecture:** Keep the domain pure: states consume `DomainEvent` and return typed `StateDecision`. Runtime services own repository commits, effects, ports, worker nodes, admission leases, and event normalization. The application composes these services and never owns a second state machine.

**Tech Stack:** Python 3.11+, frozen dataclasses, `typing.Protocol`, JSON journal/snapshot persistence, `unittest`, existing Multica/Feishu adapters, and bounded `concurrent.futures` workers.

---

## Working rules

- Preserve unrelated existing working-tree changes. Stage only files belonging to the current task in each commit.
- Do not modify `C:\Users\Administrator\.codex\config.toml` or `C:\Users\Administrator\.codex\nexus-model-catalog.json`.
- Never run the active Nexus service on another port. Validation instances use `18766`; the active instance normally uses `8766`.
- No production task may run both FSM implementations. During migration, New owns state and temporary compatibility is limited to effect runners around external adapters.

### Task 1: Lock the new contracts with failing tests

**Files:**
- Create: `cmd/test_linear_fsm_dto_migration.py`
- Create: `cmd/test_linear_fsm_parallel_effects.py`
- Create: `cmd/test_linear_fsm_concurrency.py`
- Create: `cmd/test_linear_fsm_terminal_run_status.py`
- Create: `cmd/test_linear_fsm_entrypoint.py`
- Create: `cmd/test_linear_fsm_no_legacy_references.py`
- Modify: `cmd/test_linear_fsm_architecture.py`

- [ ] **Step 1: Add tests for the expanded immutable context.**

  Assert that the context contains typed request, dispatch, review, parallel, recovery, human-gate, and audit aggregates; all aggregates are frozen; and arbitrary mapping patches are rejected.

- [ ] **Step 2: Add the DTO migration test cases.**

  Cover every legacy field in the design mapping, derived finding-list equality, malformed state rejection, artifact-reference preservation, and round-trip serialization.

- [ ] **Step 3: Add the parallel effect test cases.**

  Build one node with two workers, assert deterministic fan-in order, one admission lease per worker, terminal worker-result reuse after restart, and no mutation of the main context.

- [ ] **Step 4: Add terminal remote-run status tests.**

  Assert `FAILED` becomes a durable failure event even when `poll()` returns no comment and no result file, while `UNKNOWN` is bounded by a deadline and cannot produce an infinite wait.

- [ ] **Step 5: Add entrypoint reference tests.**

  Inspect production source under `cmd/orchestrator` and fail if it constructs/imports `StateMachine`, `StateContext`, or the deleted legacy registries after cutover.

- [ ] **Step 6: Add the no-legacy reference test.**

  Walk only tracked production `.py` files under `cmd/orchestrator`, exclude the migration converter's source-field string table, and fail on imports/usages of `StateContext`, `StateMachine`, `BaseState`, `orchestrator.events.Event`, `orchestrator.models.AgentRequest`, or the old state/transition registries.

- [ ] **Step 7: Run the new tests and verify they fail for the missing contracts.**

Run from `cmd`:

```powershell
rtk python -m unittest -v test_linear_fsm_dto_migration test_linear_fsm_parallel_effects test_linear_fsm_concurrency test_linear_fsm_terminal_run_status test_linear_fsm_entrypoint
```

Expected: failures identify missing aggregate fields, effect runners, status events, and New entrypoint wiring.

### Task 2: Complete the immutable domain model

**Files:**
- Modify: `cmd/orchestrator/domain/context.py`
- Modify: `cmd/orchestrator/domain/decisions.py`
- Modify: `cmd/orchestrator/domain/events.py`
- Modify: `cmd/orchestrator/domain/findings.py`
- Modify: `cmd/orchestrator/domain/states.py`
- Modify: `cmd/orchestrator/domain/transitions.py`
- Modify: `cmd/orchestrator/models.py`
- Modify: `cmd/test_linear_fsm_states.py`
- Modify: `cmd/test_linear_fsm_reducer.py`

- [ ] **Step 1: Add typed aggregates for request, dispatch, parallel, audit, and detailed recovery state.**

  Use frozen dataclasses and tuples for collections. Keep large request/reply payloads as artifact references with digests; do not add an arbitrary mutable context dictionary.

- [ ] **Step 2: Move the durable `Finding` value object to `domain.findings`.**

  Preserve its identity, scope, status normalization, active/resolved predicates, and serialization behavior. All New code must import the domain value object; the legacy `models.Finding` name becomes a temporary re-export and is removed with the old API.

- [ ] **Step 3: Extend `StateDecision` and `EffectRequest` for typed effect identity.**

  Require `effect_id`, `effect_type`, `task_id`, and `idempotency_key`; add effect payload metadata sufficient to reconstruct an Agent, Node, Artifact, Notification, or recovery operation.

- [ ] **Step 4: Add domain events for dispatch accepted, remote completed, remote failed, timeout, node completed, node failed, human reply, and persistence failure.**

  Every event carries `task_id`, `sequence`, `occurred_at`, and a typed or validated payload reference.

- [ ] **Step 5: Replace generic placeholder state behavior with concrete decisions.**

  Each business state must validate its role-specific event and return the correct typed review/update/effect decision. `on_timeout()` must return a bounded retry/failure decision, never raise `timeout handling is not migrated`.

- [ ] **Step 6: Keep transition resolution closed.**

  Add registered actions for external completion, failure, timeout, node fan-in, human resume, retry, block, cancel, and fail. Resume targets must come only from validated context metadata.

- [ ] **Step 7: Run domain and reducer tests.**

```powershell
rtk python -m unittest -v test_linear_fsm_states test_linear_fsm_transitions test_linear_fsm_reducer test_linear_fsm_architecture
```

Expected: all domain decisions are immutable, all legal transitions are registered, and invalid actions/targets fail closed.

### Task 3: Define runtime ports and preserve concurrency admission

**Files:**
- Modify: `cmd/orchestrator/runtime/ports.py`
- Create: `cmd/orchestrator/runtime/concurrency.py`
- Modify: `cmd/orchestrator/runtime/engine.py`
- Modify: `cmd/orchestrator/runtime/effects.py`
- Modify: `cmd/orchestrator/runtime/nodes.py`
- Modify: `cmd/orchestrator/runtime/__init__.py`
- Modify: `cmd/orchestrator/concurrency.py`
- Modify: `cmd/test_linear_fsm_concurrency.py`

- [ ] **Step 1: Add exact typed port DTOs and Protocols.**

  Define `AgentDispatchRequest`, `RemoteRunStatus`, `ArtifactInput/Receipt`, `PollRequest`, `NotificationRequest/Receipt`, `AdmissionKey`, `LeaseReceipt`, `DomainEventInbox`, and `ConcurrencyAdmissionPort`. `AgentTransportPort.status()` must return `RUNNING`, `COMPLETED`, `FAILED`, or `UNKNOWN`.

  The admission DTOs have these exact fields: `AdmissionKey(task_id, phase, worker_id, revision_id, external_target_key)`, and `LeaseReceipt(lease_id, key, acquired_at, expires_at)`. `acquire(key, deadline)` returns `LeaseReceipt | None`; `refresh()` and `release()` return `bool`.

- [ ] **Step 2: Adapt the existing `ConcurrencyAdmission` behind the new port.**

  Preserve global, per-task, analyst, critic, lease TTL, duplicate logical-key rejection, wait-with-deadline, refresh, release, expiration, and `(issue_id, agent_id)` serialization.

- [ ] **Step 3: Make `EffectManager` emit durable terminal result records.**

  Map runner success to `SUCCEEDED`, typed runner failure to `FAILED` with `FailureRecord`, and remote terminal failure to a failure event rather than a pending effect.

- [ ] **Step 4: Add the event inbox and effect-to-event bridge.**

  After each effect result, normalize it into a `DomainEvent` with the persisted task/request/operation identity. Unknown correlation must carry a deadline and diagnostic reason.

- [ ] **Step 5: Run runtime contract tests.**

```powershell
rtk python -m unittest -v test_linear_fsm_engine test_linear_fsm_effects test_linear_fsm_nodes test_linear_fsm_locking test_linear_fsm_concurrency
```

Expected: the engine commits one immutable transition per event, effect failures are durable, and capacity/lease ownership cannot be bypassed.

### Task 4: Extract business policies from the legacy state module

**Files:**
- Create: `cmd/orchestrator/domain/policies/__init__.py`
- Create: `cmd/orchestrator/domain/policies/prompts.py`
- Create: `cmd/orchestrator/domain/policies/replies.py`
- Create: `cmd/orchestrator/domain/policies/zhongshu.py`
- Create: `cmd/orchestrator/domain/policies/menxia.py`
- Modify: `cmd/orchestrator/contracts/*.py`
- Modify: `cmd/test_analyst_contract_v31.py`
- Modify: `cmd/test_solver_contract_fix.py`
- Modify: `cmd/test_role_phase_contracts.py`
- Modify: `cmd/test_linear_fsm_direct_transport.py`

- [ ] **Step 1: Move pure prompt projections and role schemas.**

  Convert functions currently accepting `StateContext` into functions accepting the new typed aggregates and returning `PromptSpec`, normalized role replies, or typed validation errors.

- [ ] **Step 2: Move Analyst/Solver/Critic finding and repair rules.**

  Preserve requirement binding, evidence merge, task-graph validation, critic finding identity, revision limits, and repair feedback without mutating context or performing I/O.

- [ ] **Step 3: Move Menxia scope and finding-ledger rules.**

  Require `(group_id, item_id)` scope before resolving findings; reject unscoped direct calls that could close an unrelated finding.

- [ ] **Step 4: Add policy tests before deleting equivalent legacy helpers.**

  Reuse the current fixture payloads but instantiate `WorkflowContext` and typed policy inputs. Preserve all acceptance/rejection reasons.

### Task 5: Map parallel execution to typed nodes and effects

**Files:**
- Create: `cmd/orchestrator/runtime/agent_effects.py`
- Create: `cmd/orchestrator/runtime/node_effects.py`
- Create: `cmd/orchestrator/runtime/recovery.py`
- Modify: `cmd/orchestrator/runtime/nodes.py`
- Modify: `cmd/orchestrator/runtime/repository.py`
- Modify: `cmd/orchestrator/zhongshu_parallel.py`
- Modify: `cmd/orchestrator/menxia_parallel.py`
- Modify: `cmd/test_linear_fsm_parallel_effects.py`
- Modify: `cmd/test_zhongshu_parallel.py`
- Modify: `cmd/test_menxia_parallel.py`
- Modify: `cmd/test_menxia_canary.py`
- Modify: `cmd/test_parallel_runtime_correlation.py`

- [ ] **Step 1: Implement `AgentWorkerRunner`.**

  Acquire admission, dispatch one idempotent `AgentDispatchRequest`, refresh the lease while polling, call `status()`, normalize replies, write result artifacts, and return `WorkerResult` without mutating workflow context.

- [ ] **Step 2: Implement `NodeEffectRunner`.**

  Execute a deterministic `ReviewNode`, reuse terminal worker artifacts by `(task_id, node_run_id, worker_id, request_id)`, persist one node result, and produce a node completion/failure event.

- [ ] **Step 3: Convert Zhongshu fan-out/fan-in.**

  Convert `ParallelWorker` to `WorkerBinding`, `ParallelFanIn` to `NodeResult`, and coordinator validation/merge logic to typed joiners and domain policies. Preserve target serialization and deterministic order.

- [ ] **Step 4: Convert Menxia group/item coordination.**

  Replace mutable `MenxiaItemRuntime` state updates with immutable item ledger records in `ReviewState`/node artifacts. Group dependencies, item revision, failure policy, and group gate remain explicit domain decisions.

- [ ] **Step 5: Run parallel and recovery tests.**

```powershell
rtk python -m unittest -v test_linear_fsm_parallel_effects test_linear_fsm_nodes test_linear_fsm_persistence test_zhongshu_parallel test_menxia_parallel test_parallel_runtime_correlation
```

Expected: fan-out/fan-in works with bounded concurrency, restart reuses terminal results, and no worker writes the main workflow snapshot.

### Task 6: Implement legacy external adapters as temporary effect runners

**Files:**
- Create: `cmd/orchestrator/runtime/compat_effects.py`
- Modify: `cmd/orchestrator/adapters.py`
- Modify: `cmd/orchestrator/agent_result_file.py`
- Modify: `cmd/orchestrator/transport/normalizer.py`
- Modify: `cmd/orchestrator/transport/replies.py`
- Modify: `cmd/test_linear_fsm_terminal_run_status.py`
- Modify: `cmd/test_agent_result_file.py`
- Modify: `cmd/test_agent_result_file_poll.py`
- Modify: `cmd/test_prompt_bundle.py`
- Modify: `cmd/test_prompt_bundle_dispatch.py`
- Modify: `cmd/test_feishu_command_parser_v32.py`
- Modify: `cmd/test_json_bom_v31.py`

- [ ] **Step 1: Wrap existing Multica calls behind `AgentTransportPort`.**

  Keep dispatch, poll, lookup, and status correlation in the adapter. Return `RemoteRunStatus.FAILED` immediately for a failed Run even if no comment/result artifact exists.

- [ ] **Step 2: Wrap lifecycle/artifact and notification writes behind ports.**

  Keep file naming and external message compatibility in adapters; domain states receive only artifact references and normalized events.

- [ ] **Step 3: Add regression tests for the Analyst failure.**

  Reproduce a failed remote run with null trigger/coalesced/delivered comment ids. Assert the adapter still returns the exact failed run by request/agent/issue fallback and the runtime does not poll forever.

- [ ] **Step 4: Run transport and failure tests.**

```powershell
rtk python -m unittest -v test_linear_fsm_terminal_run_status test_linear_fsm_transport test_linear_fsm_direct_transport test_agent_result_file test_agent_result_file_poll test_prompt_bundle test_prompt_bundle_dispatch
```

### Task 7: Switch the application to the New engine

**Files:**
- Modify: `cmd/orchestrator/app.py`
- Modify: `cmd/orchestrator/persistence.py`
- Modify: `cmd/orchestrator/recovery.py`
- Modify: `cmd/orchestrator/lifecycle.py`
- Modify: `cmd/orchestrator/runtime/repository.py`
- Modify: `cmd/orchestrator/runtime/lock_adapter.py`
- Modify: `cmd/orchestrator/runtime/__init__.py`
- Modify: `cmd/test_linear_fsm_entrypoint.py`
- Modify: `cmd/test_full_workflow_v31.py`
- Modify: `cmd/test_review_orchestrator_v2.py`

- [ ] **Step 1: Add repository initialization and legacy DTO conversion.**

  Detect an existing legacy state document, write large payloads as immutable artifacts, map every field from the design table, verify derived finding ids, and initialize `workflow-state.json`/`workflow-events.jsonl` without exposing `StateContext` to New runtime code.

- [ ] **Step 2: Replace the app constructor wiring.**

  Construct New repository, lock, admission, ports, state registry, reducer, engine, effect manager, and event inbox. Remove `self.machine`, `self.store`, and direct mutable context writes.

- [ ] **Step 3: Replace the main loop.**

  Load a snapshot, enqueue/normalize one event, call `WorkflowEngine.dispatch()`, execute pending effects, enqueue effect result events, and stop only on New terminal states. A bounded timeout event must be produced when no remote terminal state is observed.

- [ ] **Step 4: Move human gate, notification, final-delivery, interruption, and recovery behavior to effects/events.**

  Preserve idempotency keys, notification dedupe, lock ownership, journal-first ordering, and final artifact delivery.

- [ ] **Step 5: Run the isolated end-to-end suite.**

```powershell
rtk python -m unittest -v test_linear_fsm_entrypoint test_full_workflow_v31 test_review_orchestrator_v2 test_process_interruption_v31 test_runtime_diagnostics_v31 test_human_gate_protocol_v31 test_human_gate_queue_v31
```

Expected: production orchestration runs through New Engine and produces durable state/effect/event records.

### Task 8: Port the remaining behavior tests

**Files:**
- Modify: `cmd/test_fsm_core_v31.py`
- Modify: `cmd/test_timeout_loop_v31.py`
- Modify: `cmd/test_heartbeat_and_timeout_v31.py`
- Modify: `cmd/test_heartbeat_display_v32.py`
- Modify: `cmd/test_notifications_and_gate_v31.py`
- Modify: `cmd/test_menxia_item_prompt.py`
- Modify: `cmd/test_menxia_persistence.py`
- Modify: `cmd/test_zhongshu_iterative_convergence.py`
- Modify: `cmd/test_zhongshu_task_graph.py`
- Modify: `cmd/test_zhongshu_task_review_queue.py`
- Modify: `cmd/test_zhongshu_evidence_routing.py`
- Modify: `cmd/test_group_revision.py`
- Modify: `cmd/test_orchestrator_correlation_v32.py`
- Modify: `cmd/test_multica_dispatch_assignment_v31.py`
- Modify: `cmd/test_multica_poll_filter_v31.py`
- Modify: `cmd/test_final_delivery.py`
- Modify: `cmd/test_notification_dedupe_v31.py`
- Modify: `cmd/test_notification_rendering_v32.py`
- Modify: `cmd/test_reply_retry_scope.py`
- Modify: `cmd/test_unstructured_reply_repair.py`
- Modify: `cmd/test_repair_mvp.py`
- Modify: `cmd/test_comment_feed_v32.py`
- Modify: `cmd/test_role_phase_contracts.py`
- Modify: `cmd/test_solver_contract_fix.py`
- Modify: `cmd/test_solver_prompt_v31.py`
- Modify: `cmd/test_solver_revision_prompt.py`
- Modify: `cmd/test_reply_fallback_v32.py`
- Modify: `cmd/test_lifecycle.py`
- Modify: `cmd/test_core_hardening.py`
- Modify: `cmd/test_deterministic_hardening.py`
- Modify: `cmd/test_hourly_logging_v31.py`
- Modify: `cmd/test_v31_audit.py`
- Modify: `cmd/test_menxia_canary.py`
- Modify: `cmd/test_feishu_command_parser_v32.py`
- Modify: `cmd/test_json_bom_v31.py`

- [ ] **Step 1: Replace legacy fixtures.**

  Replace `StateContext` fixtures with `WorkflowContext` snapshots, `Event` with `DomainEvent`, and direct state-machine calls with `WorkflowEngine` dispatches or pure policy calls.

- [ ] **Step 2: Preserve behavior assertions.**

  Keep exact action names only at the transport normalization boundary; assert canonical domain events, typed decisions, durable failures, artifact references, and final snapshots inside the runtime.

- [ ] **Step 3: Remove only duplicated V2 API tests.**

  Delete a test file only after its behavior assertions exist in New tests and a repository-wide reference check proves it exercises no remaining production API.

- [ ] **Step 4: Run all migrated behavior tests.**

```powershell
rtk python -m unittest -v test_analyst_contract_v31 test_analyst_feedback test_role_phase_contracts test_solver_contract_fix test_solver_prompt_v31 test_solver_revision_prompt test_zhongshu_iterative_convergence test_zhongshu_task_graph test_zhongshu_task_review_queue test_zhongshu_evidence_routing test_human_gate_protocol_v31 test_human_gate_queue_v31 test_notifications_and_gate_v31
```

### Task 9: Remove the legacy FSM and compatibility branches

**Files:**
- Delete: `cmd/orchestrator/state_machine.py`
- Delete: `cmd/orchestrator/states.py`
- Delete: `cmd/orchestrator/transitions.py`
- Delete: `cmd/orchestrator/context.py`
- Delete: `cmd/orchestrator/events.py`
- Delete: `cmd/orchestrator/concurrency.py`
- Delete: `cmd/orchestrator/persistence.py`
- Modify: `cmd/orchestrator/__init__.py`
- Delete: `cmd/orchestrator/recovery.py`
- Modify: `cmd/orchestrator/models.py`
- Modify: `cmd/orchestrator/locks.py`
- Modify: `cmd/orchestrator/runtime/compat_effects.py`
- Modify: `cmd/orchestrator/app.py`
- Delete: only tests proven to be V2-API-only after Task 8

- [ ] **Step 1: Run the production reference audit.**

```powershell
rtk rg -n "StateContext|StateMachine|from \.states|from \.transitions|from \.context|BaseState|build_state_registry|save_state\(" cmd/orchestrator --glob '*.py'
```

  Expected: only the one-time migration converter may mention source field names; no production runtime imports or constructs legacy FSM types or old root-level event/model/concurrency/persistence types.

- [ ] **Step 2: Delete the four legacy FSM modules and old exports.**

  Use `apply_patch` for file deletion and remove imports from all production modules. Do not delete user-owned unrelated files.

- [ ] **Step 3: Remove temporary compatibility effect runners if native transport runners are complete.**

  If an external adapter is still required, retain only the typed port implementation and its boundary normalizer, not a legacy FSM wrapper.

- [ ] **Step 4: Run compile and static checks.**

```powershell
rtk python -m compileall cmd/orchestrator
rtk rg -n "StateContext|StateMachine|BaseState|if .*workflow_state|self\.ctx\.|save_state\(" cmd/orchestrator/domain cmd/orchestrator/runtime
```

Expected: no legacy context/FSM references and no direct mutable-context writes in domain/runtime.

### Task 10: Complete verification and live smoke test

**Files:**
- Modify: `cmd/test_linear_fsm_no_legacy_references.py`
- Modify: `openwiki/quickstart.md` only if the production startup command changes
- Modify: relevant OpenWiki source documentation only if runtime ownership changes

- [ ] **Step 1: Run New contract suites.**

```powershell
rtk python -m unittest -v test_linear_fsm_architecture test_linear_fsm_direct_transport test_linear_fsm_effects test_linear_fsm_engine test_linear_fsm_failures test_linear_fsm_locking test_linear_fsm_nodes test_linear_fsm_persistence test_linear_fsm_reducer test_linear_fsm_states test_linear_fsm_transitions test_linear_fsm_transport test_linear_fsm_dto_migration test_linear_fsm_parallel_effects test_linear_fsm_concurrency test_linear_fsm_terminal_run_status test_linear_fsm_entrypoint test_linear_fsm_no_legacy_references
```

- [ ] **Step 2: Run the complete core suite.**

```powershell
rtk python cmd/run_core_tests.py
rtk python -m unittest discover -s cmd -p 'test_*.py' -v
```

- [ ] **Step 3: Run the isolated service smoke test.**

  Start a validation instance on `18766`, exercise one Analyst success and one remote failure, inspect `workflow-events.jsonl` and `workflow-state.json`, and verify the failure has a `failure_id`, terminal status, request correlation, and no unbounded polling.

- [ ] **Step 4: Inspect the final diff and working-tree scope.**

```powershell
rtk git diff --check
rtk git status --short
```

Expected: only intended migration files are changed/deleted; unrelated user changes remain untouched.
