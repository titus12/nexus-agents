---
name: wf-go-bugfix
description: Go bugfix workflow entry. Use for Go errors, crashes, regressions, incorrect behavior, or root-cause diagnosis. Invoke explicitly with $wf-go-bugfix.
---

# wf-go-bugfix

Use this skill as the Codex workflow entry for Go bug diagnosis and root-cause fixes.

## Source of truth

Before taking workflow-specific actions, read `.claude/workflows/go-bugfix.md` from the repository root and follow it as the source of truth. If that workflow references additional rule or skill files, read those files before applying the referenced step.

## Invocation

- Codex skill trigger: `$wf-go-bugfix`

## Procedure

1. Treat the user's remaining prompt as the workflow input.
2. Read `.claude/workflows/go-bugfix.md`.
3. Load referenced Go workflow, rule, and skill files exactly as instructed by that workflow.
4. Follow the workflow's clarification, implementation, testing, review, and reporting gates.
5. End with concrete evidence: changed files, commands run, and verification results.
