---
name: wf-go-review
description: Go code-review workflow entry. Use for reviewing Go diffs through bounded logic, performance, and security review capsules.
---

# wf-go-review

## Invocation

- Codex skill trigger: `$wf-go-review`

## Workflow

Read `.claude/workflows/go-code-review.md` from the repository root and follow it as the source of truth.

The workflow defaults to role-based reviewer subagents. Sisyphus owns context packaging, review-scope decomposition, finding aggregation, the quality gate, and the final report. Do not pass the whole conversation or an unbounded diff to a reviewer.

## TaskRun lifecycle

Before reading the diff, initialize the local payload:

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs start --projectId <projectId> --workflowType code-review --taskTitle "<ascii task title>" --payloadFile .nexus/task-run-code-review.json --contextFile .nexus/workflow-context-code-review.json
```

At workflow end, submit the same payload once:

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs submit --payloadFile .nexus/task-run-code-review.json --contextFile .nexus/workflow-context-code-review.json
```

Keep the payload and context files after submission. Payload fields, compact evidence rules, and prohibited workflow IDs are defined by `$nexus-taskrun-submit`.
