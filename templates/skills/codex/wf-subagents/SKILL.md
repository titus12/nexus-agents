---
name: wf-subagents
description: Subagent-driven workflow entry. Use when the user explicitly asks for subagents, parallel work, delegation, or independent task ownership. Invoke explicitly with $wf-subagents.
---

# wf-subagents

## Invocation

- Codex skill trigger: `$wf-subagents`

## Workflow

Read `.claude/workflows/subagent-driven-development.md` from the repository root and follow it as the source of truth.

Treat the user's remaining prompt as the workflow input.
