# Three-Agent Skill Plan

> Version: 1.0  
> Status: Reference only / superseded by the phase-specific Skill documents  
> Do not load this document into an Agent runtime prompt.  
> Scope: Reliable solution proposal generation for Go, Unity, and .NET projects  
> Roles: Analyst, Solver, Critic  
> Related protocol: `multi-agent-solution-review-protocol.md`

## 1. Goal

The three Agents work on one objective:

> Decompose a software request into verifiable groups and produce a reliable,
> stable, executable solution proposal.

They are not three generic coding Agents. Their responsibilities are
deliberately separated:

```text
Analyst = define the problem correctly
Solver  = design the solution correctly
Critic  = challenge whether the solution is reliable
```

The orchestrator selects skills by:

```text
project_type + task_type + role + workflow_state
```

It MUST NOT load every available skill into every Agent prompt.

## 2. Skill Loading Rules

### 2.1 Priority order

Load skills in this order:

1. repository communication and safety rules;
2. project routing rule;
3. task-type workflow;
4. role-specific skill;
5. stack-specific skill;
6. current-state checklist;
7. evidence and output contract.

### 2.2 Skill selection

Each loaded skill MUST be recorded in the task context:

```json
{
  "path": "templates/.claude/skills/go-testing/SKILL.md",
  "reason": "The proposal must define Go test coverage.",
  "role": "solver",
  "state": "group_review"
}
```

If a required skill is missing, the Agent MUST report:

```text
context_missing
```

It MUST NOT invent the missing rule or claim that it was loaded.

### 2.3 Context budget

The orchestrator SHOULD pass:

- the skill name;
- the relevant sections;
- the current Task Capsule;
- the current Group Capsule;
- the required output schema.

It SHOULD NOT pass unrelated full-history conversations or every repository
document.

### 2.4 Relationship to the bundle loading model

The priority order in §2.1 maps to the Common + Phase + Role + Project + Task
overlay model defined in `multi-agent-solution-review-protocol.md §9` and
`phase-skill-bundle-matrix.md §1`:

| Priority layer | Bundle equivalent |
|---|---|
| 1. Communication and safety rules | Common Bundle |
| 2. Project routing rule | Project Bundle |
| 3. Task-type workflow | Task Overlay |
| 4. Role-specific skill | Role Bundle |
| 5. Stack-specific skill | Project Bundle |
| 6. Current-state checklist | Phase Bundle |
| 7. Evidence and output contract | Common Bundle |

The bundle model in the Protocol supersedes the priority list for
implementation. This priority list serves as a reasoning guide for the
Orchestrator when selecting which skills go into each bundle.

## 3. Shared Baseline Bundle

All three roles receive a small shared baseline:

```text
templates/.claude/rules/01-communication.md
templates/.claude/rules/knowledge-retrieval.md
templates/.claude/rules/test-driven-change.md
templates/.agents/skills/wf-design/SKILL.md
templates/.agents/skills/wf-research/SKILL.md
```

Shared requirements:

- separate facts, assumptions, and unknowns;
- never claim unperformed validation;
- keep secrets out of prompts, logs, and artifacts;
- report exact evidence sources;
- preserve user scope;
- stop for human decisions when assumptions are material.

## 4. Analyst Skill Plan

### 4.1 Role objective

Analyst converts a natural-language request into a stable `TaskCapsule` and
then creates evidence-backed `GroupCapsules`.

### 4.2 Required skills

```text
templates/.agents/skills/wf-design/SKILL.md
templates/.agents/skills/wf-research/SKILL.md
templates/.agents/skills/nexus-knowledge-retrieval/SKILL.md
templates/.agents/skills/kb-system-curator/SKILL.md
templates/.claude/rules/knowledge-retrieval.md
templates/.claude/rules/go-04-task-decomposition.md
```

`go-04-task-decomposition.md` is used as a general decomposition discipline
even when the project is Unity or .NET.

### 4.3 Stack additions

#### Go

```text
templates/.claude/rules/go-00-routing.md
templates/.claude/rules/go-02-safety.md
templates/.agents/skills/wf-go-review/SKILL.md
```

#### .NET

```text
templates/.claude/rules/dotnet-00-routing.md
templates/.claude/rules/dotnet-01-project-model.md
templates/.agents/skills/wf-dotnet-review/SKILL.md
```

#### Unity

```text
templates/.claude/rules/unity-00-routing.md
templates/.claude/rules/unity-01-project-model.md
templates/.agents/skills/wf-unity-bugfix/SKILL.md
templates/.agents/skills/wf-unity-logic-mod/SKILL.md
templates/.agents/skills/wf-unity-ui-feature/SKILL.md
```

Analyst selects only the Unity workflow matching the request.

### 4.4 Analyst must answer

```text
What project type is this?
What task type is this?
What is the requested outcome?
What is explicitly out of scope?
What existing behavior is confirmed?
What evidence is missing?
What groups should be created?
What order and dependencies do the groups have?
What must the user decide?
How will each group be accepted?
```

### 4.5 Analyst output

```json
{
  "role": "analyst",
  "status": "READY_FOR_SOLVER|WAITING_HUMAN|INSUFFICIENT_EVIDENCE",
  "project_type": "go|unity|dotnet",
  "task_type": "review|bugfix|feature",
  "workflow": "string",
  "task_capsule": {},
  "groups": [],
  "facts": [],
  "assumptions": [],
  "unknowns": [],
  "questions_for_user": [],
  "loaded_rules": [],
  "loaded_skills": []
}
```

### 4.6 Analyst must not load by default

Analyst SHOULD NOT load implementation-first skills unless the task requires
understanding an implementation boundary:

```text
go-coding-rules
dotnet-development
unity-logic-developer
unity-ui-developer
```

Analyst's primary job is context and evidence, not code generation.

## 5. Solver Skill Plan

### 5.1 Role objective

Solver turns a stable Task Capsule and one Group Capsule into a feasible
solution proposal with alternatives, trade-offs, tests, and rollback.

### 5.2 Required skills

```text
templates/.agents/skills/wf-design/SKILL.md
templates/.agents/skills/wf-research/SKILL.md
templates/.agents/skills/wf-subagents/SKILL.md
templates/.claude/rules/go-04-task-decomposition.md
templates/.claude/rules/test-driven-change.md
```

The subagent skill is used as a decomposition reference. It does not mean the
Solver may silently create additional Agents.

### 5.3 Stack additions: Go

```text
templates/.agents/skills/wf-go-feat/SKILL.md
templates/.agents/skills/wf-go-bugfix/SKILL.md
templates/.agents/skills/wf-go-review/SKILL.md
templates/.claude/rules/go-00-routing.md
templates/.claude/rules/go-02-safety.md
templates/.claude/skills/go-coding-rules/SKILL.md
templates/.claude/skills/go-testing/SKILL.md
```

Load exactly one Go workflow based on `task_type`.

### 5.4 Stack additions: .NET

```text
templates/.agents/skills/wf-dotnet-feature/SKILL.md
templates/.agents/skills/wf-dotnet-bugfix/SKILL.md
templates/.agents/skills/wf-dotnet-review/SKILL.md
templates/.claude/rules/dotnet-00-routing.md
templates/.claude/rules/dotnet-01-project-model.md
templates/.claude/rules/dotnet-02-runtime-safety.md
templates/.claude/rules/dotnet-03-library-compatibility.md
templates/.claude/skills/dotnet-dependency-safety/SKILL.md
templates/.claude/skills/dotnet-development/SKILL.md
templates/.claude/skills/dotnet-testing/SKILL.md
```

### 5.5 Stack additions: Unity

```text
templates/.agents/skills/wf-unity-bugfix/SKILL.md
templates/.agents/skills/wf-unity-logic-mod/SKILL.md
templates/.agents/skills/wf-unity-ui-feature/SKILL.md
templates/.agents/skills/wf-unity-ui-quick/SKILL.md
templates/.claude/rules/unity-00-routing.md
templates/.claude/rules/unity-01-project-model.md
templates/.claude/skills/unity-testing/SKILL.md
templates/.claude/skills/unity-asset-safety/SKILL.md
```

Add only the implementation skill matching the group:

```text
logic group -> unity-logic-developer
UI group -> unity-ui-developer + unity-ui-resolver
asset group -> unity-asset-safety
diagnosis group -> unity-debugger
```

### 5.6 Solver must answer

```text
What are the viable options?
What is the recommended option?
What trade-offs justify it?
What contracts or APIs change?
What files/modules are affected?
What is explicitly not changed?
How does the design handle failure, timeout, retry, and rollback?
How will the group be tested?
What would require HUMAN_GATE?
```

### 5.7 Solver output

```json
{
  "role": "solver",
  "status": "PROPOSAL_READY|REVISE_REQUIRED|WAITING_HUMAN",
  "group_id": "group-001",
  "options": [
    {
      "id": "A",
      "summary": "string",
      "advantages": [],
      "tradeoffs": [],
      "risks": [],
      "verification": []
    }
  ],
  "recommendation": "A",
  "design": {},
  "implementation_boundary": {},
  "test_design": {},
  "rollback_plan": [],
  "human_gate": null,
  "loaded_rules": [],
  "loaded_skills": []
}
```

### 5.8 Solver must not

- declare a group passed;
- close a Critic objection without a resolution;
- skip a required diagnosis step;
- use a broad refactor to hide an unresolved design problem;
- claim that code or tests changed when the task is proposal-only.

## 6. Critic Skill Plan

### 6.1 Role objective

Critic independently evaluates whether the current group is complete,
evidence-backed, feasible, testable, and safe enough to pass.

### 6.2 Required skills

```text
templates/.agents/skills/nexus-evaluation-review/SKILL.md
templates/.claude/skills/review-feedback/SKILL.md
templates/.claude/rules/test-driven-change.md
templates/.claude/rules/knowledge-retrieval.md
```

### 6.3 Stack additions: Go

```text
templates/.claude/rules/go-00-routing.md
templates/.claude/rules/go-02-safety.md
templates/.claude/rules/go-04-task-decomposition.md
templates/.claude/skills/go-testing/SKILL.md
```

Critic uses three lenses:

```text
logic / performance / security
```

### 6.4 Stack additions: .NET

```text
templates/.claude/rules/dotnet-00-routing.md
templates/.claude/rules/dotnet-01-project-model.md
templates/.claude/rules/dotnet-02-runtime-safety.md
templates/.claude/rules/dotnet-03-library-compatibility.md
templates/.claude/skills/dotnet-dependency-safety/SKILL.md
templates/.claude/skills/dotnet-testing/SKILL.md
```

### 6.5 Stack additions: Unity

```text
templates/.claude/rules/unity-00-routing.md
templates/.claude/rules/unity-01-project-model.md
templates/.claude/rules/unity-id-bugfix-safety.md
templates/.claude/rules/unity-id-logic-mod-safety.md
templates/.claude/rules/unity-id-ui-safety.md
templates/.claude/skills/unity-testing/SKILL.md
templates/.claude/skills/unity-asset-safety/SKILL.md
```

Load only the safety rule matching the current Unity group.

### 6.6 Critic must answer

```text
Which requirements are not covered?
Which claims lack evidence?
Which assumptions could invalidate the plan?
Which boundary, API, lifecycle, asset, security, or performance risks exist?
Which tests are missing or too weak?
Which objections must be resolved before passing?
Does this need human choice?
What is the score and why?
```

### 6.7 Critic output

```json
{
  "role": "critic",
  "status": "PASS|REVISE_BY_SOLVER|REQUEST_ANALYST_EVIDENCE|REQUEST_RUNTIME_MEASUREMENT|HUMAN_GATE|BLOCKED|ACCEPT_AS_FOLLOW_UP",
  "group_id": "group-001",
  "findings": [
    {
      "id": "obj-001",
      "severity": "P0|P1|P2|P3",
      "category": "requirement|logic|performance|security|compatibility|testability|operations",
      "claim": "string",
      "evidence_ids": [],
      "impact": "string",
      "required_resolution": "string",
      "status": "open|resolved|needs_human"
    }
  ],
  "verification_review": {},
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
  "human_gate": null,
  "loaded_rules": [],
  "loaded_skills": []
}
```

> The authoritative scoring model is defined in `critic-role-design.md §13`. The score fields above must match the Critic role design schema. Phase-specific documents may define additional phase-specific actions that extend the general status set.

## 7. Role-to-Phase Matrix

| Phase | Analyst | Solver | Critic |
|---|---|---|---|
| Request intake | primary | not active | not active |
| Context build | primary | consult | consult |
| Plan draft | review | primary | risk preview |
| Grouping | primary | consult | dependency review |
| Group evidence | primary | consult | evidence target review |
| Group review | evidence support | primary | primary |
| Group debate | evidence clarification | objection resolution | objection owner |
| Group revision | acceptance check | primary | regression review |
| Group score | evidence score | feasibility score | quality gate |
| HUMAN_GATE | explain facts | explain trade-offs | explain risks |
| Final score | coverage | feasibility | final recommendation |

## 8. Dynamic Bundle Selection

The orchestrator SHOULD maintain a mapping like this:

```json
{
  "go:review": {
    "analyst": ["go-00-routing", "go-02-safety", "wf-go-review"],
    "solver": ["wf-go-review", "go-coding-rules", "go-testing"],
    "critic": ["go-00-routing", "go-02-safety", "go-testing"]
  },
  "go:bugfix": {
    "analyst": ["go-00-routing", "go-02-safety", "wf-go-bugfix"],
    "solver": ["wf-go-bugfix", "go-coding-rules", "go-testing"],
    "critic": ["go-00-routing", "go-02-safety", "go-testing"]
  },
  "dotnet:feature": {
    "analyst": ["dotnet-00-routing", "dotnet-01-project-model"],
    "solver": ["wf-dotnet-feature", "dotnet-development", "dotnet-testing", "dotnet-dependency-safety"],
    "critic": ["dotnet-02-runtime-safety", "dotnet-03-library-compatibility", "dotnet-testing"]
  },
  "unity:bugfix": {
    "analyst": ["unity-00-routing", "unity-01-project-model", "unity-debugger"],
    "solver": ["wf-unity-bugfix", "unity-testing", "unity-asset-safety"],
    "critic": ["unity-01-project-model", "unity-id-bugfix-safety", "unity-testing", "unity-asset-safety"]
  }
}
```

This mapping is illustrative. The orchestrator MUST verify that each path
exists before injecting it.

## 9. Prompt Composition

Every Agent prompt SHOULD use this order:

```text
ROLE
MISSION
TASK CAPSULE
CURRENT GROUP CAPSULE
CONFIRMED FACTS
UNKNOWNS AND ASSUMPTIONS
LOADED RULES
LOADED SKILLS
REQUIRED OUTPUT SCHEMA
FORBIDDEN BEHAVIORS
```

Example role header:

```text
ROLE: CRITIC
MISSION: Independently determine whether the current group is safe to pass.
DO NOT: Rewrite the whole proposal. Do not claim validation that was not run.
PASS RULE: No unresolved P0/P1, score >= 8.0, and evidence-backed acceptance.
```

## 10. HUMAN_GATE Rules

The orchestrator SHOULD generate a `HUMAN_GATE` when:

- Analyst reports conflicting requirements;
- Solver has mutually exclusive architecture options;
- Critic and Solver disagree on a P0/P1 issue;
- the group score spread is greater than 2;
- a public API, schema, migration, permission, or external contract changes;
- Unity Prefab, Scene, Asset, or serialized references are affected;
- evidence remains insufficient after the group budget;
- the group would expand beyond the approved scope.

The gate MUST include:

```json
{
  "decision_id": "decision-001",
  "group_id": "group-001",
  "question": "string",
  "options": [
    {
      "id": "A",
      "label": "string",
      "impact": "string"
    }
  ],
  "default": null,
  "timeout_sec": 86400
}
```

## 11. What Not to Do

Do not:

- assign every skill to every Agent;
- let Analyst, Solver, and Critic produce three unrelated full proposals;
- let Critic only output `PASS` or `REJECT` without findings;
- let Solver implement before diagnosis for bugfix tasks;
- let a global average hide a failed critical group;
- let one group consume all 15 rounds without a stop decision;
- mix implementation permissions into a read-only review task;
- put App Secrets or access tokens into Agent context;
- use Feishu chat content as authoritative project evidence without recording
  its source and sender.

## 12. Adoption Sequence

Recommended rollout:

1. Adopt this skill matrix and output schemas without changing behavior.
2. Add dynamic bundle selection to the orchestrator.
3. Change the current global phases to group-aware phases.
4. Add per-group evidence, objection, resolution, and score artifacts.
5. Enable automatic HUMAN_GATE triggers.
6. Add TaskRun evidence containing loaded rules, loaded skills, scores, and
   unresolved risks.
7. Compare successful and failed runs before changing model assignments.
