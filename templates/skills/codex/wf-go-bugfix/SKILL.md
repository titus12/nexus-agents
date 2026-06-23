---
name: wf-go-bugfix
description: Go bugfix workflow entry. Use for Go errors, crashes, regressions, incorrect behavior, or root-cause diagnosis. Invoke explicitly with $wf-go-bugfix.
---

# wf-go-bugfix

## Invocation

- Codex skill trigger: `$wf-go-bugfix`

## Workflow

Read `.claude/workflows/go-bugfix.md` from the repository root and follow it as the source of truth.

Treat the user's remaining prompt as the workflow input.

## Task Run Evidence Protocol

At the end of this workflow, submit a Task Run Evidence payload to Nexus instead of scoring the task inline. If the local API is unavailable, include the same JSON payload in the final response so the user can submit it later.

Preferred automation path: call `$nexus-taskrun-submit` at workflow end.

```text
go run .\cmd\nexus-agents submit-task-run --file <task-run-payload.json>
```

If Nexus is not running but local direct write is preferred:

```text
go run .\cmd\nexus-agents submit-task-run --file <task-run-payload.json> --use-store
```

Endpoint:

```text
POST http://127.0.0.1:8766/api/task-runs
```

Payload shape:

```json
{
  "projectId": "<nexus project id or repo name>",
  "workflowTemplateId": "<workflow template id>",
  "workflowCopyId": "<project workflow copy id if known>",
  "workflowType": "<bugfix|code-review|research|refactor|feature-development|design|commit-gate|lark-integration|subagent-driven-development>",
  "taskTitle": "<short task title>",
  "submittedStatus": "<success|partial_success|failed|cancelled>",
  "startedAt": "<ISO-8601 if known>",
  "endedAt": "<ISO-8601 if known>",
  "durationMs": 0,
  "context": {
    "agent": "<primary agent>",
    "model": "<model id>",
    "rules": ["<rule ids loaded>"],
    "skills": ["<skill ids loaded>"],
    "tools": ["<tools used>"]
  },
  "metrics": {
    "turnCount": 0,
    "toolCallCount": 0,
    "testRunCount": 0,
    "retryCount": 0,
    "errorCount": 0,
    "filesChangedCount": 0
  },
  "evidence": {
    "summary": "<what was done>",
    "finalResult": "<delivered result>",
    "verification": {
      "hasVerification": true,
      "passed": true,
      "types": ["test", "build", "manual_check"],
      "commands": ["<commands run>"]
    },
    "unfinishedItems": [],
    "risks": [],
    "contextMissing": false
  }
}
```

Nexus will evaluate pending task runs asynchronously, produce attribution statistics for workflow / agent / model / rules / skills / context / tools, and index high-value learning cases with chromem-go for future retrieval.

