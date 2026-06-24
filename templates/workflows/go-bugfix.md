# go-bugfix

Source: .claude/rules/go-00-routing.md
Stack: go

Entry skill: $wf-go-bugfix

Use for Go runtime errors, crashes, and behavioral bugs.


Codex execution notes:

- Do not spawn subagents unless the user explicitly asks for subagent / parallel / delegation; otherwise execute the named role strategy in the main thread.
- Before acting, load the referenced current rule/skill files by exact path and report which ones were loaded.
- Prefer precise search/codegraph before reading large files.
- End with concrete evidence: changed files, commands run, and verification result.

Workflow:

1. Start with go-debugger and identify the root cause before editing.
2. Escalate to go-oracle for cross-module or ambiguous failures.
3. Add or describe a reproduction path.
4. Load go-coding-rules.md before changing code.
5. Verify the reproduction path plus build or targeted tests.

## Debugging Discipline

When fixing a bug or failing test:

1. Reproduce the failure first, or document why it cannot be reproduced locally.
2. Capture the exact command, input, error, stack trace, and relevant environment.
3. Form one hypothesis at a time and inspect evidence before editing code.
4. Prefer the smallest failing reproduction or targeted test.
5. Fix the root cause, not only the visible symptom.
6. Add or update a regression test when practical.
7. Re-run the reproduction and relevant tests after the fix.
8. Report the cause, changed files, commands run, and verification result.

Do not make speculative edits without a reproduced failure or supporting evidence.

## Regression Test Preference

After reproducing the bug and before fixing it, prefer to turn the reproduction into a focused regression test.

Use this when:

- The bug affects business logic, state transitions, rewards, deductions, validation, or public helpers.
- The failure can be reproduced with a stable input or scenario.
- The same area has regressed before.

Minimal loop:

1. Reproduce the bug.
2. Add a targeted test that fails for the observed bug.
3. Confirm the test fails for the expected reason.
4. Implement the smallest fix.
5. Re-run the regression test and relevant build/test commands.

If a regression test is impractical, document the reproduction path and the alternative verification used.

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

1. Write the payload JSON to a local file such as `.nexus/task-run-go-bugfix.json`.
2. Invoke the `nexus-taskrun-submit` skill at workflow end.
3. The skill should submit it with the Nexus CLI helper:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-go-bugfix.json
```

If Nexus is not running but local direct write is preferred, the skill may submit straight into the evaluation store:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-go-bugfix.json --use-store
```

Preferred automation path via `$nexus-taskrun-submit`:

1. Write the payload JSON to a local file such as `.nexus/task-run-go-bugfix.json`.
2. Invoke the `nexus-taskrun-submit` skill at workflow end.
3. The skill should submit it with the Nexus CLI helper:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-go-bugfix.json
```

If Nexus is not running but local direct write is preferred, the skill may submit straight into the evaluation store:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-go-bugfix.json --use-store
```

Preferred automation path:

1. Write the payload JSON to a local file such as `.nexus/task-run-go-bugfix.json`.
2. Submit it with the Nexus CLI helper:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-go-bugfix.json
```

If Nexus is not running but local direct write is preferred, submit straight into the evaluation store:

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-go-bugfix.json --use-store
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

