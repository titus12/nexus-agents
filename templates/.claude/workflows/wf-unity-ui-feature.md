# Unity UI 功能开发

Source: `.claude/rules/unity-00-routing.md`  
Stack: Unity / C# / UGUI / TextMeshPro / UIArchitect

## Start Gate

Before Knowledge Retrieval, CodeGraph, search, planning, subagent dispatch, or editing, initialize the local Nexus TaskRun payload. Use the cross-platform Node helper by default so the same workflow works on Windows and macOS:

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs start --projectId btd-client --workflowType ui-feature-development --taskTitle "<task title>" --payloadFile .nexus/task-run-ui-feature-development.json --contextFile .nexus/workflow-context-ui-feature-development.json
```

Continue only after confirming `.nexus/task-run-ui-feature-development.json` exists and contains `sessionId` and `startedAt`. If start fails, stop the workflow and report the exact error; do not continue investigation or implementation.

## Knowledge Loading

Follow `.claude/rules/knowledge-retrieval.md`. Record `knowledgeRetrieval` and include the returned `Loaded Knowledge` section in the final summary.

## 适用场景

Use for business UI feature work in `btdgame` when the task may involve View/ViewModel/Service/DataEvents/Cache,
protocol/config integration, prefab interaction, validation evidence, review, and Nexus reporting.

Use for:

- UI page, popup, or component development;
- UI business logic;
- consuming PSD Import/UIArchitect generated output in game UI prefabs/views;
- View / ViewModel / Presenter / Binding work;
- Prefab, UGUI, TMP, layout, or interaction changes.

Do not use this workflow for UIArchitect toolchain internals such as PSD Import Pipeline, NamingChecker, TagResolver,
ViewGenerator, PrefabExtractor, Diff/Merge, AuditWindow, or Atlas/TexturePacker changes. Route those to
`$wf-uiarchitect-tool`.

## 核心原则

UI feature must wire UI resources before business logic. First translate the prefab into business meaning, then decide
which scripts to create or mount, which serialized fields bind to which nodes, and which ViewModel properties/commands
drive each node. Only after key prefab references are non-null should the workflow move to data binding and business
logic.

## 角色分工

These roles are part of the default feature workflow split. Tasks handled by this workflow are expected to benefit from
real role separation.

1. `requirement-collector` - default **subagent**, spawn with model `gpt-5.4` to collect and compress requirements from user input, Feishu docs,
   screenshots/tables, protocol paths, config paths, Feature Cards, UI AIConfig, and KnowledgeBase. Output only the
   minimal task context package.
2. `ui-developer` - default **main agent role**, use the current main model unless the user explicitly requests another model. Implement UI and business logic from the task context package. Read generated
   bindings, Service/DataEvents/Cache, protocol, config, one example, or one template only when needed.
3. `ui-tester` - default **subagent**, spawn with model `gpt-5.4` to validate compile, Console, tests, interactions, state transitions,
   and input-lock release paths.
4. `ui-reviewer` - default **subagent**, spawn with model `gpt-5.5` to review diff, assets, layout adaptation, View/ViewModel/Service/DataEvents/
   Cache boundaries, generated-file safety, and remaining risks.
5. `workflow-evaluator` - default **subagent**, spawn with model `gpt-5.4` to prepare verification evidence, role/model usage records,
   Nexus TaskRun payload, metrics, and final report.

## 计划门禁

Implementation must not start until a plan has been shown to the user and explicitly approved.

### Requirement-source hard gate

When the user's requirement source is a Feishu/Lark Doc/Wiki/URL, that source is authoritative for the first plan.
The workflow must not replace a failed Feishu/Lark read with guesses from local candidates.

Before presenting the first user-facing plan:

- Attempt to read the Feishu/Lark source with `$lark-doc` / official docs v2 (`docs +fetch --api-version v2 --as user` or the project wrapper that invokes it).
- If the read fails because of authorization, connector, sandbox, scope, or tool availability, stop requirement collection and request the required user authorization/tool escalation, then retry the same v2 read path.
- If a requirement-collector subagent cannot access Feishu/Lark tools, it must mark the package as `BLOCKED_FEISHU_READ` instead of producing a local-candidate-only plan.
- The main agent must not accept a `BLOCKED_FEISHU_READ` package as sufficient. It must perform or request the v2 Feishu/Lark read itself before planning.
- Only when the user explicitly says to proceed without the Feishu/Lark document may the workflow produce a degraded plan. That degraded plan must clearly state that the primary requirement source was unavailable.

### UI-resource mapping hard gate

When the task involves a prefab, `*.Gen.cs`, UI screenshot, UI page, popup, HUD, list, tile, item render, or UI resource,
the first plan must start from UI resource semantics before business logic.

The plan is incomplete and must not be presented as an implementation-ready plan unless it includes:

- a UI resource table mapping each important generated field/node to business meaning, data source, binding/update path, and user-visible result;
- an interaction table mapping user actions to ViewModel commands, Service/DataEvents/Cache effects, UI state changes, and failure/disabled behavior;
- a state/logic description for loading, empty, locked/unlocked, selected/unselected, enabled/disabled, max/limit, error, close/reopen, and list item states that apply to the prefab;
- a list/tile/item-render mapping when any list/tile/item exists, including item data type, display fields, click behavior, recycled item refresh behavior, and selection rules;
- explicit uncertain bindings or inferred resource meanings for user audit.

The plan must include:

- task scope and why the full UI feature workflow is being used;
- requirement context summary;
- requirement conflict report when Feishu/design docs, config, protocol, prefab, generated bindings, or current UI state disagree;
- expected changed files and forbidden files;
- data flow boundaries;
- validation plan;
- subagent mode decision;
- Nexus evidence plan.
- UI resource mapping plan when UI resources are involved:
  - node business meaning: what each important text/button/image/list/tile/item displays or does;
  - script plan: scripts to create or reuse, mount node, responsibility, and what they must not own;
  - serialized reference plan: script field, type, UI node, component, and business need;
  - data/command plan: ViewModel property or command, View field/method, UI result, and trigger;
  - list/tile/item plan: container, item node, data source, item fields/states, click behavior, and ItemRender script;
  - conflicts or uncertain inferred bindings for user audit.

If read-only research cannot confirm the real issue or root cause, the plan must switch to a **diagnosis-first plan**
instead of presenting a speculative fix. A diagnosis-first plan must include:

- what is still unconfirmed and why the current evidence is insufficient;
- the smallest temporary or macro-gated diagnostic instrumentation needed;
- expected log lines, UI observations, console evidence, or config/protocol facts that will distinguish the candidate causes;
- the decision rule for moving from diagnosis to implementation;
- whether the diagnostic code should be removed or kept behind a macro after the root cause is confirmed.

Do not label a speculative behavior change as the implementation plan. If the user asks to proceed while the cause is
uncertain, implement only the approved diagnosis-first plan until evidence confirms the fix.

If research finds conflicts between requirement documents and local facts, list them in the first-stage plan before any
implementation. Each conflict must include:

- conflicting sources, for example Feishu doc vs config/protocol/prefab/generated binding/current UI;
- impact on implementation or UX;
- 2-3 concrete resolution options;
- recommended default when safe;
- explicit user decision required when the choice changes behavior, data meaning, UI layout, economy/cost, unlock
  rules, server contract, or asset/prefab structure.

If the user explicitly says "execute this plan" or equivalent in the same turn, that counts as approval for that plan.

## CodeGraph / 代码事实规则

Use read-only CodeGraph directly when available. Do not ask the user for permission for read-only CodeGraph exploration.
Ask for permission only for write operations, destructive actions, network access, or environment-level escalation.

Code facts should be gathered with the smallest sufficient query. Prefer CodeGraph or precise searches over loading broad
source directories.

## 子代理策略

## Subagent Dispatch Mode

All default support subagents use a minimal, evidence-focused non-fork context package:

```text
context_mode: capsule_non_fork
fork_context: false
requestedModel: <optional model override>
requestedReasoningEffort: <optional reasoning override>
runtimeModelConfirmed: false
```

- The package contains only the feature goal, authoritative sources, confirmed facts, relevant files/assets, allowed scope, acceptance criteria, verification, and risks.
- A role with a model or reasoning-effort override must use `fork_context: false`.
- Record `requestedModel` and `requestedReasoningEffort` as requested settings only. Keep `runtimeModelConfirmed: false` unless the runtime independently confirms the selected model and effort.
- A full-history fork is allowed only for explicitly justified read-only analysis that cannot be expressed in a context package and has no model or reasoning override. Do not use it for implementation, review, testing, asset safety, or evidence preparation.

Explicit invocation of `$wf-unity-ui-feature` is an explicit request to run this workflow, including the
default support subagents below. Do not reinterpret generic agent-tool restrictions as a reason to suppress
`requirement-collector`, `ui-reviewer`, `ui-tester`, or `workflow-evaluator` after this workflow has been selected.

Default mode for this workflow is **one implementation owner plus four support subagents**:

- `requirement-collector`: split by default;
- `ui-developer`: stays on the main agent by default;
- `ui-reviewer`: split by default;
- `ui-tester`: split by default;
- `workflow-evaluator`: split by default.

### 默认功能任务

For the normal tasks that still use this workflow:

- `requirement-collector` runs first as an independent read-only subagent with `model: gpt-5.4` and produces the Requirement Context Package;
- `ui-developer` stays in the main session and owns the implementation;
- `ui-reviewer` reviews the implementation diff independently with `model: gpt-5.5`;
- `ui-tester` validates compile / console / interaction / edge paths independently with `model: gpt-5.4`;
- `workflow-evaluator` independently prepares the Nexus Evidence Package and metrics with `model: gpt-5.4`.

When spawning these default subagents, pass the model override explicitly in the subagent call. Do not merely print a
role/model label while continuing to execute every role on the main model.

### 大型或高风险任务

This workflow already enables the four support subagents above by default. Extra splits are only needed when one or more
of the following is true:

- expected changes exceed 8 files;
- the task crosses UI, Service, Cache/DataEvents, protocol, and config layers;
- independent code investigation is needed before planning;
- generated files, prefabs, scenes, or asset safety are high risk;
- the user explicitly requests subagents, parallel work, delegation, or role-specific models.

### 实现工作者拆分

Do **not** split `ui-developer` into multiple implementation workers by default.

Split additional implementation workers only when:

- ownership boundaries are clear;
- write sets do not overlap;
- the main agent will not be blocked waiting on a single ambiguous worker result;
- each worker can own a bounded file/module slice.

Prefer keeping one implementation owner and multiple independent reviewers/testers over many concurrent code writers.

### 角色切换输出

Do not prefix every message with role/stage labels. If a subagent is actually spawned, the UI/tooling already makes the
role separation visible.

Emit at most one concise checkpoint per actual role transition when it helps the user understand progress:

```text
[阶段切换]
角色: <role>
模型: <model or inherited/default>
输入: <context package / diff / validation target>
目标: <what this role must decide or produce>
```

Skip the checkpoint for routine main-agent messages. The main agent remains responsible for merging, accepting, or
rejecting subagent outputs.

### 上下文包规则

Each split role receives only the smallest context package required for its job:

- `requirement-collector`: requirement sources, explicit paths, and routing targets only;
- `ui-developer`: approved requirement context package, approved plan, target code facts, and implementation constraints;
- `ui-reviewer`: approved plan, diff, forbidden-file list, and review checklist;
- `ui-tester`: changed-file summary, validation targets, environment constraints, and test checklist;
- `workflow-evaluator`: final changed files, verification evidence, skipped checks, remaining risks, and model/role usage.

Do not pass the entire chat history by default.

## Plan Compliance

使用本地 `.nexus/plan-compliance-ui-feature-development.json` 及通用 Plan Compliance loop；不得写入 TaskRun payload。批准的 UI 计划为资源、行为、验证和非目标分配 `planItemIds`。`ui-reviewer`、`ui-tester` 和 `workflow-evaluator` 对其负责项返回 `met | deviated | unverified | not_started` 与简短证据；Owner 只有在所有必需项为 `met`、偏离已获批准时才可完成。

## Unity Quality Gate

`ui-reviewer`、`ui-tester` 和 `workflow-evaluator` 必须读取 `.agents/skills/wf-subagents/unity-quality-rubric.md` 并返回 `QualityResult`。Owner 将汇总写入本地 ledger 的 `quality.blockingFindings`、`quality.majorFindings`、`quality.skippedRequiredChecks`、`quality.requiredChecks`、`quality.passedChecks`、`quality.manualAcceptancePending` 和 `quality.unexpectedChanges`，再运行：

```text
node .agents/skills/wf-subagents/plan-loop.mjs gate --file .nexus/plan-compliance-ui-feature-development.json --stage quality
```

不得依据口头“测试通过”继续；结果为 `repair` 时修复后重跑，`awaiting_user_acceptance` 时等待用户验收，`blocked` 时停止。

## 必需规则

- `.claude/rules/01-communication.md`
- `.claude/rules/knowledge-retrieval.md`
- `.claude/rules/unity-00-routing.md`
- `.claude/rules/unity-01-project-model.md`

## 必需技能

- `.agents/skills/wf-unity-ui-feature/SKILL.md`
- `.claude/skills/unity-ui-requirement-collector/SKILL.md`
- `.claude/skills/unity-ui-developer/SKILL.md`
- `.claude/skills/unity-testing/SKILL.md`
- `.claude/skills/unity-asset-safety/SKILL.md`

## 可复用项目技能

- `.claude/skills/unity-ui-resolver/SKILL.md` - add or update PSD Component Resolver.
- `.claude/skills/unity-mcp-skill/` - Unity Editor automation, Console, tests, screenshots.
- `.claude/skills/vm-logic/` - ViewModel or UI state logic when applicable.

## Business UI Knowledge Sources

Nexus Knowledge Retrieval selects the required UI AIConfig and KnowledgeBase context. Do not manually read these files before retrieval.

Local fallback/source references when retrieval is unavailable or the returned context explicitly points to them:

- `design/UIDevelop/AIConfig/README.md`
- `design/UIDevelop/AIConfig/knowledge/routing.md`
- `KnowledgeBase/project/domains/ui/index.md`
- `KnowledgeBase/project/domains/ui/routing.md`
- `design/UIArchitect/AIConfig/` for UIArchitect plugin/toolchain work only; route those tasks to `$wf-uiarchitect-tool`.

Use CodeGraph or precise search only for code facts after knowledge retrieval.

## 需求上下文包

`requirement-collector` must produce a compact task context package before implementation:

```text
需求摘要: <1-5 bullets>
UI 页面/组件: <ViewName / Prefab / Dialog / HUD / ItemRender / unknown>
用户操作与状态: <click/open/close/loading/empty/error/disabled/guide/etc.>
协议路径: <proto/C# protocol paths, none, or unknown>
配置路径: <config table/file paths, none, or unknown>
Feature Card: <path or none>
候选代码路径:
  Gen.cs: <path or unknown>
  View: <path or unknown>
  ViewModel: <path or unknown>
  Service: <path, none, or unknown>
  DataEvents: <path, none, or unknown>
  Cache/Data: <path, none, or unknown>
必读知识库条目: <minimal files only>
缺失信息与假设: <blocking items or assumptions allowed for progress>
工作流选择: <unity-ui-feature>
subagent 决策: <none | explorer | reviewer | tester | worker split with reason>
冲突报告: <none | Feishu/design doc vs config/protocol/prefab/generated/UI conflict with options and recommended default>
需求读取状态: <ok | BLOCKED_FEISHU_READ | degraded-with-user-approval>
UI资源映射:
  资源表: <generated field/node -> business meaning -> data source -> binding/update path -> UI result>
  交互表: <user action -> command/view method -> data/service/event effect -> UI state/result>
  状态逻辑: <selected/unselected, locked/unlocked, disabled, max, empty/loading/error, close/reopen>
  List/Tile/ItemRender: <container, item data type, item fields/states, click behavior, refresh/recycle rules>
  不确定绑定: <inferred or conflicting mappings requiring user audit>
```

Token constraints:

- `requirement-collector` reads only the minimal requirement source, Feature Card, path candidates, and required
  KnowledgeBase entries.
- `ui-developer` consumes the task context package by default and reads additional files only when needed.

## 工作流

1. `requirement-collector` classifies scope. If it is UIArchitect toolchain work, route to `$wf-uiarchitect-tool`.
2. Load required rules and retrieve workflow knowledge through `.claude/rules/knowledge-retrieval.md`.
3. Read requirement sources. If the user provides a Feishu/Lark Wiki/Docx link, use `$lark-doc` and keep the读取流程简化为：
   - 先尝试官方 `docs +fetch`；
   - Wiki/Docx 结构不明确时，先取 outline，再取目标章节；
   - 如果读取失败且文档仍然是主要需求来源，必须先要求用户授权/工具放行，并用 docs v2 重新连接读取；不得用本地候选 prefab/Feature Card 猜测替代需求；
   - 如果 requirement-collector 子代理无法读取飞书，主代理必须接管读取或请求授权；子代理输出的候选模块清单不能作为最终需求包；
   - 如果文档能读到，就直接采用读取结果，不再展开更多 fallback 说明。
4. Produce the Requirement Context Package and the user-facing implementation plan. If the root cause is not
   confirmed, produce a diagnosis-first plan instead of a speculative fix plan. For UI resource tasks, the first
   plan must include UI resource/data binding/interaction/state logic mappings before file-level changes.
5. Wait for explicit plan approval.
6. Spawn `requirement-collector` as the default requirement subagent and consume its Requirement Context Package.
7. `ui-developer` presents the plan and waits for explicit approval. The plan must put UI resource mapping before
   business logic. If the approved plan is diagnosis-first, the next
   implementation step is diagnostic instrumentation only; do not change business behavior until the diagnostic evidence
   confirms the root cause.
8. `ui-developer` implements the smallest clear solution in this order:
   - create missing View / ViewModel / ItemRender scripts only after stating their mount node, responsibility, fields,
     UI nodes, and business need;
   - bind UI resources first: mount scripts and bind serialized references with Unity MCP;
   - for list/tile/item/cell nodes, implement the container data source and the item render fields/states/click behavior
     explicitly, not just "bind the list";
   - reuse existing components and patterns first;
   - View displays, binds, and gives visual feedback;
   - ViewModel owns UI state, commands, and business decisions;
   - Service sends requests but does not directly persist Player/Cache state in callbacks;
   - persistent data updates go through NetMsgEventManager/Cache/DataEvents paths;
   - Editor-only and Runtime code stay separated;
   - generated View files are not hand-edited.
   - UI resource component mounts and serialized references must be bound with Unity MCP first; do not replace resource
     binding with runtime Find code or direct prefab YAML/script patching unless Unity MCP is unavailable and the user
     explicitly approves the fallback.

### 诊断证据门禁

在修改运行时 UI 行为前，先判断：当前证据是否已经足够确认唯一故障点，并能证明修改点就是故障点。

当当前解释仍只是猜测、可疑点或未确认假设时，必须先加宏控诊断日志取证，而不是直接修改业务逻辑。典型触发条件包括：

- 静态阅读无法定位唯一断层，仍存在多个合理候选原因；
- 当前证据只能说明某处“可能有问题”，但不能证明用户复现场景中一定走到了该分支；
- 拟议修改会改变业务行为、数据含义、解锁/显示规则、服务流程或 prefab 假设；
- 问题跨越配置、Cache、Service、DataEvents、ViewModel、View、Prefab、ItemRender 等两层以上，且断点尚未确认。

不要因为用户报告了运行时现象就机械地加日志；加日志的原因应当是“证据不足以证明精确故障点”。

只有当本轮已有证据能明确定位故障点时，才允许直接实现修复，例如：清晰的 Console 堆栈、编译错误、失败断言、已有运行时日志、确定性的本地事实，或用户给出了明确修复位置。

需要日志时，优先打“边界日志”而不是深入内部细节。推荐取证链路：

- ViewModel 数据构造：配置/缓存/服务数据数量、最终 item 数、选中 id、关键条件判断结果；
- View 交接：`OnBind`、关键序列化字段、`SetDataProvider` / List provider 数量；
- ItemRender 渲染：`SetData`、item id / 状态、必要 child / icon / text / lock / mask 查找结果。

When the approved plan adds diagnostic logs, logs must be feature-scoped and macro-gated:

- use a compile-time macro named `<FEATURE_NAME>_LOG`, for example `TALENT_LOG`, `HERO_LOG`, or `SHOP_LOG`;
- every log line must start with a matching feature prefix such as `[Talent]`, `[Hero]`, or `[Shop]`;
- diagnostic logs must be placed only in handwritten Runtime code, never in generated `*.Gen.cs` files;
- do not edit global scripting define symbols or Codex/global configuration just to enable the macro unless the user
  explicitly approves that environment change;
- the diagnosis plan must say how to enable the macro for local verification and what evidence each log line should
  prove;
- after root cause confirmation, either remove the diagnostic code or keep it behind the same macro with the user's
  approval.
9. Spawn `ui-reviewer` as the default review subagent to inspect diff, boundaries, generated-file safety, and risks.
   When UI resources are involved, empty required prefab references are blocking. Review must check script mounts,
   non-null serialized fields, list/tile ItemRender wiring, and data/command-to-node bindings against the approved plan.
10. Spawn `ui-tester` as the default verification subagent to validate:
   - Unity compile and Console errors;
   - prefab script mounts and required serialized fields are non-null;
   - available EditMode / PlayMode tests;
   - open, close, repeat open, key interactions, empty/error/loading states;
   - loading/modal/input/guide blocker release paths;
   - rapid clicks, close-during-request, failed request, timeout, page switching, and scene switching risks.
11. Emit stage progress at meaningful checkpoints, especially after requirement package, plan approval, implementation
    completion, review, verification, and Nexus evidence preparation.
12. Spawn `workflow-evaluator` as the default evidence subagent to prepare the final report and TaskRun evidence with
    changed files, verification evidence, skipped checks and reasons, remaining risks, actual role/model usage, and
    metrics.

## Token and Context Hygiene

Keep workflow context small and evidence-focused:

- Prefer precise CodeGraph/file/symbol reads over broad exploration; ask for only the symbols or files needed for the next decision.
- Do not paste full source files, full diffs, large JSON/YAML assets, or repeated console stacks into the conversation unless the exact content is needed for diagnosis or review.
- Prefer `git diff --stat`, `git diff --check`, targeted hunks, and file lists before full diffs.
- For Unity Console checks, start with a small count and no stacktrace; request stacktrace only for errors related to the touched area.
- For subagents, pass the smallest task context package; do not fork or paste the whole conversation unless the subagent truly needs it.
- Use ASCII task titles for Nexus TaskRun payloads when invoking shell scripts to avoid encoding-related JSON corruption and retries.


## Nexus TaskRun 协议

本工作流采用 本地开始阶段 + 仅提交 HTTP协议。

开始阶段规则：

1. Do not call `POST /api/workflow-runs/start`.
2. Auto-detect the current Codex session from `CODEX_THREAD_ID` as `sessionId`; ask the user for `/status` only if auto-detection is unavailable.
3. Record current UTC time as `startedAt`.
4. Initialize a local TaskRun payload/context with `projectId`, `workflowType`, `taskTitle`, `sessionId`, and `startedAt`.
5. Use `start-workflow-run.ps1` only as this local initializer; despite the historical name, it must not send HTTP.

提交阶段规则：

1. Complete the same payload with `endedAt`, `submittedStatus`, `context`, `metrics`, and `evidence`.
2. Submit exactly once to `POST http://127.0.0.1:8766/api/task-runs`.
3. The submit body must include `sessionId`, `startedAt`, and `endedAt`; do not rely on a `Session-Id` header.
4. The backend aggregates token / route metrics by `sessionId + startedAt/endedAt` and injects them into TaskRun metrics.
5. Do not include `workflowId`, `workflowRunId`, `workflowTemplateId`, `workflowCopyId`, or `X-Nexus-Workflow-Run-Id`.

## Task Run Evidence 协议

在工作流结束时，向 Nexus 提交精简的 Task Run Evidence，不在本地直接打分。
Nexus 上报相关的本地 JSON 产物不要删除。无论提交成功、提交失败，还是本地 API 不可用，都必须保留
`.nexus/task-run-*.json` payload；如果生成了按任务命名的快照 JSON，也必须一并保留，方便用户后续手动检查。
最终回复必须写明保留的 payload / snapshot 文件路径。

Compact payload rules:

- keep `changedFiles`; it is required for scope, risk, constraint, and history evaluation;
- use project-relative paths;
- omit empty fields and unknown placeholder values;
- keep `summary` to one short sentence;
- omit `finalResult` unless it adds non-duplicated status information;
- use short reason strings in `skippedChecks` and `remainingRisks`;
- use `{ "status": "skipped", "reason": "<short reason>" }` for skipped console/UI checks instead of all-false maps;
- use short command labels in `verification.commands`.

Preferred automation path via `$nexus-taskrun-submit`:

1. Start stage creates or updates `.nexus/task-run-ui-feature-development.json` with `sessionId` and `startedAt`.
2. End stage completes the same payload with `endedAt`, `submittedStatus`, `context`, `metrics`, and `evidence`.
3. Submit with:

```text
powershell -ExecutionPolicy Bypass -File .agents\skills\nexus-taskrun-submit\submit-workflow-result.ps1 -PayloadFile .nexus\task-run-ui-feature-development.json
```

The payload must include `projectId`, `workflowType`, `taskTitle`, `submittedStatus`, `sessionId`, `startedAt`,
`endedAt`, `context`, `metrics`, and `evidence`. Do not use `go run`, local store fallback, workflow IDs, or `Session-Id`
header routing from this business repository.

After submission, keep `.nexus/task-run-ui-feature-development.json` on disk. Do not clean up, delete, or overwrite it
with an unrelated task payload before the user has had a chance to inspect it. If another TaskRun is needed later,
create or keep a task-specific snapshot file under `.nexus/` as well.

## Compact Nexus Evidence Rules

Submit concise TaskRun evidence:

- Keep `summary` to one short sentence.
- Keep `changedFiles` as project-relative paths.
- Use short command labels in `verification`; do not embed long command output, full diffs, source snippets, stack traces, or repeated logs.
- Use short strings for `skippedChecks` and `remainingRisks`; include only information that changes review or follow-up decisions.
- Record local payload/snapshot paths when required, but do not duplicate the full JSON in the final user response after a successful submit.


## KnowledgeBase Recommendation Gate

At workflow end, run the KnowledgeBase Recommendation Checklist. Report recommendation only; do not update KB
automatically unless the user explicitly asked for a separate KB update task.

Before final reporting, check whether this task exposed any reusable stable knowledge or routing/index maintenance need:

- new or changed stable UI coding/data-flow convention;
- reusable pattern, anti-pattern, pitfall, or verification/debugging path;
- new domain entry point, workflow route, skill route, or source-of-truth path.

If a KB update is recommended, name the smallest target file, usually one of:

- `KnowledgeBase/project/domains/ui/routing.md`;
- `KnowledgeBase/project/domains/ui/coding_rules.md`;
- `KnowledgeBase/project/domains/ui/data_flow_rules.md`;
- `KnowledgeBase/project/domains/ui/feature_patterns.md`;
- `KnowledgeBase/project/domains/ui/development_workflow.md`;
- `KnowledgeBase/framework/sync-checklist.md`.

If the recommended target would add or move KnowledgeBase content, note that the separate KB update task must follow
the KnowledgeBase maintenance rules for index/routing updates and encoding checks.

Use exactly one final line:

```text
KB Recommendation: none - <reason>.
KB Recommendation: consider <target> - <reason>.
```
