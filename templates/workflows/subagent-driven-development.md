# subagent-driven-development

Trigger: `--subagents` / `--parallel` / 用户明确要求 subagent、并行、分工执行

Use for executing an implementation plan with independent tasks in the current session.

Codex execution notes:

- Only spawn subagents when the user explicitly asks for subagent / parallel / delegation.
- Do not delegate overlapping write scopes to multiple agents.
- Tell every worker they are not alone in the codebase and must not revert others' changes.
- Main session owns integration, final diff review, and final verification.
- End with concrete evidence: changed files, commands run, and verification result.

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
Do not edit: [out-of-scope files/modules]
Coordination: You are not alone in the codebase. Do not revert edits made by others. Adapt to existing changes.
Verification: [commands/tests to run if possible]
Report: changed files, tests run, status, blockers, concerns.
```

## Result Status

- Done: requirements met and verification reported.
- Done with concerns: inspect concerns before integration.
- Needs context: provide missing context and retry if worthwhile.
- Blocked: change plan, split task, or escalate to the user.

Never treat an agent success message as proof. Inspect diffs and verify in the main session.
