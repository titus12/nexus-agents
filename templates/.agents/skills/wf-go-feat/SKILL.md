---
name: wf-go-feat
description: Unified Go business-change workflow entry. Use for new Go features, existing behavior modifications, and cross-file business adjustments. Invoke explicitly with $wf-go-feat.
---

# wf-go-feat

## Invocation

- Codex skill trigger: `$wf-go-feat`

## Workflow

Read `.claude/workflows/go-feature-development.md` from the repository root and follow it as the source of truth. It requires evidence-driven exploration, an auditable target contract, bounded task loops, goal and quality gates, and final task-run evidence.

Treat the user's remaining prompt as the workflow input.

## Workflow Run Header Protocol

When invoked inside a Nexus project workflow:

1. Start or reuse a workflow run record from Nexus.
2. Use the returned run `id` as `workflowRunId`.
3. Ensure every subsequent Nexus-routed model request carries:

```text
X-Nexus-Workflow-Run-Id: <workflowRunId>
X-Nexus-Workflow-Role: <current role>
```

The role value must change with the active stage, but the workflow run ID must stay stable for the whole workflow.

## Task Run Evidence Protocol

At the end of this workflow, invoke `$nexus-taskrun-submit` to submit actual Task Run Evidence to Nexus. If the local API is unavailable, include the same JSON payload in the final response so the user can submit it later.

Endpoint:

```text
POST http://127.0.0.1:8766/api/task-runs
```

Use the API status vocabulary `success | partial_success | failed | cancelled`. Evidence must list the commands actually run, their results, unfinished items, and risks; do not claim planned validation passed.

```json
{
  "projectId": "<nexus project id or repo name>",
  "workflowTemplateId": "<workflow template id>",
  "workflowCopyId": "<project workflow copy id if known>",
  "workflowType": "feature-development",
  "taskTitle": "<short task title>",
  "submittedStatus": "<success|partial_success|failed|cancelled>",
  "startedAt": "<ISO-8601 if known>",
  "endedAt": "<ISO-8601 if known>",
  "durationMs": 0,
  "context": {
    "agent": "<primary agent>",
    "model": "<model id>",
    "sessionId": "<codex Session-Id if known>",
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
      "commands": ["<commands actually run>"]
    },
    "unfinishedItems": [],
    "risks": [],
    "contextMissing": false
  }
}
```
