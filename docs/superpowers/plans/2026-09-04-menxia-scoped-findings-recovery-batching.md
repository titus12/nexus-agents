# Menxia findings/recovery/batching 修复实施计划

## 目标

修复已确认的三个确定性问题：

1. 门下省 Finding 缺少 group/item 归属，导致并发处理多个 item 时跨 item 串 findings。
2. 并发 worker 重启恢复时未查询已有请求，可能重复派发同一请求。
3. 门下省一次性 fan-out 全部 item，超过并发上限时将整组判失败。

保持中书省的全局 Finding 语义不变；保持门下省 Solver → Analyst → Critic 的阶段屏障、顺序和现有幂等键语义；不引入飞书通知行为变更。

## 实施步骤

### 1. 建立 Finding 的稳定作用域与合并键

文件：`cmd/orchestrator/models.py`、`cmd/orchestrator/context.py`

- 为 `Finding` 增加可选的 `group_id`、`item_id` 字段，兼容已有无作用域的持久化数据。
- 在 `Finding`/`StateContext` 中集中定义作用域归一化和身份键：
  - 两者为空表示全局 Finding；
  - `group_id` 与 `item_id` 都存在表示 item Finding；
  - 只有 `group_id` 表示 group Finding；
  - 只有 `item_id` 判定为非法输入。
- 将 `merge_findings` 的键从 `finding_id` 改为“作用域 + finding_id”，避免不同 item 的同名 finding 相互覆盖。
- 增加按 group/item 查询 Finding 的方法，并让活动/已解决索引保持向后兼容。
- 对非法作用域、跨作用域更新和重复身份进行确定性拒绝或记录错误，避免静默污染状态。

### 2. 在应用边界给 Finding 绑定当前 item，并隔离门下省判断

文件：`cmd/orchestrator/app.py`、`cmd/orchestrator/transitions.py`、必要时 `cmd/orchestrator/policies.py` 与 `cmd/orchestrator/state_machine.py`

- 扩展 `_update_findings` 接收当前阶段作用域；中书省更新保留全局作用域，门下省 item Critic 更新强制绑定当前 `active_group_id`/`active_item_id`。
- 如果 agent 返回的 `group_id`/`item_id` 与当前 worker 上下文不一致，拒绝该 payload，而不是将其归入当前 item。
- `MENXIA_ITEM_CRITIC -> APPROVE_ITEM` 只检查当前 group/item 的 active findings，不再扫描全局列表。
- `MENXIA_GROUP_GATE` 检查当前 group 的 findings 以及全局 findings；其他 group/item 的 findings 不得阻塞当前 group。
- 保留中书省冻结检查与全局阻塞策略；审查记录中的 finding ID 仍按原协议输出，同时持久化作用域字段。
- 检查状态恢复、Finding 状态重算和已有旧状态文件的读取路径，确保新增字段缺失时按全局 Finding 兼容加载。

### 3. 为并发恢复增加先查后发的请求查找接口

文件：`cmd/orchestrator/parallel_runtime.py`、`cmd/orchestrator/adapters.py`，必要时 `cmd/orchestrator/recovery.py`

- 给 `ParallelCoordinatorDriver` 注入可选的 `find_existing_request` 回调，参数使用本次 attempt 的稳定 idempotency key 和 issue_id。
- 每次 attempt 在 dispatch 前先查询已有 receipt：找到则直接复用其 external message ID，跳过重新派发；找不到才 dispatch。
- 查询失败与“明确不存在”分开处理：查询异常时 fail-closed，不直接重新 dispatch；将错误记录为可恢复的 worker failure。
- 保持 attempt 的逻辑请求 ID、idempotency key、revision、worker、item 上下文一致；恢复复用时继续用原 external message ID poll。
- 日志明确区分 `REUSED_EXISTING_REQUEST`、`DISPATCHED_NEW_REQUEST`、`REQUEST_LOOKUP_FAILED`，便于后续审计幂等性。
- 检查现有 `MulticaCliAdapter.find_existing_request` 的增量 comment cursor 语义，沿用从 cursor 开始读取，不改回全量/固定 100 条拉取。

### 4. 将门下省并发 fan-out 改成受限批次

文件：`cmd/orchestrator/app.py`，必要时 `cmd/orchestrator/concurrency.py`

- 抽取按 item 列表执行单个阶段的批次 helper；批次大小取有效并发上限与剩余 item 数的较小值。
- 对每个批次调用现有 `run_parallel_fanout`，只要求当前批次完整收敛，再进入下一批次。
- Solver 完成所有批次后才启动 Analyst；Analyst 完成所有批次后才启动 Critic，保留阶段屏障。
- 只把每个批次的结果按 item_id 写入 payload map，建立完整的 item → Solver → Analyst → Critic 链路；禁止重复 item、未知 item、缺失 item 被静默接受。
- 若某批次 worker failure/rejection，保留现有可恢复 rejected event 与错误原因；不得将前面已完成的批次重复派发。
- 在 group 级别记录批次编号、总批次、批次 item IDs 和完成数，便于判断是否是真正的容量限制。
- 让批次大小与 admission 实际限制一致，避免仍以整组数量申请 lease；不改变 `GLOBAL_MAX_WORKERS`、`PER_TASK_MAX_WORKERS` 和 analyst/critic 上限的配置接口。

### 5. 接通生产恢复回调与状态路径

文件：`cmd/orchestrator/app.py` 及其初始化/驱动构造位置、`cmd/orchestrator/recovery.py`

- 在应用创建 `ParallelCoordinatorDriver` 时传入当前 Multica adapter 的 request lookup 回调。
- 确认重启后从 state/lifecycle 恢复时，批次进度、已写入结果和 pending payload 不会导致已完成批次重新发出。
- 对缺失或损坏的状态数据保持 fail-closed；不通过猜测恢复到另一个 item 或另一个请求。

### 6. 静态自审与变更审查

- 用代码搜索核对所有门下省 gate 仍有全局 `finding_objects()` 扫描的位置，并逐一替换为作用域查询；中书省全局扫描保留。
- 核对所有 `ParallelCoordinatorDriver` 构造调用，避免新增回调后测试/假适配器失配。
- 核对新增字段的序列化、反序列化、日志和恢复兼容性。
- 不运行自动化测试，按用户要求仅进行静态审查和必要的语法级检查；不发送任何飞书消息。

## 完成判据

- 两个 item 即使返回相同 `finding_id`，也能在状态中独立保存、独立更新、独立通过 gate。
- item A 的 P1/P2 不会阻塞 item B；当前 group gate 仍会看到本 group 与全局阻塞项。
- 并发 worker 在重启/重试时，已存在的 idempotency key 不会再次 dispatch；查找异常不会盲发。
- 门下省 item 数超过 6 时按批次完成，不因 admission rejection 将整组直接判失败；阶段屏障与结果完整性仍成立。
- 变更不包含飞书通知发送，也不修改 Codex 全局配置文件。
