# 中书省任务级动态 Critic 队列设计

## 1. 目标

中书省由 Solver 产出任务组和任务后，Critic 以“单个任务”为最小审核单元。任务组只提供上下文和归属，不再把同一组或完整任务图复制给多个 Critic。

以 3 组 10 个任务为例，建立稳定队列：

```text
G1:T1,G1:T2,G1:T3,G2:T4,G2:T5,G2:T6,G3:T7,G3:T8,G3:T9,G3:T10
```

最多运行 6 个 Critic worker。初始领取前 6 个任务；任意 worker 完成后，原子领取下一个未处理任务，直到 10 个任务全部完成。

本设计不增加 LLM 汇总步骤，也不再使用“多个 Critic 审同一个任务”的全局 quorum。编排器只负责队列调度、结果登记、覆盖检查和状态推进。

## 2. 动态任务队列

每个审核任务包含：

```yaml
review_job_id: zhongshu:R1:G1:T1
revision_id: R1
group_id: G1
item_id: T1
status: PENDING|CLAIMED|RUNNING|COMPLETED|RETRYABLE|CANCELLED|HUMAN_GATE
lease_id: ""
worker_id: ""
attempt: 0
request_id: ""
task_hash: ""
dependency_hash: ""
result_path: ""
```

领取必须是持久化层的原子条件更新：只有 `PENDING` 任务才能被领取，并同时写入唯一 `lease_id`、worker、attempt 和 request_id。

必须保证：

- 同一个 `review_job_id` 同时只有一个有效 lease；
- 一个 worker 同时只处理一个任务；
- 已完成任务不能再次领取；
- 结果写入校验 `review_job_id + lease_id + attempt`；
- 晚到旧回复不能覆盖新 attempt。

## 3. Critic 输入

每个 Critic 只接收一个任务的审核 capsule：

```yaml
review_scope:
  scope_type: item
  revision_id: R1
  group_id: G1
  item_id: T1
  task_hash: "..."
  dependency_hash: "..."
group_context:
  group_id: G1
  title: "..."
  objective: "..."
  dependencies: []
  shared_acceptance: []
task: {}
requirement_refs: []
dependency_refs: []
historical_findings: []
```

提示词必须约束 Critic：

- 只审核指定的 `group_id/item_id`；
- 不得对未分配任务创建 Finding；
- 检查需求覆盖、任务边界、依赖、验收信号、未知项和风险；
- 不输出实现方案；
- 跨任务依赖只通过摘要引用，不复制其他任务完整内容。

## 4. Critic 输出和 Finding

每个任务返回一个任务级协议：

```yaml
action: TASK_APPROVED|TASK_CHANGES_REQUIRED|HUMAN_GATE|BLOCKED
revision_id: R1
group_id: G1
item_id: T1
reviewed_task_hash: "..."
reviewed_dependency_hash: "..."
review_checks: {}
findings: []
evidence_ids: []
unknowns: []
```

Finding 必须绑定任务：

```yaml
finding_id: worker-2:T1:001
scope: item|dependency
group_id: G1
item_id: T1
related_item_ids: []
severity: P0|P1|P2|P3
owner_role: review-solver|review-analyst|human
next_action: REQUEST_SOLVER_REVISION|REQUEST_ANALYST_EVIDENCE|HUMAN_GATE
claim: "..."
required_action: "..."
evidence_ids: []
```

跨任务依赖问题必须指定唯一主任务，其他任务放到 `related_item_ids`。编排器按稳定的需求键或依赖边做去重，不让多个任务各自创建同一个主 Finding。

## 5. 收敛流程

```text
Solver 生成 candidate plan R1
          ↓
创建所有任务的 review jobs
          ↓
最多 6 个 Critic 动态领取任务
          ↓
每个任务独立完成审核
          ↓
所有任务完成覆盖检查
          ↓
存在 Finding → Solver 修复受影响任务
          ↓
只重新排队受影响任务及受影响依赖任务
          ↓
Critic 继续审核，直到收敛
```

例如第一轮只有 T2、T7、T9 有问题，则下一轮只审核 T2、T7、T9。若修改 T2 影响 T3 的依赖，也重新审核 T3。未受影响且已通过的任务不重审。

每个任务通过 `task_hash + dependency_hash` 判断是否需要重审，而不是因为整个 plan hash 变化就全部重审。

Solver 不得静默删除或重编号未受影响任务；新增任务创建新 review job，删除任务必须有明确取消原因，并将对应 review job 标记为 `CANCELLED`。

## 6. 完成条件

中书省本轮只有同时满足以下条件才完成：

- 所有任务都有最终审核结果；
- 没有 PENDING、CLAIMED、RUNNING 或 RETRYABLE 任务；
- 没有未解决的 P0/P1 Finding；
- 没有任务被静默遗漏；
- 所有结果都属于当前 revision，并匹配当前任务 hash。

编排器可以产生 fan-in 状态事件，但只汇总完成数量、阻塞数量和任务状态，不调用 LLM 汇总内容。

## 7. 恢复与错误处理

- 重启时复用已完成 stage result；RUNNING/CLAIMED 任务先查询已有 request，再决定是否重派。
- 超时后回收过期 lease，任务重新进入 RETRYABLE/PENDING；超过重试上限进入 HUMAN_GATE。
- 结构化回复错误只影响当前任务，不阻塞其他任务。
- 旧 revision 或旧 lease 的晚到回复只能记录为 stale/rejected。
- 领取、完成、失败和 lease 回收都必须依赖条件写，不能只依赖内存状态。
- 同一任务连续无进展达到阈值时进入 HUMAN_GATE，避免无限循环。

## 8. 需要改造的组件

- `app.py`：将固定 Critic fan-out 改为任务队列生命周期。
- `parallel_runtime.py`：增加动态领取、worker slot 复用、lease 和旧回复拒绝。
- `states.py`：构造任务级 Critic capsule，校验任务作用域和 hash。
- `models.py`：增加 review job、lease 和任务作用域 Finding 字段。
- `zhongshu_review.py`：改为任务结果登记和跨任务 Finding 去重，不再做全局 Critic quorum。
- `transitions.py`：依据任务覆盖率和任务级阻塞状态推进。
- `structured_output.py`、Critic Skill 和提示词：同步任务级协议。
- `persistence.py`、`recovery.py`：持久化审核快照、作业状态和恢复信息。

## 9. 验证范围

实现后验证：

- 3 组 10 任务、6 worker 的动态领取；
- 8 任务、6 worker 时完成后继续领取；
- 同一任务并发领取冲突；
- 超时、租约回收、重启和晚到回复；
- 任务级 Finding 归属和跨依赖去重；
- 只重审受影响任务；
- 新增、删除和修改依赖任务；
- 全部任务覆盖后才允许推进；
- P0/P1 未解决时不能通过；
- 不调用飞书通知适配器。

验证使用独立端口，不影响当前 Nexus 服务，不修改 Codex 全局配置。
