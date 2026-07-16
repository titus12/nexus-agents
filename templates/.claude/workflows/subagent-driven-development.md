# subagent-driven-development

Entry skill: `$wf-subagents` / 用户明确要求 subagent、并行、分工执行

Use for executing an implementation plan with independent tasks in the current session.

Codex execution notes:

- Only spawn subagents when the user explicitly asks for subagent / parallel / delegation.
- Do not delegate overlapping write scopes to multiple agents.
- Tell every worker they are not alone in the codebase and must not revert others' changes.
- Main session owns integration, final diff review, and final verification.
- End with concrete evidence: changed files, commands run, and verification result.

## Dispatch Mode

Use capsule-based non-fork dispatch by default:

```text
context_mode: capsule_non_fork
fork_context: false
requestedModel: <optional model override>
requestedReasoningEffort: <optional reasoning override>
runtimeModelConfirmed: false
```

- Build the capsule from only the task goal, ownership, confirmed facts, relevant paths, constraints, acceptance criteria, verification, and risks.
- A task that requests a model or reasoning-effort override must use `fork_context: false`.
- Treat `requestedModel` and `requestedReasoningEffort` as requests, not as proof of the runtime selection. Keep `runtimeModelConfirmed: false` unless the runtime returns independent confirmation.
- A full-history fork is an exception only for a read-only task whose material constraints cannot be captured in a capsule, does not need a model or reasoning override, and has an explicitly recorded reason. Do not use it for implementation, independent review, verification, or evidence preparation.

## Plan Compliance

Before implementation, assign stable IDs (`P1`, `P2`, ...) to approved required items and non-goals. Every worker, tester, reviewer, or evaluator capsule includes `planItemIds` for the items it owns.

Quality output uses `met | deviated | unverified | not_started` for each assigned item, with short implementation or verification evidence. The main session may report `success` only when every required item is `met`; a `deviated` item requires explicit approval, and `unverified` or `not_started` requires `partial_success` or `blocked`.

Workflow:

1. Read the plan or requirements and extract independent tasks.
2. Define ownership boundaries for each task:
   - files or modules owned
   - allowed edit scope
   - expected output
   - required tests or checks
3. Dispatch implementer agents only for tasks with non-overlapping ownership.
4. Continue useful non-overlapping work in the main session while agents run.
5. Review each agent result:
   - inspect changed files
   - check whether requirements were met
   - reject or revise changes that exceed scope
6. Run a code-quality or self-review pass after integration.
7. Run final verification in the main session.
8. Report completed tasks, changed files, commands run, and remaining risks.

## Good Candidates

- Independent packages or modules
- Separate frontend/backend slices
- Read-only investigation tasks
- Parallel test or verification passes
- Review after implementation

## Avoid Subagents When

- Tasks modify the same files or tightly coupled logic
- Requirements are still ambiguous
- The design is not stable
- Coordination overhead is larger than the work
- A single linear fix is safer

## Dispatch Prompt Template

```text
Task: [specific task]
Ownership: [files/modules this agent may edit]
Context: [requirements, relevant files, constraints]
Context mode: capsule_non_fork
Fork context: false
Requested model: [optional model override]
Requested reasoning effort: [optional reasoning override]
Plan item IDs: [P1, P2]
Do not edit: [out-of-scope files/modules]
Coordination: You are not alone in the codebase. Do not revert edits made by others. Adapt to existing changes.
Verification: [commands/tests to run if possible]
Report: changed files, tests run, plan compliance status per plan item, blockers, concerns.
```

## Result Status

- Done: requirements met and verification reported.
- Done with concerns: inspect concerns before integration.
- Needs context: provide missing context and retry if worthwhile.
- Blocked: change plan, split task, or escalate to the user.

Never treat an agent success message as proof. Inspect diffs and verify in the main session.

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

