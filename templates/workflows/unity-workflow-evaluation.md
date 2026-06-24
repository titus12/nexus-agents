# unity-workflow-evaluation

Evaluate completed Unity workflow Task Run Evidence for bug investigation, logic modification, and UI feature development.

## Roles

1. `unity-workflow-evaluator` - normalizes evidence and creates the initial score.
2. `unity-regression-evaluator` - checks compile, Console, test, reproduction, and manual verification evidence.
3. `unity-asset-safety-evaluator` - checks Prefab, Scene, `.meta`, generated file, imported asset, and serialized reference risk.
4. `model-arbiter` - resolves low-confidence, failed, grey-zone, high-risk, or recurring cases.
5. `learning-curator` - archives high-value success and representative failure cases.

## Workflow

1. Read Task Run Evidence, workflow type, changed files, context, metrics, and verification artifacts.
2. Classify the source workflow as bug investigation, logic modification, or UI feature development.
3. Score workflow adherence, Unity verification evidence, asset safety, regression coverage, and final report quality.
4. Escalate failed, low-confidence, grey-zone, high-risk asset, or missing verification cases to the arbiter.
5. Generate recommendations for missing rules, skills, tests, or reviewer checkpoints.
6. Archive useful successful paths or representative failures as learning cases.

## Workflow Run Header Protocol

When this workflow is started from a Nexus-enabled project, create or reuse one workflow run record first and treat its `id` as the canonical workflow run ID for the entire session.

Start endpoint:

```text
POST http://127.0.0.1:8766/api/workflow-runs/start
```

Use the returned `id` as `workflowRunId`.

For every subsequent model message routed through Nexus Codex, carry these headers:

```text
X-Nexus-Workflow-Run-Id: <workflowRunId>
X-Nexus-Workflow-Role: <current role>
```

Role should reflect the active workflow stage, for example: `planner`, `worker`, `reviewer`, `tester`, `arbiter`, or `unknown`.

Rules:

1. Do not change `workflowRunId` mid-workflow.
2. Every routed message in the workflow must include both headers.
3. When role changes, update only `X-Nexus-Workflow-Role`.
4. At workflow end, submit or complete evidence against the same workflow run ID.

## Task Run Evidence Protocol

This evaluation workflow consumes Task Run Evidence submitted by source workflows and may submit its own evaluation run to Nexus when it is executed as a workflow. If the local API is unavailable, include the same JSON payload in the final response so the user can submit it later.

Preferred automation path via `$nexus-taskrun-submit`:

1. Write the payload JSON to a local file such as `.nexus/task-run-unity-workflow-evaluation.json`.
2. Invoke the `nexus-taskrun-submit` skill at workflow end.
3. The skill should submit it with the Nexus CLI helper:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-unity-workflow-evaluation.json
```

If Nexus is not running but local direct write is preferred, the skill may submit straight into the evaluation store:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-unity-workflow-evaluation.json --use-store
```

Preferred automation path via `$nexus-taskrun-submit`:

1. Write the payload JSON to a local file such as `.nexus/task-run-unity-workflow-evaluation.json`.
2. Invoke the `nexus-taskrun-submit` skill at workflow end.
3. The skill should submit it with the Nexus CLI helper:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-unity-workflow-evaluation.json
```

If Nexus is not running but local direct write is preferred, the skill may submit straight into the evaluation store:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-unity-workflow-evaluation.json --use-store
```

Preferred automation path:

1. Write the payload JSON to a local file such as `.nexus/task-run-unity-workflow-evaluation.json`.
2. Submit it with the Nexus CLI helper:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-unity-workflow-evaluation.json
```

If Nexus is not running but local direct write is preferred, submit straight into the evaluation store:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-unity-workflow-evaluation.json --use-store
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
