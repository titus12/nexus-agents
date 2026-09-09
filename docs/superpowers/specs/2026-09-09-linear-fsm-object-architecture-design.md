# Linear FSM Object Architecture Design

**Date:** 2026-09-09  
**Status:** Proposed  
**Scope:** `cmd/orchestrator` internal architecture  
**Compatibility constraint:** Preserve the existing CLI, Multica transport, Feishu notifications, prompt bundle, and agent result-file protocols.

## 1. Goal

将 review orchestrator 收敛为一套线性有限状态机。业务流程由有限个状态节点按固定顺序推进；某个节点可以在内部并发执行多个 worker，但并发结果必须先在该节点内完成聚合，再以一个领域事件返回主 FSM。

重构后的系统必须满足：

- 每个状态由一个有明确职责的对象封装；
- 主流程只有一个状态机和一个事件循环；
- 外部调用、通知、artifact 写入和持久化都通过统一副作用边界执行；
- 顺序路径和并行路径使用同一套回复绑定、校验、重试和恢复协议；
- worker 不得直接修改主工作流上下文；
- 进程中断后可以根据 checkpoint、effect 状态和幂等键恢复；
- 对现有外部 CLI、Multica、Feishu 和结果文件协议保持兼容。

## 2. Non-goals

本次架构不做以下事情：

- 不改变现有工作流的业务顺序；
- 不重新设计 agent prompt 内容；
- 不引入分布式消息队列或 actor runtime；
- 不把现有外部 transport 一次性替换掉；
- 不把所有历史 artifact 内容迁移成新的存储格式；
- 不在第一阶段增加新的审核角色或新的并发节点。

## 3. Design principles

### 3.1 一个主 FSM

只有 `WorkflowEngine` 可以推进主状态。状态对象不能直接调用下一个状态，也不能直接修改 `workflow_state`。

状态转换必须由以下数据决定：

```text
CurrentState + DomainEvent + WorkflowContext -> StateDecision
```

### 3.2 状态对象只负责领域决策

状态对象负责：

- 接受哪些事件；
- 校验领域数据；
- 判断下一状态；
- 产生需要执行的 effects。

状态对象不负责：

- 调用 Multica 或 Feishu；
- 写文件；
- 管理线程池；
- 直接更新状态文件；
- 解析 transport 兼容格式。

### 3.3 副作用显式化

网络请求、通知和 artifact 写入表示为 `EffectRequest`。状态转换先产生 effect intent，再由 `EffectManager` 执行。状态持久化不是业务 effect，由 `WorkflowRepository` 在提交状态转换时完成。

每个 effect 都有：

```text
effect_id
effect_type
task_id
state_sequence
idempotency_key
status: PENDING | RUNNING | SUCCEEDED | FAILED
attempt
payload_ref
last_error
```

### 3.4 并发只存在于节点内部

并发节点对主 FSM 表现为一个普通状态。节点内部可以 fan-out，但必须经过：

```text
Fan-out -> Worker execution -> Reply validation -> Join -> Domain aggregation
```

只有 Join 完成后，节点才产生一个主 FSM 可消费的 `NodeCompleted`、`NodeRejected` 或 `NodeFailed` 事件。

### 3.5 Context 保存业务事实，不保存过程噪音

原始 transport message、完整 prompt、完整 agent 回复和详细日志保存为 artifact。工作流上下文只保存可恢复所需的 canonical facts、引用和摘要。

## 4. Target object model

### 4.1 WorkflowEngine

职责：

- 通过 `LockPort` 获得并维护 task ownership；
- 通过 `WorkflowRepository` 加载 `WorkflowSnapshot`；
- 从 `DomainEventInbox` 接收已经规范化的领域事件；
- 调用当前 `WorkflowState`；
- 生成 `StateDecision` 并交给 `WorkflowRepository.commit_transition()`；
- 将持久化后的 effect intent 交给 `EffectManager` 执行；
- 处理中断、超时、取消和恢复。

接口：

```python
class WorkflowEngine:
    def run(self, task_id: str) -> RunResult: ...
    def dispatch(self, event: DomainEvent) -> StateDecision: ...
    def resume(self, snapshot: WorkflowSnapshot) -> RunResult: ...
    def cancel(self, task_id: str) -> bool: ...
```

`WorkflowEngine` 不包含角色 prompt、finding merge、并行 worker 逻辑、transport 兼容解析、文件读写或 retry 判断。它只通过 `LockPort`、`WorkflowRepository`、`DomainEventInbox`、`StateRegistry` 和 `EffectManager` 工作。

### 4.2 WorkflowState

所有状态实现同一个对象接口：

```python
class WorkflowState(Protocol):
    name: WorkflowStateName

    def enter(self, context: WorkflowContext) -> StateDecision: ...
    def handle(
        self,
        context: WorkflowContext,
        event: DomainEvent,
    ) -> StateDecision: ...
    def on_timeout(self, context: WorkflowContext) -> StateDecision: ...
    def on_resume(self, context: WorkflowContext) -> StateDecision: ...
```

`StateDecision` 只包含业务决策：

```python
@dataclass(frozen=True)
class StateDecision:
    transition: TransitionRequest | None
    effects: tuple[EffectRequest, ...]
    update: ContextUpdate
```

`ContextUpdate` 必须是按聚合定义的不可变类型，不允许使用任意字典或路径字符串修改 context。状态对象只产生 decision，不直接修改 context；所有 target 必须通过集中式 transition registry 校验。禁止在通用状态基类中通过 `if state.name == ...` 承载状态差异。

### 4.3 WorkflowContext

`StateContext` 拆为以下聚合对象：

```text
WorkflowContext
  - identity: TaskIdentity
  - progression: ProgressState
  - review: ReviewState
  - delivery: DeliveryState
  - human_gate: HumanGateState | None
  - recovery: RecoveryState
```

其中：

- `TaskIdentity`：task_id、issue_id、project、request_id；
- `ProgressState`：当前 FSM 状态、sequence、entered_at、resume_state；
- `ReviewState`：review plan、group/item、findings、revision；
- `DeliveryState`：active request、dispatch status、idempotency key；
- `HumanGateState`：decision_id、reason、prompt reference、resume state；
- `RecoveryState`：retry budget、last error、recoverable、blocked reason。

这些对象只保存结构化业务数据，不保存 transport message 对象和线程对象。

### 4.4 ReplyEnvelope

所有 agent 回复必须先转换为统一 envelope：

```python
@dataclass(frozen=True)
class ReplyEnvelope(Generic[TPayload]):
    task_id: str
    request_id: str
    author_id: str
    external_message_id: str
    phase: str
    role: str
    target_state: str
    payload: TPayload
    received_at: str
    source: str
```

transport adapter 先将旧格式转换成内部 `TransportReply`，再由 `ReplyNormalizer` 生成带有具体 role payload 类型的 `ReplyEnvelope`。`Mapping[str, Any]` 只允许存在于 adapter 边界，不得进入 domain 层。

`ReplyEnvelope` 在 transport 边界完成：

- task/request/phase/role binding；
- author identity 校验；
- request freshness；
- result-file hash 和 pointer 校验；
- 兼容旧格式所需的 transport backfill。

进入 domain 层之后不再使用原始 `ExternalMessage` 或任意 transport dict。

### 4.5 EffectManager and external ports

外部端口只负责一次实际 I/O，不负责业务 retry、状态持久化或主 FSM 推进：

```python
class ExternalEffectRunner(Protocol):
    def run_once(self, effect: EffectRequest) -> EffectResult: ...
```

具体 effect 类型：

```text
DispatchAgentEffect
PollAgentEffect
NotifyEffect
WriteArtifactEffect
ReleaseLeaseEffect
```

`EffectManager` 负责 effect 生命周期、幂等、retry、timeout、错误分类和 effect 结果事件；`ExternalEffectRunner` 只负责一次外部 I/O。

`EffectManager` 负责 effect 生命周期、幂等、retry 和 effect 结果事件。状态对象不能绕过 effect manager 直接访问外部端口。

### 4.6 NodeExecutor

`WorkerRunner` 负责单个 worker 的 binding、dispatch、poll、reply validation 和 attempt；`NodeJoiner` 负责纯聚合和 quorum 判断。`NodeExecutor` 只负责编排一个或多个 `WorkerRunner` 并调用 `NodeJoiner`，不负责主 FSM、effect 持久化或 finding 生命周期。

节点执行器封装顺序或并发 worker：

```python
class NodeExecutor(Protocol):
    def execute(
        self,
        node: ReviewNode,
        context: NodeContext,
    ) -> NodeResult: ...
```

实现包括：

- `SequentialNodeExecutor`；
- `ConcurrentNodeExecutor`。

二者共用：

- `ReplyEnvelope`；
- `WorkerBinding`；
- `RetryPolicy`；
- `WorkerResult`；
- `JoinPolicy`。

`ConcurrentNodeExecutor` 的 worker 只返回不可变 `WorkerResult`，不能访问主 `WorkflowContext` 的可变对象。

## 5. FSM state model

### 5.1 Business states

主流程保持以下顺序：

```text
REQUEST_INTAKE
  -> ZHONGSHU_ANALYST
  -> ZHONGSHU_SOLVER
  -> ZHONGSHU_CRITIC
  -> ZHONGSHU_FREEZE_CHECK
  -> MENXIA_ITEM_SOLVER
  -> MENXIA_ITEM_ANALYST
  -> MENXIA_ITEM_CRITIC
  -> MENXIA_GROUP_GATE
  -> DONE
```

### 5.2 System states

系统状态不参与业务流程顺序，只表示控制流异常：

```text
HUMAN_GATE
RETRY_WAIT
BLOCKED
CANCELLED
FAILED
PERSISTENCE_DEGRADED
```

系统状态必须保留 `resume_state`，但不能任意跳转到其他业务节点。恢复只能回到记录的 `resume_state`，并且必须通过 transition registry 验证。

### 5.3 Transition rules

transition registry 是唯一允许的状态图来源。每条边包含：

```text
from_state
event
to_state
guard
reason_code
```

业务状态只产生领域动作，例如：

```text
APPROVE_FREEZE
REQUEST_SOLVER_REVISION
REQUEST_ANALYST_EVIDENCE
APPROVE_ITEM
OPEN_HUMAN_GATE
```

状态对象不直接产生字符串状态名。

## 6. Parallel node contract

以 `ZHONGSHU_ANALYST` 为例：

```text
ZHONGSHU_ANALYST.enter
  -> 创建 node_run_id 和 worker bindings
  -> 产生 DispatchAgentEffect[]
  -> 保存 node checkpoint

ConcurrentNodeExecutor
  -> 每个 worker 独立 acquire lease
  -> dispatch / poll / validate
  -> 写 worker artifact
  -> 生成 WorkerResult

JoinPolicy
  -> 验证 worker 数量、角色、revision、scope
  -> 聚合 findings/evidence
  -> 产生 AnalystCompleted 或 AnalystFailed

ZHONGSHU_ANALYST.handle
  -> 消费 AnalystCompleted
  -> 产生 ZHONGSHU_SOLVER transition
```

并发节点必须满足：

- worker 之间不共享可变 context；
- worker 结果按 worker_id 和 logical_request_id 去重；
- 任一 worker 的 author binding 不匹配时，该 worker 失败，不得静默替换 author；
- join 只读取持久化的 worker result；
- join 结果可重复计算；
- worker retry 不推进主 FSM sequence；
- node completion 才推进主 FSM sequence。

## 7. Persistence and recovery

### 7.1 Checkpoint boundary

每次状态推进按以下顺序执行：

```text
1. 接收并规范化 DomainEvent
2. State.handle 生成 StateDecision
3. 持久化 state transition 和 effect intent
4. 执行 effects
5. 持久化 effect result
6. 更新 checkpoint
```

如果 effect 执行期间进程退出，恢复逻辑读取 `PENDING` 或 `RUNNING` effect，通过 idempotency key 查询外部状态，决定确认、重试或失败。

### 7.2 Event and snapshot policy

事件日志保存状态转换和 effect 生命周期；snapshot 保存恢复所需的最小上下文。

事件追加失败不得静默忽略。系统必须将任务标记为 `PERSISTENCE_DEGRADED`，保留可重试的 append intent，并禁止继续推进新的业务状态，直到事件链恢复。

### 7.3 Lock policy

task lock 必须提供：

- 原子获取；
- owner token；
- lease version；
- heartbeat；
- 安全接管；
- 失去所有权后的停止执行。

不能通过“删除旧文件后重新创建”实现无保护的锁接管。

## 8. Error and retry model

异常按类型分类，不使用宽泛的 `RuntimeError` 作为业务分类：

```text
TransportError
ReplyBindingError
ReplyValidationError
WorkerTimeoutError
LeaseLostError
PersistenceError
InvariantViolation
HumanGateDeliveryError
```

每一类错误通过统一 `RetryPolicy` 判断：

```text
retryable
max_attempts
backoff
resume_state
terminal_state
notification_policy
```

retry 只改变 effect 或 node attempt，不直接修改业务 finding 和主 FSM 顺序。

## 9. Compatibility boundary

现有协议保留在 infrastructure 层：

```text
CanonicalTransport
  |- MulticaTransportAdapter
  |- LegacyPromptAdapter
  |- ResultFileAdapter
  |- RemoteResultBridge

NotificationPort
  |- FeishuNotificationAdapter
```

兼容 adapter 可以继续支持旧格式，但必须在进入 `ReplyEnvelope` 前完成转换。domain 层不得知道：

- comment body 的格式；
- prompt file 的路径；
- result pointer 的旧字段；
- Feishu comment 的渲染细节。

## 10. Migration plan

### Phase 1: Freeze contracts

- 为现有状态图建立 transition table 测试；
- 建立 `ReplyEnvelope` 和 `WorkerBinding` 的契约测试；
- 固化 finding scope、human gate、blocked reason 和 cancellation 语义；
- 增加并行路径 author binding 测试。

### Phase 2: Introduce domain objects

- 新增 `WorkflowContext` 聚合对象；
- 新增 `TransitionRequest`、`StateDecision`、`EffectRequest`；
- 保留旧 `StateContext` 作为兼容序列化 DTO；
- 添加双向转换并验证字段完整性。

### Phase 3: Unify reply pipeline

- 将 direct polling 和 parallel polling 都转换为 `ReplyEnvelope`；
- 将 transport backfill 限制在 adapter；
- 删除 app 和 state 层重复的 author/request binding 校验。

### Phase 4: Extract state objects

- 每个业务状态实现 `WorkflowState`；
- 将 `BaseState` 中的角色分支迁移为状态对象或状态策略；
- `OrchestratorApp` 只保留 engine wiring 和生命周期控制。

### Phase 5: Unify node execution

- 将 Zhongshu 和 Menxia worker 调度都接入 `NodeExecutor`；
- 保留一个顺序 executor 和一个并发 executor；
- 将旧 fan-out coordinator 降级为兼容层，完成迁移后删除。

### Phase 6: Harden persistence and recovery

- 引入 effect intent/result；
- 修复 event append failure 的静默容忍；
- 修复 task lock 的原子接管；
- 将 human gate 恢复纳入统一恢复流程。

### Phase 7: Remove legacy paths

- 删除重复的并行 runner；
- 删除旧 transport 分支；
- 删除只用于过渡的 context 转换；
- 保留外部协议兼容测试，防止 CLI 和 Multica 行为回归。

## 11. Testing strategy

### Unit tests

- 每个状态的合法事件和非法事件；
- transition registry 的完整性；
- finding scope 和 finding lifecycle；
- ReplyEnvelope binding；
- retry policy；
- join policy；
- effect idempotency。

### Integration tests

- 顺序节点完整流程；
- 并发节点 fan-out/join；
- worker 超时和部分失败；
- 进程中断后 effect recovery；
- human gate delivery failure/resume；
- duplicate external reply；
- task lock ownership loss；
- event append failure。

### Compatibility tests

- 现有 CLI 参数；
- Multica legacy comment reply；
- prompt bundle；
- canonical result file；
- remote result bridge；
- Feishu notification fallback。

### Acceptance criteria

重构完成后必须满足：

- 主 FSM 的状态推进只能由 `WorkflowEngine` 完成；
- worker 不再直接修改主 context；
- direct 和 parallel reply 使用同一套 envelope 校验；
- 任意状态转换都能定位到 transition registry 的一条边；
- effect 失败可以重试或明确进入终态；
- event append failure 不会被静默吞掉；
- task lock 不存在双持有窗口；
- 核心测试、并发测试、恢复测试和兼容测试全部通过；
- 外部 CLI 和 transport 协议行为不变。

## 12. Explicit architectural decisions

本设计明确选择：

- 不引入 actor model；
- 不允许并行 worker 直接写主上下文；
- 不允许状态对象直接执行网络和文件副作用；
- 不允许多个并行 runner 长期并存；
- 不允许用任意动态 target 绕过 transition registry；
- 不允许用宽泛异常类型决定状态损坏；
- 不允许在 domain 层处理 legacy transport 格式。

## 13. Self-review corrections before implementation

本节是对前述设计边界的强制约束，优先级高于任何含义较宽的接口描述。完成本节后，设计才满足开工条件。

### 13.1 WorkflowEngine 不承载基础设施语义

`WorkflowEngine` 只做流程协调，不直接实现文件读写、锁、transport、worker、retry 或异常分类。它只依赖以下端口：

```text
LockPort
WorkflowRepository
DomainEventInbox
StateRegistry
EffectManager
```

职责边界固定为：

- `LockPort`：task ownership 和 lease；
- `WorkflowRepository`：journal、snapshot、effect intent/result；
- `DomainEventInbox`：接收已经规范化的领域事件；
- `StateRegistry`：根据状态名返回状态对象；
- `EffectManager`：执行已持久化的 effect 并投递 effect result event；
- `WorkflowEngine`：按照顺序调用这些端口。

### 13.2 StateDecision 不允许成为万能 patch

`StateDecision` 中的更新必须是强类型 `ContextUpdate`，按聚合拆分为 `ReviewUpdate`、`DeliveryUpdate`、`HumanGateUpdate`、`RecoveryUpdate` 等。禁止使用任意字典、路径字符串或反射修改 context。

状态对象只能返回 decision，不能直接改变 context、写文件或访问外部服务。禁止在通用状态基类中通过 `if state.name == ...` 承载状态差异；差异必须位于具体状态对象或具体策略对象中。

### 13.3 Effect、worker 和 join 必须各自只有一种语义

- `ExternalEffectRunner`：只执行一次外部 I/O，不负责 retry、持久化或主 FSM 推进；
- `EffectManager`：只管理 effect 生命周期、幂等、retry 和 effect 结果；
- `WorkerRunner`：只负责一个 worker 的 dispatch、poll、binding 和 worker attempt；
- `NodeExecutor`：只负责顺序/并发调度 worker；
- `NodeJoiner`：只负责纯聚合和 quorum 判断；
- `WorkflowState`：只负责领域决策和状态转换。

`PersistCheckpointEffect` 不属于 effect 类型。transition record、snapshot 和 effect intent 由 `WorkflowRepository.commit_transition()` 在一个逻辑提交中完成。

### 13.4 持久化提交顺序

`WorkflowRepository` 采用 journal-first 语义：

```text
normalize event
  -> state decision
  -> append transition journal + fsync
  -> atomically update snapshot
  -> EffectManager runs persisted intents
  -> append effect result + fsync
```

journal 是提交事实来源，snapshot 是可重建的加速视图。journal 追加失败时不得推进业务状态，任务进入 `PERSISTENCE_DEGRADED`，并保留 append intent。`PERSISTENCE_DEGRADED` 必须列入 system state，并保留原始 `resume_state`。

### 13.5 错误必须可归因

所有跨边界错误统一转换为不可变 `FailureRecord`，至少包含：

```text
failure_id
stage
owner_component
task_id
state
sequence
node_run_id
worker_id
effect_id
error_code
retryable
cause_type
external_id
```

每个边界只允许转换一次错误，并保留 cause chain。日志、journal、snapshot 和最终失败状态都引用同一个 `failure_id`。`last_error` 只能作为展示摘要，不能作为归因数据源。

### 13.6 ReplyEnvelope 必须是类型化领域输入

transport adapter 可以使用 `Mapping[str, Any]`，但进入 domain 层前必须由 `ReplyNormalizer` 生成 `ReplyEnvelope[RoleReply]`。直接路径和并行路径必须调用同一个 normalizer 和同一个 binding validator。

### 13.7 开工前的硬门槛

在开始迁移运行时代码前，必须先完成以下契约测试：

- transition registry 覆盖所有业务状态和 system state；
- `PERSISTENCE_DEGRADED` 可以安全暂停和恢复；
- journal 追加失败不会推进主 FSM；
- direct/parallel reply 的 author binding 结果完全一致；
- worker 只能产出 `WorkerResult`，不能修改主 context；
- 每个失败场景都能通过 `failure_id` 定位到唯一 stage 和 owner；
- `StateDecision` 不接受任意动态 context patch；
- 现有 CLI、Multica、Feishu 和 result-file compatibility tests 保持通过。
