# Multi-Agent Review Runtime Core Design

## Status

Approved for implementation on 2026-08-16.

This is a new implementation path. Existing review scripts, old Agent prompts,
legacy action names, and legacy artifact formats are reference material only.
They are not compatibility targets and will not be migrated into the new
runtime.

## Goal

Build a deterministic Runtime Core for the v2.1 multi-agent solution review
protocol. The first implementation slice owns canonical identifiers, artifact
envelopes, workflow states, legal action transitions, revision budgets,
immutable FrozenPlan handling, and Finding lifecycle rules.

The Runtime Core must be independently testable before Agent dispatch, Feishu
HUMAN_GATE, or UI integration is added.

## Architecture

The new implementation lives under `internal/multireview` and is isolated from
the legacy Python orchestrator and the existing generic workflow runner.

The runtime is divided into these boundaries:

- `model.go`: canonical domain values and artifact/task structures.
- `validation.go`: identifier, envelope, score, Finding, and plan validation.
- `state_machine.go`: workflow action validation and state transitions.
- `plan.go`: FrozenPlan immutability and accepted ReviewOverlay application
  boundaries.
- `store.go`: storage interfaces used by the orchestrator and test doubles.
- `persistence.go`: durable, crash-safe state and artifact persistence added
  after the in-memory contract is proven.

The package must not call Multica, Feishu, Agent APIs, or the old
`workflowrunner`. Those integrations will depend on the Runtime Core through
explicit interfaces in later slices.

## Canonical model

The package mirrors the normative v2.1 contracts:

- workflow states from `REQUEST_INTAKE` through `DONE` and `BLOCKED`;
- canonical Agent actions for Zhongshu Analyst/Solver/Critic and Menxia
  Analyst/Solver/Critic;
- six-digit stable IDs such as `task-000001`, `ev-000001`, and
  `finding-000001`;
- artifact envelopes containing schema version, artifact type, task identity,
  revision, creator, Skill lock hash, and payload;
- Finding severity/status/owner rules;
- score dimension limits and pass prerequisites.

No legacy identifiers such as `D1`, `E-001`, or `ev-001` are accepted by the
new package.

## State transition rules

Only the orchestrator-facing transition function changes workflow state.
Agents return actions; they do not mutate state directly.

The transition function must:

1. reject actions not legal for the current state;
2. enforce HUMAN_GATE and BLOCKED as terminal waits until explicitly resumed;
3. increment material revision counters only on revision transitions;
4. enforce global, Zhongshu, group, and item round ceilings;
5. require an active group/item for Menxia item states;
6. prevent completion while unresolved P0/P1 findings or active decisions remain.

The first slice will make transition decisions pure and deterministic so they
can be tested without a database or external service.

## FrozenPlan and amendments

`FrozenPlan` is immutable after creation. Menxia changes are represented as
accepted `ReviewAmendment` records applied to a separate working
`ReviewedPlan`.

The first slice validates amendment scope and computes invalidation targets:
the target item, dependent items, the containing group gate, and later groups
that depend on changed outputs. Applying the actual working-plan overlay is a
later slice, but the boundary is defined now so downstream code does not
silently mutate the frozen plan.

## Persistence direction

The production store will provide atomic commits for task state, artifacts,
transition records, and consumed HUMAN_GATE message IDs. The storage interface
is defined before the concrete implementation so the state machine remains
independent of persistence details.

The durable implementation will use a transactional local store rather than
the existing process-memory workflow runner. Every persisted record carries a
task scope and revision; prior artifact revisions are retained.

## Integration order

1. Runtime Core types, validation, and pure transitions.
2. Durable store and restart recovery.
3. Six versioned Composite Skill bundles and project appendices.
4. Agent dispatch adapter with scoped context isolation.
5. Feishu HUMAN_GATE adapter with decision-ID binding and idempotent message
   consumption.
6. ReviewedPlan/ApprovedPlan assembly and Multica/UI integration.

## Error handling and safety

- Invalid identifiers, actions, artifact envelopes, scores, and state
  transitions return typed errors.
- Unsupported or unknown actions are rejected; they are not silently mapped
  to legacy values.
- P0/P1 findings block approval until resolved, rejected with evidence, or
  explicitly accepted through a human decision.
- The package never claims Agent execution, code changes, tests, or device
  validation that it did not perform.

## Testing strategy

The first slice uses table-driven Go unit tests for:

- every legal and illegal state/action pair;
- revision budget enforcement and no-progress handling;
- canonical ID validation;
- artifact envelope validation;
- Finding blocking rules;
- FrozenPlan immutability;
- HUMAN_GATE and BLOCKED pause behavior.

Later slices add persistence crash/restart tests, schema fixture tests,
dispatcher contract tests, and end-to-end one-group/one-item pilot tests.

## Non-goals

- migrating or wrapping the old Python orchestrator;
- accepting legacy action names or IDs;
- modifying existing generic workflow-run behavior;
- implementing Feishu, Multica, or UI integration in the first slice;
- executing actual code changes proposed by an ApprovedPlan.
