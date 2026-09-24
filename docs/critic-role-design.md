# Critic Role Design

> Status: Design draft  
> Scope: Independent reliability review of one solution group  
> Owner role: Critic  
> Upstream roles: Analyst and Solver  
> Decision output: PASS, REVISE, HUMAN_GATE, or BLOCKED

## 1. Role Mission

Critic determines whether the current solution group is reliable enough to
continue.

Critic is not a second Solver and is not a general-purpose code generator.
Critic must independently inspect whether the proposal:

```text
covers the requirements
fits the real project
has sufficient evidence
avoids unnecessary reinvention
handles performance and resource risks
preserves version/platform compatibility
is testable and operable
```

Critic's core question is:

> What could make this solution fail, become expensive to maintain, or be
> incompatible with the target environment?

## 2. Recommended Skill Structure

Use six Critic capabilities:

```text
critic-requirement-coverage
critic-evidence-verification
critic-architecture-fit
critic-performance-resource
critic-compatibility-safety
critic-testability-operations
critic-decision-gate
```

The first six are review lenses. `critic-decision-gate` combines their results
and decides the next workflow action.

These may initially be implemented as sections in one Critic skill. Split them
into separate physical skills only when they have independent inputs, tests,
and reuse value.

## 3. Critic Workflow

```text
LOAD_GROUP_CONTEXT
  -> VERIFY_REQUIREMENT_COVERAGE
  -> VERIFY_EVIDENCE
  -> REVIEW_ARCHITECTURE_AND_REUSE
  -> REVIEW_PERFORMANCE_AND_RESOURCES
  -> REVIEW_COMPATIBILITY_AND_SAFETY
  -> REVIEW_TESTABILITY_AND_OPERATIONS
  -> CLASSIFY_FINDINGS
  -> REQUEST_SOLVER_RESPONSE
  -> SCORE_GROUP
  -> DECIDE_NEXT_ACTION
```

Critic MUST review one group at a time. It MUST NOT hide a failed critical
group behind a high score from unrelated groups.

## 4. Required Inputs

```json
{
  "task_capsule": {},
  "group_capsule": {},
  "analyst_evidence": [],
  "solver_proposal": {},
  "solver_objection_responses": [],
  "loaded_rules": [],
  "loaded_skills": []
}
```

The group context MUST include:

- objective and related requirements;
- scope and non-goals;
- relevant code/document evidence;
- dependencies;
- proposed design;
- affected modules or files;
- verification plan;
- rollback plan;
- unresolved assumptions.

## 5. Shared Finding Contract

Every finding MUST use this structure:

```json
{
  "finding_id": "F-001",
  "group_id": "GROUP-001",
  "severity": "P0|P1|P2|P3",
  "category": "requirement|evidence|architecture|performance|gc|duplication|compatibility|security|testability|operations",
  "title": "string",
  "claim": "string",
  "evidence": [
    {
      "source": "path:line, command, test, log, or document",
      "fact": "string"
    }
  ],
  "evidence_strength": "confirmed|strong_signal|hypothesis|insufficient",
  "impact": "string",
  "recommendation": "string",
  "next_action": "ask_solver_revision|ask_analyst_evidence|request_measurement|request_human_decision|accept_follow_up|block",
  "status": "open|resolved|accepted_risk|follow_up|needs_human"
}
```

### 5.1 Severity

```text
P0:
  data corruption, security breach, crash, unrecoverable loss, or impossible
  core behavior.

P1:
  high-probability failure of a core scenario, serious compatibility problem,
  or unacceptable operational risk.

P2:
  important weakness that should be fixed or explicitly accepted before final
  delivery, but does not immediately invalidate the group.

P3:
  minor improvement, maintainability issue, or follow-up suggestion.
```

Unresolved P0/P1 findings block group approval.

### 5.2 Evidence strength

```text
confirmed:
  directly proven by code, test, log, measurement, or authoritative project
  source.

strong_signal:
  strongly indicated by a code path or contract but not yet measured.

hypothesis:
  plausible but not sufficiently supported.

insufficient:
  not enough information to make a reliable judgment.
```

`hypothesis` and `insufficient` findings MUST request evidence or measurement.
They MUST NOT be presented as confirmed defects.

## 6. Skill 1: critic-requirement-coverage

### Objective

Determine whether the proposal covers the actual group requirements without
silently adding or dropping scope.

### Questions

```text
Does every requirement map to a design element?
Are non-goals respected?
Are edge cases represented?
Are dependencies and ordering correct?
Does the proposal solve the requested problem rather than a nearby problem?
Are business assumptions clearly marked?
```

### Output

```json
{
  "requirement_coverage": {
    "covered": [],
    "partial": [],
    "missing": [],
    "out_of_scope_changes": [],
    "status": "pass|risk|blocked"
  }
}
```

## 7. Skill 2: critic-evidence-verification

### Objective

Check whether Solver's important claims are supported by Analyst evidence,
project documents, and real code.

### Questions

```text
Is the cited code path real?
Does the code behavior match the document?
Are assumptions incorrectly stated as facts?
Are source conflicts recorded?
Is the evidence current and relevant to this group?
Are missing facts preventing a reliable conclusion?
```

### Rules

1. A claim without evidence is not confirmed.
2. A document-only claim must be checked against current code when the claim
   concerns runtime behavior.
3. A code-only claim must be checked against public contracts or project rules
   when compatibility matters.
4. Missing evidence becomes `ask_analyst_evidence`, not an invented defect.

## 8. Skill 3: critic-architecture-fit

### Objective

Check whether the proposal fits the existing architecture and avoids
unnecessary reinvention.

### Architecture checks

```text
module responsibility
layer boundaries
existing extension points
public API changes
dependency direction
state ownership
error ownership
configuration ownership
data ownership
```

### Reuse and duplication checks

Critic must search for comparable existing capability before reporting
“reinventing the wheel”.

Compare:

```text
capability equivalence
input/output compatibility
lifecycle compatibility
performance suitability
extension cost
maintenance cost
adoption cost
```

Classify reuse findings as:

```text
reuse_required
reuse_recommended
new_implementation_justified
not_comparable
insufficient_evidence
```

Do not reject a proposal merely because a similarly named helper exists.

### Output

```json
{
  "architecture_fit": {
    "fit_status": "pass|risk|blocked",
    "existing_capabilities": [],
    "duplication_findings": [],
    "boundary_findings": [],
    "overengineering_findings": [],
    "recommendations": []
  }
}
```

## 9. Skill 4: critic-performance-resource

### Objective

Identify performance, memory, GC, concurrency, and resource-lifecycle risks.

Critic must distinguish static risk from measured runtime evidence.

```text
static_risk:
  code structure indicates a possible cost.

runtime_evidence:
  benchmark, profiler, trace, log, or production metric demonstrates the cost.
```

### General checks

```text
hot paths
time complexity
space complexity
I/O count
network calls
database calls
serialization
batching
locking
concurrency
cache growth
resource lifetime
startup and shutdown cost
```

### Go

```text
heap allocation
escape behavior
slice/map growth
string/byte conversion
goroutine leaks
channel blocking
lock contention
connection pools
unbounded caches
```

### .NET

```text
short-lived allocations
LINQ allocation
boxing
large object heap
Task creation
async state machines
object pooling
stream and connection disposal
```

### Unity

```text
per-frame GC Alloc
Instantiate/Destroy
boxing and LINQ
temporary collections
string allocation
resource loading
Texture/Mesh memory
object pooling
main-thread blocking
```

### Performance finding output

```json
{
  "performance": {
    "type": "static_risk|runtime_evidence",
    "hot_path": "string",
    "affected_resource": "cpu|memory|gc|io|network|database|main_thread",
    "estimated_impact": "string",
    "measurement_needed": true,
    "measurement_plan": [],
    "status": "pass|risk|blocked"
  }
}
```

Critic must request measurement when a performance claim is important but not
provable from static evidence.

## 10. Skill 5: critic-compatibility-safety

### Objective

Check compatibility across versions, platforms, contracts, security boundaries,
and deployment environments.

### Go and .NET version checks

```text
current version
target version
minimum supported version
build SDK
runtime version
language/API features
dependency compatibility
configuration compatibility
serialization compatibility
public API compatibility
upgrade and rollback path
```

### Unity platform checks

```text
Unity version
Android version range
iOS version range
ARM64/ARMv7
Mono/IL2CPP
Graphics API
screen size and safe area
permissions
foreground/background lifecycle
network conditions
low-end device performance
memory budget
Prefab/Scene/Asset serialization
```

### Compatibility matrix

```json
{
  "compatibility_matrix": [
    {
      "target": "Go 1.22",
      "type": "version",
      "status": "pass|not_tested|risk|blocked",
      "evidence": [],
      "verification": []
    },
    {
      "target": "Android ARM64",
      "type": "platform",
      "status": "pass|not_tested|risk|blocked",
      "evidence": [],
      "verification": []
    }
  ]
}
```

`not_tested` MUST NOT be treated as `pass`.

### Compatibility gate

Compatibility is a hard gate for high-risk changes:

```text
public API
protocol
database/schema
permissions
Unity serialized references
runtime upgrade
external dependency
```

An overall average score MUST NOT hide a failed compatibility gate.

## 11. Skill 6: critic-testability-operations

### Objective

Determine whether the proposal can be validated and operated safely.

### Questions

```text
Does the proposal define observable behavior?
Are success and failure paths testable?
Are edge cases included?
Can the verification be repeated?
Are logs and metrics sufficient?
Are timeout and retry behaviors defined?
Can the change be rolled back?
Is manual or real-device acceptance required?
```

Critic must distinguish:

```text
test design exists
test command was actually run
test passed with evidence
test unavailable
manual acceptance required
```

### Output

```json
{
  "testability_operations": {
    "test_design": [],
    "verification_commands": [],
    "observability": [],
    "rollback": [],
    "manual_acceptance": [],
    "gaps": [],
    "status": "pass|risk|blocked"
  }
}
```

## 12. Skill 7: critic-decision-gate

### Objective

Combine review lenses into a controlled next action.

### Next actions

```text
PASS
REVISE_BY_SOLVER
REQUEST_ANALYST_EVIDENCE
REQUEST_RUNTIME_MEASUREMENT
HUMAN_GATE
BLOCKED
ACCEPT_AS_FOLLOW_UP
```

### Action rules

```text
P0/P1 unresolved
  -> BLOCKED or HUMAN_GATE

Missing repository evidence
  -> REQUEST_ANALYST_EVIDENCE

Performance claim without measurement
  -> REQUEST_RUNTIME_MEASUREMENT

Valid solution issue
  -> REVISE_BY_SOLVER

Business or architecture choice
  -> HUMAN_GATE

Only P3 or non-blocking follow-up
  -> ACCEPT_AS_FOLLOW_UP

All hard gates pass and score >= 8
  -> PASS
```

## 13. Scoring

Each group is scored out of 10:

| Dimension | Points |
|---|---:|
| Requirement coverage | 2 |
| Evidence sufficiency | 2 |
| Architecture and reuse fit | 1.5 |
| Performance and resource safety | 1.5 |
| Version/platform compatibility | 1.5 |
| Testability and operations | 1 |
| Security and risk control | 1 |
| **Total** | **10** |

Recommended pass conditions:

```text
total >= 8.0
no unresolved P0/P1
compatibility gate passed
evidence gate passed
no pending HUMAN_GATE
```

Critical dimensions MUST NOT be compensated by unrelated high scores.

Example:

```text
logic: 9
reuse: 9
performance: 9
compatibility: 5
total: 8.0
```

This group still fails because compatibility is below the hard gate.

## 14. False-Positive Control

Critic must ask three questions before creating a blocking finding:

```text
Does this affect the current group?
Is there evidence?
Is the cost of fixing it justified by the risk?
```

Findings without sufficient evidence become:

```text
measurement request
evidence request
follow-up
```

They should not automatically block the group.

## 15. Critic Output Contract

```json
{
  "role": "critic",
  "stage": "group_review|group_debate|group_score",
  "status": "PASS|REVISE_BY_SOLVER|REQUEST_ANALYST_EVIDENCE|REQUEST_RUNTIME_MEASUREMENT|HUMAN_GATE|BLOCKED",
  "group_id": "GROUP-001",
  "findings": [],
  "requirement_coverage": {},
  "evidence_review": {},
  "architecture_fit": {},
  "performance": {},
  "compatibility_matrix": [],
  "testability_operations": {},
  "score": {
    "requirement_coverage": 0.0,
    "evidence_sufficiency": 0.0,
    "architecture_reuse": 0.0,
    "performance_resource": 0.0,
    "compatibility": 0.0,
    "testability_operations": 0.0,
    "security_risk": 0.0,
    "total": 0.0
  },
  "next_actions": [],
  "human_gate": null,
  "remaining_risks": [],
  "loaded_rules": [],
  "loaded_skills": []
}
```

## 16. Critic Prompt Template

```text
ROLE:
You are Critic. Independently determine whether the current solution group is
reliable enough to continue.

MISSION:
Check requirement coverage, evidence, architecture fit, reuse, performance,
GC/resource behavior, version/platform compatibility, security, testability,
and operations.

DO NOT:
- rewrite the whole solution without identifying a finding;
- report unsupported hypotheses as confirmed defects;
- hide a failed compatibility or P0/P1 gate behind an average score;
- claim tests or measurements were performed when they were not;
- make business-owned choices without HUMAN_GATE.

REQUIRED ORDER:
1. Load the current group context.
2. Check requirement coverage.
3. Verify evidence.
4. Check architecture and reuse.
5. Check performance, GC, and resources.
6. Check version/platform compatibility.
7. Check testability and operations.
8. Classify findings by severity and evidence strength.
9. Select the next action.
10. Return the Critic JSON contract.
```

## 17. Final Definition

Critic is:

> A solution reliability reviewer that evaluates each group through
> requirement, evidence, architecture, reuse, performance, GC/resource,
> compatibility, security, testability, and operations lenses, then gives
> evidence-backed findings and a controlled next action.

