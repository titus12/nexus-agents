# go-feature-development

Source: `templates/rules/go-00-routing.md`
Stack: `go`
Entry skill: `$wf-go-feat`

适用于 Go 新功能、既有行为修改和跨文件业务调整。

## Nexus TaskRun Start Gate

在知识检索、代码探索、任务拆分、subagent 派发或编辑前，初始化本地 TaskRun payload。优先使用跨平台 Node helper，任务标题使用 ASCII：

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs start --projectId <projectId> --workflowType feature-development --taskTitle "<ascii task title>" --payloadFile .nexus/task-run-feature-development.json --contextFile .nexus/workflow-context-feature-development.json
```

仅当 payload 存在且包含 `sessionId` 与 `startedAt` 后继续。初始化失败时停止工作流并报告精确错误。

## 强约束

1. **先探索，后实施。** 未加载适用 Rules、知识库 routing 和真实代码证据，且未生成待审核目标契约前，禁止修改代码。
2. **目标和质检必须可审核。** 用户描述不完整时，AI 必须补齐完成标准、质检项、假设和证据；权威需求来源不可读取时必须如实阻塞或取得用户同意后降级；不得伪造量化结论。
3. **重大假设必须确认。** 涉及多个业务解释、公共 API/协议、数据或配置迁移、删除旧行为、大范围重构或只能由业务方定义的验收标准时，必须等待用户确认。
4. **任务拆分必须遵守 `go-04-task-decomposition.md`。** 方案阶段必须记录任务规模信号，并明确“是否拆分、为何拆分或为何不拆分”；未输出该决策不得实施。
5. **Workflow 默认按角色使用 subagent。** 选择 `$wf-go-feat` 即授权 Owner 派发探索、实现、验证和复核 Capsule；派发前必须通过 `go-04-task-decomposition.md` 的边界和规模检查，禁止把大任务直接交给单个 subagent。
6. **目标门和质量门不得省略。** 子任务完成或代码写完不等于工作流完成。
7. **循环必须受证据和上限约束。** 父工作流最多 3 个完整 Loop；每个子任务最多 2 次实施-质检尝试；没有新证据时禁止重复同一失败路径。
8. **最终结果必须可复核。** 如实报告改动、验证命令和结果、未验证项、风险及最终状态：`success | partial_success | blocked | failed | cancelled`。

## 工作流

1. **加载上下文并探索**：读取 AGENTS、适用 Rules、知识库 routing 及其最少必要文档；先读取用户提供的权威需求来源，再搜索真实代码、调用方、测试、配置和相似实现，形成当前行为、冲突和风险证据。
2. **生成待审核目标契约**：补齐目标、完成标准、非目标、范围、验证、假设/证据和风险。证据不足时改为 diagnosis-first 方案，只实施已批准的诊断步骤；低风险且证据充分时可继续，重大假设或高风险必须等待用户确认。
3. **计划并决定是否拆分**：加载 `go-04-task-decomposition.md`，评估目标数、模块/package、非机械性生产文件、验证路径、高风险项和未确认假设；在计划中明确是否拆分、拆分顺序、集成点，或说明不拆分原因。仅在边界清晰时拆成 Task Capsule。
4. **执行 Task Capsule Loop 并集成**：以最小改动实施、验证子目标并记录证据；通过的子任务回到主线程集成，失败的子任务只能携带新证据重试。
5. **目标门**：逐项确认待审核目标契约中的必须完成项、受影响调用方和非目标边界均有证据。未通过时补最小任务、回到探索，或请求必要的业务确认。
6. **质量门与退出**：检查 Rules、最小 diff、错误处理、兼容性、构建/测试和真实验证证据。通过后交付；可修复且未超限时回到计划；无新证据、超限或环境阻塞时退出为 `partial_success` 或 `blocked`。

## 待审核目标契约

```text
任务类型：feature | modification
用户原始目标：
权威需求来源及读取状态：
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

## 需求来源与事实冲突

用户提供的 Feishu/Lark 文档、需求文档、接口说明或设计稿属于权威需求来源。无法读取时，不得用本地候选代码猜测替代；必须请求必要授权，或在用户明确同意后标注为降级方案。

当需求来源与知识库、代码、配置、协议、测试或当前行为冲突时，方案必须列出：

```text
冲突来源：
对实现或行为的影响：
可选解决方案：
推荐方案及证据：
是否需要用户决定：
```

## Diagnosis-First 方案

只读探索无法确认唯一原因或真实行为时，不得把推测性改动当作实现计划。先输出：

```text
未确认事实与证据缺口：
最小诊断步骤或临时日志：
预期观察结果：
区分候选原因的判断规则：
诊断代码完成后删除、保留或宏控的条件：
```

只有诊断证据满足判断规则后，才能进入业务实现。

## 探索上下文包

给后续角色或用户审核的上下文必须保持最小化：

```text
需求摘要：
权威需求来源与读取状态：
关键代码、测试、配置和知识库路径：
当前行为与目标行为：
已确认事实：
冲突、未确认项与风险：
任务拆分决策：
```

不得默认传递完整对话历史、大段日志或无关代码。

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

默认由 **Sisyphus** 作为工作流 Owner 编排 subagent。选择 `$wf-go-feat` 即授权按角色派发 Capsule；Owner 始终负责拆分、上下文压缩、集成和最终两道门，不能把整个需求交给单个 subagent。

| 阶段 | 主角色 | 触发条件与职责 |
|---|---|---|
| 加载上下文与探索 | Sisyphus + Oracle | Owner 提供最小上下文；Oracle 默认负责独立的代码、KB routing 和调用方探索。 |
| 跨模块或不确定性探索 | Oracle | 扩展只读分析，确认调用链、影响范围和未确认事实。 |
| 外部 API/文档核对 | Librarian | 仅在需要外部文档、协议或第三方库证据时使用。 |
| 目标契约与拆分方案 | Sisyphus | 汇总探索包，产出目标契约、拆分决策和 Task Capsule。 |
| 高复杂度设计 | Prometheus | 仅在高风险、多个可选方案或先设计后实施时提供只读方案。 |
| 单文件、小范围实施 | Quick | 作为实现 subagent 执行单一 Capsule、范围小且无接口变化的最小改动。 |
| 多文件实施 | Hephaestus | 作为实现 subagent 执行已确认的多文件窄 Capsule；不负责替用户决定未确认业务语义。 |
| 独立验证或辅助子任务 | Worker | 只接收边界不重叠、验收和验证明确的 Capsule。 |
| 质量门 | Reviewer Logic | 检查逻辑、边界、并发、事务和错误处理；高风险时再按需加入 Reviewer Perf 与 Reviewer Security。 |
| 集成与最终交付 | Sisyphus | 合并结果，执行总体目标门、质量门和最终验证。 |

## KnowledgeBase Recommendation Gate

工作流结束前判断本次是否发现了可复用的稳定知识，例如领域入口、代码模式、反模式、验证路径或 routing 规则。只输出建议，不自动修改知识库：

```text
KB Recommendation: none - <reason>.
KB Recommendation: consider <target path> - <reason>.
```

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

结束时完成同一个 `.nexus/task-run-feature-development.json` payload，并调用 `$nexus-taskrun-submit` **只提交一次**。payload 不得删除；成功或失败后都在最终回复中报告 payload/context 文件路径。

优先使用：

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs submit --payloadFile .nexus/task-run-feature-development.json --contextFile .nexus/workflow-context-feature-development.json
```

Windows 可使用：

```text
powershell -ExecutionPolicy Bypass -File .agents\skills\nexus-taskrun-submit\submit-workflow-result.ps1 -PayloadFile .nexus\task-run-feature-development.json -ContextFile .nexus\workflow-context-feature-development.json
```

最终 payload 字段、禁止字段、紧凑证据规则和提交 endpoint 以 `$nexus-taskrun-submit` 为唯一来源。现有 workflow-run headers 只用于路由 telemetry；最终 payload 不得包含任何 workflow ID。证据必须包含实际执行的验证命令、结果和未验证范围；不得把计划中的验证写成已通过。
