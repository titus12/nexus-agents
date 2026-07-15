# lark-integration

Source: `.claude/rules/go-00-routing.md`

Entry skill: `$wf-lark`

Use for Feishu/Lark documents, messages, sheets, Base, and related operations.


Codex execution notes:

- Do not spawn subagents unless the user explicitly asks for subagent / parallel / delegation; otherwise execute the named role strategy in the main thread.
- Before acting, load the referenced current rule/skill files by exact path and report which ones were loaded.
- Prefer precise search/codegraph before reading large files.
- End with concrete evidence: changed files, commands run, and verification result.

Workflow:

1. Route to the matching Feishu/Lark skill.
2. Use `go-librarian` when repository context is needed.
3. Return links, extracted content, or operation results.

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

