# logic-modification

Source: `.claude/rules/unity-00-routing.md`  
Stack: Unity / C# / existing logic

## Trigger

Use for:

- Modifying existing C# logic behavior.
- Adjusting existing rules, state flow, calculations, validation, parsing, or process logic.
- Scoped behavior changes that do not involve UI, Prefab, Scene, or visual presentation.

Do not use for UI page, Prefab, View/ViewModel/Presenter presentation, UIArchitect, UGUI, TMP, or Resolver changes; use `ui-feature-development` instead.

## Roles

1. `unity-logic-developer` - locates the impact area and implements the smallest safe behavior change.
2. `unity-logic-tester` - verifies new behavior, key old behavior, Unity compile, Console, and targeted tests.
3. `unity-logic-reviewer` - reviews scope, compatibility, temporary data, boundary conditions, and verification evidence.

## Required Rules

- `.claude/rules/01-communication.md`
- `.claude/rules/unity-00-routing.md`
- `.claude/rules/unity-01-project-model.md`
- `.claude/rules/unity-logic-mod-safety.md`

## Required Skills

- `.agents/skills/wf-logic-mod/SKILL.md`
- `.claude/skills/unity-logic-developer/SKILL.md`
- `.claude/skills/unity-testing/SKILL.md`
- `.claude/skills/unity-logic-review/SKILL.md`

## Reusable Project Skills

- `.claude/skills/unity-mcp-skill/` - Unity Editor automation, Console, tests.
- `.claude/skills/unity-asset-safety/` - use if diff unexpectedly touches Unity assets.
- `.codex/skills/behaviour-tree/` - use only for Battle AI, BonsaiBT, monster, boss, or behavior tree logic.
- `.claude/skills/vm-logic/` - use only for ViewModel state logic that does not require UI presentation changes.

## Workflow

1. Locate the impact area with search, references, or code graph.
2. Read matching module docs, nearby code, tests, and relevant skills when they exist.
3. Summarize current behavior and target behavior before editing.
4. Load Unity/C# logic coding guidance from `unity-logic-developer`.
5. Implement the smallest scoped behavior change.
6. Add or update targeted tests when practical; otherwise record a concrete manual verification path.
7. Unity Logic Tester verifies:
   - Unity compile and Console errors when available.
   - Targeted EditMode / PlayMode tests when applicable.
   - New behavior.
   - Key old behavior that could regress.
8. Unity Logic Reviewer checks:
   - Diff stays within target logic.
   - No accidental UI, Prefab, Scene, generated file, or asset changes.
   - Compatibility with existing callers.
   - Null, boundary, exception, cancel, timeout, and destroyed-object risks.
   - Temporary data, Debug logs, mock/fake/test data, local paths, and temporary switches.
   - Hot-path performance risks.
9. Final report includes changed files, verification evidence, skipped checks with reasons, and remaining risks.

## Task Run Evidence Protocol

At the end of this workflow, submit a Task Run Evidence payload to Nexus instead of scoring the task inline. If the local API is unavailable, include the same JSON payload in the final response so the user can submit it later.

Preferred automation path via `$nexus-taskrun-submit`:

1. Write the payload JSON to a local file such as `.nexus/task-run-unity-logic-modification.json`.
2. Invoke the `nexus-taskrun-submit` skill at workflow end.
3. The skill should submit it with the Nexus CLI helper:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-unity-logic-modification.json
```

If Nexus is not running but local direct write is preferred, the skill may submit straight into the evaluation store:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-unity-logic-modification.json --use-store
```

Preferred automation path via `$nexus-taskrun-submit`:

1. Write the payload JSON to a local file such as `.nexus/task-run-unity-logic-modification.json`.
2. Invoke the `nexus-taskrun-submit` skill at workflow end.
3. The skill should submit it with the Nexus CLI helper:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-unity-logic-modification.json
```

If Nexus is not running but local direct write is preferred, the skill may submit straight into the evaluation store:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-unity-logic-modification.json --use-store
```

Preferred automation path:

1. Write the payload JSON to a local file such as `.nexus/task-run-unity-logic-modification.json`.
2. Submit it with the Nexus CLI helper:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-unity-logic-modification.json
```

If Nexus is not running but local direct write is preferred, submit straight into the evaluation store:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-unity-logic-modification.json --use-store
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

