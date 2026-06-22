---
name: wf-lark
description: Lark integration workflow entry. Use for Feishu/Lark documents, messages, sheets, bases, meetings, tasks, wiki, or Lark tool routing. Invoke explicitly with $wf-lark.
---

# wf-lark

Use this skill as the Codex workflow entry for Feishu and Lark operations.

## Source of truth

Before taking workflow-specific actions, read `.claude/workflows/lark-integration.md` from the repository root and follow it as the source of truth. If that workflow references additional rule or skill files, read those files before applying the referenced step.

## Invocation

- Codex skill trigger: `$wf-lark`

## Procedure

1. Treat the user's remaining prompt as the workflow input.
2. Read `.claude/workflows/lark-integration.md`.
3. Load referenced workflow, rule, and skill files exactly as instructed by that workflow.
4. Follow the workflow's clarification, execution, review, and reporting gates.
5. End with concrete evidence: outputs produced, commands run, files changed when applicable, and verification results.
