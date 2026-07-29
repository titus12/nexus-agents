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

## Mandatory Task Run submission

`taskrun.mjs start` creates a local payload only; it does not submit anything
to Nexus. Before the final response, complete the payload with actual changed
files, validation commands/results, risks, and final status, then invoke
`$nexus-taskrun-submit` exactly once.

Do not report this workflow complete until the submit command succeeds and
returns a Nexus Task Run ID. If submission fails, retain the payload and report
the exact error as a blocked or partial result; do not claim planned validation
passed.
