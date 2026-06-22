---
name: wf-subagents
description: Subagent-driven workflow entry. Use when the user explicitly asks for subagents, parallel work, delegation, or independent task ownership. Invoke explicitly with $wf-subagents.
---

# wf-subagents

Use this skill as the Codex workflow entry for subagent-driven and parallel task execution.

## Source of truth

Before taking workflow-specific actions, read `.claude/workflows/subagent-driven-development.md` from the repository root and follow it as the source of truth. If that workflow references additional rule or skill files, read those files before applying the referenced step.

## Invocation

- Codex skill trigger: `$wf-subagents`

## Procedure

1. Treat the user's remaining prompt as the workflow input.
2. Read `.claude/workflows/subagent-driven-development.md`.
3. Load referenced workflow, rule, and skill files exactly as instructed by that workflow.
4. Follow the workflow's clarification, execution, review, and reporting gates.
5. End with concrete evidence: outputs produced, commands run, files changed when applicable, and verification results.
