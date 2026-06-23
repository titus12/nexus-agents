# go-code-review

Source: .claude/rules/go-00-routing.md
Stack: go

Entry skill: $wf-go-review

Use for review of the current change set.


Codex execution notes:

- Do not spawn subagents unless the user explicitly asks for subagent / parallel / delegation; otherwise execute the named role strategy in the main thread.
- Before acting, load the referenced current rule/skill files by exact path and report which ones were loaded.
- Prefer precise search/codegraph before reading large files.
- End with concrete evidence: changed files, commands run, and verification result.

Workflow:

1. Decide whether a full review or self-review checklist is appropriate.
2. For full review, dispatch reviewers in parallel:
   - go-reviewer-logic
   - go-reviewer-perf
   - go-reviewer-security
3. Merge findings.
4. Deduplicate repeated issues.
5. Sort by severity.
6. Report concrete file and line references when available.

## When to Request Review

Request or perform review when:

- A feature, bugfix, or refactor changes multiple files.
- Behavior, data flow, concurrency, persistence, permissions, economy, or security changes.
- The implementation is ready for merge or commit.
- A previous bug was subtle, high-impact, or hard to reproduce.
- The agent is uncertain about design trade-offs.

For small mechanical changes, a self-review checklist may be enough.

## Review Context Template

Provide reviewers with:

- Goal / requirement
- Files changed and important diffs
- Base and head revision if available
- Commands already run and their results
- Known risks or intentional trade-offs
- Areas where feedback is specifically requested

## Review Result Handling

- Critical: fix before proceeding.
- Important: fix before merge or explicitly defer with reason.
- Minor: fix if cheap, otherwise track as follow-up.
- Incorrect: respond with concise technical evidence.

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

