# 门下阶段并发化与增量 Prompt 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不破坏单个 item 内部 `Solver → Analyst → Critic` 依赖的前提下，并发执行独立 item/group，同时将 Solver 输入收敛为当前 item 的增量上下文，显著缩短门下阶段墙钟时间。

**Architecture:** 中书省保持现有单线程 FSM。中书冻结后，创建一个门下执行协调器；协调器为每个 item 保存独立的运行状态和 request_id，只允许同一 item 按 `Solver → Analyst → Critic` 前进，不同 item/group 通过有界 worker 并发推进。Solver Prompt 由不可变的 item 基础信息、当前 evidence、当前 review finding 和上一版 proposal 组成，不再复制完整 frozen plan、其他 item 或完整历史评论。

**Tech Stack:** Python 3、现有 `StateContext`/`StateMachine`、`MulticaAdapter`、JSON 原子持久化、`unittest`，不引入外部队列或新的运行时依赖。

---

## 1. 现状与目标边界

当前实现的门下路径位于：

- `cmd/orchestrator/app.py`
  - `_consume_agent_payload`
  - `_freeze_check_event`
  - `_set_active_group_item`
  - `_next_event`
- `cmd/orchestrator/states.py`
  - `BaseState.dispatch`
  - `BaseState.update`
  - `_menxia_item_solver_capsule`
  - `_menxia_item_solver_prompt`
- `cmd/orchestrator/context.py`
  - `StateContext.active_group_id`
  - `StateContext.active_item_id`
  - `StateContext.request_payload`
- `cmd/orchestrator/transitions.py`
  - `MENXIA_ITEM_SOLVER`
  - `MENXIA_ITEM_ANALYST`
  - `MENXIA_ITEM_CRITIC`
  - `MENXIA_GROUP_GATE`

日志证据显示，当前门下执行仍然是单 item 串行推进；单个 Solver Prompt 仍可达到约 36 KB。此次改造只处理两个目标：

1. 门下 item/group 有界并发；
2. Solver Prompt 增量化。

不在本计划内修改：

- 中书阶段的 Agent 顺序；
- Critic 业务规则；
- 飞书通知格式；
- 全局配置和模型路由；
- `C:\Users\Administrator\.codex\config.toml`；
- `C:\Users\Administrator\.codex\nexus-model-catalog.json`。

## 2. 关键不变量

实现必须保持以下约束：

1. 同一个 item 内不能并发执行两个阶段。
2. 每个 item 使用独立的 `request_id`、`dispatch_status`、超时和重试计数。
3. 一个 item 的异常只能将该 item 标记为 `BLOCKED`，不能覆盖其他 item 的 proposal/review。
4. 最终汇总必须按 frozen plan 的原始 `group`/`item` 顺序输出，不能按完成顺序输出。
5. 每次 Agent 回复必须仍然经过现有关联校验和契约校验。
6. 并发协调器重启后可以从持久化状态恢复，不得重复派发已确认且仍在等待回复的 request。
7. Prompt 中必须保留当前 item 的 objective、evidence、review finding、上一版 proposal 和输出契约。

## 3. 文件职责

计划修改/新增以下文件：

- 新增：`cmd/orchestrator/menxia_parallel.py`
  - 门下并发协调器、item runtime、fan-out/fan-in。
- 修改：`cmd/orchestrator/context.py`
  - 保存门下并发运行快照。
- 修改：`cmd/orchestrator/app.py`
  - 中书冻结后进入并发协调器；协调器完成后进入最终 Group Gate/Done。
- 修改：`cmd/orchestrator/states.py`
  - 抽取增量 Prompt 构造函数；保留现有单 item 路径作为兼容模式。
- 修改：`cmd/orchestrator/persistence.py`
  - 确认新增并发状态随现有 JSON 状态原子保存。
- 新增/修改测试：
  - `cmd/test_menxia_parallel.py`
  - `cmd/test_menxia_item_prompt.py`
  - `cmd/test_full_workflow_v31.py`
  - `cmd/test_runtime_diagnostics_v31.py`

## Task 1: 先固定增量 Prompt 契约

**Files:**

- Modify: `cmd/orchestrator/states.py`
- Test: `cmd/test_menxia_item_prompt.py`

- [ ] **Step 1: 增加 Prompt 输入契约测试**

测试必须构造包含多个 group、多个 item、完整 `frozen_plan` 和历史 review 的 `StateContext`，调用 `_menxia_item_solver_capsule(ctx)`，断言：

```python
capsule = _menxia_item_solver_capsule(ctx)
inputs = capsule["inputs"]

assert set(inputs) == {
    "group",
    "item",
    "evidence",
    "analyst_review",
    "critic_review",
    "previous_item_proposal",
}
assert capsule["scope"] == {
    "group_id": "group-000001",
    "item_id": "item-000001",
}
assert "frozen_plan" not in json.dumps(capsule, ensure_ascii=False)
assert "item-000002" not in json.dumps(capsule, ensure_ascii=False)
```

- [ ] **Step 2: 固定字段裁剪规则**

在 `states.py` 中将 `_menxia_item_solver_capsule` 的输入固定为：

```python
{
    "group": _pick_dict(
        active_group,
        ("group_id", "title", "objective", "dependencies",
         "suggested_order", "shared_acceptance"),
    ),
    "item": active_item,
    "evidence": _item_evidence(request_payload, active_item),
    "analyst_review": current_item_analyst_review,
    "critic_review": current_item_critic_review,
    "previous_item_proposal": current_item_proposal,
}
```

`item` 只允许当前 item 的字段；禁止把整个 `groups`、`candidate_plan`、`frozen_plan`、`notification_payload` 放入 Prompt。

- [ ] **Step 3: 增加 Prompt 大小断言和来源日志**

保留现有 `AGENT_PROMPT_BUILT`，增加：

```text
prompt_schema=MENXIA_ITEM_SOLVER_V2
base_item_bytes
evidence_bytes
analyst_review_bytes
critic_review_bytes
previous_proposal_bytes
```

日志只记录字节数、布尔值、finding 数量和 ID，不记录业务正文。测试要求一个普通 item 的 fixture Prompt 小于 12 KB。

## Task 2: 实现增量 Prompt 和 revision delta

**Files:**

- Modify: `cmd/orchestrator/states.py`
- Test: `cmd/test_menxia_item_prompt.py`
- Test: `cmd/test_solver_revision_prompt.py`

- [ ] **Step 1: 增加统一的 item delta 构造函数**

新增函数签名：

```python
def _menxia_item_prompt_delta(ctx: StateContext) -> dict[str, Any]:
    ...
```

返回值必须包含：

```python
{
    "task_id": ctx.task_id,
    "phase": "MENXIA",
    "scope": {
        "group_id": ctx.active_group_id,
        "item_id": ctx.active_item_id,
    },
    "objective": current_item_objective,
    "inputs": {
        "group": trimmed_group,
        "item": current_item,
        "evidence": current_evidence,
        "analyst_review": current_analyst_review,
        "critic_review": current_critic_review,
        "previous_item_proposal": current_proposal,
    },
    "required_output": {
        "action": ["FEASIBLE", "READY_FOR_CRITIC", "HUMAN_GATE", "BLOCKED"],
        "must_preserve_scope": True,
        "must_answer_findings": True,
    },
}
```

- [ ] **Step 2: 将 `_menxia_item_solver_prompt` 改为只序列化 delta**

Prompt 主体保留输出契约，但数据区只能序列化 `_menxia_item_prompt_delta(ctx)`：

```python
def _menxia_item_solver_prompt(ctx: StateContext) -> str:
    delta = _menxia_item_prompt_delta(ctx)
    return (
        "You are solving exactly one MENXIA item.\n"
        "Do not solve other items or rewrite the frozen plan.\n"
        "Return one structured JSON reply for this item only.\n"
        "Every critic finding must have a finding_resolution entry.\n"
        + json.dumps(delta, ensure_ascii=False, separators=(",", ":"))
    )
```

- [ ] **Step 3: revision 只传 finding delta**

当当前 item 存在 Critic finding 时，`critic_review` 只保留：

```python
{
    "finding_id": finding_id,
    "severity": severity,
    "title": title,
    "required_action": required_action,
    "next_action": next_action,
    "previous_resolution": previous_resolution,
    "must_answer": True,
}
```

不得再次嵌入完整 Critic payload 或全局历史。测试覆盖 4 个 finding 时仍能逐条保留 ID 和要求。

## Task 3: 建立可持久化的门下 item runtime

**Files:**

- Add: `cmd/orchestrator/menxia_parallel.py`
- Modify: `cmd/orchestrator/context.py`
- Test: `cmd/test_menxia_parallel.py`

- [ ] **Step 1: 增加独立 runtime 数据结构**

在 `menxia_parallel.py` 定义：

```python
@dataclass
class MenxiaItemRuntime:
    group_id: str
    item_id: str
    group_index: int
    item_index: int
    state: str = "MENXIA_ITEM_SOLVER"
    status: str = "PENDING"
    request_id: str = ""
    trigger_comment_id: str = ""
    dispatch_status: str = "none"
    attempt: int = 0
    revision_round: int = 0
    started_at: str = ""
    updated_at: str = ""
    proposal: dict[str, Any] = field(default_factory=dict)
    analyst_review: dict[str, Any] = field(default_factory=dict)
    critic_review: dict[str, Any] = field(default_factory=dict)
    last_error: dict[str, Any] = field(default_factory=dict)
```

`status` 只允许 `PENDING`、`RUNNING`、`WAITING_REPLY`、`APPROVED`、`BLOCKED`。

- [ ] **Step 2: 增加协调器接口**

```python
class MenxiaParallelCoordinator:
    def __init__(
        self,
        ctx: StateContext,
        states: dict[str, State],
        max_groups: int = 2,
        max_items: int = 3,
    ) -> None:
        ...

    def initialize_from_frozen_plan(self) -> None:
        ...

    def tick(self) -> CoordinatorResult:
        ...

    def is_complete(self) -> bool:
        ...

    def snapshot(self) -> dict[str, Any]:
        ...
```

协调器不直接绕过 `BaseState.update`、`validate_agent_reply` 或 `TransitionPolicy`。它只负责选择可运行的 item，并将 item runtime 映射到隔离的 child `StateContext`；每个 child context 的事件仍按现有状态校验流程处理。

- [ ] **Step 3: 为每个 item 创建独立 request_id**

格式固定为：

```text
{task_id}:MENXIA:{group_id}:{item_id}:{state}:{attempt}:{random_suffix}
```

同一个 item 的旧 request_id 在新 attempt 开始时标记为 stale；回复关联仍然只接受当前 runtime 的 request_id/trigger thread。

- [ ] **Step 4: 增加并发调度规则**

`tick()` 的规则固定为：

```python
while running_groups < max_groups and pending_group_exists():
    start_next_group()

while running_items < max_items and pending_item_exists_in_running_groups():
    start_next_item()

for runtime in running_items:
    poll_one_runtime(runtime)

if all_items_terminal():
    return CoordinatorResult.FAN_IN_READY
return CoordinatorResult.WAITING
```

同一 item 的下一阶段只能在上一阶段返回合法 action 后启动。不同 item 不共享 proposal/review 字典。

## Task 4: 集成 app 状态机和 fan-in

**Files:**

- Modify: `cmd/orchestrator/app.py`
- Modify: `cmd/orchestrator/state_machine.py`
- Modify: `cmd/orchestrator/context.py`
- Test: `cmd/test_full_workflow_v31.py`

- [ ] **Step 1: 冻结成功后初始化协调器**

在 `_freeze_check_event()` 成功保存 `frozen_plan` 后，不再只调用 `_set_active_group_item(first_group, 0)` 作为唯一执行入口；改为写入：

```python
self.ctx.request_payload["menxia_parallel"] = {
    "enabled": True,
    "max_groups": 2,
    "max_items": 3,
    "runtimes": [],
}
```

然后由协调器创建全部 group/item runtime。

- [ ] **Step 2: 在 app 主循环中增加协调器 tick**

当 `ctx.request_payload["menxia_parallel"]["enabled"] is True` 且工作流进入门下时，`OrchestratorApp._next_event()` 调用协调器 `tick()`，而不是只处理单个 `active_item_id`。

返回值映射：

```text
WAITING       → NOOP
ITEM_UPDATED  → AGENT_REPLY_ACCEPTED（内部事件，不发送重复状态通知）
FAN_IN_READY  → AGENT_REPLY_ACCEPTED(action=MENXIA_FAN_IN_READY)
FATAL_ERROR   → MULTICA_ERROR
```

- [ ] **Step 3: 将 fan-in 结果按原计划顺序合并**

协调器完成后，生成：

```python
ctx.request_payload["menxia_results"] = [
    {
        "group_id": group_id,
        "items": [
            {
                "item_id": item_id,
                "proposal": proposal,
                "analyst_review": analyst_review,
                "critic_review": critic_review,
                "status": status,
            }
        ],
    }
]
```

排序使用 frozen plan 的 `group_index` 和 `item_index`，禁止使用完成时间排序。

- [ ] **Step 4: 保留单 item 兼容模式**

当配置缺少 `menxia_parallel` 或 `enabled=False` 时，继续走现有 `_set_active_group_item` 串行逻辑。这样可以先用开关进行 canary，不改变已有任务的恢复格式。

## Task 5: 持久化、恢复与错误隔离

**Files:**

- Modify: `cmd/orchestrator/context.py`
- Modify: `cmd/orchestrator/persistence.py`
- Add: `cmd/test_menxia_parallel.py`

- [ ] **Step 1: 将 runtime 快照纳入 `StateContext.to_dict/from_dict`**

新增字段：

```python
menxia_parallel: dict[str, Any] = field(default_factory=dict)
```

序列化必须包含每个 runtime 的 `state/status/request_id/attempt/proposal/review/last_error`。

- [ ] **Step 2: 实现恢复幂等**

恢复时按以下顺序处理：

1. `APPROVED`/`BLOCKED` runtime 不再派发；
2. `WAITING_REPLY` 且 `dispatch_status=confirmed` 的 runtime 复用原 request_id；
3. `RUNNING` 但没有 confirmed dispatch 的 runtime 回退为 `PENDING`；
4. 不允许因进程重启生成第二个同 item request；
5. 收到 stale reply 时记录并丢弃，不修改当前 runtime。

- [ ] **Step 3: 测试失败隔离**

测试场景：

```text
item-000001 → APPROVED
item-000002 → BLOCKED
item-000003 → WAITING_REPLY
```

断言 item-000002 的失败不会清空 item-000001 proposal，也不会阻止 item-000003 继续等待/完成；只有 Group Gate 根据明确策略决定最终是否阻塞。

## Task 6: 回归、性能和 canary 验证

**Files:**

- Add/Modify: `cmd/test_menxia_parallel.py`
- Modify: `cmd/test_full_workflow_v31.py`
- Modify: `cmd/test_menxia_item_prompt.py`

- [ ] **Step 1: 运行现有定向测试**

从 `D:\workspace\src\nexus-agents\cmd` 执行：

```powershell
python test_menxia_item_prompt.py -v
python test_menxia_parallel.py -v
python test_full_workflow_v31.py -v
python test_multica_poll_filter_v31.py -v
python test_notifications_and_gate_v31.py -v
```

预期：全部通过，且原有回复关联和通知测试结果不变。

- [ ] **Step 2: 增加并发行为测试**

使用 Fake Multica adapter 为 2 个 group、每个 3 个 item 注入不同完成时间，断言：

```python
max_active_items <= 3
max_active_groups <= 2
same_item_stage_overlap == 0
fan_in_order == frozen_plan_order
```

- [ ] **Step 3: 增加 Prompt 预算测试**

测试必须记录：

```text
prompt_chars
prompt_bytes
base_item_bytes
evidence_bytes
analyst_review_bytes
critic_review_bytes
previous_proposal_bytes
```

固定验收线：

```text
普通 item Prompt < 12 KB
带 4 个 finding 的修订 Prompt < 16 KB
不包含其他 item_id
不包含完整 frozen_plan
```

- [ ] **Step 4: 通过开关进行 canary**

第一轮只打开：

```text
menxia_parallel.enabled=true
max_groups=1
max_items=2
```

验证以下指标：

- 单 item 阶段顺序正确；
- 多 item 没有交叉 proposal；
- 重启恢复不重复派发；
- stale reply 不污染当前 item；
- fan-in 顺序稳定；
- Prompt 大小下降。

第二轮再打开：

```text
max_groups=2
max_items=3
```

只有两轮均通过后才允许扩大并发度。

## 4. 验收标准

实现完成后必须满足：

1. 2 个以上 item 时，item pipeline 可以并发运行；
2. 同一 item 的 Solver、Analyst、Critic 仍然严格串行；
3. 一个 item 的失败不会覆盖其他 item 的结果；
4. 进程重启不会重复派发 confirmed request；
5. 所有回复仍然经过现有关联和契约校验；
6. 普通 item Prompt 小于 12 KB；
7. Critic 修订 Prompt 小于 16 KB；
8. 原有中书、通知、关联测试全部通过；
9. 日志能回答每个 item 的：
   - 开始时间；
   - 当前阶段；
   - request_id；
   - attempt；
   - Prompt 字节数；
   - 完成/阻塞原因；
   - fan-in 是否已收齐。

## 5. 风险与回滚

主要风险是并发状态持久化和共享 `StateContext` 写入冲突。解决方式不是给现有全局 context 加锁，而是将每个 item 隔离为 child runtime，并由主 context 只保存快照和汇总结果。

回滚方式：

```text
menxia_parallel.enabled=false
```

关闭后继续使用现有单 item 串行流程；不删除并发快照，便于问题定位。只有确认新版本数据格式稳定后，才清理兼容代码。

本计划不要求修改模型路由、Codex 配置或 Agent 全局 Skill。Prompt 变化限制在 Orchestrator 生成的门下 item 输入，避免引入新的 Agent 侧契约冲突。


---

# 开工前补齐版：并发、持久化与回复解析约束

> 本补充条款优先于本文前文中相互冲突或不完整的描述。未满足 P0 条款前，不得进入 app.py 集成和真实 Multica canary。

## A. 最终架构决定

采用“单进程 coordinator + 有限并发外部请求 + 单写入者持久化”模型：

~~~text
OrchestratorApp 主循环
        |
        v
MenxiaParallelCoordinator
        |
  +-----+-----+
  |           |
item runtime item runtime ...
  |           |
child state machine / adapter request
        |
        v
RuntimeUpdate 只返回结果，不写共享状态
        |
        v
coordinator 串行合并并持久化
~~~

明确禁止：

- worker 直接修改父 StateContext
- worker 直接写 state.json
- worker 直接追加 events.jsonl
- 多个 child StateMachine 共享同一个可变 request_payload
- 使用多个线程同时对同一个 item 派发请求

第一阶段可以并发派发/轮询不同 item，但结果必须回到 coordinator 单线程提交。若 adapter 不支持安全并发，则使用批量拉取或顺序 poll，不以增加线程为目标。

## B. 并发上限与调度不变量

配置字段统一命名为：

~~~python
max_concurrent_groups: int = 1
max_concurrent_items: int = 2
~~~

旧名称 max_groups、max_items 只作为读取配置时的兼容别名，不得继续作为新代码内部字段。

每次 tick() 必须满足：

~~~python
active_group_ids = {
    r.group_id for r in runtimes
    if r.execution_status in {"RUNNING", "WAITING_REPLY", "RETRYING"}
}
active_items = [
    r for r in runtimes
    if r.execution_status in {"RUNNING", "WAITING_REPLY", "RETRYING"}
]

assert len(active_group_ids) <= max_concurrent_groups
assert len(active_items) <= max_concurrent_items
~~~

max_concurrent_items 是整个 task 的全局上限，不是每个 group 的上限。

### B.1 Group readiness

启动 group 前必须同时满足：

~~~python
def group_is_ready(group_id: str) -> bool:
    group = plan_group(group_id)
    return (
        group_status(group_id) == "PENDING"
        and all(group_status(dep) == "APPROVED" for dep in group_dependencies(group))
    )
~~~

无依赖的 group 视为 ready。suggested_order 只作为无依赖 group 的稳定 tie-breaker，不能绕过依赖关系。

### B.2 Item readiness

~~~python
def item_is_ready(runtime: MenxiaItemRuntime) -> bool:
    return (
        runtime.execution_status == "PENDING"
        and group_status(runtime.group_id) == "RUNNING"
        and all(item_status(dep) == "APPROVED" for dep in item_dependencies(runtime))
    )
~~~

同一 item 同时只能存在一个 active stage 和一个 active request。

## C. Runtime 数据结构

MenxiaItemRuntime 使用以下字段，state、execution_status、outcome 三者不得混用：

~~~python
@dataclass
class MenxiaItemRuntime:
    group_id: str
    item_id: str
    group_index: int
    item_index: int
    plan_revision_id: str

    state: str = "MENXIA_ITEM_SOLVER"
    execution_status: str = "PENDING"
    outcome: str | None = None

    request_id: str = ""
    dispatch_idempotency_key: str = ""
    dispatch_operation_id: str = ""
    dispatch_external_message_id: str = ""
    dispatch_status: str = "none"
    sent_after: str = ""

    attempt: int = 0
    revision_round: int = 0
    reply_retry_count: int = 0
    external_retry_count: int = 0
    timeout_retry_count: int = 0

    started_at: str = ""
    updated_at: str = ""
    last_error: dict[str, Any] = field(default_factory=dict)

    proposal: dict[str, Any] = field(default_factory=dict)
    analyst_review: dict[str, Any] = field(default_factory=dict)
    critic_review: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)

    active_decision_id: str = ""
    human_gate: dict[str, Any] = field(default_factory=dict)
    resume_state: str = ""
~~~

合法值：

~~~text
state:
  MENXIA_ITEM_SOLVER
  MENXIA_ITEM_ANALYST
  MENXIA_ITEM_CRITIC
  HUMAN_GATE
  TIMEOUT
  INVALID_AGENT_REPLY
  MULTICA_ERROR

execution_status:
  PENDING
  RUNNING
  WAITING_REPLY
  RETRYING
  TERMINAL

outcome:
  APPROVED
  BLOCKED
  CANCELLED
  null
~~~

只有 execution_status=TERMINAL 且 outcome 属于 APPROVED、BLOCKED、CANCELLED 时，才能进入 fan-in。

## D. 内部事件与状态机集成

不得把 coordinator 的内部结果伪装成 AGENT_REPLY_ACCEPTED。新增内部事件：

| 事件 | 产生方 | 处理结果 |
|---|---|---|
| MENXIA_ITEM_RUNTIME_UPDATED | coordinator | 合并一个 item 的已验证结果 |
| MENXIA_GROUP_READY | coordinator | 进入该 group 的 Group Gate |
| MENXIA_FAN_IN_READY | coordinator | 进入最终 Group Gate / Done 判断 |
| MENXIA_COORDINATOR_ERROR | coordinator | 进入 MULTICA_ERROR 或 BLOCKED |
| MENXIA_STALE_REPLY_DROPPED | reply router | 只记录事件，不改变 runtime |

AGENT_REPLY_ACCEPTED 只允许表示真实 Agent 回复已经通过现有校验。

TransitionPolicy 必须显式覆盖：

~~~text
ZHONGSHU_FREEZE_CHECK + FREEZE_OK
  -> MENXIA_COORDINATOR

MENXIA_COORDINATOR + MENXIA_GROUP_READY
  -> MENXIA_GROUP_GATE

MENXIA_COORDINATOR + MENXIA_FAN_IN_READY
  -> MENXIA_GROUP_GATE

MENXIA_GROUP_GATE + APPROVE_GROUP
  -> MENXIA_COORDINATOR 或 DONE

MENXIA_GROUP_GATE + REQUEST_GROUP_REVISION
  -> 仅重置受影响 item runtime 后回到 MENXIA_COORDINATOR
~~~

如果不新增顶层 MENXIA_COORDINATOR 状态，也必须在 OrchestratorApp._next_event() 中建立等价的显式协调器分支，不能依靠父 ctx 当前的 item state 推断 fan-in。

## E. 单写入者持久化协议

StateContext 只保留一个 canonical coordinator snapshot：

~~~python
ctx.menxia_parallel: dict[str, Any]
~~~

不得同时把同一份运行状态作为 ctx.menxia_parallel 和 ctx.request_payload["menxia_parallel"]。

worker 返回不可变结果：

~~~python
@dataclass(frozen=True)
class RuntimeUpdate:
    task_id: str
    group_id: str
    item_id: str
    plan_revision_id: str
    request_id: str
    state: str
    event: str
    payload: dict[str, Any]
    observed_at: str
~~~

coordinator 合并前必须校验 task_id、plan_revision_id、group_id、item_id、request_id 和 state 均与当前 runtime 匹配。校验失败的 update 只写诊断事件，不修改 runtime。

### E.1 提交顺序

~~~text
1. coordinator 获取 TaskLock
2. 读取当前 snapshot
3. 合并一批 RuntimeUpdate
4. 校验 runtime 不变量和 request_id 唯一性
5. append events.jsonl
6. 原子写 state.json
7. flush + fsync
8. 释放 TaskLock
~~~

worker 不得直接写 state.json 或 events.jsonl。

### E.2 冲突保护

StateContext 增加：

~~~python
state_version: int = 0
~~~

保存时使用 expected version。版本不匹配时抛出 PersistenceConflict，coordinator 必须重新加载、重新合并尚未提交的 RuntimeUpdate，禁止后写覆盖先写。

events.jsonl 的 sequence 必须由 coordinator 单点生成，不能由 worker 预先分配。

## F. request、幂等与恢复

request_id 用于回复关联，可以包含随机后缀：

~~~text
{task_id}:MENXIA:{group_id}:{item_id}:{state}:{attempt}:{random_suffix}
~~~

dispatch_idempotency_key 不得包含随机值：

~~~text
{task_id}:MENXIA:{plan_revision_id}:{group_id}:{item_id}:{state}:{revision_round}:{attempt}
~~~

规则：

1. 同一个 item + state + revision_round + attempt 只允许一个幂等 key。
2. 进程重启后优先复用已持久化的幂等 key。
3. dispatch_status != confirmed 不等于远端未执行。
4. 恢复前必须调用 find_existing_request() 查询远端。
5. 只有确认远端不存在时，才允许重新派发。
6. dispatch_external_message_id、sent_after、request_id 必须按 runtime 保存。

恢复矩阵：

| 快照状态 | 恢复动作 |
|---|---|
| TERMINAL | 不派发，只参与 fan-in |
| WAITING_REPLY + confirmed | 复用原 request_id poll |
| RUNNING + confirmed | 转为 WAITING_REPLY，复用原 request_id |
| RUNNING + executing | 先查幂等记录，不能直接重派 |
| PENDING + 无 request_id | 创建新 attempt 和新 request |
| MULTICA_ERROR | 按 external retry policy 处理 |
| TIMEOUT | 按 timeout retry policy 处理 |

## G. LLM 回复提取与验收

回复协议固定为一个 JSON object：

~~~json
{
  "protocol_version": "MENXIA_ITEM_V2",
  "task_id": "task-000001",
  "group_id": "group-000001",
  "item_id": "item-000001",
  "request_id": "...",
  "phase": "MENXIA",
  "state": "MENXIA_ITEM_SOLVER",
  "action": "FEASIBLE",
  "payload": {
    "proposal": {},
    "finding_resolutions": []
  }
}
~~~

解析顺序固定为：

~~~text
1. 去除 BOM 和首尾空白
2. 允许纯 JSON 或单个 json 代码块
3. 禁止前置/后置自然语言
4. 禁止多个 JSON 对象
5. JSON 顶层必须是 object
6. 校验 protocol_version
7. 校验 task_id/group_id/item_id/request_id
8. 校验 author_id、phase、state
9. 校验当前 state 的 action
10. 校验业务 payload
11. 校验 finding_resolution 与当前 finding 一一对应
12. 通过后才生成 RuntimeUpdate
~~~

解析失败分类固定为：

~~~text
MALFORMED_JSON
MULTIPLE_JSON_OBJECTS
UNSUPPORTED_REPLY_WRAPPER
MISSING_ENVELOPE_FIELD
AUTHOR_MISMATCH
TASK_MISMATCH
GROUP_MISMATCH
ITEM_MISMATCH
REQUEST_MISMATCH
STALE_REPLY
INVALID_ACTION
INVALID_STATE_PAYLOAD
FINDING_NOT_RESOLVED
~~~

自动从解释文字中的 JSON 中猜测并归属当前 request 的行为禁止用于推进状态。此类内容只能进入诊断日志或 AGENT_REPLY_REJECTED。

### G.1 Solver action 统一

新协议统一使用：

~~~text
FEASIBLE
READY_FOR_ANALYST
HUMAN_GATE
BLOCKED
~~~

如果继续兼容旧的 READY_FOR_CRITIC，必须在 validator 中标记为 legacy，并在测试中证明不会绕过 Analyst。Prompt、validator、TransitionPolicy 三处必须由同一常量表生成。

## H. Prompt 数据隔离与预算

当前 item 采用递归 allowlist，不得直接序列化 active_item：

~~~python
GROUP_FIELDS = (
    "group_id", "title", "objective", "dependencies",
    "suggested_order", "shared_acceptance",
)
ITEM_FIELDS = (
    "item_id", "title", "objective", "requirements",
    "dependencies", "acceptance", "suggested_order",
)
~~~

禁止出现在 item Solver Prompt 的 key：

~~~text
groups
candidate_plan
frozen_plan
notification_payload
human_decisions
full_history
other_item_proposals
other_item_reviews
~~~

预算使用 UTF-8 bytes：

~~~python
NORMAL_PROMPT_MAX_BYTES = 12 * 1024
REVISION_PROMPT_MAX_BYTES = 16 * 1024
~~~

超限时必须优先保留 task_id、group_id、item_id、objective、acceptance、finding_id、severity、required_action；优先裁剪 evidence detail 和历史描述。仍超限时返回 PROMPT_BUDGET_EXCEEDED，不得静默发送超限 Prompt。

## I. Group Gate 与失败策略

Group 必须配置明确的失败策略：

~~~text
fail_fast
continue_and_block_group
allow_partial
~~~

默认使用 continue_and_block_group：

- BLOCKED item 不清除其他 item 的 proposal/review
- 其他 item 可以继续运行
- Group Gate 默认不能批准该 group
- 只有显式 allow_partial 才允许部分交付
- fail_fast 只停止同一 group 的未启动 item，不影响其他无依赖 group

Group revision 必须生成新的 plan_revision_id。旧 revision 下的 request/reply 全部标记 stale。

## J. 必须新增的测试

### J.1 并发调度

~~~python
assert max_active_groups <= config.max_concurrent_groups
assert max_active_items <= config.max_concurrent_items
assert same_item_max_active_requests[item_id] == 1
assert fan_in_order == frozen_plan_order
~~~

### J.2 依赖

~~~python
assert group_b.started_at >= group_a.approved_at
~~~

当 A 被 BLOCKED 时，依赖 A 的 B 不得启动。

### J.3 写入保护

模拟两个 worker 同时产生 update，验证两个 item 的 proposal 都保留、events sequence 连续、没有重复 request、没有覆盖其他 item 的 findings。

### J.4 崩溃恢复

覆盖 dispatch 成功但 snapshot 尚未保存、snapshot 保存成功但 event 尚未写入、event 写入成功后进程退出、远端执行成功但本地收到超时等故障点。

验收：不会重复派发、不会丢失 proposal、不会丢失 APPROVED/BLOCKED、不会生成第二个同 item request。

### J.5 回复解析

至少覆盖：纯 JSON、单个 json 代码块、BOM JSON、前后空白、前置解释文字拒绝、后置解释文字拒绝、多个 JSON 拒绝、非法 JSON 拒绝、缺少 request_id 拒绝、错误 group_id 拒绝、错误 item_id 拒绝、旧 request_id 记录 stale 且不修改 runtime、合法 JSON 但缺少 payload 拒绝、合法 action 但当前 state 不允许拒绝、4 个 finding 全部逐项 resolution 通过。

## K. 开工闸门

只有以下条件全部满足，才允许接入真实 app：

~~~text
1. Prompt allowlist 和 bytes budget 测试通过
2. action 契约在 prompt/validator/transition 三处一致
3. 纯内存 coordinator 测试通过
4. group dependencies 测试通过
5. max_concurrent_groups/max_concurrent_items 测试通过
6. RuntimeUpdate 单写入测试通过
7. state_version 冲突测试通过
8. request 幂等和重启恢复测试通过
9. Human Gate item 隔离测试通过
10. LLM 回复解析矩阵测试通过
11. 现有 v31 回归测试全部通过
12. canary 配置为 max_concurrent_groups=1, max_concurrent_items=2
~~~

推荐提交顺序：

~~~text
commit 1: prompt contract + parser tests
commit 2: pure in-memory coordinator
commit 3: per-runtime idempotency and recovery
commit 4: single-writer persistence
commit 5: state machine/app integration
commit 6: canary and regression validation
~~~

## 自审结论

补充上述约束后，本方案才达到开工品质。最终实现必须满足：并发数量有硬上限；不同 item 不互相覆盖；同一 item 不会重复派发或并行推进；文件写入不会 lost update；进程重启不会盲目重派；LLM 回复不会靠猜测归属；只有完整通过所有校验的回复才能推进状态机。

---

# 最终收口版：执行时唯一以本节为准

本节覆盖前文冲突描述。正式新增父 FSM 状态 MENXIA_COORDINATOR；item stage 只存在于 MenxiaItemRuntime.state，不再写入父 StateContext.workflow_state。

## 1. 固定状态流

REQUEST_INTAKE -> ZHONGSHU_ANALYST -> ZHONGSHU_SOLVER -> ZHONGSHU_CRITIC -> ZHONGSHU_FREEZE_CHECK -> MENXIA_COORDINATOR -> MENXIA_GROUP_GATE -> MENXIA_COORDINATOR -> DONE

禁止使用 AGENT_REPLY_ACCEPTED(action=MENXIA_FAN_IN_READY) 表示 coordinator 事件。AGENT_REPLY_ACCEPTED 只表示真实 Agent 回复通过现有校验。

## 2. 并发硬约束

内部字段统一为 max_concurrent_groups 和 max_concurrent_items，后者是整个 task 的全局上限。每次 tick 必须断言 active group 数和 active item 数不超过配置；同一 item 同时只能有一个 active stage 和一个 active request。启动 group 前必须检查 dependencies；suggested_order 只用于无依赖 ready group 的稳定排序。

## 3. 单写入者保护

worker 只返回不可变 RuntimeUpdate，不得写 StateContext、state.json 或 events.jsonl。coordinator 是唯一写入者。每次提交按 TaskLock -> 读取 snapshot -> 合并 update -> 校验不变量 -> 生成连续 event sequence -> 写 events.jsonl -> 写 state.json -> fsync 的顺序执行。StateContext 增加 state_version；版本冲突必须拒绝并重新加载合并，禁止后写覆盖先写。

## 4. 幂等和恢复

request_id 可带随机后缀，但 dispatch_idempotency_key 不得带随机值，格式为 task_id:MENXIA:plan_revision_id:group_id:item_id:state:revision_round:attempt。恢复时 dispatch_status 不是 confirmed 不能直接重派，必须先查询远端 existing request。

## 5. 回复解析

LLM 回复必须是唯一 JSON object，包含 protocol_version、task_id、group_id、item_id、request_id、phase、state、action、payload。只允许纯 JSON 或单个 json 代码块；前后自然语言、多个 JSON、缺少绑定字段、旧 request_id、错误 group/item 都必须拒绝或记录 stale，不得猜测归属推进状态。

## 6. 开工顺序

Task 0：transitions.py 和 app.py 正式接入 MENXIA_COORDINATOR；Task 1：Prompt/parser；Task 2：纯内存 coordinator；Task 3：幂等与恢复；Task 4：persist_batch 和 state_version；Task 5：Human Gate 隔离；Task 6：真实 app canary。Task 0 至 Task 5 未全部通过前，不得接入真实并发。

## 7. 最低验收条件

必须测试并发上限、依赖顺序、同 item 不重入、双 RuntimeUpdate 不丢数据、version conflict、dispatch 成功但 snapshot 丢失、stale reply、Human Gate 串单、LLM 多种格式解析、Prompt UTF-8 bytes budget、frozen plan fan-in 顺序和全部 v31 回归。

最终判定：完成本节后达到可分阶段开工品质；可立即开始 Prompt 和纯内存 coordinator，真实 app 并发必须等持久化、恢复和解析测试全部通过。
