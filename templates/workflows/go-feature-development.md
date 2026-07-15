# go-feature-development

Source: `templates/rules/go-00-routing.md`
Stack: `go`
Entry skill: `$wf-go-feat`

适用于 Go 新功能、既有行为修改和跨文件业务调整。

## 强约束

1. **先探索，后实施。** 未加载适用 Rules、知识库 routing 和真实代码证据，且未生成待审核目标契约前，禁止修改代码。
2. **目标和质检必须可审核。** 用户描述不完整时，AI 必须补齐完成标准、质检项、假设和证据；不得伪造量化结论。
3. **重大假设必须确认。** 涉及多个业务解释、公共 API/协议、数据或配置迁移、删除旧行为、大范围重构或只能由业务方定义的验收标准时，必须等待用户确认。
4. **任务必须有边界。** 只有独立交付物、允许修改范围和验证方式明确时才拆分 Task Capsule；默认由主线程串行执行。
5. **Subagent 必须由用户授权。** 仅当用户明确要求并行、分工或 delegation，且写入范围不重叠时，才使用 subagent。
6. **目标门和质量门不得省略。** 子任务完成或代码写完不等于工作流完成。
7. **循环必须受证据和上限约束。** 父工作流最多 3 个完整 Loop；每个子任务最多 2 次实施-质检尝试；没有新证据时禁止重复同一失败路径。
8. **最终结果必须可复核。** 如实报告改动、验证命令和结果、未验证项、风险及最终状态：`success | partial_success | blocked | failed | cancelled`。

## 工作流

1. **加载上下文并探索**：读取 AGENTS、适用 Rules、知识库 routing 及其最少必要文档；搜索真实代码、调用方、测试、配置和相似实现，形成当前行为与风险证据。
2. **生成待审核目标契约**：补齐目标、完成标准、非目标、范围、验证、假设/证据和风险。低风险且证据充分时可继续；重大假设或高风险必须等待用户确认。
3. **计划并决定是否拆分**：为每项工作定义目标、依赖、允许修改范围、完成标准和验证方式。仅在边界清晰时拆成 Task Capsule。
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
