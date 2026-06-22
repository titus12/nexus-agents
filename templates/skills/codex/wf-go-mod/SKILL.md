---
name: wf-go-mod
description: Go existing-feature modification workflow entry. Use for adjustments to existing Go behavior or scoped Go changes. Invoke explicitly with $wf-go-mod.
---

# wf-go-mod

Use this skill as the Codex workflow entry for modifying existing Go functionality.

## Source of truth

Before taking workflow-specific actions, read `templates/workflows/go-modify-existing.md` from the repository root and follow it as the source of truth. If that workflow references additional rule or skill files, read those files before applying the referenced step.

## Invocation

- Codex skill trigger: `$wf-go-mod`

## Procedure

1. Treat the user's remaining prompt as the workflow input.
2. Read `templates/workflows/go-modify-existing.md`.
3. Load referenced Go workflow, rule, and skill files exactly as instructed by that workflow.
4. Follow the workflow's clarification, implementation, testing, review, and reporting gates.
5. End with concrete evidence: changed files, commands run, and verification results.
