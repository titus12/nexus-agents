# dotnet-feature-development

Source: `templates/.claude/rules/dotnet-00-routing.md`  
Stack: `.NET`  
Entry skill: `$wf-dotnet-feature`

Use for a focused class-library or hosted/background-service capability.

## Task Run start gate

Before exploration or edits, initialize Task Run evidence:

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs start --projectId <projectId> --workflowType dotnet-feature-development --taskTitle "<ascii task title>" --payloadFile .nexus/task-run-dotnet-feature-development.json --contextFile .nexus/workflow-context-dotnet-feature-development.json
```

Stop and report the exact error if the payload does not contain `sessionId` and
`startedAt`.

## Required process

1. Read `AGENTS.md`, `dotnet-01-project-model.md`,
   `dotnet-02-runtime-safety.md`, `dotnet-03-library-compatibility.md`, and
   the relevant .NET skills.
2. Inspect the real solution/project, SDK, target framework, package
   management, CI command, call sites, configuration, and tests.
3. Write an auditable target contract with goal, non-goals, affected callers,
   validation, assumptions, and API/runtime/dependency risks.
4. Implement the smallest compatible change. Do not create or mutate
   `global.json`, target frameworks, or package versions unless explicitly
   requested.
5. Run the narrowest relevant restore/build/test commands and record actual
   output.
6. Review cancellation, host lifecycle, disposal, retries/timeouts, logging,
   configuration, and public API compatibility.

## Task Run completion

## Mandatory Task Run submission gate

The start command creates a local payload only; it does not submit anything to
Nexus. Before the final response, update that payload with actual changed
files, validation commands/results, unverified paths, risks, and final status.
Complete the same payload once with `$nexus-taskrun-submit`:

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs submit --payloadFile .nexus/task-run-dotnet-feature-development.json --contextFile .nexus/workflow-context-dotnet-feature-development.json
```

Do not report success or completion until this command returns a Nexus Task Run ID.
If it fails, retain the payload and report the exact submission error as a
blocked or partial result. Do not claim planned validation passed.
