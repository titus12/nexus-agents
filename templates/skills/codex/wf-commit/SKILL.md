---
name: wf-commit
description: Commit-gate workflow entry. Use for pre-commit review, diff risk scanning, validation checks, or preparing a safe commit. Invoke explicitly with $wf-commit.
---

# wf-commit

## Invocation

- Codex skill trigger: `$wf-commit`

## Workflow

Read `.claude/workflows/commit-gate.md` from the repository root and follow it as the source of truth.

Treat the user's remaining prompt as the workflow input.
