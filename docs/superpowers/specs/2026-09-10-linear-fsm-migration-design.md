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

