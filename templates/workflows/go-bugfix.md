# go-bugfix

来源：`.claude/rules/go-00-routing.md`
技术栈：Go

入口 skill：`$wf-go-bugfix`

适用于 Go 运行时错误、崩溃、失败测试和行为类 bug；目标是交付一个小范围的根因修复。

## Codex 执行说明

- 默认由一个主线程担任 **Bugfix Owner** 推进。除非用户明确要求 subagent / 并行 / 分工，或当前 loop 需要窄范围的独立探索、复核、验证，否则不要拉起 subagent。
- 如果使用 subagent，只传递当前 **Bugfix Loop Capsule** 和明确的窄任务边界；不要传递完整对话历史。
- 开始前按精确路径加载当前规则和 skill，并说明已加载哪些文件。
- 优先用精确搜索或 codegraph 定位，再读取大文件。
- 结束时必须给出具体证据：根因、改动文件、执行命令、验证结果和剩余风险。

## 角色

- **Bugfix Owner**：负责 loop、Bugfix Loop Capsule、重试预算和退出决策。
- **Debugger**：基于证据一次只形成一个主要根因假设。
- **Reproducer**：复现失败，或创建/描述最小失败路径。
- **Implementer**：在证据足够后实施最小根因修复。
- **Verifier**：执行复现、回归、构建和影响面验证，并分类失败原因。
- **Reviewer**：对高风险或多轮重试后的 diff 做范围、安全性和根因一致性复核。
- **Learning Curator**：在工作流结束时提交 Task Run Evidence。

## Loop

1. **启动与检索**：创建或复用 workflow run，加载规则/skill，初始化 Bugfix Loop Capsule；如可用，检索相关 learning cases。
2. **复现**：记录精确命令、输入、错误和环境。编辑前优先构造定向失败测试。若本地无法复现，记录证据来源。
3. **诊断**：最多保留两个活跃假设。每个假设必须包含支持/反对证据和置信度。
4. **无证据先加日志**：如果没有可靠根因线索，先在决策边界或状态转换处加入最小诊断日志/探针，再考虑修改行为。日志必须避免 secrets/PII，并且应适合生产保留或明确标记为临时诊断。
5. **修复**：实施最小根因修复，不做顺手重构。
6. **验证**：运行复现路径、可行时运行回归测试、受影响 package 测试/构建，以及安全 diff 检查。
7. **决策**：验证通过则提交成功证据；验证失败且还有重试预算时，将上下文压缩为下一轮 Bugfix Loop Capsule，并从诊断继续；否则以 partial/failed/blocked 证据退出。

## 退出规则

命中任一规则即停止自动 loop：

- 成功：根因已解释、修复已应用、验证已通过、证据已提交。
- 重试预算耗尽：默认 `maxLoops = 3`，一轮定义为 `diagnose -> patch -> verify`。
- 同一失败连续出现两次，且没有新增证据。
- 加过诊断日志后，根因置信度仍然较低。
- 修复需要公共 API 变更、新依赖、大范围重构、数据迁移，或触及禁止/高风险文件。
- 验证被环境、权限、外部服务或缺失复现数据阻塞。

最终提交状态使用：`success`、`partial_success`、`failed`、`cancelled` 或 `blocked`。当下一步需要用户或运行时带着新日志复现时，加入诊断日志本身可以作为合法的 `partial_success` 交付。

## 验证规则

- 先证明 bug 存在：复现命令、失败测试、手工路径、堆栈，或仅日志证据。
- 优先 red -> green：新增或更新定向回归测试，确认它因预期原因失败，修复后确认通过。
- 验证影响面：窄改动跑定向测试，package 级改动跑 package 测试；只有触及面足够大时才跑更宽的 `go test ./...`。
- 每次 patch 后做安全检查：diff 最小、无调试残留、无禁止文件、无未经批准的公共契约变更、修复与根因一致。
- 如果验证失败，先分类为 `same_failure`、`new_failure`、`environment_blocked`、`regression_introduced` 或 `verification_unreliable`，再决定是否进入下一轮。

## Bugfix Loop Capsule

每一轮或 subagent 交接只携带这份压缩状态；原始大日志和过期探索内容只保留路径引用，不直接塞入上下文。

保留：

- 用户症状、期望行为、约束、禁止/高风险文件。
- 复现状态、失败命令/路径、简短失败摘要。
- 当前活跃假设、置信度和证据。
- 已否定假设及一句话原因，避免重复探索。
- 每轮 patch 意图和改动文件列表。
- 最近一次验证命令、结果、失败分类和摘要。
- Loop 计数：当前轮次、剩余预算、重复失败次数、改动文件数。

丢弃或摘要化：

- 完整工具历史、大段日志、旧 diff、无关文件阅读、被否定假设的详细推理、重复 workflow 文本、无关 learning cases。

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

工作流结束时，向 Nexus 提交 Task Run Evidence，而不是在模型回复中自行打分。如果本地 API 不可用，则在最终回复中附上同样的 JSON payload，方便用户稍后提交。

推荐通过 `$nexus-taskrun-submit` 自动提交：

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-go-bugfix.json
```

如果 Nexus 未运行但希望直接写入本地 evaluation store：

```text
go run .\cmd\nexus-agents submit-task-run --file .nexus\task-run-go-bugfix.json --use-store
```

Endpoint：

```text
POST http://127.0.0.1:8766/api/task-runs
```

Payload shape：

```json
{
  "projectId": "<nexus project id or repo name>",
  "workflowTemplateId": "go-bugfix",
  "workflowCopyId": "<project workflow copy id if known>",
  "workflowType": "bugfix",
  "taskTitle": "<short task title>",
  "submittedStatus": "<success|partial_success|failed|cancelled|blocked>",
  "startedAt": "<ISO-8601 if known>",
  "endedAt": "<ISO-8601 if known>",
  "durationMs": 0,
  "context": {
    "agent": "bugfix-owner",
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
      "commands": ["<commands run>"]
    },
    "unfinishedItems": [],
    "risks": [],
    "contextMissing": false
  }
}
```
