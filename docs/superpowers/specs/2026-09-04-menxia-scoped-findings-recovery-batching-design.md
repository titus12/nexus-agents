# 门下省作用域、并发恢复与分批调度设计

## 目标

修复门下省并发流程中的三个确定性缺陷：

1. finding 目前存储在任务级全局列表，前一个 item 的 finding 会污染后一个 item 的审批判断。
2. 并发 worker 重启恢复时没有查询已有外部请求，可能重复派发相同的 Solver/Analyst/Critic 请求。
3. 一个 group 的 item 数超过并发上限时，未获得 lease 的 worker 被直接拒绝，导致整个 group 失败。

修复必须保持现有流程语义：中书省 finding 仍然是工作流级；门下省 item 仍然按 Solver → Analyst → Critic 的阶段 barrier 执行；item 之间只在明确允许时并发；不发送额外飞书通知；不依赖全量评论读取。

## 当前问题证据

- `Finding` 只有 `finding_id`、阶段和状态字段，没有 `group_id` 或 `item_id`。
- `MENXIA_ITEM_CRITIC` 处理路径调用任务级 `_update_findings()`，而 `MENXIA_ITEM_CRITIC` 的 `APPROVE_ITEM` 转换检查整个 `ctx.findings`。
- 普通状态派发会调用 `find_existing_request()`，但 `ParallelCoordinatorDriver` 的 worker 执行函数直接调用 `dispatch()`。
- 门下省应用路径为一个 group 的所有 item 创建一个 fan-out；`ParallelCoordinatorDriver` 在开始时一次性申请全部 lease，拒绝项不会排队重试；应用随后要求 `completed == expected`。
- 现有 `MenxiaParallelCoordinator` 虽有 `max_concurrent_items`，但当前 `OrchestratorApp._prepare_parallel_menxia_group()` 没有使用它。

## 设计决策

### 1. Finding 作用域模型

在 `Finding` 中新增可选的 `group_id` 和 `item_id` 字段，并放在现有字段末尾，保持已有位置参数调用兼容。`scope` 不作为第三个持久化字段，而是由这两个字段推导，避免保存两个可能不一致的作用域来源。

Finding 的逻辑身份由以下值组成：

```text
(scope, group_id, item_id, finding_id)
```

作用域推导规则：

- `group_id` 和 `item_id` 都为空：`scope=global`，用于 `ZHONGSHU_*` finding。
- `group_id` 非空且 `item_id` 非空：`scope=item`，用于 `MENXIA_ITEM_*` finding。
- `group_id` 非空且 `item_id` 为空：`scope=group`，用于 group 级 finding。
- `item_id` 非空但 `group_id` 为空：非法作用域，必须拒绝写入。

`Finding.from_dict()` 必须保留作用域字段。`StateContext.merge_findings()` 不再只按 `finding_id` 合并，而是按作用域身份合并，避免不同 item 生成同名 finding 时互相覆盖。

`_update_findings()` 的写入规则：

- 处理中书省回复时写入全局作用域。
- 处理门下省 item Critic 回复时从当前上下文补齐 `group_id/item_id`。
- 如果回复显式携带的 `item_id` 与当前 item 不一致，拒绝该回复，不能把它写入当前 item。
- 回复缺少 item 标识时，使用当前活动 item 绑定，避免旧协议回复丢失作用域。

状态判断规则：

- `MENXIA_ITEM_CRITIC` 的 `APPROVE_ITEM` 只检查当前 group/current item 的活跃 finding。
- `MENXIA_GROUP_GATE` 检查当前 group 下所有 item 的活跃 finding，并继续检查全局作用域 finding，保留中书省级别的安全阻断。
- `active_finding_ids` 等派生字段继续由 `replace_findings()` 维护，但实际门禁判断使用作用域过滤后的 Finding 对象。

门下省的 `item_critic_reviews[item_id]` 仍然保留，作为审查原文和恢复审计来源；它不再承担状态门禁的唯一数据来源。

### 2. 并发请求恢复与幂等

`ParallelCoordinatorDriver` 增加可注入的已有请求查询函数，默认使用当前 `MulticaAdapter.find_existing_request`。

每个 worker attempt 的顺序固定为：

1. 根据稳定的 attempt-level `idempotency_key` 查询已有外部请求。
2. 如果找到，复用其 `operation_id/external_message_id`，记录 `PARALLEL_DISPATCH_RECONCILED`，直接进入 polling。
3. 如果明确确认不存在，才执行 `dispatch()`。
4. 如果查询失败或结果无法判断，worker 失败并停止盲目派发；后续由现有失败策略决定是否重试。

并发 worker 的逻辑 request ID、attempt ID 和幂等键必须保持可重建：同一个 task、phase、revision、worker 和 attempt 在进程重启后生成相同的值。prompt bundle manifest 继续作为本地请求审计材料，已存在的 result 文件或外部回复优先由 polling/recovery 路径消费。

`MulticaCliAdapter.find_existing_request()` 需要区分“明确没有匹配请求”和“查询异常”。查询异常不能静默转成未找到，否则会重新发送请求。查询仍使用现有增量评论游标，不回退到固定拉取 100 条评论。

### 3. 门下省分批调度

保持阶段级 barrier，但把每个阶段的全量 item fan-out 改为批次执行：

```text
所有 item 的 Solver 批次完成
    -> 所有 item 的 Analyst 批次完成
    -> 所有 item 的 Critic 批次完成
    -> 按 item 写回结果并进入各自审批
```

批次大小取配置与运行时容量的安全下限，至少受以下约束：

- `PER_TASK_MAX_WORKERS`
- `GLOBAL_MAX_WORKERS`
- 当前阶段的 phase limit
- 一个可配置的门下省批次上限，默认不超过现有安全并发值

批次内仍使用现有 `ParallelCoordinatorDriver`。批次之间串行运行，保留 Solver→Analyst→Critic 的全量阶段依赖和 group completion barrier。一个批次因瞬时全局容量不足而无法获得 lease 时，等待并重新申请，不把未获得 lease 的 item 直接判为业务失败。

每个阶段都必须按 `item_id` 建立结果索引，并验证：

- 不存在未知 item。
- 一个 item 在同一阶段最多有一个结果。
- 当前阶段所有 item 最终都有结果，或者明确进入失败策略。

这样 item 数量超过 6 时只增加批次数，不会因为一次 fan-out 的 admission rejection 直接让整个 group 失败。

## 错误处理与安全边界

- 作用域缺失时可以从当前活动 item 补齐；作用域冲突时拒绝，不猜测归属。
- 请求查询失败时 fail-closed，禁止直接 dispatch。
- 找到已有请求但没有回复时继续 polling，不重复发送。
- 一个 item 失败不会把其它已完成 item 的结果清除；group 是否阻塞由 group gate 根据每个 item 的作用域结果统一决定。
- 批处理不能绕过全局并发上限，也不能绕过 lease 释放和超时机制。
- 不修改中书省全局 finding 的既有语义，不把门下省 item finding 混入中书省 finding。

## 修改范围

- `cmd/orchestrator/models.py`：扩展 Finding 作用域字段。
- `cmd/orchestrator/context.py`：按作用域身份合并和查询 finding。
- `cmd/orchestrator/app.py`：绑定门下省 finding 作用域，按 item/group 过滤门禁，接入并发恢复与分批执行。
- `cmd/orchestrator/transitions.py`：使用作用域查询替代任务级全量查询。
- `cmd/orchestrator/parallel_runtime.py`：增加已有请求 reconciliation 和容量等待能力。
- `cmd/orchestrator/adapters.py`：让已有请求查询区分 not-found 与 lookup failure，并保持增量评论读取。
- 相关长期价值测试文件：增加作用域隔离、重启不重复派发、超过并发上限分批完成的回归覆盖；本轮按用户要求不执行自动化测试。

## 验收标准

1. item-A 的 active P1 不会出现在 item-B 的 `APPROVE_ITEM` 阻断判断中。
2. 同一 item 可以使用与其它 item 相同的 `finding_id`，但两者不会互相覆盖或互相解析。
3. 并发进程在 dispatch 后、fan-in 前重启时，重启路径先复用已有请求，不增加重复外部 dispatch。
4. 查询已有请求发生异常时，不会盲目发送新请求。
5. 一个包含 7 个以上 item 的 group 可以通过多个批次完成；批次之间不突破全局和任务并发上限。
6. Solver、Analyst、Critic 的阶段 barrier、item 结果关联和 group gate 语义保持不变。
7. 所有新增请求、作用域和批次行为都有可检索的日志字段，且不产生额外飞书通知。
