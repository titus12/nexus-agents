---
name: wf-dotnet-feature
description: "Unified .NET class-library and hosted-service feature workflow entry. Invoke explicitly with $wf-dotnet-feature."
---

# wf-dotnet-feature

## Invocation

- Codex skill trigger: `$wf-dotnet-feature`

## Workflow

Read `.claude/workflows/dotnet-feature-development.md` from the repository
root and follow it as the source of truth. Treat the user's remaining prompt
as the workflow input.

At completion, invoke `$nexus-taskrun-submit` with actual evidence. Do not
claim planned validation passed.
