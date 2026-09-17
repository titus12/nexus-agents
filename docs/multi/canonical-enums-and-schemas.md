# Canonical Enums and Artifact Contracts

> Status: Historical artifact reference
> Machine-protocol authority: `cmd/orchestrator/contracts/`

## 1. Validation Rule

This document preserves the earlier artifact vocabulary for human reference.
Runtime Agent responses MUST use the target-state Python contract instead.
Phase-role Skills describe semantics only and do not define a second machine
protocol.

Every artifact uses this envelope:

The machine-readable baseline is
`schemas/runtime-artifacts.schema.json`. The prose below explains constraints
that also require Orchestrator logic, such as invalidation and atomic message
consumption.

```json
{
  "schema_version": "2.1",
  "artifact_type": "EvidencePacket",
  "artifact_id": "artifact-000001",
  "task_id": "task-000001",
  "plan_id": "plan-000001",
  "plan_version": 1,
  "group_id": null,
  "item_id": null,
  "revision": 1,
  "created_at": "2026-08-15T00:00:00Z",
  "created_by": "review-analyst",
  "skill_lock_hash": "sha256:example",
  "payload": {}
}
```

Required envelope rules:

- `artifact_id` is globally unique within the task;
- `revision` increases when the same logical artifact is replaced;
- prior revisions are retained;
- `plan_version` identifies the FrozenPlan under review;
- `group_id` and `item_id` are present when the artifact is scoped;
- `created_by` is one of the canonical actors;
- timestamps are UTC ISO-8601.

## 2. Canonical Actors

```text
orchestrator
review-analyst
review-solver
review-critic
human
```

## 3. Canonical Project and Task Types

### Project type

```text
go
dotnet
unity
multi_project
unknown
```

`multi_project` MUST include `project_components`, with one component record per
technology. `unknown` MUST cause additional routing work or HUMAN_GATE before
the plan can freeze.

### Task type

```text
review
bugfix
feature
refactor
test
unknown
```

`unknown` MAY be used during intake but MUST NOT remain unknown at freeze time
unless the uncertainty is explicitly accepted by a human decision.

## 4. ID Conventions

IDs are lowercase, stable, and never reused:

```text
task-000001
plan-000001
group-000001
item-000001
ev-000001
finding-000001
amendment-000001
decision-000001
artifact-000001
```

Documents using `E-001`, `F-001`, `OBJ-001`, or uppercase group IDs are
historical examples. The Orchestrator MUST normalize them before persistence or
reject the output when normalization is ambiguous.

## 5. Workflow States

```text
REQUEST_INTAKE
PROJECT_ROUTING

ZHONGSHU_ANALYST
ZHONGSHU_SOLVER
ZHONGSHU_CRITIC
ZHONGSHU_FREEZE_CHECK
ZHONGSHU_PLAN_FROZEN

MENXIA_GROUP_START
MENXIA_ITEM_ANALYST
MENXIA_ITEM_SOLVER
MENXIA_ITEM_CRITIC
MENXIA_ITEM_REVISION
MENXIA_GROUP_GATE

WAITING_HUMAN
FINALIZE
DONE
BLOCKED
```

Agents never set `workflow_state`. They emit a canonical action. Only the
Orchestrator validates the action and changes the state.

## 6. Canonical Agent Actions

### Zhongshu Analyst

```text
READY_FOR_SOLVER
NEEDS_MORE_EVIDENCE
HUMAN_GATE
BLOCKED
```

### Zhongshu Solver

```text
READY_FOR_CRITIC
REQUEST_ANALYST_EVIDENCE
HUMAN_GATE
BLOCKED
```

### Zhongshu Critic

```text
APPROVE_FREEZE
REQUEST_ANALYST_EVIDENCE
REQUEST_SOLVER_REVISION
REQUEST_REGROUP
REQUEST_PROJECT_REROUTING
HUMAN_GATE
BLOCKED
```

### Menxia Analyst

```text
EVIDENCE_SUFFICIENT
NEEDS_MORE_EVIDENCE
HUMAN_GATE
BLOCKED
```

### Menxia Solver

```text
FEASIBLE
REVISE
SPLIT
MERGE
REMOVE
NEEDS_MORE_EVIDENCE
HUMAN_GATE
BLOCKED
```

### Menxia Critic item gate

```text
APPROVE_ITEM
REVISE_ITEM
SPLIT_ITEM
MERGE_ITEM
REMOVE_ITEM
HUMAN_GATE
BLOCKED
```

### Menxia Critic group gate

```text
APPROVE_GROUP
REVISE_GROUP
HUMAN_GATE
BLOCKED
```

## 7. Legacy Action Mapping

The following mappings are permitted only while migrating existing Skill
documents:

| Legacy value | Canonical value |
|---|---|
| `READY_TO_FREEZE` | `READY_FOR_CRITIC` |
| `READY_FOR_FREEZE_CHECK` | `APPROVE_FREEZE` |
| `PASS` at item scope | `APPROVE_ITEM` |
| `PASS` at group scope | `APPROVE_GROUP` |
| `REJECT` at item scope | `REVISE_ITEM` |
| `REJECT` at group scope | `REVISE_GROUP` |
| `WAITING_HUMAN`, `NEEDS_USER_INPUT`, `REQUEST_USER_INPUT` | `HUMAN_GATE` |
| `INSUFFICIENT_CONTEXT` | `NEEDS_MORE_EVIDENCE` or `BLOCKED`; Orchestrator must disambiguate |
| `finding_id`, `objection_id` | `finding_id` |

New runtime output MUST use canonical values directly.

## 8. Finding Contract

All objections, defects, gaps, and risks use one `Finding` model:

```json
{
  "finding_id": "finding-000001",
  "scope": "plan|group|item",
  "severity": "P0|P1|P2|P3",
  "category": "requirement|evidence|logic|architecture|reuse|performance|resource|gc|security|compatibility|testability|operations",
  "claim": "The proposed cache has no bounded eviction policy.",
  "evidence_ids": ["ev-000001"],
  "impact": "Memory may grow without limit.",
  "required_resolution": "Define bounds, eviction, telemetry, and fallback.",
  "owner": "review-solver",
  "status": "open",
  "resolution_artifact_id": null
}
```

Finding status:

```text
open
accepted
rejected_with_evidence
resolved
needs_human
deferred
```

P0 and P1 findings cannot be `deferred` for a passing item or group.

## 9. Evidence Contract

```json
{
  "evidence_id": "ev-000001",
  "kind": "code|local_doc|official_doc|command|test|log|user_statement|measurement",
  "statement": "The project targets net8.0.",
  "source": "src/App/App.csproj:4",
  "source_version": "git:abc123",
  "retrieved_at": "2026-08-15T00:00:00Z",
  "confidence": 1.0,
  "supports": ["item-000001"],
  "limitations": []
}
```

Facts, inferences, assumptions, and unknowns MUST remain separate fields.
An inference is not upgraded to a fact without new evidence.

## 10. FrozenPlan, ReviewOverlay, and ReviewedPlan

### FrozenPlan

`FrozenPlan` is immutable:

```json
{
  "plan_id": "plan-000001",
  "version": 1,
  "content_hash": "sha256:example",
  "groups": [],
  "evidence_snapshot_ids": ["artifact-000001"],
  "skill_lock_hash": "sha256:example",
  "frozen_at": "2026-08-15T00:00:00Z"
}
```

### Review amendment

Menxia never overwrites FrozenPlan. It creates an amendment:

```json
{
  "amendment_id": "amendment-000001",
  "plan_id": "plan-000001",
  "plan_version": 1,
  "operation": "UPDATE_ITEM",
  "target_ids": ["item-000001"],
  "replacement_items": [],
  "reason": "Implementation detail was not feasible against the current API.",
  "finding_ids": ["finding-000001"],
  "invalidates": ["item-000002"],
  "created_by": "review-solver"
}
```

Operations:

```text
UPDATE_ITEM
SPLIT_ITEM
MERGE_ITEMS
REMOVE_ITEM
UPDATE_DEPENDENCY
UPDATE_GROUP
```

### ReviewedPlan

The Orchestrator deterministically applies accepted amendments:

```text
FrozenPlan vN + accepted ReviewOverlay vN -> ReviewedPlan vN
```

If an amendment changes group boundaries, public contracts, cross-group
dependencies, or the primary architecture direction, Menxia MUST NOT apply it
locally. The workflow returns to Zhongshu and produces `FrozenPlan vN+1`.

## 11. Invalidation Rules

An accepted amendment invalidates:

1. the target item;
2. every item whose dependency list references the target;
3. the containing group gate;
4. every later group whose declared inputs depend on the changed output.

Invalidated items return to `MENXIA_ITEM_ANALYST`. Unaffected, already approved
items remain approved.

An amendment that changes only explanatory text, without changing behavior,
contracts, dependencies, risk, or verification, may be classified
`NON_MATERIAL` and does not invalidate prior review.

## 12. Score Contract

Only Menxia Critic emits the authoritative numeric score. Analyst emits an
evidence assessment; Solver emits a feasibility assessment.

```json
{
  "requirement_coverage": 0.0,
  "evidence_sufficiency": 0.0,
  "architecture_reuse": 0.0,
  "performance_resource_gc": 0.0,
  "compatibility": 0.0,
  "testability_operations": 0.0,
  "security_risk": 0.0,
  "total": 0.0
}
```

Maximum points:

| Dimension | Maximum |
|---|---:|
| Requirement coverage | 2.0 |
| Evidence sufficiency | 2.0 |
| Architecture and reuse | 1.5 |
| Performance, resource, and GC | 1.5 |
| Compatibility | 1.5 |
| Testability and operations | 1.0 |
| Security and risk | 0.5 |
| **Total** | **10.0** |

Pass requires all conditions:

```text
total >= 8.0
no unresolved P0/P1
requirement_coverage >= 1.5
evidence_sufficiency >= 1.5
compatibility >= 1.0
no pending HUMAN_GATE
all required verification steps are concrete
```

## 13. HUMAN_GATE Contract

```json
{
  "decision_id": "decision-000001",
  "task_id": "task-000001",
  "plan_id": "plan-000001",
  "plan_version": 1,
  "group_id": "group-000001",
  "item_id": "item-000001",
  "question": "Choose the compatibility strategy.",
  "options": [
    {
      "id": "A",
      "label": "Preserve backward compatibility",
      "impact": "More implementation work, lower migration risk."
    }
  ],
  "required_actor_ids": ["configured-human-id"],
  "status": "open",
  "expires_at": null,
  "consumed_message_id": null
}
```

The accepted Feishu reply format is:

```text
decision-000001 A
```

Validation requires:

- the decision is active;
- task/group/item scope matches;
- sender is authorized;
- message ID has not been consumed;
- option exists;
- the reply is newer than gate creation;
- consumption is persisted atomically.

## 14. Persisted Task State

```json
{
  "task_id": "task-000001",
  "workflow_state": "MENXIA_ITEM_CRITIC",
  "plan_id": "plan-000001",
  "plan_version": 1,
  "active_group_id": "group-000001",
  "active_item_id": "item-000001",
  "global_revision_round": 3,
  "zhongshu_revision_round": 1,
  "group_revision_round": 1,
  "item_revision_round": 1,
  "agent_call_count": 14,
  "skill_lock_hash": "sha256:example",
  "active_decision_id": null,
  "last_artifact_id": "artifact-000014",
  "updated_at": "2026-08-15T00:00:00Z"
}
```

The state update, artifact write, transition log, and message consumption MUST
be one transaction or use an equivalent crash-safe protocol.
