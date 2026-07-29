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

## Mandatory Task Run submission

`taskrun.mjs start` creates a local payload only; it does not submit anything
to Nexus. Before the final response, complete the payload with actual review
findings, validation gaps, risks, and final status, then invoke
`$nexus-taskrun-submit` exactly once.

Do not report this workflow complete until the submit command succeeds and
returns a Nexus Task Run ID. If submission fails, retain the payload and report
the exact error as a blocked or partial result. Do not edit unless the user
explicitly requests changes.
