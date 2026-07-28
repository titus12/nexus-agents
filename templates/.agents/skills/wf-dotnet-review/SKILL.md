---
name: wf-dotnet-review
description: "Unified .NET class-library and hosted-service review workflow entry. Invoke explicitly with $wf-dotnet-review."
---

# wf-dotnet-review

## Invocation

- Codex skill trigger: `$wf-dotnet-review`

## Workflow

Read `.claude/workflows/dotnet-code-review.md` from the repository root and
follow it as the source of truth. Treat the user's remaining prompt as the
workflow input.

At completion, invoke `$nexus-taskrun-submit` with actual review evidence and
findings. Do not edit unless the user explicitly requests changes.
