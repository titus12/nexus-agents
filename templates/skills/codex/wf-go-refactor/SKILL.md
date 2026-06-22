---
name: wf-go-refactor
description: Go refactor workflow entry. Use for Go structural changes, behavior-preserving refactors, or staged impact analysis. Invoke explicitly with $wf-go-refactor.
---

# wf-go-refactor

Use this skill as the Codex workflow entry for Go behavior-preserving refactoring.

## Source of truth

Before taking workflow-specific actions, read `.claude/workflows/go-refactor.md` from the repository root and follow it as the source of truth. If that workflow references additional rule or skill files, read those files before applying the referenced step.

## Invocation

- Codex skill trigger: `$wf-go-refactor`

## Procedure

1. Treat the user's remaining prompt as the workflow input.
2. Read `.claude/workflows/go-refactor.md`.
3. Load referenced Go workflow, rule, and skill files exactly as instructed by that workflow.
4. Follow the workflow's clarification, implementation, testing, review, and reporting gates.
5. End with concrete evidence: changed files, commands run, and verification results.
