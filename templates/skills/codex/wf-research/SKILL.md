---
name: wf-research
description: Research workflow entry. Use for read-only code understanding, call-chain tracing, investigation, documentation lookup, or analysis before deciding changes. Invoke explicitly with $wf-research.
---

# wf-research

Use this skill as the Codex workflow entry for read-only research, understanding, and investigation.

## Source of truth

Before taking workflow-specific actions, read `.claude/workflows/research.md` from the repository root and follow it as the source of truth. If that workflow references additional rule or skill files, read those files before applying the referenced step.

## Invocation

- Codex skill trigger: `$wf-research`

## Procedure

1. Treat the user's remaining prompt as the workflow input.
2. Read `.claude/workflows/research.md`.
3. Load referenced workflow, rule, and skill files exactly as instructed by that workflow.
4. Follow the workflow's clarification, execution, review, and reporting gates.
5. End with concrete evidence: outputs produced, commands run, files changed when applicable, and verification results.
