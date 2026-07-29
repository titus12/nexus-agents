# dotnet-code-review

Source: `templates/.claude/rules/dotnet-00-routing.md`  
Stack: `.NET`  
Entry skill: `$wf-dotnet-review`

Use for a read-only review of a class-library or hosted/background-service
change.

## Task Run start gate

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs start --projectId <projectId> --workflowType dotnet-code-review --taskTitle "<ascii task title>" --payloadFile .nexus/task-run-dotnet-code-review.json --contextFile .nexus/workflow-context-dotnet-code-review.json
```

## Required process

1. Read `AGENTS.md`, `dotnet-01-project-model.md`,
   `dotnet-02-runtime-safety.md`, `dotnet-03-library-compatibility.md`, and
   `dotnet-dependency-safety`.
2. Inspect the diff and relevant callers/tests without editing unless the user
   explicitly requests a correction.
3. Review public API/nullability/exception compatibility, dependency impact,
   cancellation, host lifecycle, disposal, timeout/retry, logging,
   configuration, and missing validation.
4. Report findings first by severity with exact paths and evidence. If none are
   found, report `No findings` and the remaining validation gaps.

## Task Run completion

## Mandatory Task Run submission gate

The start command creates a local payload only; it does not submit anything to
Nexus. Before the final response, update that payload with actual findings,
validation gaps, risks, and final status. Use `$nexus-taskrun-submit` once:

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs submit --payloadFile .nexus/task-run-dotnet-code-review.json --contextFile .nexus/workflow-context-dotnet-code-review.json
```

Do not report success or completion until this command returns a Nexus Task Run ID.
If it fails, retain the payload and report the exact submission error as a
blocked or partial result. Do not claim planned validation passed.
