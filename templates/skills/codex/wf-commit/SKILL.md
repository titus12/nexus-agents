---
name: wf-commit
description: Commit-gate workflow entry. Use for pre-commit review, diff risk scanning, validation checks, or preparing a safe commit. Invoke explicitly with $wf-commit.
---

# wf-commit

Use this skill as the Codex workflow entry for pre-commit validation and risk review.

## Source of truth

Before taking workflow-specific actions, read `templates/workflows/commit-gate.md` from the repository root and follow it as the source of truth. If that workflow references additional rule or skill files, read those files before applying the referenced step.

## Invocation

- Codex skill trigger: `$wf-commit`

## Procedure

1. Treat the user's remaining prompt as the workflow input.
2. Read `templates/workflows/commit-gate.md`.
3. Load referenced workflow, rule, and skill files exactly as instructed by that workflow.
4. Follow the workflow's clarification, execution, review, and reporting gates.
5. End with concrete evidence: outputs produced, commands run, files changed when applicable, and verification results.
