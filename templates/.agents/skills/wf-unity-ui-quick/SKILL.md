---
name: wf-unity-ui-quick
description: Lightweight Unity UI workflow for simple UI fixes. Use for narrow View/ViewModel binding or display changes that do not need the full UI feature workflow. Invoke explicitly with $wf-unity-ui-quick.
---

# wf-unity-ui-quick

## Invocation

- Codex skill trigger: `$wf-unity-ui-quick`

## Scope

Use this workflow only for simple UI tasks:

- at most 2 handwritten files are expected to change;
- no protocol, persistent cache, service API, prefab structure, scene asset, generated file, or cross-feature data flow changes;
- no independent Explorer/Reviewer/Tester subagent is needed;
- validation can be completed with compile-risk checks, targeted code inspection, and optional Unity Console/compile checks.

If the task grows beyond this scope, stop and route to `$wf-unity-ui-feature`.

## Workflow

Read `.claude/workflows/unity-ui-quick.md` from the repository root completely and follow it as the source of truth.

Treat the user's remaining prompt as the workflow input.
