---
name: wf-unity-eval
description: Invoke with $wf-unity-eval to load templates/workflows/unity-workflow-evaluation.md and evaluate Unity workflow evidence.
---

# wf-unity-eval

Invoke with $wf-unity-eval to load templates/workflows/unity-workflow-evaluation.md and evaluate Unity workflow evidence.

Read the corresponding workflow markdown completely before acting and follow its required rules and skills.
## Task Run Evidence Protocol

This evaluation workflow consumes Task Run Evidence submitted by source workflows and may submit its own evaluation run to Nexus when it is executed as a workflow. If the local API is unavailable, include the same JSON payload in the final response so the user can submit it later.

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
  "workflowTemplateId": "unity-workflow-evaluation",
  "workflowCopyId": "<project workflow copy id if known>",
  "workflowType": "unity-workflow-evaluation",
  "taskTitle": "<short evaluation title>",
  "submittedStatus": "<success|partial_success|failed|cancelled>",
  "startedAt": "<ISO-8601 if known>",
  "endedAt": "<ISO-8601 if known>",
  "durationMs": 0,
  "context": {
    "agent": "unity-workflow-evaluator",
    "model": "<model id>",
    "rules": ["<rule ids loaded>"],
    "skills": ["wf-unity-eval"],
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
    "summary": "<evaluation summary>",
    "finalResult": "<score and decision>",
    "evaluatedWorkflowType": "<bug-investigation|logic-modification|ui-feature-development>",
    "score": 0,
    "decision": "<pass|needs_review|fail>",
    "verification": {
      "hasVerification": true,
      "passed": true,
      "types": ["evidence_review"],
      "commands": ["<commands or checks run>"]
    },
    "compile": {
      "passed": "<true|false|unknown>"
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
    "skippedChecks": ["<check and reason>"],
    "remainingRisks": [],
    "successfulPath": "<recommended reusable path or empty>",
    "tags": ["unity", "workflow-evaluation"],
    "contextMissing": false
  }
}
```

