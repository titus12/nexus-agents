# Unity UI 快速修复

来源：`.claude/rules/unity-00-routing.md`  
技术栈：Unity / C# / UGUI / TextMeshPro / UIArchitect generated bindings

## Start Gate

Before Knowledge Retrieval, CodeGraph, search, planning, subagent dispatch, or editing, initialize the local Nexus TaskRun payload. Use the cross-platform Node helper by default so the same workflow works on Windows and macOS:

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs start --projectId btd-client --workflowType unity-ui-quick --taskTitle "<task title>" --payloadFile .nexus/task-run-unity-ui-quick.json --contextFile .nexus/workflow-context-unity-ui-quick.json
```

Continue only after confirming `.nexus/task-run-unity-ui-quick.json` exists and contains `sessionId` and `startedAt`. If start fails, stop the workflow and report the exact error; do not continue investigation or implementation.

## Knowledge Loading

Follow `.claude/rules/knowledge-retrieval.md`. Record `knowledgeRetrieval` and include the returned `Loaded Knowledge` section in the final summary.

## 适用场景

用于简单的 btdgame UI 任务，例如：

- fixing a wrong dialog/resource path;
- binding one missing button/list event;
- correcting a small ViewModel display state;
- adjusting a narrow UI text/state refresh rule;
- making a small handwritten View or ViewModel change.

如果任务涉及 protocol、持久化 Cache/Player 数据、Service API、DataEvents、prefab 层级、场景资源、生成文件，
或者修改超过 2 个手写文件，则不要使用此工作流，应改用 `$wf-unity-ui-feature`。

## 角色分工

以下是主会话中的顺序阶段角色，不是自动子代理：

1. `quick-planner` - 确认范围、风险、具体文件和验证路径。
2. `quick-developer` - 在合理必要范围内实现安全的手写修改。
3. `quick-verifier` - 检查编译风险、生成文件安全和目标行为。

## 子代理派发模式

本工作流的角色默认留在主会话；只有用户明确要求独立委派且任务边界仍然安全时，才派发非 fork 子代理：

```text
context_mode: capsule_non_fork
fork_context: false
requestedModel: <optional model override>
requestedReasoningEffort: <optional reasoning override>
runtimeModelConfirmed: false
```

- 子代理仅接收当前目标、相关 View/ViewModel 路径、允许范围、验证和风险。
- 请求模型或 reasoning override 时必须使用 `fork_context: false`。
- `requestedModel` 和 `requestedReasoningEffort` 是请求；没有独立运行时证据时 `runtimeModelConfirmed` 保持 `false`。
- full-history fork 只可用于无法由最小上下文包表达的只读分析，且不需要 override；快速修复、验证和复核不得使用。

## 必需行为

### 计划门禁

在编辑前，先输出简短计划并等待用户明确批准；如果用户在同一轮已经明确要求执行该计划，则可直接继续。

计划必须简短包含：

- 当前证据；
- 判断原因；
- 修复方案；
- 为什么适合 `quick`；
- 预计修改点；
- 验证方式；
- 何时升级到 `$wf-unity-ui-feature`。

### 证据与确认

quick 修复前必须先说明当前证据、判断原因和修复方案，并等待用户确认；如果用户同轮已经明确要求执行该方案，可继续。

可接受证据包括：用户提供的具体运行现象、截图、Console 报错或日志；Unity Console / MCP 读取到的错误；
prefab/component/serialized 引用事实；代码事实能确定唯一断点；或用户明确指出修复位置。

如果证据只能说明“可能是某处问题”，但不能确认局部断点，先加少量宏控边界日志或让用户复现后读取
Console，不要直接改业务行为。不要因为是 UI 现象就机械加日志；只有原因仍是猜测时才加。

quick 日志只打边界点：ViewModel 最终数据/关键状态、View `OnBind` / 字段是否为空、ItemRender `SetData`
/ 关键节点是否为空、Button click 是否触发。

### 代码事实规则

如果可用，直接使用只读 CodeGraph；只读查询不需要再向用户请求许可，编辑前先调用 CodeGraph 即可。
只有在写操作、破坏性操作、网络访问或环境级提权时才需要请求许可。

### 阶段进度输出

输出简洁的阶段进度：

```text
[阶段: quick-planner] ...
[阶段: quick-developer] ...
[阶段: quick-verifier] ...
```

### 安全边界

- 不要编辑生成文件，例如 `*.Gen.cs`。
- 不要删除或重写 `.meta` 文件。
- 此工作流中不要修改 prefab 或 scene。
- 如果需要改动 protocol、Cache、Service、DataEvents 或 prefab，就停止并切换到 `$wf-unity-ui-feature`。

## 工作流

1. 开始本地 Nexus payload 阶段：运行 `start-workflow-run.ps1` 作为本地初始化器，优先从当前进程 `CODEX_THREAD_ID` 自动读取 `sessionId`，并把 `sessionId` 和当前 `startedAt` 写入 payload/context；只有自动读取不可用时，才让用户在 Codex 窗口输入 `/status` 并提供显示的 **会话id**。
   不要调用 `POST /api/workflow-runs/start`。
2. `quick-planner` 基于证据输出判断原因、修复方案、验证方式和升级条件；证据不足时先输出诊断日志计划。
3. `quick-developer` 只读取相关的生成绑定、手写 View/ViewModel/ItemRender、Console 或必要 prefab facts。
4. `quick-developer` 在合理必要范围内实现安全修改；若前一步是诊断计划，则只加诊断日志，不直接改业务行为。
5. `quick-verifier` 验证目标行为和编译风险。验证必须包含一次 Unity MCP 控制台/编译检查；如果 Unity MCP
   不可用，需要记录具体不可用原因。
   - 如果本次修复涉及 prefab 上的脚本引用丢失/恢复，不能只验证组件类型存在；必须同时检查该脚本的关键
     serialized 字段引用是否已绑定（例如手写 `[SerializeField]`、生成绑定字段、以及目标行为依赖的
     ObjectReference 字段）。只要关键字段仍为 `{fileID: 0}` / `null`，review 必须判定未通过并继续修复或升级。
6. 工作流结束时，补齐同一份 payload 的 `endedAt` 和证据，然后用 `submit-workflow-result.ps1` 提交
   Nexus TaskRun；请求体必须包含 `sessionId`、`startedAt` 和 `endedAt`，并报告：
   - 修改了哪些文件；
   - 做了哪些验证；
   - 跳过了哪些检查以及原因；
   - Nexus TaskRun 提交结果。

## Plan Compliance

使用本地 `.nexus/plan-compliance-unity-ui-quick.json` 及通用 Plan Compliance loop；不得写入 TaskRun payload。`quick-planner` 为必须项和非目标分配 `planItemIds`，`quick-verifier` 对每项返回 `met | deviated | unverified | not_started` 与简短证据。所有必需项为 `met` 且偏离已获批准后，工作流才可报告成功。

## Unity Quality Gate

`quick-verifier` 必须读取 `.agents/skills/wf-subagents/unity-quality-rubric.md` 并在主会话返回 `QualityResult`。Owner 将汇总写入本地 ledger 的 `quality.blockingFindings`、`quality.majorFindings`、`quality.skippedRequiredChecks`、`quality.requiredChecks`、`quality.passedChecks`、`quality.manualAcceptancePending` 和 `quality.unexpectedChanges`，再运行：

```text
node .agents/skills/wf-subagents/plan-loop.mjs gate --file .nexus/plan-compliance-unity-ui-quick.json --stage quality
```

关键 Prefab 引用为 `null` / `{fileID: 0}`、编译失败或触及禁止范围均为 blocker；仅人工路径未验证时进入 `awaiting_user_acceptance`。

## Progress Output Discipline

Do not prefix every routine message with role/stage labels. Emit at most one concise checkpoint per actual role or stage transition when it helps the user understand progress. Skip checkpoints for ordinary tool calls, small corrections, repeated verification attempts, and routine main-agent messages.

Use this compact checkpoint shape only when a real transition occurs:

```text
[checkpoint]
role: <role>
goal: <what this role must decide or produce>
```


## Token and Context Hygiene

Keep workflow context small and evidence-focused:

- Prefer precise CodeGraph/file/symbol reads over broad exploration; ask for only the symbols or files needed for the next decision.
- Do not paste full source files, full diffs, large JSON/YAML assets, or repeated console stacks into the conversation unless the exact content is needed for diagnosis or review.
- Prefer `git diff --stat`, `git diff --check`, targeted hunks, and file lists before full diffs.
- For Unity Console checks, start with a small count and no stacktrace; request stacktrace only for errors related to the touched area.
- For subagents, pass the smallest task context package; do not fork or paste the whole conversation unless the subagent truly needs it.
- Use ASCII task titles for Nexus TaskRun payloads when invoking shell scripts to avoid encoding-related JSON corruption and retries.


## Nexus TaskRun 协议

本工作流使用“本地开始阶段 + 仅提交 HTTP”协议。

开始阶段规则：

1. 不要调用 `POST /api/workflow-runs/start`。
2. 优先从当前进程 `CODEX_THREAD_ID` 自动读取 `sessionId`；只有自动读取不可用时，才获取 Codex `/status` 的会话id。
3. 记录当前 UTC 时间作为 `startedAt`。
4. 用 `projectId`、`workflowType`、`taskTitle`、`sessionId`、`startedAt` 初始化本地 TaskRun payload/context。
5. `start-workflow-run.ps1` 只作为本地初始化器使用，尽管名字里有 start，也不能发送 HTTP。

提交阶段规则：

1. 补齐同一份 payload 的 `endedAt`、`submittedStatus`、`context`、`metrics` 和 `evidence`。
2. 只提交一次到 `POST http://127.0.0.1:8766/api/task-runs`。
3. 请求体必须包含 `sessionId`、`startedAt` 和 `endedAt`，不要依赖 `Session-Id` header。
4. 后端按 `sessionId + startedAt/endedAt` 汇总 token / route 指标，并注入到 TaskRun metrics。
5. 不要包含 `workflowId`、`workflowRunId`、`workflowTemplateId`、`workflowCopyId` 或 `X-Nexus-Workflow-Run-Id`。


## 建议的 payload 字段

```json
{
  "projectId": "<nexus project id or repo name>",
  "workflowType": "unity-ui-quick",
  "taskTitle": "<short task title>",
  "submittedStatus": "success",
  "context": {
    "roles": ["quick-planner", "quick-developer", "quick-verifier"],
    "subagentsUsed": false
  },
  "metrics": {
    "filesChangedCount": 0,
    "toolCallCount": 0,
    "testRunCount": 0,
    "errorCount": 0
  },
  "evidence": {
    "summary": "<one short sentence>",
    "changedFiles": [],
    "verification": {
      "hasVerification": true,
      "types": [],
      "commands": ["<short command labels>"]
    },
    "compile": {
      "passed": true
    },
    "assets": {
      "prefabChanged": false,
      "sceneChanged": false,
      "metaSafe": true,
      "generatedFilesTouched": false
    },
    "skippedChecks": ["<short check: reason>"],
    "remainingRisks": ["<short risk>"]
  }
}
```
## Required Rules

- `.claude/rules/01-communication.md`
- `.claude/rules/knowledge-retrieval.md`
- `.claude/rules/unity-00-routing.md`
- `.claude/rules/unity-01-project-model.md`

## Compact Nexus Evidence Rules

Submit concise TaskRun evidence:

- Keep `summary` to one short sentence.
- Keep `changedFiles` as project-relative paths.
- Use short command labels in `verification`; do not embed long command output, full diffs, source snippets, stack traces, or repeated logs.
- Use short strings for `skippedChecks` and `remainingRisks`; include only information that changes review or follow-up decisions.
- Record local payload/snapshot paths when required, but do not duplicate the full JSON in the final user response after a successful submit.


## KnowledgeBase Recommendation Gate

At workflow end, run the KnowledgeBase Recommendation Checklist. Report recommendation only; do not update KB
automatically. Use one final line:

```text
KB Recommendation: none — <reason>.
KB Recommendation: consider <target> — <reason>.
```
