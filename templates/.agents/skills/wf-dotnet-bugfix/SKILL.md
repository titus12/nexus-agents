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

At completion, invoke `$nexus-taskrun-submit` with actual reproduction,
root-cause, and validation evidence.
