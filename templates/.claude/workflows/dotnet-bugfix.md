# dotnet-bugfix

Source: `templates/.claude/rules/dotnet-00-routing.md`  
Stack: `.NET`  
Entry skill: `$wf-dotnet-bugfix`

Use for a confirmed build, test, runtime, configuration, concurrency, or
cancellation defect.

## Task Run start gate

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs start --projectId <projectId> --workflowType dotnet-bugfix --taskTitle "<ascii task title>" --payloadFile .nexus/task-run-dotnet-bugfix.json --contextFile .nexus/workflow-context-dotnet-bugfix.json
```

## Required process

1. Read `AGENTS.md`, `dotnet-01-project-model.md`,
   `dotnet-02-runtime-safety.md`, and `dotnet-testing`.
2. Reproduce the failure with an exact command or documented execution path.
3. Collect logs, stack traces, configuration evidence, call paths, and tests;
   distinguish an environment failure from a product defect.
4. State root cause before changing code, then make the smallest compatible
   correction.
5. Run focused build/test and the reproduction path. Check cancellation,
   lifecycle, disposal, retry/timeout, logging, and configuration behavior.

## Task Run completion

## Mandatory Task Run submission gate

The start command creates a local payload only; it does not submit anything to
Nexus. Before the final response, update that payload with actual reproduction,
root cause, changed files, validation commands/results, risks, and final
status. Use `$nexus-taskrun-submit` once:

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs submit --payloadFile .nexus/task-run-dotnet-bugfix.json --contextFile .nexus/workflow-context-dotnet-bugfix.json
```

Do not report success or completion until this command returns a Nexus Task Run ID.
If it fails, retain the payload and report the exact submission error as a
blocked or partial result. Do not claim planned validation passed.
