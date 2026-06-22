---
name: wf-go-review
description: Go code-review workflow entry. Use for reviewing Go diffs or logic/performance/security checks. Invoke explicitly with $wf-go-review.
---

# wf-go-review

Use this skill as the Codex workflow entry for Go code review across logic, performance, and security.

## Source of truth

Before taking workflow-specific actions, read `templates/workflows/go-code-review.md` from the repository root and follow it as the source of truth. If that workflow references additional rule or skill files, read those files before applying the referenced step.

## Invocation

- Codex skill trigger: `$wf-go-review`

## Procedure

1. Treat the user's remaining prompt as the workflow input.
2. Read `templates/workflows/go-code-review.md`.
3. Load referenced Go workflow, rule, and skill files exactly as instructed by that workflow.
4. Follow the workflow's clarification, implementation, testing, review, and reporting gates.
5. End with concrete evidence: changed files, commands run, and verification results.
