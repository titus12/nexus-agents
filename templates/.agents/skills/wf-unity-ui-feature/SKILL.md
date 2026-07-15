---
name: wf-unity-ui-feature
description: Unity UI feature workflow entry. Use for concrete btdgame UI View/ViewModel/Service/DataEvents feature logic. Invoke explicitly with $wf-unity-ui-feature.
---

# wf-unity-ui-feature

## Invocation

- Codex skill trigger: `$wf-unity-ui-feature`
- Explicit invocation of `$wf-unity-ui-feature` means the user selected the full feature workflow,
  including its default role split and support subagents defined in `.claude/workflows/wf-unity-ui-feature.md`.
  Do not disable those workflow subagents merely because the user did not separately say "use subagents".

## Scope Guard

This is the full business UI feature workflow. It is intended for UI work that needs requirement collection,
View/ViewModel/Service/DataEvents/Cache/protocol coordination, Unity verification, review evidence, and Nexus reporting.

For simple UI tasks, do **not** reuse this heavyweight workflow. Route to `$wf-unity-ui-quick` instead when all of the
following are true:

- the task is a small fix or narrow change;
- the expected implementation touches no more than 2 handwritten files;
- no protocol, persistent cache, service API, prefab structure, scene asset, generated file, or cross-feature data flow is changed;
- independent subagents are not needed.

Examples for `$wf-unity-ui-quick`: fix a dialog path, bind one missing button event, correct a label/state refresh,
or adjust a small ViewModel display rule.

## Minimal Prompt Example

```text
[$wf-unity-ui-feature]

UI资源:
Assets/ResourcesAB/UI/xxx/XXX.prefab

需求:
这个界面打开后显示什么，用户点击什么，点击后发生什么。

关键节点:
哪些文本/按钮/list/tile 分别是什么业务含义。

数据:
配置/Cache/Service/协议名字或路径。
```

## Workflow

Read `.claude/workflows/wf-unity-ui-feature.md` from the repository root completely and follow it as the source of truth.

Treat the user's remaining prompt as the workflow input.

## First-plan stability requirements

When this skill is invoked for a UI prefab/page:

- Do not produce a first implementation plan from local code guesses if the user's primary requirement source is a
  Feishu/Lark Doc/Wiki/URL and it has not been read successfully.
- Apply the common source-reading procedure in `KnowledgeBase/framework/external-source-reading.md`. A
  subagent's inability to read Feishu/Lark is not sufficient evidence to continue with a guessed plan.
- The first user-facing plan must include UI resource mapping before file changes:
  - generated field/node -> business meaning -> data source -> binding/update path -> visible result;
  - user action -> command/view method -> service/cache/DataEvents effect -> UI state/result;
  - list/tile/item render data type, display fields, states, click behavior, and refresh/recycle rules;
  - locked/unlocked, selected/unselected, disabled, max/limit, loading/empty/error, close/reopen states when applicable;
  - uncertain inferred bindings that need user audit.
