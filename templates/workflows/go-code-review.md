# go-code-review

Source: `templates/rules/go-00-routing.md`
Stack: `go`
Entry skill: `$wf-go-review`

适用于当前 Go 变更集的逻辑、性能、安全、兼容性和验证证据审查。

## Nexus TaskRun Start Gate

在读取 diff、派发 reviewer 或执行检查前，初始化本地 TaskRun payload。任务标题使用 ASCII：

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs start --projectId <projectId> --workflowType code-review --taskTitle "<ascii task title>" --payloadFile .nexus/task-run-code-review.json --contextFile .nexus/workflow-context-code-review.json
```

仅当 payload 存在且包含 `sessionId` 与 `startedAt` 后继续。初始化失败时停止审查并报告精确错误。

## 强约束

1. **先建立审查上下文。** 未加载适用 Rules、目标契约、diff、已执行验证、风险和变更范围前，不得输出结论。
2. **默认按角色使用 reviewer subagent。** `$wf-go-review` 选择后，Sisyphus 默认编排 Reviewer Logic、Reviewer Perf 和 Reviewer Security；每个 reviewer 只接收最小审查 Capsule。
3. **大 diff 必须拆审查范围。** 按 `go-04-task-decomposition.md` 以 package、风险面、写入范围或验证路径拆为独立 review Capsule；禁止把不受控的大 diff 原样交给一个 reviewer。
4. **只报告有证据的问题。** 每项 finding 必须有文件/行号、触发条件、风险和可执行建议。
5. **Owner 必须汇总。** reviewer 的“无问题”或“完成”不能替代 Sisyphus 的去重、严重度排序和质量门。
6. **最终结果可复核。** 区分已修复、必须修复、可延期、信息不足和错误 finding；如实记录未审查范围与原因。

## 审查上下文包

```text
审查目标：
需求/目标契约与权威来源：
base / head revision：
变更文件与重要 diff：
已执行命令与结果：
已知风险、非目标和有意取舍：
需要重点审查的区域：
未覆盖范围与环境限制：
```

若需求、设计、代码、配置、协议、测试或当前 diff 的事实冲突，先报告冲突来源、影响、选项、推荐解释和是否需要用户决定。

## 审查范围与拆分决策

```text
受影响模块或 package：
变更文件和非机械性生产文件：
独立风险面：逻辑 / 性能 / 安全 / 兼容性 / 数据 / 并发
独立验证路径：
未确认事实：

是否拆分 review Capsule：是 / 否
拆分或不拆分原因：

Capsule 1：范围 / reviewer / 检查重点 / 验收
Capsule 2：范围 / reviewer / 检查重点 / 验收
汇总目标：去重 / 严重度 / 结论 / 剩余风险
```

## 工作流

1. **加载与定界**：读取 Rules、KB routing、目标契约、diff、调用方、已执行验证和风险；生成审查上下文包。
2. **拆分审查 Capsule**：按风险和范围决定 reviewer 的最小输入。Logic、Perf、Security 默认独立执行；没有对应风险时仍要明确记录“已检查且无适用面”或“未覆盖及原因”。
3. **并行审查**：
   - Reviewer Logic：控制流、边界、nil、并发、事务、错误处理、调用方兼容性；
   - Reviewer Perf：热点路径、复杂度、分配、锁、goroutine、批量与缓存；
   - Reviewer Security：权限、输入校验、泄漏、重放、经济与外部边界。
4. **汇总质量门**：Sisyphus 去重 findings，核对目标契约、验证证据和非目标边界，并按严重度分类。
5. **处理结论**：
   - Critical：修复后重新审查受影响 Capsule；
   - Important：修复后才能合并，或由用户明确延期；
   - Minor：低成本修复，否则记录 follow-up；
   - Insufficient evidence：列出缺口和最小补充检查；
   - Incorrect：用简短技术证据驳回。
6. **提交证据**：记录审查范围、reviewer 角色、findings、已修复项、未覆盖项和风险，完成本地 TaskRun payload。

## 角色与阶段

| 阶段 | 角色 | 职责 |
|---|---|---|
| 审查 Owner 与汇总 | Sisyphus | 定界、拆分、分发、去重、质量门与最终报告。 |
| 逻辑审查 | Reviewer Logic | 默认 subagent；检查逻辑正确性和边界风险。 |
| 性能审查 | Reviewer Perf | 默认 subagent；检查热点、资源和并发性能风险。 |
| 安全审查 | Reviewer Security | 默认 subagent；检查权限、输入、泄漏、重放和经济风险。 |
| 跨模块事实补充 | Oracle | 仅在 reviewer 无法确定调用链或影响范围时提供只读分析。 |
| 外部文档/协议核对 | Librarian | 仅在需要外部证据时使用。 |
| 最终修复验证 | Sisyphus | 对修复后 Capsule 重新审查或要求相关 workflow 回归。 |

## KnowledgeBase Recommendation Gate

工作流结束前判断是否发现可复用的稳定知识，例如反模式、审查清单、验证路径或 routing 规则。只输出建议，不自动修改知识库：

```text
KB Recommendation: none - <reason>.
KB Recommendation: consider <target path> - <reason>.
```

## Workflow Run Header Protocol

现有 workflow-run headers 可继续用于模型路由 telemetry：

```text
X-Nexus-Workflow-Run-Id: <workflowRunId>
X-Nexus-Workflow-Role: <current role>
```

角色随阶段变化；最终 TaskRun payload 不携带 workflow ID。

## Task Run Evidence Protocol

结束时完成同一个 `.nexus/task-run-code-review.json` payload，并调用 `$nexus-taskrun-submit` **只提交一次**。成功或失败后都保留 payload/context 文件，并在最终回复中报告路径。

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs submit --payloadFile .nexus/task-run-code-review.json --contextFile .nexus/workflow-context-code-review.json
```

Windows 可使用：

```text
powershell -ExecutionPolicy Bypass -File .agents\skills\nexus-taskrun-submit\submit-workflow-result.ps1 -PayloadFile .nexus\task-run-code-review.json -ContextFile .nexus\workflow-context-code-review.json
```

最终 payload 字段、禁止字段、紧凑证据规则和提交 endpoint 以 `$nexus-taskrun-submit` 为唯一来源。
