# bug-investigation

Source: `.claude/rules/unity-00-routing.md`  
Stack: Unity / C# / UGUI / UIArchitect / assets

## Trigger

Use for:

- Unity compile failures.
- Console errors or warnings that indicate broken behavior.
- Runtime exceptions, crashes, NullReference, MissingReference, or incorrect behavior.
- Broken UI behavior, import failures, Prefab/Scene reference issues, and regressions.
- Failing tests.

## Roles

1. `unity-debugger` - reproduces the issue, captures evidence, and identifies root cause before editing.
2. `unity-bugfix-developer` - implements the smallest root-cause fix.
3. `unity-regression-tester` - verifies the reproduction path, regression tests, Unity compile, Console, and relevant behavior.
4. `unity-bugfix-reviewer` - reviews diff scope, root-cause alignment, asset safety, temporary residue, and verification evidence.

## Required Rules

- `.claude/rules/01-communication.md`
- `.claude/rules/unity-00-routing.md`
- `.claude/rules/unity-01-project-model.md`
- `.claude/rules/unity-bugfix-safety.md`

## Required Skills

- `.agents/skills/wf-unity-bugfix/SKILL.md`
- `.claude/skills/unity-debugger/SKILL.md`
- `.claude/skills/unity-bugfix-developer/SKILL.md`
- `.claude/skills/unity-testing/SKILL.md`
- `.claude/skills/unity-bugfix-review/SKILL.md`

## Reusable Project Skills

- `.claude/skills/unity-mcp-skill/` - Unity Editor automation, Console, compile, tests, screenshots.
- `.claude/skills/unity-ui-developer/` - use if the root cause is UI behavior or UIArchitect integration.
- `.claude/skills/unity-logic-developer/` - use if the root cause is existing non-UI logic.
- `.claude/skills/unity-asset-safety/` - use if assets, Prefabs, Scenes, or `.meta` files are involved.
- `.codex/skills/behaviour-tree/` - use for Battle AI, BonsaiBT, monster, boss, or behavior tree issues.

## Workflow

1. Reproduce the failure, or document why it cannot be reproduced locally.
2. Capture exact evidence: steps, error, stack trace, Console output, test failure, asset path, scene/page path, and environment.
3. Locate the impact area with search, references, code graph, Console stack trace, or Unity MCP.
4. Identify the root cause before editing; avoid speculative fixes.
5. Add or describe a minimal reproduction path; add a regression test first when practical.
6. Implement the smallest root-cause fix.
7. Unity Regression Tester verifies:
   - Original reproduction path.
   - Regression test or targeted test when available.
   - Unity compile and Console errors when applicable.
   - Related behavior that could regress.
8. Unity Bugfix Reviewer checks:
   - Fix matches the root cause.
   - Diff does not include unrelated refactors.
   - No accidental UI/Prefab/Scene/generated/asset changes unless required.
   - Null, lifecycle, async, cancellation, destroyed-object, missing-reference, and boundary risks.
   - Temporary data, Debug logs, mock/fake/test data, local paths, and debug bypasses.
9. Final report includes bug symptom, root cause, changed files, verification evidence, skipped checks with reasons, and remaining risks.

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

At the end of this workflow, submit a Task Run Evidence payload to Nexus instead of scoring the task inline. If the local API is unavailable, include the same JSON payload in the final response so the user can submit it later.

Preferred automation path via `$nexus-taskrun-submit`:

1. Write the payload JSON to a local file such as `.nexus/task-run-unity-bug-investigation.json`.
2. Invoke the `nexus-taskrun-submit` skill at workflow end.
3. The skill should submit it with the Nexus CLI helper:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-unity-bug-investigation.json
```

If Nexus is not running but local direct write is preferred, the skill may submit straight into the evaluation store:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-unity-bug-investigation.json --use-store
```

Preferred automation path via `$nexus-taskrun-submit`:

1. Write the payload JSON to a local file such as `.nexus/task-run-unity-bug-investigation.json`.
2. Invoke the `nexus-taskrun-submit` skill at workflow end.
3. The skill should submit it with the Nexus CLI helper:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-unity-bug-investigation.json
```

If Nexus is not running but local direct write is preferred, the skill may submit straight into the evaluation store:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-unity-bug-investigation.json --use-store
```

Preferred automation path:

1. Write the payload JSON to a local file such as `.nexus/task-run-unity-bug-investigation.json`.
2. Submit it with the Nexus CLI helper:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-unity-bug-investigation.json
```

If Nexus is not running but local direct write is preferred, submit straight into the evaluation store:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-unity-bug-investigation.json --use-store
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

