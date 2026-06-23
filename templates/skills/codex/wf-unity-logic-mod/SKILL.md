---
name: wf-unity-logic-mod
description: Invoke with $wf-unity-logic-mod to load templates/workflows/unity-logic-modification.md and follow the Unity logic modification workflow.
---

# wf-unity-logic-mod

Invoke with $wf-unity-logic-mod to load templates/workflows/unity-logic-modification.md and follow the Unity logic modification workflow.

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
  "workflowTemplateId": "logic-modification",
  "workflowCopyId": "<project workflow copy id if known>",
  "workflowType": "logic-modification",
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
    "summary": "<what behavior was changed>",
    "finalResult": "<delivered result>",
    "currentBehavior": "<behavior before the change>",
    "targetBehavior": "<requested behavior after the change>",
    "compatibilityChecked": true,
    "verification": {
      "hasVerification": true,
      "passed": true,
      "types": ["unity_compile", "console", "editmode_test", "playmode_test", "manual_check"],
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
    "tags": ["unity", "logic-modification"],
    "contextMissing": false
  }
}
```

