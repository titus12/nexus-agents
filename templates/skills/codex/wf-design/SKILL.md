---
name: wf-design
description: Design workflow entry. Use for architecture decisions, technical design, implementation planning, or requests that need a design before code. Invoke explicitly with $wf-design.
---

# wf-design

Use this skill as the Codex workflow entry for technical design and architecture planning.

## Source of truth

Before taking workflow-specific actions, read `.claude/workflows/design.md` from the repository root and follow it as the source of truth. If that workflow references additional rule or skill files, read those files before applying the referenced step.

## Invocation

- Codex skill trigger: `$wf-design`

## Procedure

1. Treat the user's remaining prompt as the workflow input.
2. Read `.claude/workflows/design.md`.
3. Load referenced workflow, rule, and skill files exactly as instructed by that workflow.
4. Follow the workflow's clarification, execution, review, and reporting gates.
5. End with concrete evidence: outputs produced, commands run, files changed when applicable, and verification results.
