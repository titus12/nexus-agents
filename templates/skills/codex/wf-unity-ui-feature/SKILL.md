---
name: wf-unity-ui-feature
description: Invoke with $wf-unity-ui-feature to load templates/workflows/unity-ui-feature-development.md and follow the Unity UI feature workflow.
---

# wf-unity-ui-feature

Invoke with $wf-unity-ui-feature to load templates/workflows/unity-ui-feature-development.md and follow the Unity UI feature workflow.

Read the corresponding workflow markdown completely before acting and follow its required rules and skills.

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
  "workflowTemplateId": "ui-feature-development",
  "workflowCopyId": "<project workflow copy id if known>",
  "workflowType": "ui-feature-development",
  "taskTitle": "<short task title>",
  "submittedStatus": "<success|partial_success|failed|cancelled>",
  "startedAt": "<ISO-8601 if known>",
  "endedAt": "<ISO-8601 if known>",
  "durationMs": 0,
  "context": {
    "agent": "<primary Unity UI agent>",
    "model": "<model id>",
    "rules": ["<rule ids loaded>"],
    "skills": ["<skill ids loaded>"],
    "tools": ["<tools used, including Unity MCP tools>"]
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
    "summary": "<what UI was implemented or changed>",
    "finalResult": "<delivered result>",
    "verification": {
      "hasVerification": true,
      "passed": true,
      "types": ["unity_compile", "console", "editmode_test", "playmode_test", "manual_ui_check", "screenshot"],
      "commands": ["<commands or Unity MCP operations run>"]
    },
    "compile": {
      "passed": true
    },
    "console": {
      "errors": 0,
      "warnings": 0
    },
    "tests": {
      "editMode": "<passed|failed|skipped|not_applicable>",
      "playMode": "<passed|failed|skipped|not_applicable>"
    },
    "uiChecks": {
      "openClose": true,
      "repeatOpen": true,
      "loadingState": true,
      "errorState": true,
      "emptyState": true,
      "inputLockRelease": true,
      "resolutionAdaptation": true
    },
    "assets": {
      "prefabChanged": true,
      "sceneChanged": false,
      "metaSafe": true,
      "generatedFilesTouched": false
    },
    "changedFiles": ["<paths changed>"],
    "skippedChecks": ["<check and reason>"],
    "remainingRisks": [],
    "successfulPath": "<short reusable path if this should become a learning case>",
    "tags": ["unity", "ui-feature"],
    "contextMissing": false
  }
}
```

