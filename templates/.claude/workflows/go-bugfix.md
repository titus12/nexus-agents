# go-bugfix

来源：`.claude/rules/go-00-routing.md`
技术栈：Go

入口 skill：`$wf-go-bugfix`

适用于 Go 运行时错误、崩溃、失败测试和行为类 bug；目标是交付一个小范围的根因修复。

## Nexus TaskRun Start Gate

在知识检索、复现、诊断、任务拆分、subagent 派发或编辑前，初始化本地 TaskRun payload。优先使用跨平台 Node helper，任务标题使用 ASCII：

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs start --projectId <projectId> --workflowType bugfix --taskTitle "<ascii task title>" --payloadFile .nexus/task-run-bugfix.json --contextFile .nexus/workflow-context-bugfix.json
```

仅当 payload 存在且包含 `sessionId` 与 `startedAt` 后继续。初始化失败时停止工作流并报告精确错误。

## Codex 执行说明

- 默认由一个主线程担任 **Bugfix Owner**，并按角色编排 subagent。选择 `$wf-go-bugfix` 即授权 Owner 派发复现、诊断、实现、验证和复核 Capsule；禁止把超过预算的大 bugfix 直接交给单个 subagent。
- 如果使用 subagent，只传递当前 **Bugfix Loop Capsule** 和明确的窄任务边界；不要传递完整对话历史。
- 开始前按精确路径加载当前规则和 skill，并说明已加载哪些文件。
- 先读取用户提供的权威需求来源、报错记录、测试失败、日志、接口说明或设计文档；来源无法读取时不得用本地候选代码猜测替代，必须请求授权或经用户同意后标记为降级诊断。
- 优先用精确搜索或 codegraph 定位，再读取大文件。
- 结束时必须给出具体证据：根因、改动文件、执行命令、验证结果和剩余风险。

## 子代理派发模式

Bugfix Owner 派发 Debugger、Reproducer、Implementer、Verifier、Reviewer 或 Learning Curator 时，默认使用：

```text
context_mode: capsule_non_fork
fork_context: false
requestedModel: <optional model override>
requestedReasoningEffort: <optional reasoning override>
runtimeModelConfirmed: false
```

- 输入只包含当前 Bugfix Loop Capsule：症状、已确认事实、失败路径、候选假设、允许范围、验证和风险。
- 需要模型或 reasoning override 时不得改用 full-history fork。
- `requestedModel` 和 `requestedReasoningEffort` 是请求而非运行时证明；没有独立回执时 `runtimeModelConfirmed` 保持 `false`。
- full-history fork 仅可用于无法压缩为 Capsule 的只读分析，且不需要 override；不得用于修复、复现、验证、审查或证据整理。

## Plan Compliance

使用本地 `.nexus/plan-compliance-bugfix.json` 及通用 Plan Compliance loop；不得写入 TaskRun payload。为修复、复现/回归验证和非目标项分配 `planItemIds`。Verifier 和 Reviewer 对每项返回 `met | deviated | unverified | not_started` 与简短证据。Bugfix Owner 只有在所有必需项为 `met`、偏离已获批准时才可报告 `success`。

## 角色

- **Bugfix Owner**：负责 loop、Bugfix Loop Capsule、重试预算和退出决策。
- **Debugger**：基于证据一次只形成一个主要根因假设。
- **Reproducer**：复现失败，或创建/描述最小失败路径。
- **Implementer**：在证据足够后实施最小根因修复。
- **Verifier**：执行复现、回归、构建和影响面验证，并分类失败原因。
- **Reviewer**：对高风险或多轮重试后的 diff 做范围、安全性和根因一致性复核。
- **Learning Curator**：在工作流结束时提交 Task Run Evidence。

默认由 **Sisyphus** 担任 Bugfix Owner 并编排 subagent。Owner 负责 Loop Capsule、任务拆分、集成、退出决策和最终证据；Debugger、Implementer、Verifier 和 Reviewer 使用边界清晰的窄 Capsule，不能接收整个 bugfix。

| 阶段 | 角色 | 使用条件与职责 |
|---|---|---|
| 权威来源、知识和代码探索 | Sisyphus + Oracle | Owner 构造最小上下文；Oracle 独立探索 KB routing、代码、调用方和影响范围。 |
| 复现与根因诊断 | Debugger | 作为诊断 subagent，基于最小失败路径和证据维护根因假设。 |
| 跨模块或影响范围不清 | Oracle | 仅在复杂调用链、多个候选根因或影响范围不清时提供只读分析。 |
| 外部协议、依赖或文档核对 | Librarian | 仅在需要外部证据时使用。 |
| 最小修复 | Quick / Hephaestus | 作为实现 subagent：单文件、小范围使用 Quick；已确认的多文件窄 Capsule 使用 Hephaestus。 |
| 独立验证任务 | Worker | 作为验证 subagent，只接收范围不重叠、验收和验证明确的 Capsule。 |
| 验证与质量复核 | Reviewer Logic | 作为复核 subagent，检查根因一致性、边界、并发、事务和错误处理；高风险时按需加入 Reviewer Perf / Reviewer Security。 |
| 证据提交与最终交付 | Sisyphus | 汇总复现、验证、风险和 Task Run Evidence。 |

## Loop

1. **启动与检索**：创建或复用 workflow run，加载规则/skill、KB routing、权威需求来源和故障证据，初始化 Bugfix Loop Capsule；如可用，检索相关 learning cases。
2. **复现**：记录精确命令、输入、错误和环境。编辑前优先构造定向失败测试。若本地无法复现，记录证据来源。
3. **冲突与诊断**：若需求来源、日志、代码、配置、协议、测试或当前行为冲突，先报告冲突来源、影响、选项、推荐方案和是否需要用户决定。最多保留两个活跃假设；每个假设必须包含支持/反对证据和置信度。
4. **无证据先加日志**：如果没有可靠根因线索，先在决策边界或状态转换处加入最小诊断日志/探针，再考虑修改行为。日志必须避免 secrets/PII，并且应适合生产保留或明确标记为临时诊断。
5. **修复范围决策**：根因确认后，加载 `go-04-task-decomposition.md`，记录修复目标、受影响模块/package、非机械性生产文件、验证路径、高风险项和未确认假设。修复不再是单一可验证 Capsule 时，必须说明拆分方案；涉及公共契约、迁移或未确认业务语义时必须等待用户确认或以 partial/blocked 退出。
6. **修复**：实施最小根因修复，不做顺手重构。
7. **验证**：运行复现路径、可行时运行回归测试、受影响 package 测试/构建，以及安全 diff 检查。
8. **决策**：验证通过则提交成功证据；验证失败且还有重试预算时，将上下文压缩为下一轮 Bugfix Loop Capsule，并从诊断继续；否则以 partial/failed/blocked 证据退出。

## 退出规则

命中任一规则即停止自动 loop：

- 成功：根因已解释、修复已应用、验证已通过、证据已提交。
- 重试预算耗尽：默认 `maxLoops = 3`，一轮定义为 `diagnose -> patch -> verify`。
- 同一失败连续出现两次，且没有新增证据。
- 加过诊断日志后，根因置信度仍然较低。
- 修复需要公共 API 变更、新依赖、大范围重构、数据迁移，或触及禁止/高风险文件。
- 验证被环境、权限、外部服务或缺失复现数据阻塞。

最终提交状态使用：`success`、`partial_success`、`failed`、`cancelled` 或 `blocked`。当下一步需要用户或运行时带着新日志复现时，加入诊断日志本身可以作为合法的 `partial_success` 交付。

## Regression Test Gate

复现缺陷后必须记录共享规则中的 Test decision。可靠的自动化复现默认成为长期保留
的回归测试：先对缺陷行为获得 red 证据，再实施最小根因修复并获得 green 证据。
诊断探针只有保护独特回归风险时才保留，不得因排障时有用而直接成为长期测试。

当前无法自动化时，必须使用完整例外协议：说明原因、记录实际替代复现/验证，并给出
后续自动化回归测试的触发条件。非契约诊断日志本身不构成回归测试。

## 验证规则

- 先证明 bug 存在：复现命令、失败测试、手工路径、堆栈，或仅日志证据。
- 优先 red -> green：新增或更新定向回归测试，确认它因预期原因失败，修复后确认通过。
- 验证影响面：窄改动跑定向测试，package 级改动跑 package 测试；只有触及面足够大时才跑更宽的 `go test ./...`。
- 每次 patch 后做安全检查：diff 最小、无调试残留、无禁止文件、无未经批准的公共契约变更、修复与根因一致。
- 如果验证失败，先分类为 `same_failure`、`new_failure`、`environment_blocked`、`regression_introduced` 或 `verification_unreliable`，再决定是否进入下一轮。

## Test Evidence in Bugfix Capsules

Bugfix Loop Capsule 和最终证据必须记录 red/green 命令或完整例外证据，以及测试资产
的 retain / extend / parameterize / merge / delete 决策。

## 需求来源与事实冲突

当用户提供 Feishu/Lark 文档、需求文档、接口说明或设计稿时，它们属于权威需求来源。无法读取时，不得用本地候选代码替代；必须请求必要授权，或在用户明确同意后以降级诊断继续。

冲突报告必须包含：

```text
冲突来源：
对复现、根因或修复的影响：
可选解决方案：
推荐方案及证据：
是否需要用户决定：
```

## Diagnosis-First 方案

复现或只读探索无法确认唯一根因时，不得把推测性修复当作实施计划。先输出：

```text
未确认事实与证据缺口：
最小诊断日志或探针：
预期观察结果：
区分候选根因的判断规则：
诊断代码删除、保留或宏控的条件：
```

只有新证据满足判断规则后，才进入修复范围决策。

## 修复范围与拆分决策

```text
根因与证据：
修复目标：
受影响模块或 package：
预计非机械性生产文件：
独立验证路径数：
高风险项：
未确认假设：

是否拆分：是 / 否
拆分或不拆分原因：

Task 1：目标 / 范围 / 验收 / 验证 / 风险
Task 2：目标 / 范围 / 验收 / 验证 / 风险
集成目标：复现路径 / 总体质量门 / 回归验证
```

## Bugfix Loop Capsule

每一轮或 subagent 交接只携带这份压缩状态；原始大日志和过期探索内容只保留路径引用，不直接塞入上下文。

保留：

- 用户症状、期望行为、约束、禁止/高风险文件。
- 权威需求来源及读取状态；相关知识库、日志、测试、配置和代码路径。
- 复现状态、失败命令/路径、简短失败摘要。
- 已确认事实、冲突、未确认项和诊断判断规则。
- 当前活跃假设、置信度和证据。
- 已否定假设及一句话原因，避免重复探索。
- 每轮 patch 意图和改动文件列表。
- 最近一次验证命令、结果、失败分类和摘要。
- Loop 计数：当前轮次、剩余预算、重复失败次数、改动文件数。

丢弃或摘要化：

- 完整工具历史、大段日志、旧 diff、无关文件阅读、被否定假设的详细推理、重复 workflow 文本、无关 learning cases。

## KnowledgeBase Recommendation Gate

工作流结束前判断本次是否发现可复用的稳定知识，例如故障模式、反模式、诊断路径、验证路径或 routing 规则。只输出建议，不自动修改知识库：

```text
KB Recommendation: none - <reason>.
KB Recommendation: consider <target path> - <reason>.
```

## Workflow Run Header Protocol

当此工作流在 Nexus-enabled 项目中启动时，先创建或复用一个 workflow run，并将返回的 `id` 作为整个会话的 canonical workflow run ID。

启动 endpoint：

```text
POST http://127.0.0.1:8766/api/workflow-runs/start
```

使用返回的 `id` 作为 `workflowRunId`。

后续每条经 Nexus Codex 路由的模型消息都携带：

```text
X-Nexus-Workflow-Run-Id: <workflowRunId>
X-Nexus-Workflow-Role: <current role>
```

Role 应反映当前阶段，例如：`owner`、`debugger`、`reproducer`、`implementer`、`verifier`、`reviewer` 或 `learning-curator`。

规则：

1. 不要在同一工作流中途更换 `workflowRunId`。
2. 工作流中的每条路由消息都必须包含这两个 header。
3. 角色变化时，只更新 `X-Nexus-Workflow-Role`。
4. 工作流结束时，使用同一个 workflow run ID 提交或完成证据。

## Task Run Evidence Protocol

工作流结束时，完成同一个 `.nexus/task-run-bugfix.json` payload，并调用 `$nexus-taskrun-submit` **只提交一次**。payload 不得删除；成功或失败后都在最终回复中报告 payload/context 文件路径。

优先使用：

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs submit --payloadFile .nexus/task-run-bugfix.json --contextFile .nexus/workflow-context-bugfix.json
```

Windows 可使用：

```text
powershell -ExecutionPolicy Bypass -File .agents\skills\nexus-taskrun-submit\submit-workflow-result.ps1 -PayloadFile .nexus\task-run-bugfix.json -ContextFile .nexus\workflow-context-bugfix.json
```

最终 payload 字段、禁止字段、紧凑证据规则和提交 endpoint 以 `$nexus-taskrun-submit` 为唯一来源。现有 workflow-run headers 只用于路由 telemetry；最终 payload 不得包含任何 workflow ID。
