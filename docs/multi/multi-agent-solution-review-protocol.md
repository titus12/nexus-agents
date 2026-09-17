# Multi-Agent Solution Review Protocol

> Status: Historical semantic workflow reference
> Machine-protocol authority: `cmd/orchestrator/contracts/`
> Audience: Orchestrator, review-analyst, review-solver, review-critic, human approver

## 1. Mission

This protocol defines a stable mechanism for producing reliable implementation
proposals for Go, .NET, Unity, and mixed projects.

The same three runtime agents serve in two institutionally separate phases:

```text
ZHONGSHU
  research, plan, decompose, group, challenge, freeze

MENXIA
  review each group and item, add feasibility detail, debate, score, approve or reject
```

```text
review-analyst
review-solver
review-critic
```

The Orchestrator owns state, transitions, persistence, Skill loading, result
validation, round budgets, HUMAN_GATE, and final assembly. The exact Agent
response envelope, fields, types, enums, contract id, and schema hash are
defined only by the target-state Python contract; this document must not be
copied as a machine response protocol.

The primary result is an `ApprovedPlan`. The mechanism MUST NOT claim code was
changed, tests passed, or devices were validated unless a separate execution
workflow records that evidence.

## 2. Historical References

For runtime machine validation, use `cmd/orchestrator/contracts/<state>.py`
and `cmd/orchestrator/contracts/common.py`. The remaining documents are
semantic references only.

The generic role-design documents are reference-only. They MUST NOT be loaded
as runtime instructions.

## 3. Design Invariants

1. Evidence precedes conclusions.
2. Facts, inferences, assumptions, and unknowns remain separate.
3. Zhongshu and Menxia never share the same active Skill.
4. Menxia starts from a versioned, immutable FrozenPlan.
5. Menxia reviews one group at a time and one item at a time.
6. The next item cannot start until the active item is approved or explicitly
   superseded by an accepted structural amendment.
7. The next group cannot start until the active group is approved.
8. No unresolved P0/P1 finding can be hidden by a score.
9. Only the Orchestrator changes workflow state.
10. Every transition is schema-validated, persisted, and auditable.
11. HUMAN_GATE resumes only through an authorized, decision-ID-bound reply.
12. `MAX_ROUNDS=15` is a hard ceiling on material revision rounds, not ordinary
    Agent calls.

## 4. Context Isolation

The same model identity may serve both phases, but each phase MUST use a clean
session or equivalent context reset.

After Zhongshu freezes the plan:

```text
close Zhongshu context
  -> persist FrozenPlan and evidence snapshot
  -> create new Menxia context
  -> load only Menxia Skill and scoped artifacts
```

Menxia receives:

- the original request;
- project and task metadata;
- the FrozenPlan;
- the evidence snapshot;
- the active group and active item;
- relevant approved dependencies;
- relevant findings and amendment history;
- only the code/doc excerpts needed for the active item.

Menxia MUST NOT receive the complete Zhongshu conversation or hidden reasoning.

## 5. Skill Model

There are six runtime composite Skills:

```text
zhongshu-analyst
zhongshu-solver
zhongshu-critic
menxia-analyst
menxia-solver
menxia-critic
```

Each dispatch loads:

```text
common protocol contract
  + exactly one composite phase-role Skill
  + one project appendix
  + optional small task overlay
```

The capability names inside a Skill document are internal sections, not
separate physical Skills unless a later implementation explicitly creates and
versions them.

All Skill identifiers, versions, and content hashes are pinned at task
creation. A running task MUST NOT silently pick up a changed Skill.

## 6. Canonical Workflow

Canonical state names and actions are defined in
`canonical-enums-and-schemas.md`. The Orchestrator MUST reject or normalize
legacy action names before persistence.

```text
REQUEST_INTAKE
  -> PROJECT_ROUTING
  -> ZHONGSHU_ANALYST
  -> ZHONGSHU_SOLVER
  -> ZHONGSHU_CRITIC
  -> ZHONGSHU_FREEZE_CHECK
  -> ZHONGSHU_PLAN_FROZEN
  -> MENXIA_GROUP_START
  -> MENXIA_ITEM_ANALYST
  -> MENXIA_ITEM_SOLVER
  -> MENXIA_ITEM_CRITIC
  -> MENXIA_GROUP_GATE
  -> FINALIZE
  -> DONE
```

Any eligible state may enter:

```text
WAITING_HUMAN
BLOCKED
```

## 7. Zhongshu Input and Output

### Input

- user request;
- project identifier and available repository context;
- local rules, Skills, knowledge sources, and official documentation access;
- configured project constraints;
- previous human decisions for the task.

### Output

```text
TaskContext
EvidencePacket
AnalystDraft with CandidateGroups
ZhongshuPlan with formal groups and ordered items
Finding set
FrozenPlan vN
```

Zhongshu does not produce final approval. It produces a sufficiently grounded
plan that is ready for independent Menxia review.

## 8. Zhongshu Internal Loop

### 8.1 Analyst

Analyst:

- determines project type and task type;
- searches knowledge sources, local docs, official docs, and real code;
- records facts, inferences, assumptions, unknowns, reusable components, and
  affected areas;
- proposes CandidateGroups;
- reports evidence gaps and human questions.

Analyst MUST NOT define final acceptance criteria or final formal groups.

Actions:

```text
READY_FOR_SOLVER
NEEDS_MORE_EVIDENCE
HUMAN_GATE
BLOCKED
```

### 8.2 Solver

Solver:

- turns Analyst evidence and CandidateGroups into an implementation direction;
- compares alternatives for material choices;
- creates formal groups and ordered items;
- defines dependencies, boundaries, compatibility expectations, verification
  intent, fallback, and rollback;
- keeps unsupported claims visible.

Actions:

```text
READY_FOR_CRITIC
REQUEST_ANALYST_EVIDENCE
HUMAN_GATE
BLOCKED
```

### 8.3 Critic

Critic:

- challenges requirement coverage and evidence;
- checks group boundaries, order, dependencies, architecture direction, reuse,
  compatibility, performance/resource/GC risks, testing, security, and
  operations;
- opens canonical Findings;
- recommends freeze or a specific bounded return path.

Actions:

```text
APPROVE_FREEZE
REQUEST_ANALYST_EVIDENCE
REQUEST_SOLVER_REVISION
REQUEST_REGROUP
REQUEST_PROJECT_REROUTING
HUMAN_GATE
BLOCKED
```

### 8.4 Return routing

```text
REQUEST_ANALYST_EVIDENCE
  -> ZHONGSHU_ANALYST

REQUEST_SOLVER_REVISION
  -> ZHONGSHU_SOLVER

REQUEST_REGROUP
  -> ZHONGSHU_SOLVER

REQUEST_PROJECT_REROUTING
  -> PROJECT_ROUTING

APPROVE_FREEZE
  -> ZHONGSHU_FREEZE_CHECK
```

The Orchestrator increments a Zhongshu revision round only when an artifact is
materially revised after a return.

## 9. Freeze Check

The Orchestrator may create FrozenPlan only when:

- project type and task type are resolved or human-accepted;
- user objective, constraints, goals, and non-goals are explicit;
- material code and documentation evidence is recorded;
- formal groups and items exist;
- each group has one primary objective;
- item and group dependencies are acyclic and ordered;
- assumptions and unknowns are visible;
- no Zhongshu P0/P1 finding remains open;
- Solver has produced a feasible planning baseline;
- Critic returned `APPROVE_FREEZE`;
- all artifacts pass schema validation.

FrozenPlan contains the plan content, evidence snapshot references, Skill lock,
content hash, and version. It is immutable.

## 10. Menxia Input and Output

### Input

- original request and TaskContext;
- FrozenPlan vN;
- frozen evidence snapshot;
- current group;
- current item;
- approved dependency outputs;
- current findings and accepted amendments;
- project/version/platform context.

### Output

```text
ItemEvidenceAudit
ItemFeasibilityProposal
ItemReviewDecision
GroupReviewDecision
ReviewOverlay vN
ReviewedPlan vN
ApprovedPlan
```

Menxia examines and improves the plan through explicit amendments. It never
silently edits FrozenPlan.

## 11. Menxia Group and Item Loop

Groups are processed in declared dependency order. Items are processed in
declared item order.

```text
MENXIA_GROUP_START
  -> MENXIA_ITEM_ANALYST
  -> MENXIA_ITEM_SOLVER
  -> MENXIA_ITEM_CRITIC
       | approve item -> next item
       | revise item  -> bounded item revision
       | structural amendment -> apply and invalidate affected scope
       | human gate -> wait
       | blocked -> stop
  -> MENXIA_GROUP_GATE
       | approve group -> next group
       | revise group -> return to first invalidated item
       | human gate -> wait
       | blocked -> stop
```

### 11.1 Menxia Analyst

For the active item, Analyst:

- verifies claim-to-source alignment;
- verifies paths, versions, platform context, and dependency assumptions;
- detects stale, contradictory, or missing evidence;
- builds requirement-to-item-to-evidence traceability;
- reports an evidence assessment without a numeric final score.

Actions:

```text
EVIDENCE_SUFFICIENT
NEEDS_MORE_EVIDENCE
HUMAN_GATE
BLOCKED
```

### 11.2 Menxia Solver

For the active item, Solver:

- writes or repairs the implementation-level proposal;
- verifies feasibility against real code and APIs;
- checks existing reusable components and duplicate-work risk;
- defines failure, timeout, cancellation, retry, idempotency, cleanup, rollback,
  tests, and observability when applicable;
- checks Go/.NET compatibility and Unity device/platform/asset compatibility;
- proposes explicit amendments where the frozen item is not feasible.

Actions:

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

Solver cannot approve an item or group.

### 11.3 Menxia Critic item gate

Critic:

- independently reviews evidence and feasibility;
- opens or resolves Findings;
- applies requirement, evidence, architecture/reuse, performance/resource/GC,
  compatibility, testability/operations, and security/risk gates;
- emits the only authoritative numeric score;
- selects an item decision.

Actions:

```text
APPROVE_ITEM
REVISE_ITEM
SPLIT_ITEM
MERGE_ITEM
REMOVE_ITEM
HUMAN_GATE
BLOCKED
```

### 11.4 Group gate

After every non-removed item is approved, Critic evaluates:

- group objective coverage;
- consistency among item proposals;
- dependency satisfaction;
- accepted amendment completeness;
- unresolved Findings;
- group verification and rollback coherence.

Actions:

```text
APPROVE_GROUP
REVISE_GROUP
HUMAN_GATE
BLOCKED
```

Only `APPROVE_GROUP` permits the next group to start.

## 12. Amendment and Version Model

Menxia writes `ReviewOverlay` amendments:

```text
UPDATE_ITEM
SPLIT_ITEM
MERGE_ITEMS
REMOVE_ITEM
UPDATE_DEPENDENCY
UPDATE_GROUP
```

The Orchestrator validates and applies accepted amendments to a working
ReviewedPlan. FrozenPlan remains unchanged.

A local amendment is allowed only when it preserves:

- the original user objective;
- the primary architecture direction;
- public contracts outside the active group;
- group dependency topology outside the affected scope.

If an amendment changes group boundaries, cross-group dependencies, public
contracts, data migration strategy, or primary architecture direction, the
workflow returns to Zhongshu and produces FrozenPlan vN+1.

Accepted material amendments invalidate the target, dependents, containing
group gate, and dependent later groups as defined in
`canonical-enums-and-schemas.md`.

## 13. Findings and Resolution

All roles use one canonical Finding model. There is no separate objection
schema.

Rules:

- every P0/P1 Finding has an owner and required resolution;
- `resolved` requires evidence, an accepted amendment, verified revision, or
  human decision;
- a rejected Finding must include contrary evidence;
- a repeated Finding references the existing ID rather than creating a
  duplicate;
- P0/P1 cannot be deferred in a passing item or group.

## 14. Scoring

Only Menxia Critic provides the authoritative score defined in
`canonical-enums-and-schemas.md`.

Analyst provides:

```text
SUPPORTED
SUPPORTED_WITH_GAPS
CONTRADICTED
INSUFFICIENT_EVIDENCE
```

Solver provides:

```text
FEASIBLE
FEASIBLE_WITH_CHANGES
NOT_FEASIBLE
NEEDS_MEASUREMENT
```

An item passes only when:

```text
score >= 8.0
no unresolved P0/P1
critical dimension floors pass
evidence is sufficient for material claims
verification steps are concrete
no pending HUMAN_GATE
```

The group gate uses the same hard conditions and may additionally reject
cross-item inconsistency. A group score is not the arithmetic average of three
role opinions.

## 15. HUMAN_GATE

HUMAN_GATE is required when:

- multiple materially different interpretations remain;
- mutually exclusive architecture/API/data choices remain;
- a public contract or migration strategy changes;
- a high-risk dependency is introduced;
- Unity Prefab, Scene, Asset, `.meta`, or serialized references face an
  unresolved trade-off;
- Solver and Critic disagree on a P0/P1 issue after one evidence-backed reply;
- evidence remains insufficient after the applicable budget;
- scope would expand;
- cost, compatibility, performance, release, or rollback policy requires user
  preference.

The Orchestrator:

1. creates and persists a `decision_id`;
2. sends the decision, scope, options, and impacts to Feishu;
3. enters `WAITING_HUMAN`;
4. accepts only an authorized reply in the form `decision-xxxxxx A`;
5. atomically records `consumed_message_id`;
6. resumes the exact saved continuation state.

A bare `A`, `B`, `go`, or similar reply MUST NOT resume a workflow.

## 16. Round and Progress Budgets

Recommended defaults:

```text
MAX_ROUNDS=15
ZHONGSHU_MAX_ROUNDS=5
GROUP_MAX_ROUNDS=5
ITEM_MAX_ROUNDS=3
NO_PROGRESS_LIMIT=2
ITEM_NO_PROGRESS_LIMIT=2
```

Definitions:

- `agent_call_count`: every Agent invocation; telemetry only;
- `revision_round`: one material return-and-revision cycle;
- `item_revision_round`: revision cycles for the active item;
- `group_revision_round`: revision cycles affecting the active group;
- `global_revision_round`: all material revision cycles in the task.

`MAX_ROUNDS=15` applies to `global_revision_round`.

A round shows progress only when at least one occurs:

- new relevant evidence;
- a materially changed artifact;
- a resolved Finding;
- a changed decision supported by evidence;
- a reduced unknown set;
- an accepted amendment.

Two consecutive no-progress revision rounds cause HUMAN_GATE or BLOCKED.
Reaching an item/group/global limit never causes automatic approval.

## 17. Persistence and Recovery

The mechanism is not single-session-only. The Orchestrator MUST persist:

- TaskState;
- Skill lock and hashes;
- all artifact revisions;
- active group/item;
- round counters;
- Findings and resolutions;
- FrozenPlan and ReviewOverlay versions;
- active HUMAN_GATE and continuation state;
- consumed Feishu message IDs;
- transition log.

State transition, artifact persistence, and message consumption MUST be
transactional or crash-safe.

On restart, the Orchestrator:

1. loads the last committed TaskState;
2. verifies Skill lock and artifact hashes;
3. restores the active gate or active group/item;
4. does not replay a committed Agent result or consumed message;
5. resumes from the saved continuation state.

Artifact names MUST include scope and revision, for example:

```text
task-000001/evidence/artifact-000003-r2.json
task-000001/plans/frozen-plan-v1.json
task-000001/groups/group-000001/items/item-000002/critic-r3.json
task-000001/decisions/decision-000001.json
task-000001/transition-log.jsonl
```

## 18. Project-Specific Mandatory Review

### Go

- Go and module versions;
- public API and backward compatibility;
- goroutines, channels, locks, races, context cancellation;
- allocations, escape behavior, GC pressure, throughput;
- dependency and `go.mod` impact;
- unit, integration, benchmark, and race-test design.

### .NET

- Target Framework and runtime versions;
- NuGet and assembly compatibility;
- public API, nullable behavior, and serialization compatibility;
- async lifecycle, cancellation, disposal, and idempotency;
- allocations, GC, pooling, and resource ownership;
- DI, configuration, deployment, and OS differences;
- unit, integration, performance, and regression-test design.

### Unity

- Unity Editor and player runtime versions;
- Android/iOS real-device compatibility;
- CPU architecture and Graphics API;
- Mono/IL2CPP/Burst and package compatibility;
- main-thread and lifecycle assumptions;
- Prefab, Scene, Asset, `.meta`, and serialized references;
- frame time, loading, memory, GC, and resource lifetime;
- automated tests plus explicit manual visual/device verification.

## 19. Finalization

After all groups pass, the Orchestrator:

1. verifies no approved result is invalidated;
2. verifies no P0/P1 or HUMAN_GATE remains open;
3. applies accepted ReviewOverlay amendments;
4. creates ReviewedPlan;
5. validates traceability from request to group to item to evidence;
6. creates ApprovedPlan and an audit summary;
7. enters `DONE`.

ApprovedPlan includes:

- objectives and non-goals;
- project/version/platform context;
- ordered implementation groups and items;
- implementation details and reuse decisions;
- compatibility constraints;
- risks and resolved Findings;
- verification, observability, fallback, and rollback;
- human decisions;
- remaining P2/P3 follow-ups;
- evidence references and confidence limits.

## 20. Forbidden Behavior

The system MUST NOT:

- invent project facts or command results;
- load both phases together;
- reuse the unreset Zhongshu context in Menxia;
- let an Agent directly change workflow state;
- silently mutate FrozenPlan;
- overwrite prior artifact revisions;
- accept a bare Feishu option without `decision_id`;
- consume one message for multiple decisions;
- approve with unresolved P0/P1;
- use a high score to hide a failed critical dimension;
- continue after a required HUMAN_GATE;
- let one item consume the full task budget without escalation;
- place secrets in prompts, artifacts, logs, or source files;
- broaden scope without an amendment and, when material, human approval.
