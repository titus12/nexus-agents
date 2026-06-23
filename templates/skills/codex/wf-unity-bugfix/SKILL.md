---
name: wf-unity-bugfix
description: Invoke with $wf-unity-bugfix to load templates/workflows/unity-bug-investigation.md and follow the Unity bug investigation workflow.
---

# wf-unity-bugfix

Invoke with $wf-unity-bugfix to load templates/workflows/unity-bug-investigation.md and follow the Unity bug investigation workflow.

Read the corresponding workflow markdown completely before acting and follow its required rules and skills.

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
  "workflowTemplateId": "bug-investigation",
  "workflowCopyId": "<project workflow copy id if known>",
  "workflowType": "bug-investigation",
  "taskTitle": "<short task title>",
  "submittedStatus": "<success|partial_success|failed|cancelled>",
  "startedAt": "<ISO-8601 if known>",
  "endedAt": "<ISO-8601 if known>",
  "durationMs": 0,
  "context": {
    "agent": "<primary Unity agent>",
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
    "summary": "<what was fixed or investigated>",
    "finalResult": "<delivered result>",
    "reproduction": {
      "steps": ["<original reproduction steps>"],
      "reproduced": true,
      "fixedOnSamePath": true
    },
    "rootCause": "<confirmed root cause before editing>",
    "verification": {
      "hasVerification": true,
      "passed": true,
      "types": ["unity_compile", "console", "editmode_test", "playmode_test", "manual_repro"],
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
    "assets": {
      "prefabChanged": false,
      "sceneChanged": false,
      "metaSafe": true,
      "generatedFilesTouched": false
    },
    "changedFiles": ["<paths changed>"],
    "skippedChecks": ["<check and reason>"],
    "remainingRisks": [],
    "successfulPath": "<short reusable path if this should become a learning case>",
    "tags": ["unity", "bugfix"],
    "contextMissing": false
  }
}
```

