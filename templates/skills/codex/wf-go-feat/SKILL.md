---
name: wf-go-feat
description: Go feature-development workflow entry. Use for new Go features or substantial Go modules. Invoke explicitly with $wf-go-feat.
---

# wf-go-feat

Use this skill as the Codex workflow entry for new Go feature development and substantial modules.

## Source of truth

Before taking workflow-specific actions, read `templates/workflows/go-feature-development.md` from the repository root and follow it as the source of truth. If that workflow references additional rule or skill files, read those files before applying the referenced step.

## Invocation

- Codex skill trigger: `$wf-go-feat`

## Procedure

1. Treat the user's remaining prompt as the workflow input.
2. Read `templates/workflows/go-feature-development.md`.
3. Load referenced Go workflow, rule, and skill files exactly as instructed by that workflow.
4. Follow the workflow's clarification, implementation, testing, review, and reporting gates.
5. End with concrete evidence: changed files, commands run, and verification results.
