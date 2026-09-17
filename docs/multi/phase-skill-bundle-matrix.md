# Phase Skill Bundle Matrix

> Status: Historical Skill-loading reference
> Machine-protocol authority: `cmd/orchestrator/contracts/`

## 1. Decision

The runtime uses six composite phase-role Skills:

```text
zhongshu-analyst
zhongshu-solver
zhongshu-critic
menxia-analyst
menxia-solver
menxia-critic
```

Names such as `project-router`, `evidence-auditor`, `scorecard`, or
`compatibility-reviewer` describe capability sections inside a composite
Skill. They are not separate physical Skills in version 2.1.

This avoids unstable prompt assembly, ordering conflicts, excessive context,
and partial Skill version upgrades.

## 2. Dispatch Formula

Every Agent dispatch contains the selected runtime Skill plus a contract-derived
prompt bundle. The Python contract, not this prose matrix, defines the machine
response shape:

```text
ContractDerivedPromptBundle
  + exactly one CompositePhaseRoleSkill
  + exactly one ProjectAppendix
  + zero or one TaskOverlay
  + ScopedTaskArtifacts
```

Example lock:

```json
{
  "protocol": {
    "name": "multi-agent-solution-review-protocol",
    "version": "2.1",
    "sha256": "sha256:protocol"
  },
  "composite_skill": {
    "name": "menxia-critic",
    "version": "1.0.0",
    "sha256": "sha256:skill"
  },
  "project_appendix": {
    "name": "project-unity",
    "version": "1.0.0",
    "sha256": "sha256:project"
  },
  "task_overlay": null
}
```

The complete lock is persisted at task creation and referenced by hash in each
artifact.

## 3. Composite Skill Matrix

| Phase | Runtime agent | Composite Skill | Primary output |
|---|---|---|---|
| Zhongshu | `review-analyst` | `zhongshu-analyst` | EvidencePacket only; no task/group proposals |
| Zhongshu | `review-solver` | `zhongshu-solver` | One formal Task Graph with complete `plan.items` and compact `groups[*].item_ids` |
| Zhongshu | `review-critic` | `zhongshu-critic` | Findings and freeze recommendation |
| Menxia | `review-analyst` | `menxia-analyst` | ItemEvidenceAudit |
| Menxia | `review-solver` | `menxia-solver` | ItemFeasibilityProposal and amendments |
| Menxia | `review-critic` | `menxia-critic` | Item/Group decision and authoritative score |

## 4. Internal Capabilities

### 4.1 Zhongshu Analyst

```text
project and task routing
requirement reading
local/knowledge/official-document search
real-code evidence collection
fact/inference/assumption/unknown separation
reuse and affected-area discovery
human-question detection
```

### 4.2 Zhongshu Solver

```text
requirement-to-task coverage
task boundary and acceptance quality
formal group and item construction
dependency, ordering, and parallelism analysis
scope, unknown, risk, and evidence-boundary preservation
targeted evidence revalidation

The Solver is the only Zhongshu role allowed to create, split, merge, name, or
group task items. Analyst workers contribute complementary evidence lenses; the
Orchestrator merges their evidence provenance and never unions their task
lists.
```

Zhongshu Solver does not design implementation modules, interfaces, data flow,
migrations, rollbacks, or code changes. Those responsibilities begin in
Menxia after the formal task graph is frozen.

### 4.3 Zhongshu Critic

```text
requirement and evidence gap detection
group boundary and dependency challenge
architecture and reuse challenge
performance/resource/GC risk preview
compatibility risk preview
test/security/operations risk preview
freeze recommendation
```

### 4.4 Menxia Analyst

```text
source verification
claim-to-evidence audit
version and platform verification
traceability
stale/contradictory evidence detection
unknown and assumption audit
```

### 4.5 Menxia Solver

```text
real-code feasibility review
implementation detail
reuse and duplicate-work review
API and architecture boundary review
failure/cancellation/retry/idempotency/cleanup
compatibility review
test/observability/fallback/rollback detail
ReviewOverlay amendment proposal
```

### 4.6 Menxia Critic

```text
requirement gate
evidence gate
architecture and reuse gate
performance/resource/GC gate
compatibility gate
testability and operations gate
security and risk gate
Finding lifecycle
authoritative score
item and group decision
HUMAN_GATE recommendation
```

## 5. Project Appendices

Project appendices provide domain rules but do not change workflow enums.

### Go appendix

```text
Go/module version compatibility
public API backward compatibility
concurrency and cancellation
allocation, escape, GC, and throughput
dependency impact
unit/integration/benchmark/race testing
```

### .NET appendix

```text
Target Framework/runtime compatibility
NuGet/assembly compatibility
public API/nullable/serialization compatibility
async/cancellation/disposal/idempotency
allocation/GC/resource ownership
DI/configuration/deployment/OS differences
unit/integration/performance/regression testing
```

### Unity appendix

```text
Editor/player version compatibility
Android/iOS real-device matrix
CPU architecture and Graphics API
Mono/IL2CPP/Burst/package compatibility
main-thread and lifecycle
Prefab/Scene/Asset/.meta/serialized references
frame time/loading/memory/GC/resource lifetime
automated plus manual visual/device verification
```

### Mixed-project appendix

A mixed project loads one bounded appendix containing only the technologies
present in `project_components`. It MUST define cross-boundary contracts and
version compatibility. It MUST NOT concatenate every available repository
Skill.

## 6. Task Overlays

Allowed overlays:

```text
review
bugfix
feature
refactor
test
```

An overlay may narrow focus but cannot:

- add new workflow states;
- override canonical actions;
- lower pass thresholds;
- remove mandatory project checks;
- permit direct code modification in a proposal-only task.

## 7. Prompt Composition

The Orchestrator uses this order:

```text
ROLE AND PHASE
MISSION
CANONICAL ACTIONS
SCOPED TASK CONTEXT
ACTIVE GROUP AND ITEM
CONFIRMED EVIDENCE
ASSUMPTIONS AND UNKNOWNS
ACTIVE FINDINGS
COMPOSITE SKILL
PROJECT APPENDIX
TASK OVERLAY
REQUIRED OUTPUT CONTRACT
FORBIDDEN BEHAVIORS
```

The prompt MUST include only the active group/item and relevant dependencies.
Full cross-phase conversation history is forbidden.

## 8. Loader Validation

Reject a dispatch when:

- zero or multiple composite Skills are active;
- the Skill phase does not match workflow state;
- the Skill role does not match the runtime agent;
- project appendix does not match TaskContext;
- any version or hash is absent;
- a task overlay conflicts with the protocol;
- required artifact references are absent;
- context exceeds the configured budget;
- Zhongshu history is injected into a Menxia session;
- secret material appears in the prompt.

## 9. Physical Skill Layout

Recommended implementation:

```text
.agents/skills/
├── zhongshu-analyst/SKILL.md
├── zhongshu-solver/SKILL.md
├── zhongshu-critic/SKILL.md
├── menxia-analyst/SKILL.md
├── menxia-solver/SKILL.md
└── menxia-critic/SKILL.md
```

Project appendices may be referenced resources within those six Skills or
versioned files loaded by the Orchestrator. They do not need to become dozens
of independently selectable Skills.
