# Review Orchestrator V2 有限状态机重构设计 v3.1

> 状态：待用户审核（v3.1 修订版）  
> 日期：2026-08-23  
> 目标文件：`D:\workspace\src\nexus-agents\cmd\review_orchestrator_v2.py`

## 1. 目标与非目标

### 1.1 目标

将当前基于 `run_zhongshu()`、`run_menxia()` 和大量条件分支的流程，重构为一个可恢复、可验证、可审计的有限状态机（FSM）。

重构后必须满足：

1. 每个状态具有明确的 `enter / update / exit` 生命周期；
2. 状态迁移只能由统一的 Transition Policy 决定；
3. 每个状态的输入、输出、持久化数据和允许事件明确可查；
4. Agent 回复严格绑定 `author_id + task_id + request_id + role + phase`；
5. 所有超时、错误、人工决策和进程重启都可恢复或明确终止；
6. 中书省 Critic 不通过时，能区分“回 Analyst”“回 Solver”“人工决策”“真正阻断”；
7. 门下省按任务线性推进，不允许跳过任务、跳过双视角审查或跳过组门禁；
8. 所有迁移都能通过确定性的 Fake Adapter 测试复现。

### 1.2 非目标

本次重构不改变：

- Analyst、Solver、Critic 的业务职责；
- 中书省先于门下省的总体顺序；
- 门下省不直接修改代码的约束；
- 默认中书省最大修订次数 8；
- 当前 Multica、飞书和本地 artifact 的业务含义。

本次只重构流程控制、状态持久化、事件处理和恢复机制。

---

## 2. 当前问题

当前脚本已经包含部分校验和超时处理，但控制流仍分散在多个函数中，存在以下结构性问题：

1. Agent action 可以直接触发分支中的 `return False`；
2. Critic 返回 `BLOCKED` 时，程序无法统一判断是否实际上应该回 Solver；
3. `WAITING_HUMAN`、`TIMEOUT`、`BLOCKED` 的恢复入口不统一；
4. Human Gate 的 decision、飞书消息和状态更新不是一个原子操作；
5. 状态、artifact、Issue 评论、飞书消息之间没有统一的序列号；
6. `enter`、轮询、持久化、通知混在一个函数内；
7. 当前测试主要验证函数行为，缺少完整状态迁移和故障恢复测试。

典型错误链路：

```text
Critic 返回 BLOCKED
→ 分支直接写 BLOCKED
→ 没有判断 Finding 是否可由 Solver 修订
→ Solver 没有收到任务
```

另一个典型错误链路：

```text
写入 WAITING_HUMAN
→ 飞书门禁发送失败
→ 没有 decision_id 或 gate_message_id
→ 用户没有收到问题
```

---

## 3. 总体架构

```text
CLI
 └── OrchestratorApp
      ├── StateMachine
      │    ├── StateRegistry
      │    ├── TransitionPolicy
      │    ├── EventRouter
      │    └── RecoveryManager
      ├── StateStore
      ├── ArtifactStore
      └── Adapters
           ├── MulticaAdapter
           ├── FeishuAdapter
           └── Clock
```

建议文件结构：

```text
cmd/
  review_orchestrator_v2.py        # CLI 兼容入口，禁止继续承载业务分支
  orchestrator/
    __init__.py
    app.py                         # 应用启动、恢复和主循环
    context.py                     # StateContext 数据模型
    events.py                      # 事件模型
    states.py                      # 所有状态 enter/update/exit
    transitions.py                 # 唯一状态迁移表
    policies.py                    # Agent action 和 Finding 路由策略
    validators.py                  # 回复、方案、状态和数据契约校验
    persistence.py                 # state、event、artifact 原子持久化
    adapters.py                    # Multica、飞书、时钟接口
    recovery.py                    # 崩溃和超时恢复
tests/
  test_state_transitions.py
  test_reply_binding.py
  test_human_gate_recovery.py
  test_failure_matrix.py
```

迁移期间允许保留旧脚本作为 CLI 入口，但新业务逻辑不得继续添加到旧的嵌套分支。

---

## 4. 状态枚举

### 4.1 生命周期状态

```text
REQUEST_INTAKE
ZHONGSHU_ANALYST
ZHONGSHU_SOLVER
ZHONGSHU_CRITIC
ZHONGSHU_FREEZE_CHECK
MENXIA_ITEM_SOLVER
MENXIA_ITEM_ANALYST
MENXIA_ITEM_CRITIC
MENXIA_GROUP_GATE
HUMAN_GATE
TIMEOUT
HUMAN_GATE_TIMEOUT
HUMAN_GATE_ERROR
INVALID_AGENT_REPLY
MULTICA_ERROR
STATE_CORRUPTED
BLOCKED
DONE
```

### 4.2 状态语义

| 状态 | 含义 | 是否可自动继续 |
|---|---|---:|
| `REQUEST_INTAKE` | 初始化任务、Issue 和上下文 | 是 |
| `ZHONGSHU_ANALYST` | 需求、证据、任务和候选组分析 | 是 |
| `ZHONGSHU_SOLVER` | 生成正式中书省方案 | 是 |
| `ZHONGSHU_CRITIC` | 审查中书省方案 | 是 |
| `ZHONGSHU_FREEZE_CHECK` | 冻结前结构校验 | 是 |
| `MENXIA_ITEM_SOLVER` | 为单个任务编写实现方案 | 是 |
| `MENXIA_ITEM_ANALYST` | 对单个实现方案正向审查 | 是 |
| `MENXIA_ITEM_CRITIC` | 对单个实现方案对抗性审查 | 是 |
| `MENXIA_GROUP_GATE` | 审查一个组是否完成 | 是 |
| `HUMAN_GATE` | 等待用户决策 | 依赖用户 |
| `TIMEOUT` | Agent 阶段超时 | 通过 resume |
| `HUMAN_GATE_TIMEOUT` | 用户决策超时 | 需要人工重开 |
| `HUMAN_GATE_ERROR` | 门禁无法投递或恢复 | 需要修复/重试 |
| `INVALID_AGENT_REPLY` | 回复结构不符合协议 | 可重新派发 |
| `MULTICA_ERROR` | Multica 服务或 CLI 错误 | 重试 |
| `STATE_CORRUPTED` | 状态或事件文件损坏 | 人工恢复 |
| `BLOCKED` | 明确不可自动继续 | 终端状态 |
| `DONE` | 全流程完成 | 终端状态 |

---

## 5. StateContext 数据契约

所有状态共享一个上下文对象，禁止通过隐式成员传递状态。

```json
{
  "schema_version": "3.0",
  "task_id": "task-20260823-xxxxxx",
  "issue_id": "multica-issue-id",
  "workflow_state": "ZHONGSHU_SOLVER",
  "sequence": 12,
  "entered_at": "2026-08-23T13:00:00Z",
  "updated_at": "2026-08-23T13:01:00Z",

  "raw_request": "...",
  "project_type": "go",
  "task_type": "review",

  "current_phase": "ZHONGSHU",
  "current_role": "solver",
  "expected_agent_id": "4d0e9fab-...",
  "active_request_id": "task:ZHONGSHU_SOLVER:2:uuid",
  "last_sent_at": "...",

  "active_group_id": null,
  "active_item_id": null,
  "group_index": null,
  "item_index": null,

  "zhongshu_revision_round": 1,
  "max_zhongshu_revision_rounds": 8,
  "item_revision_round": 0,
  "max_item_revision_rounds": 3,

  "active_decision_id": null,
  "resume_state": null,
  "gate_message_id": null,

  "last_artifact_id": "artifact-000012",
  "active_finding_ids": [],
  "pending_solver_finding_ids": [],
  "resolved_finding_ids": [],

  "last_error": null,
  "recoverable": true
}
```

### 5.1 状态级数据要求

| 状态 | 必须有的数据 |
|---|---|
| `ZHONGSHU_ANALYST` | `expected_agent_id=Analyst`、`active_request_id` |
| `ZHONGSHU_SOLVER` | `EvidencePacket`、`PlanWorkspace`、pending findings |
| `ZHONGSHU_CRITIC` | `EvidencePacket`、`ZhongshuPlan`、active findings |
| `ZHONGSHU_FREEZE_CHECK` | 最新 Solver 方案、最新 Analyst 证据 |
| `MENXIA_ITEM_SOLVER` | `active_group_id`、`active_item_id`、FrozenPlan |
| `MENXIA_ITEM_ANALYST` | ImplementationProposal |
| `MENXIA_ITEM_CRITIC` | ImplementationProposal、AnalystReview |
| `MENXIA_GROUP_GATE` | 当前组、组内所有已通过任务 |
| `HUMAN_GATE` | `active_decision_id`、`resume_state`、delivery status |
| `TIMEOUT` | timeout phase、role、request_id、last artifact |

---

## 6. 状态生命周期

每个状态必须实现以下接口：

```python
class State(Protocol):
    name: str

    def enter(self, ctx: StateContext) -> None:
        """创建本状态的 request、检查输入并持久化。"""

    def update(self, ctx: StateContext) -> Event:
        """执行一次轮询或一次本地校验，不直接跳状态。"""

    def exit(self, ctx: StateContext, event: Event) -> None:
        """保存本状态产物、清理 active request、记录审计事件。"""
```

### 6.1 enter 的固定顺序

```text
1. 校验上一个状态的输出；
2. 设置当前 state、phase、role；
3. 生成 request_id；
4. 保存 checkpoint；
5. 派发外部请求；
6. 保存 dispatch 结果；
```

### 6.2 update 的固定顺序

```text
1. 读取当前 active_request；
2. 检查超时；
3. 读取 Multica/飞书事件；
4. 按 author_id/task_id/request_id/role/phase 校验；
5. 解析 action；
6. 返回 Event；
```

### 6.3 exit 的固定顺序

```text
1. 保存 AgentReply 或本地校验 artifact；
2. 更新 Finding 和 plan workspace；
3. 写入 TransitionEvent；
4. 清空旧 active_request；
5. 交给 TransitionPolicy 决定下一状态；
```

状态实现不得在 `update()` 中直接调用另一个状态的 `enter()`。

---

## 7. 严格回复绑定

合法 Agent 回复必须包含：

```json
{
  "task_id": "task-20260823-xxxxxx",
  "request_id": "task-20260823-xxxxxx:ZHONGSHU_SOLVER:2:abcd1234",
  "role": "review-solver",
  "phase": "ZHONGSHU",
  "action": "READY_FOR_CRITIC"
}
```

校验顺序：

```text
1. 评论作者类型必须是 agent；
2. author_id == expected_agent_id；
3. task_id == current task_id；
4. request_id == active_request_id；
5. role == expected role；
6. phase == expected phase；
7. action 在当前状态允许集合内；
8. payload 结构满足当前状态 schema。
```

任一失败：

```text
AgentReplyRejected
→ 保存 RejectedAgentReply
→ 当前状态继续 update 或进入 INVALID_AGENT_REPLY
→ 不能推进状态
```

---

## 8. 统一状态迁移表

### 8.1 中书省

| 当前状态 | 事件/action | 目标状态 |
|---|---|---|
| `ZHONGSHU_ANALYST` | `READY_FOR_SOLVER` | `ZHONGSHU_SOLVER` |
| `ZHONGSHU_ANALYST` | `HUMAN_GATE` | `HUMAN_GATE` |
| `ZHONGSHU_ANALYST` | `BLOCKED` 且有用户问题 | `HUMAN_GATE` |
| `ZHONGSHU_ANALYST` | `BLOCKED` 无恢复路径 | `BLOCKED` |
| `ZHONGSHU_SOLVER` | `READY_FOR_CRITIC` | `ZHONGSHU_CRITIC` |
| `ZHONGSHU_SOLVER` | `HUMAN_GATE` | `HUMAN_GATE` |
| `ZHONGSHU_CRITIC` | `APPROVE_FREEZE` 且无 active P0/P1 | `ZHONGSHU_FREEZE_CHECK` |
| `ZHONGSHU_CRITIC` | `APPROVE_FREEZE` 但有 active P0/P1 | `ZHONGSHU_SOLVER` |
| `ZHONGSHU_CRITIC` | `REQUEST_ANALYST_EVIDENCE` | `ZHONGSHU_ANALYST` |
| `ZHONGSHU_CRITIC` | `REQUEST_SOLVER_REVISION` | `ZHONGSHU_SOLVER` |
| `ZHONGSHU_CRITIC` | `REQUEST_REGROUP` | `ZHONGSHU_SOLVER` |
| `ZHONGSHU_CRITIC` | `HUMAN_GATE` | `HUMAN_GATE` |
| `ZHONGSHU_CRITIC` | `BLOCKED` 且有可修复 Finding | `ZHONGSHU_SOLVER` |
| `ZHONGSHU_CRITIC` | `BLOCKED` 且达到 8 次或不可修复 | `BLOCKED` |
| `ZHONGSHU_FREEZE_CHECK` | 校验通过 | `MENXIA_ITEM_SOLVER` |
| `ZHONGSHU_FREEZE_CHECK` | 校验失败 | `ZHONGSHU_SOLVER` |

### 8.2 门下省

| 当前状态 | 事件/action | 目标状态 |
|---|---|---|
| `MENXIA_ITEM_SOLVER` | `FEASIBLE` | `MENXIA_ITEM_ANALYST` |
| `MENXIA_ITEM_SOLVER` | `HUMAN_GATE` | `HUMAN_GATE` |
| `MENXIA_ITEM_ANALYST` | `EVIDENCE_SUFFICIENT` | `MENXIA_ITEM_CRITIC` |
| `MENXIA_ITEM_ANALYST` | `NEEDS_MORE_EVIDENCE` | `MENXIA_ITEM_SOLVER` |
| `MENXIA_ITEM_ANALYST` | `HUMAN_GATE` | `HUMAN_GATE` |
| `MENXIA_ITEM_CRITIC` | `APPROVE_ITEM` | 下一个任务或 `MENXIA_GROUP_GATE` |
| `MENXIA_ITEM_CRITIC` | `REVISE_ITEM` | `MENXIA_ITEM_SOLVER` |
| `MENXIA_ITEM_CRITIC` | `SPLIT_ITEM` | `MENXIA_ITEM_SOLVER` |
| `MENXIA_ITEM_CRITIC` | `MERGE_ITEM` | `MENXIA_ITEM_SOLVER` |
| `MENXIA_ITEM_CRITIC` | `HUMAN_GATE` | `HUMAN_GATE` |
| `MENXIA_GROUP_GATE` | `APPROVE_GROUP` | 下一个组或 `DONE` |
| `MENXIA_GROUP_GATE` | `HUMAN_GATE` | `HUMAN_GATE` |
| 任意 Agent 状态 | `TIMEOUT` | `TIMEOUT` |
| 任意状态 | 持久化失败 | `STATE_CORRUPTED` |

---

## 9. Critic ActionPolicy

Critic 的 action 不能直接等价于状态迁移，必须经过 Policy。

```python
def resolve_critic_action(
    action: str,
    findings: list[Finding],
    revision_round: int,
    max_rounds: int,
) -> ResolvedAction:
    ...
```

规则：

```text
BLOCKED + 存在 Solver 可处理 Finding + round < max
  → REQUEST_SOLVER_REVISION

BLOCKED + Finding 要求 HUMAN_GATE
  → HUMAN_GATE

BLOCKED + round >= max
  → BLOCKED

APPROVE_FREEZE + active P0/P1 > 0
  → REQUEST_SOLVER_REVISION

REQUEST_ANALYST_EVIDENCE
  → ZHONGSHU_ANALYST

REQUEST_REGROUP
  → ZHONGSHU_SOLVER
```

每次规范化都必须保存：

```text
ActionNormalization artifact
```

---

## 10. 人工门禁协议

`HUMAN_GATE` 是正式状态，不是普通函数中的隐式等待。

进入后必须保存：

```json
{
  "decision_id": "decision-xxx",
  "resume_state": "ZHONGSHU_CRITIC",
  "resume_phase": "ZHONGSHU",
  "resume_role": "critic",
  "delivery_status": "feishu_sent",
  "gate_message_id": "om_xxx",
  "status": "open"
}
```

状态合法性的必要条件：

```text
WAITING_HUMAN 必须有 active_decision_id
decision.json 必须存在且 status=open
delivery_status 必须是 feishu_sent 或 issue_fallback
```

否则必须是：

```text
HUMAN_GATE_ERROR
```

进程重启时：

```text
读取 WAITING_HUMAN
→ 读取 decision.json
→ 恢复原 gate
→ 不创建新 decision
→ 用户回复后恢复 resume_state
```

---

## 11. 超时、错误与恢复

### 11.1 Agent 超时

```text
当前状态 update 超时
→ exit
→ 保存 TimeoutEvent
→ TIMEOUT
```

`TIMEOUT` 必须包含：

```json
{
  "timeout_phase": "ZHONGSHU",
  "timeout_role": "solver",
  "timeout_request_id": "...",
  "last_artifact_id": "...",
  "recoverable": true
}
```

### 11.2 外部服务错误

```text
Multica CLI 失败 → MULTICA_ERROR
Feishu 发送失败但 Issue 成功 → WAITING_HUMAN + issue_fallback
Feishu 和 Issue 都失败 → HUMAN_GATE_ERROR
```

### 11.3 状态恢复

启动时只允许以下恢复：

```text
TIMEOUT → 恢复 timeout_role 对应状态
WAITING_HUMAN → 恢复原 decision
HUMAN_GATE_TIMEOUT → 人工重开
STATE_CORRUPTED → 停止，不自动猜测
BLOCKED → 不自动恢复，必须显式新建或人工批准
DONE → 幂等退出
```

---

## 12. 持久化与审计

所有迁移写入 append-only 事件：

```json
{
  "sequence": 18,
  "task_id": "task-xxx",
  "from_state": "ZHONGSHU_CRITIC",
  "to_state": "ZHONGSHU_SOLVER",
  "event": "REQUEST_SOLVER_REVISION",
  "request_id": "...",
  "artifact_id": "artifact-xxx",
  "created_at": "..."
}
```

持久化顺序：

```text
1. Event
2. Artifact
3. New State
4. Notification
```

不得只写 `state.json` 而不写对应事件。

建议使用临时文件 + 原子替换：

```text
state.json.tmp
→ flush
→ replace state.json
```

避免进程中断留下半个 JSON。

---

## 13. 状态追踪日志与可观测性

状态追踪日志必须和业务日志分开设计。目标不是“输出更多文字”，而是让任何一个任务都可以按照 `task_id` 完整还原：

```text
任务创建
→ 状态进入
→ 外部请求派发
→ 轮询过程
→ 回复校验
→ artifact 保存
→ 状态迁移
→ 飞书通知
→ 下一状态
```

### 13.1 统一日志字段

每条状态机日志必须是结构化 JSON，至少包含：

```json
{
  "ts": "2026-08-23T13:00:00.123Z",
  "level": "INFO",
  "event": "STATE_TRANSITION",
  "task_id": "task-20260823-xxxxxx",
  "issue_id": "multica-issue-id",
  "sequence": 18,
  "state": "ZHONGSHU_CRITIC",
  "from_state": "ZHONGSHU_SOLVER",
  "to_state": "ZHONGSHU_CRITIC",
  "phase": "ZHONGSHU",
  "role": "critic",
  "agent_id": "1a9139c5-...",
  "request_id": "task:ZHONGSHU_CRITIC:5:uuid",
  "event_id": "event-000018",
  "artifact_id": "artifact-000010",
  "decision_id": null,
  "group_id": null,
  "item_id": null,
  "duration_ms": 1200,
  "result": "success",
  "error_code": null,
  "message": "Critic reply accepted"
}
```

禁止只记录：

```text
Agent returned
继续处理
等待中
```

因为这些信息无法用于定位是哪一个任务、哪一次请求、哪一个 Agent 的结果。

### 13.2 必须记录的事件类型

事件名固定，不允许每个状态自行发明名称：

```text
TASK_CREATED
TASK_RESUMED
STATE_ENTER
STATE_UPDATE
STATE_EXIT
STATE_TRANSITION
STATE_CHECKPOINT_SAVED
STATE_CHECKPOINT_FAILED
AGENT_DISPATCH_REQUESTED
AGENT_DISPATCHED
AGENT_POLL_STARTED
AGENT_POLL_HEARTBEAT
AGENT_REPLY_RECEIVED
AGENT_REPLY_ACCEPTED
AGENT_REPLY_REJECTED
ROLE_MISMATCH
PHASE_MISMATCH
REQUEST_ID_MISMATCH
TASK_ID_MISMATCH
INVALID_AGENT_ACTION
ARTIFACT_SAVED
FINDING_CREATED
FINDING_RESOLVED
CRITIC_ACTION_NORMALIZED
HUMAN_GATE_CREATED
HUMAN_GATE_DELIVERED
HUMAN_GATE_FALLBACK_DELIVERED
HUMAN_GATE_DELIVERY_FAILED
HUMAN_DECISION_RECEIVED
HUMAN_GATE_RESUMED
AGENT_TIMEOUT
HUMAN_GATE_TIMEOUT
MULTICA_REQUEST_FAILED
FEISHU_REQUEST_FAILED
STATE_CORRUPTED
REVISION_LIMIT_REACHED
TASK_BLOCKED
TASK_DONE
```

### 13.3 状态生命周期日志

每个状态必须至少产生以下日志：

```text
STATE_ENTER
STATE_UPDATE（每次有意义的轮询/事件处理）
STATE_EXIT
STATE_TRANSITION
```

示例：

```json
{
  "event": "STATE_ENTER",
  "task_id": "task-xxx",
  "sequence": 12,
  "state": "ZHONGSHU_SOLVER",
  "phase": "ZHONGSHU",
  "role": "solver",
  "request_id": "task:ZHONGSHU_SOLVER:2:abcd",
  "active_finding_ids": ["finding-000001"],
  "group_id": null,
  "item_id": null
}
```

```json
{
  "event": "STATE_UPDATE",
  "task_id": "task-xxx",
  "sequence": 13,
  "state": "ZHONGSHU_SOLVER",
  "request_id": "task:ZHONGSHU_SOLVER:2:abcd",
  "poll_count": 18,
  "elapsed_ms": 144000,
  "received_comments": 3,
  "accepted_comments": 0,
  "rejected_comments": 1,
  "last_rejection_reason": "ROLE_MISMATCH"
}
```

```json
{
  "event": "STATE_EXIT",
  "task_id": "task-xxx",
  "sequence": 14,
  "state": "ZHONGSHU_SOLVER",
  "request_id": "task:ZHONGSHU_SOLVER:2:abcd",
  "exit_reason": "AGENT_REPLY_ACCEPTED",
  "action": "READY_FOR_CRITIC",
  "artifact_id": "artifact-000004",
  "duration_ms": 151200
}
```

### 13.4 外部调用日志

Multica 和飞书调用必须记录开始、成功、失败三类事件。

开始：

```json
{
  "event": "AGENT_DISPATCH_REQUESTED",
  "adapter": "multica",
  "operation": "issue_comment_add",
  "task_id": "task-xxx",
  "request_id": "task:ZHONGSHU_SOLVER:2:abcd",
  "agent_id": "4d0e9fab-...",
  "attempt": 1
}
```

成功：

```json
{
  "event": "AGENT_DISPATCHED",
  "adapter": "multica",
  "operation": "issue_update",
  "task_id": "task-xxx",
  "request_id": "task:ZHONGSHU_SOLVER:2:abcd",
  "agent_id": "4d0e9fab-...",
  "external_message_id": "comment-id",
  "duration_ms": 842,
  "result": "success"
}
```

失败：

```json
{
  "event": "MULTICA_REQUEST_FAILED",
  "adapter": "multica",
  "operation": "issue_comment_list",
  "task_id": "task-xxx",
  "request_id": "task:ZHONGSHU_SOLVER:2:abcd",
  "attempt": 3,
  "duration_ms": 30000,
  "error_code": "CLI_UNAVAILABLE",
  "retryable": true,
  "result": "failed"
}
```

### 13.5 回复拒绝日志

严格绑定失败必须分别记录原因，不能统一写成“无效回复”：

```json
{
  "event": "AGENT_REPLY_REJECTED",
  "task_id": "task-xxx",
  "state": "ZHONGSHU_SOLVER",
  "expected_agent_id": "4d0e9fab-...",
  "actual_author_id": "71dce355-...",
  "expected_role": "review-solver",
  "actual_role": "review-solver",
  "expected_phase": "ZHONGSHU",
  "actual_phase": "ZHONGSHU",
  "expected_request_id": "task:ZHONGSHU_SOLVER:2:new",
  "actual_request_id": "task:ZHONGSHU_SOLVER:2:new",
  "reason": "ROLE_MISMATCH",
  "comment_id": "comment-id",
  "rejected_artifact_id": "artifact-000003"
}
```

允许的 `reason`：

```text
ROLE_MISMATCH
PHASE_MISMATCH
REQUEST_ID_MISMATCH
TASK_ID_MISMATCH
INVALID_JSON
INVALID_ACTION
TRUNCATED_CONTENT
STALE_REPLY
```

### 13.6 心跳日志

心跳必须证明两件事：

```text
进程仍然活着
当前状态仍在等待什么
```

示例：

```json
{
  "event": "AGENT_POLL_HEARTBEAT",
  "task_id": "task-xxx",
  "state": "ZHONGSHU_SOLVER",
  "role": "solver",
  "request_id": "task:ZHONGSHU_SOLVER:2:abcd",
  "elapsed_ms": 120000,
  "poll_count": 15,
  "last_external_poll_at": "...",
  "next_deadline_at": "...",
  "process_id": 12345
}
```

心跳日志不能只发飞书而不落本地，因为飞书可能发送成功但本地进程随后崩溃。

### 13.7 查询方式

必须提供一个只读诊断命令：

```powershell
python review_orchestrator_v2.py --inspect-task task-20260823-xxxxxx
```

输出固定包含：

```text
当前状态
当前状态进入时间
当前 request_id
当前 Agent
当前 group/item
最近 10 次状态迁移
最近 10 个 Agent 回复（接受/拒绝）
最近错误
最近 artifact
最近 decision
是否有运行进程
是否可恢复
恢复命令
```

另外提供：

```powershell
python review_orchestrator_v2.py --replay-events task-20260823-xxxxxx
```

要求从 event log 重放状态迁移，输出：

```text
期望最终状态
state.json 最终状态
两者是否一致
不一致的 sequence
```

### 13.8 日志验收标准

以下情况均视为不合格：

```text
状态发生变化但没有 STATE_TRANSITION；
Agent 被派发但没有 AGENT_DISPATCHED；
回复被拒绝但没有具体 reason；
进入 WAITING_HUMAN 但没有 decision_id；
TIMEOUT 没有 timeout_request_id；
BLOCKED 没有 blocked_reason；
DONE 没有最终 artifact；
state.json 和 event log 的 sequence 不一致；
同一个 request_id 出现两个不同 expected_agent_id；
日志无法通过 task_id 还原完整流程。
```

---

## 14. AI 执行规则

重构后的 AI Agent 必须遵守：

1. 只读取当前 StateContext 允许的字段；
2. 只写自己的 artifact；
3. 不修改 `state.json`；
4. 不决定下一状态；
5. 不把业务结论写入不存在的 Finding；
6. 回复必须携带完整绑定字段；
7. 不返回 Markdown 代替结构化 JSON；
8. 不把 `BLOCKED` 当作普通审核不通过；
9. 方案、Finding、验证证据必须分离；
10. 缺证据时返回明确的 `REQUEST_ANALYST_EVIDENCE` 或 `HUMAN_GATE`。

---

## 15. 可执行重构任务

### Task 1：抽取数据模型

创建：

```text
orchestrator/context.py
orchestrator/events.py
```

实现：

- `StateContext`
- `AgentBinding`
- `DecisionContext`
- `Finding`
- `TransitionEvent`
- `TimeoutEvent`
- `RejectedReplyEvent`

验收：

```text
所有字段有类型；
state.json 可序列化/反序列化；
缺必填字段时明确报错。
```

### Task 2：抽取适配器

创建：

```text
orchestrator/adapters.py
```

实现接口：

```python
class MulticaAdapter:
    def dispatch(self, request: AgentRequest) -> DispatchReceipt: ...
    def poll(self, request: AgentRequest) -> list[ExternalMessage]: ...

class FeishuAdapter:
    def send_gate(self, gate: HumanGate) -> DeliveryReceipt: ...
    def poll_reply(self, gate: HumanGate) -> list[HumanReply]: ...

class Clock:
    def now(self) -> datetime: ...
```

验收：

```text
生产适配器和 Fake Adapter 均实现相同接口；
状态代码不再直接调用 subprocess/urllib。
```

### Task 3：实现统一回复校验

创建：

```text
orchestrator/validators.py
```

实现：

```python
validate_agent_reply(
    message,
    expected_binding,
) -> ValidReply | RejectedReply
```

验收：

```text
author_id、task_id、request_id、role、phase 缺一即拒绝。
```

### Task 4：实现状态基类和状态注册表

创建：

```text
orchestrator/states.py
orchestrator/state_machine.py
```

验收：

```text
所有状态都有 enter/update/exit；
状态类不能直接调用其他状态；
状态迁移只经过 TransitionPolicy。
```

### Task 5：实现 TransitionPolicy

创建：

```text
orchestrator/transitions.py
orchestrator/policies.py
```

验收：

```text
非法迁移会被拒绝；
Critic BLOCKED 的规范化规则可单测；
达到 8 次后不再自动回 Solver。
```

### Task 6：实现 Human Gate 状态

验收：

```text
没有 decision.json 不得 WAITING_HUMAN；
没有 delivery receipt 不得 WAITING_HUMAN；
进程重启可恢复原 decision；
用户回复后回到 resume_state。
```

### Task 7：迁移中书省

迁移顺序：

```text
Analyst → Solver → Critic → FreezeCheck
```

验收：

```text
所有中书省故障矩阵通过；
旧 run_zhongshu 不再包含状态迁移逻辑。
```

### Task 8：迁移门下省

迁移顺序：

```text
ItemSolver → ItemAnalyst → ItemCritic → GroupGate
```

验收：

```text
任务严格逐条执行；
双视角审查不能跳过；
组门禁不能跳过；
所有组和任务上下文可恢复。
```

### Task 9：故障模拟和回放

创建：

```text
tests/test_failure_matrix.py
```

至少模拟：

```text
Agent 超时
错误作者
错误 request_id
非法 action
空 groups
空 items
Critic BLOCKED
HUMAN_GATE 投递失败
进程在 HUMAN_GATE 中退出
state.json 损坏
Multica 暂时不可用
```

---

## 16. 验收标准

重构完成前，不得使用“可测试”结论。必须满足：

```text
1. 状态迁移表覆盖所有状态；
2. 每个状态有 enter/update/exit；
3. 每个状态有输入/输出数据表；
4. 所有 Agent 回复严格绑定；
5. Critic 不通过可以正确回 Analyst/Solver；
6. BLOCKED 只在不可恢复条件发生；
7. HUMAN_GATE 可投递、可恢复、可超时；
8. TIMEOUT 可恢复；
9. state/artifact/event 三者可互相校验；
10. 中书省和门下省故障矩阵全部通过；
11. 真实端到端测试中不再出现“状态前进但没有交付”；
12. 旧的分支式状态控制代码被删除或只保留兼容入口。
```

## 17. 审核重点

请重点审核以下设计决定：

1. 是否同意将 `HUMAN_GATE` 作为正式状态，而不是阻塞函数；
2. 是否同意 `BLOCKED` 必须经过 ActionPolicy；
3. 是否同意 Agent 回复缺少任一绑定字段就拒绝；
4. 是否同意 `state.json + event log + artifact` 三者同时作为审计依据；
5. 是否同意先重构中书省，再迁移门下省；
6. 是否同意在真实 Multica 测试前，先通过 Fake Adapter 故障矩阵；
7. 是否同意 `STATE_CORRUPTED` 不自动猜测、不自动继续。

---

## 18. v3.1 审核问题修订

本节是 v3.1 的规范性补充。实现者必须以本节和前文合并后的规则为准；如果前文与本节冲突，以本节为准。

### 18.1 完整状态迁移补充

迁移表是系统所有合法跳转的唯一真相。补充后的错误和恢复迁移如下：

| 当前状态 | 事件 | 条件 | 目标状态 | 是否终端 |
|---|---|---|---|---:|
| `HUMAN_GATE` | `HUMAN_DECISION_RECEIVED` | decision 校验通过 | `resume_state` | 否 |
| `HUMAN_GATE` | `HUMAN_DECISION_INVALID` | 选项或 decision_id 错误 | `HUMAN_GATE` | 否 |
| `HUMAN_GATE` | `HUMAN_GATE_TIMEOUT` | 达到 gate deadline | `HUMAN_GATE_TIMEOUT` | 是 |
| `HUMAN_GATE` | `DELIVERY_FAILED` | 飞书和 Issue 兜底都失败 | `HUMAN_GATE_ERROR` | 否 |
| `TIMEOUT` | `RESUME` | timeout 上下文完整且重试未超限 | 原 `timeout_state` | 否 |
| `TIMEOUT` | `RETRY_LIMIT_REACHED` | 阶段重试次数达到上限 | `BLOCKED` | 是 |
| `TIMEOUT` | `RESUME_CONTEXT_MISSING` | 找不到 request/artifact | `STATE_CORRUPTED` | 是 |
| `INVALID_AGENT_REPLY` | `RETRY` | reply_retry_count < max | 原 Agent 状态 | 否 |
| `INVALID_AGENT_REPLY` | `RETRY_LIMIT_REACHED` | reply_retry_count >= max | `BLOCKED` | 是 |
| `MULTICA_ERROR` | `RETRY` | external_retry_count < max | 原外部调用状态 | 否 |
| `MULTICA_ERROR` | `RETRY_LIMIT_REACHED` | external_retry_count >= max | `BLOCKED` | 是 |
| `FEISHU_REQUEST_FAILED` | `ISSUE_FALLBACK` | Issue 可用 | `HUMAN_GATE` | 否 |
| `FEISHU_REQUEST_FAILED` | `DELIVERY_FAILED` | Issue 也失败 | `HUMAN_GATE_ERROR` | 否 |
| `MENXIA_ITEM_CRITIC` | `REVISE_ITEM` | item_revision_round < 3 | `MENXIA_ITEM_SOLVER` | 否 |
| `MENXIA_ITEM_CRITIC` | `ITEM_REVISION_LIMIT` | item_revision_round >= 3 | `BLOCKED` | 是 |
| `ZHONGSHU_FREEZE_CHECK` | `FREEZE_REJECTED` | freeze_check_attempt < 2 | `ZHONGSHU_SOLVER` | 否 |
| `ZHONGSHU_FREEZE_CHECK` | `FREEZE_RETRY_LIMIT` | freeze_check_attempt >= 2 | `BLOCKED` | 是 |
| 任意非终端状态 | `CANCEL_REQUESTED` | task lock 获取成功 | `CANCELLED` | 是 |

禁止通过未列出的隐式 `return False` 结束状态。每个 `return` 必须对应一个明确事件和迁移。

### 18.2 进程互斥和 Task Lock

同一个 task 同时只能有一个状态机实例运行。启动、resume、cancel 和 recovery 都必须先获取：

```text
runs/<task-id>/task.lock
```

lock 内容：

```json
{
  "task_id": "task-xxx",
  "pid": 12345,
  "host": "machine",
  "owner_token": "uuid",
  "acquired_at": "...",
  "lease_until": "..."
}
```

规则：

```text
1. 同一 task 已有存活 owner → 拒绝第二个进程；
2. owner PID 不存在且 lease 已过期 → 允许接管；
3. owner PID 存活但 lease 过期 → 先记录 LOCK_STALE，再拒绝接管；
4. 进程退出时释放 lock；
5. Windows 使用独占文件句柄或 msvcrt.locking；
6. Linux 使用 flock；
7. lock 获取失败不得修改 state.json。
```

`task.lock` 是运行互斥，不是业务状态；不能因为删除 lock 文件就跳过状态恢复校验。

### 18.3 Dispatch Intent 与 enter 幂等

任何带外部副作用的状态必须采用：

```text
DISPATCH_INTENT → DISPATCH_EXECUTING → DISPATCH_CONFIRMED
```

在真正调用 Multica/飞书前先写入：

```json
{
  "dispatch_status": "pending",
  "dispatch_operation_id": "op-uuid",
  "request_id": "task:STATE:attempt:uuid",
  "idempotency_key": "task:STATE:attempt",
  "dispatch_attempt": 1
}
```

恢复规则：

```text
pending + 外部系统存在相同 idempotency_key
  → 认定已派发，补写 confirmed，不重复派发

pending + 外部系统不存在相同 idempotency_key
  → 允许重新派发

confirmed
  → 只轮询，不重复派发
```

`enter()` 必须幂等。进程在外部调用之后、确认写入之前退出，重启时不得盲目发送第二次请求。

### 18.4 统一重试策略

所有外部调用必须使用统一 `RetryPolicy`：

```json
{
  "max_retries": 3,
  "backoff": [1, 3, 9],
  "jitter": true,
  "escalation_target": "MULTICA_ERROR"
}
```

默认策略：

| 调用 | max retries | 超限目标 |
|---|---:|---|
| Multica comment add | 3 | `MULTICA_ERROR` |
| Multica comment list | 5 | 保持当前状态并记录错误 |
| Multica issue update | 3 | `MULTICA_ERROR` |
| Feishu send | 3 | Issue fallback |
| Feishu poll | 5/轮询周期 | `HUMAN_GATE_TIMEOUT` |
| state 写入 | 3 | `STATE_CORRUPTED` |

不可重试错误包括：

```text
权限拒绝
参数校验失败
身份绑定冲突
schema 不兼容
```

每次重试必须写入：

```text
RETRY_SCHEDULED
RETRY_EXECUTED
RETRY_EXHAUSTED
```

### 18.5 Freeze Check 断路器

Freeze Check 使用独立计数器：

```json
{
  "freeze_check_attempt": 1,
  "max_freeze_check_attempts": 2
}
```

规则：

```text
Freeze Check 失败且 attempt < 2
  → freeze_check_attempt + 1
  → zhongshu_revision_round + 1
  → ZHONGSHU_SOLVER

Freeze Check 失败且 attempt >= 2
  → BLOCKED
```

Freeze Check 失败必须生成：

```text
FreezeCheckFailure artifact
```

并列出具体缺失字段、group_id、item_id 和修复要求。

### 18.6 Finding 生命周期

Finding 必须使用独立状态机：

```text
OPEN
  → ASSIGNED_TO_ANALYST
  → ASSIGNED_TO_SOLVER
  → IN_REVIEW
  → RESOLVED

IN_REVIEW
  → REOPENED
  → HUMAN_DECISION_REQUIRED
  → WONT_FIX
  → DEFERRED
```

字段：

```json
{
  "finding_id": "finding-000001",
  "severity": "P1",
  "status": "ASSIGNED_TO_SOLVER",
  "owner_role": "review-solver",
  "source_phase": "ZHONGSHU",
  "current_phase": "ZHONGSHU",
  "parent_finding_id": null,
  "revision_round": 2,
  "resolution": null,
  "supporting_evidence": [],
  "verification": [],
  "remaining_risk": null
}
```

列表含义固定为：

```text
active_finding_ids
  = status 为 OPEN/ASSIGNED/IN_REVIEW/REOPENED 的 Finding

pending_solver_finding_ids
  = owner_role 为 Solver 且尚未 RESOLVED 的 Finding

resolved_finding_ids
  = status 为 RESOLVED/WONT_FIX/DEFERRED 的 Finding
```

Solver 修复 Finding 时必须按 `finding_id` 合并，不得整体覆盖：

```text
旧 Finding 1 → RESOLVED
旧 Finding 2 → RESOLVED
新 Finding 3 → OPEN
```

中书省 Finding 可以被门下省引用，但不能直接视为门下省已解决。门下省必须重新记录实现影响、验证结果和剩余风险。

### 18.7 取消和中止

新增终端状态：

```text
CANCELLED
```

新增命令：

```powershell
python review_orchestrator_v2.py --cancel task-xxx
```

取消流程：

```text
CANCEL_REQUESTED
→ 获取 task.lock
→ 停止当前 update/轮询
→ 不再派发新请求
→ 标记 active request cancelled
→ 保存 CancellationEvent
→ CANCELLED
```

终端状态不可取消：

```text
DONE
BLOCKED
CANCELLED
```

### 18.8 Schema Migration

当前 schema 版本定义为：

```text
CURRENT_SCHEMA_VERSION = "3.1"
```

启动策略：

```text
version < current
  → 按版本逐步执行 migration

version == current
  → 正常启动

version > current
  → 拒绝启动，STATE_CORRUPTED
```

迁移要求：

```text
1. 备份旧 state；
2. 执行单版本迁移；
3. 校验迁移后 schema；
4. 写入 SchemaMigrationEvent；
5. 更新 state.schema_version；
6. 迁移失败不得覆盖原文件。
```

### 18.9 Sequence 生成与恢复

定义：

```text
event log = 审计事实来源
state.json = 当前状态物化快照
```

启动时计算：

```text
next_sequence =
max(
  last_valid_event_sequence,
  state.sequence
) + 1
```

一致性规则：

```text
event sequence == state sequence
  → 正常

event sequence > state sequence
  → 使用 event log 重建 state 快照

state sequence > event sequence
  → STATE_CORRUPTED，不自动猜测

event sequence 存在间隙
  → STATE_CORRUPTED
```

所有 sequence 分配必须持有 task.lock，禁止两个进程同时生成相同 sequence。

### 18.10 v3.1 验收新增项

在原有验收标准基础上新增：

```text
1. 同一个 task 启动两个进程时，只有一个成功获得 task.lock；
2. enter 在派发后崩溃，resume 不会重复派发；
3. HUMAN_GATE 用户回复后能回到 resume_state；
4. HUMAN_GATE 超时能进入 HUMAN_GATE_TIMEOUT；
5. TIMEOUT 能恢复对应状态；
6. INVALID_AGENT_REPLY 达到上限后进入 BLOCKED；
7. MULTICA_ERROR 达到上限后进入 BLOCKED；
8. MENXIA_ITEM_CRITIC 达到 3 次后进入 BLOCKED；
9. Freeze Check 达到 2 次后进入 BLOCKED；
10. Finding 合并不会丢失旧 Finding；
11. --cancel 可从任意非终端状态进入 CANCELLED；
12. 旧 schema 可以迁移，新 schema 可以拒绝不兼容版本；
13. event log 与 state sequence 不一致时行为符合规定；
14. 所有上述场景都有 Fake Adapter 自动化测试。
```
