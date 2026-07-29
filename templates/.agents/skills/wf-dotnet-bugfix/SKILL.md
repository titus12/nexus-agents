---
name: wf-dotnet-bugfix
description: "Unified .NET build, test, runtime, configuration, concurrency, or cancellation bugfix workflow entry. Invoke explicitly with $wf-dotnet-bugfix."
---

# wf-dotnet-bugfix

## Invocation

- Codex skill trigger: `$wf-dotnet-bugfix`

## Workflow

Read `.claude/workflows/dotnet-bugfix.md` from the repository root and follow
it as the source of truth. Treat the user's remaining prompt as the workflow
input.

## Mandatory Task Run submission

`taskrun.mjs start` creates a local payload only; it does not submit anything
to Nexus. Before the final response, complete the payload with actual
reproduction, root-cause, changed-file, validation, risk, and final-status
evidence, then invoke `$nexus-taskrun-submit` exactly once.

Do not report this workflow complete until the submit command succeeds and
returns a Nexus Task Run ID. If submission fails, retain the payload and report
the exact error as a blocked or partial result.
