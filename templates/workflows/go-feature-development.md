# go-feature-development

Source: `templates/rules/go-00-routing.md`
Stack: `go`
Entry skill: `$wf-go-feat`

适用于 Go 新功能、既有行为修改和跨文件业务调整。

## 强约束

1. **先探索，后实施。** 未加载适用 Rules、知识库 routing 和真实代码证据，且未生成待审核目标契约前，禁止修改代码。
2. **目标和质检必须可审核。** 用户描述不完整时，AI 必须补齐完成标准、质检项、假设和证据；不得伪造量化结论。
3. **重大假设必须确认。** 涉及多个业务解释、公共 API/协议、数据或配置迁移、删除旧行为、大范围重构或只能由业务方定义的验收标准时，必须等待用户确认。
4. **任务拆分必须遵守 `go-04-task-decomposition.md`。** 方案阶段必须记录任务规模信号，并明确“是否拆分、为何拆分或为何不拆分”；未输出该决策不得实施。
5. **Subagent 必须由用户授权。** 仅当用户明确要求并行、分工或 delegation，且写入范围不重叠时，才使用 subagent。
6. **目标门和质量门不得省略。** 子任务完成或代码写完不等于工作流完成。
7. **循环必须受证据和上限约束。** 父工作流最多 3 个完整 Loop；每个子任务最多 2 次实施-质检尝试；没有新证据时禁止重复同一失败路径。
8. **最终结果必须可复核。** 如实报告改动、验证命令和结果、未验证项、风险及最终状态：`success | partial_success | blocked | failed | cancelled`。

## 工作流

1. **加载上下文并探索**：读取 AGENTS、适用 Rules、知识库 routing 及其最少必要文档；搜索真实代码、调用方、测试、配置和相似实现，形成当前行为与风险证据。
2. **生成待审核目标契约**：补齐目标、完成标准、非目标、范围、验证、假设/证据和风险。低风险且证据充分时可继续；重大假设或高风险必须等待用户确认。
3. **计划并决定是否拆分**：加载 `go-04-task-decomposition.md`，评估目标数、模块/package、非机械性生产文件、验证路径、高风险项和未确认假设；在计划中明确是否拆分、拆分顺序、集成点，或说明不拆分原因。仅在边界清晰时拆成 Task Capsule。
4. **执行 Task Capsule Loop 并集成**：以最小改动实施、验证子目标并记录证据；通过的子任务回到主线程集成，失败的子任务只能携带新证据重试。
5. **目标门**：逐项确认待审核目标契约中的必须完成项、受影响调用方和非目标边界均有证据。未通过时补最小任务、回到探索，或请求必要的业务确认。
6. **质量门与退出**：检查 Rules、最小 diff、错误处理、兼容性、构建/测试和真实验证证据。通过后交付；可修复且未超限时回到计划；无新证据、超限或环境阻塞时退出为 `partial_success` 或 `blocked`。

## 待审核目标契约

```text
任务类型：feature | modification
用户原始目标：
AI 理解后的目标：
必须完成：
可选增强：
非目标：
预计修改范围：
受影响调用方、配置和数据流：
验证与质检：功能 / 测试或构建 / 回归 / 人工证据
关键假设及证据：
风险：API / 协议 / 配置 / 数据 / 并发 / 兼容性 / 权限
是否必须等待用户确认：是 / 否；原因：
```

## 任务拆分决策

```text
业务目标数：
受影响模块或 package：
预计非机械性生产文件：
独立验证路径数：
高风险项：
未确认假设：

是否拆分：是 / 否
拆分或不拆分原因：

Task 1：目标 / 范围 / 验收 / 验证 / 风险
Task 2：目标 / 范围 / 验收 / 验证 / 风险
集成目标：接口检查 / 总体目标门 / 总体质量门
```

## Task Capsule

```text
子任务目标：
依赖与前置条件：
允许修改范围：
完成标准：
验证命令或人工验证：
当前尝试次数（最多 2 次）：
已有证据：
已知风险或阻塞：
```

每个 Capsule 都执行“最小探索 → 最小实施 → 子目标检查 → 子任务质检”。返回目标是否达成、改动文件、关键理由、验证结果和剩余风险；主线程负责集成和最终两道门。

## 角色与阶段

默认由 **Sisyphus** 作为工作流 Owner 串行推进。角色是职责分工，不自动等于启动 subagent；只有用户明确要求并行、分工或 delegation 时，才按 `go-04-task-decomposition.md` 派发边界清晰的 Task Capsule。

| 阶段 | 主角色 | 触发条件与职责 |
|---|---|---|
| 加载上下文与探索 | Sisyphus | 默认负责规则、KB routing、代码和调用方探索。 |
| 跨模块或不确定性探索 | Oracle | 仅在代码关系复杂、根因或影响范围不清时提供只读分析。 |
| 外部 API/文档核对 | Librarian | 仅在需要外部文档、协议或第三方库证据时使用。 |
| 目标契约与拆分方案 | Sisyphus | 默认产出目标契约、拆分决策和 Task Capsule。 |
| 高复杂度设计 | Prometheus | 仅在高风险、多个可选方案或先设计后实施时提供只读方案。 |
| 单文件、小范围实施 | Quick | 单一 Capsule、范围小且无接口变化时执行最小改动。 |
| 多文件实施 | Hephaestus | 已确认的多文件 Capsule；不负责替用户决定未确认业务语义。 |
| 用户授权的独立子任务 | Worker | 仅接收边界不重叠、验收和验证明确的 Capsule。 |
| 质量门 | Reviewer Logic | 检查逻辑、边界、并发、事务和错误处理；高风险时再按需加入 Reviewer Perf 与 Reviewer Security。 |
| 集成与最终交付 | Sisyphus | 合并结果，执行总体目标门、质量门和最终验证。 |

## Workflow Run Header Protocol

在 Nexus 项目中启动本工作流时，先创建或复用一个 workflow run record，并将返回的 `id` 作为整个会话唯一的 `workflowRunId`。

Start endpoint:

```text
POST http://127.0.0.1:8766/api/workflow-runs/start
```

后续每条经 Nexus 路由的模型消息都必须携带：

```text
X-Nexus-Workflow-Run-Id: <workflowRunId>
X-Nexus-Workflow-Role: <current role>
```

角色随当前阶段变化，例如 `explorer`、`planner`、`worker`、`reviewer`、`tester` 或 `arbiter`；不得在工作流中途变更 `workflowRunId`。

## Task Run Evidence Protocol

结束时调用 `$nexus-taskrun-submit` 提交真实的 Task Run Evidence；若本地 API 不可用，在最终回复中提供同一 JSON payload，供用户稍后提交。

Endpoint:

```text
POST http://127.0.0.1:8766/api/task-runs
```

`submittedStatus` 保持 API 状态词汇：`success | partial_success | failed | cancelled`。当工作流退出为 `blocked` 时，记录阻塞原因、未完成项和风险，并使用与实际交付相符的 API 状态。证据必须包含实际执行的验证命令、结果和未验证范围；不得把计划中的验证写成已通过。

```json
{
  "projectId": "<nexus project id or repo name>",
  "workflowTemplateId": "<workflow template id>",
  "workflowCopyId": "<project workflow copy id if known>",
  "workflowType": "feature-development",
  "taskTitle": "<short task title>",
  "submittedStatus": "<success|partial_success|failed|cancelled>",
  "startedAt": "<ISO-8601 if known>",
  "endedAt": "<ISO-8601 if known>",
  "durationMs": 0,
  "context": {
    "agent": "<primary agent>",
    "model": "<model id>",
    "rules": ["<rule ids loaded>"],
    "skills": ["<skill ids loaded>"],
    "tools": ["<tools used>"]
  },
  "metrics": {
    "turnCount": 0,
    "toolCallCount": 0,
    "testRunCount": 0,
    "retryCount": 0,
    "errorCount": 0,
    "filesChangedCount": 0
  },
  "evidence": {
    "summary": "<what was done>",
    "finalResult": "<delivered result>",
    "verification": {
      "hasVerification": true,
      "passed": true,
      "types": ["test", "build", "manual_check"],
      "commands": ["<commands actually run>"]
    },
    "unfinishedItems": [],
    "risks": [],
    "contextMissing": false
  }
}
```
