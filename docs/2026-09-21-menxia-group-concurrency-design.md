# 门下省按组并发 + 单任务三角色行为规格（设计稿 v1）

日期：2026-09-21
状态：待评审
范围：门下省（MENXIA_*）执行模型重构；不改动中书省既有 wave 机制。

---

## 1. 目标

1. **按组并发**：落地 `MenxiaParallelLimits`（context.py:51）已声明的语义——多个 group 的 item 可同时处于门下省流水线中，受 `max_concurrent_groups` / `max_concurrent_items` 约束，`failure_policy=continue_and_block_group` 生效。
2. **单任务（item）三角色行为可审、可实现**：Solver / Analyst / Critic 围绕一个 item 的输入、输出、折叠、去向、预算全部成文（§4、§5）。
3. 顺带修复审查发现的三个缺陷：agent 拿不到当前 item（#1）、无修订预算（#2）、REMOVE_ITEM 闭环脆弱（#3）。

非目标：改动中书省状态机；改动合同（contracts/*.v1）；改通知文案结构；组内 item 级乱序流水线（v1 用"阶段屏障"模型，见 §6.1）。

## 2. 现状与差距（事实）

| # | 事实 | 位置 |
|---|---|---|
| A | 门下省是单活动 item 串行：`ReviewState.active_item_id` 唯一，转入 ITEM_SOLVER 时全局选一个就绪项 | context.py:129、states.py:400-407 |
| B | `MenxiaParallelLimits` 无运行时消费者，仅 DTO 序列化/迁移引用 | context.py:606/770、migration.py:418 |
| C | 门下省单派发不设 `dispatch_context` → agent 的 context.json 无 item_id/item 正文；prompt.txt 也无当前项信息 | states.py:996-1011、agent_effects.py:584-604、prompts.py:199-209 |
| D | 每个 item 4 次串行 agent 调用（S/A/C/Gate），GATE 对每个 item 都跑一次 agent | transitions.py:60-64、states.py:1620-1638 |
| E | `item_revision_round`/no-progress fuse 只在中书省使用，门下省循环无预算 | item_workflows.py、policies/critic.py |
| F | REMOVE_ITEM 回 ITEM_SOLVER，靠 solver 重写 plan 才能除名；失败则 `next_menxia_item` 死选或 GroupGate 抛 InvariantViolation | transitions.py:93、states.py:1631-1632 |
| G | wave 机制（node_dispatch + bindings + fan-out + admission + fan-in）是现成的，fan-in 按 state 前缀路由：`ZHONGSHU_*` → `aggregate_zhongshu_workers` | agent_effects.py:1100-1125 |
| H | joiner 要求所有 worker 动作一致（NODE_ACTION_CONFLICT），对按 item 并发不成立 | agent_effects.py:1080-1099 |

## 3. 数据基座：每 item 的门下省流水线记录

新增冻结 dataclass（context.py，与 `ReviewTaskItem` 同层）：

```python
@dataclass(frozen=True)
class MenxiaItemState:
    item_id: str
    group_id: str
    stage: str            # SOLVING | ANALYZING | REVIEWING | APPROVED | ESCALATED | BLOCKED | REMOVED
    revision_round: int   # 本 item 的修订轮次（attempt-scoped，见 §4 预算）
    last_verdict: str     # 最近一次折叠的合同动作
    fingerprint: str      # 最近一次该 item 回复的指纹（no-progress 检测）
    blocked_reason: str = ""
```

- 挂在 `ReviewState.menxia_items: tuple[MenxiaItemState, ...]`；序列化/反序列化仿照 `item_workflows`（context.py:442 附近）。
- `active_item_id` 保留但降级为"诊断指针"；调度以 `menxia_items.stage` 为准。
- 兼容：旧快照 `menxia_items` 为空 → 首次进入门下省时从 `next_menxia_item()` 播种单条记录（等价旧行为）。

## 4. 单个 item 的三角色行为规格（审查重点）

通用约定（对所有三个角色生效）：

- **输入（prompt bundle）**：prompt.txt（现有 `build_prompt`）+ context.json 的 `dispatch_context`：
  - `menxia_dispatch_mode: "item_pipeline"`
  - `item_id` / `group_id`
  - `item`：完整 item 正文（title/objective/source_requirement_ids/acceptance_signals/dependencies，取自 `review.plan` 当前投影）
  - `item_findings`：该项 active findings（`findings_for_scope`，policies/menxia.py:30）
  - `item_evidence` / `finding_responses`：复用 `_item_evidence_context` 的切片
  - `revision_round` / `plan_hash` / `revision_id`
  - `envelope`：`editable: items:<item_id>`，contract 为对应 menxia 合同——**该 item 之外一律不可改**
- **输出**：结构化 JSON，动作必须 ∈ 合同 `_ACTIONS`，范围违规 findings 由 `validate_scope`（policies/menxia.py:11）拒绝 → CONTRACT_REJECTED 走 reply-retry 预算。
- **折叠**：worker 结果按 `item_id` 写回 `MenxiaItemState`（stage/verdict/fingerprint）与 findings 合并；单 item 失败不影响其他 item（见 §6.4）。
- **预算**：`revision_round` 只在该 item 被 solver 批次实际处理时 +1（attempt-scoped，语义同 `attempted_item_ids`，context.py:145）。`revision_round >= max_item_revision_rounds`（5）→ stage=ESCALATED → OPEN_HUMAN_GATE（reason=`MENXIA_ITEM_STALLED`，resume_state=该 item 应处阶段）。同指纹无进展复用 recovery.no_progress 语义，`max_no_progress` 次 → 同样升级。

### 4.1 MENXIA_ITEM_SOLVER（review-solver，`nexus.menxia.item_solver.v1`）

| 项 | 规格 |
|---|---|
| 触发 | item.stage == SOLVING（新 item 播种即 SOLVING） |
| 行为 | 只为当前 item 产出 `ITEM_IMPLEMENTATION_PROPOSAL`（objective/approach/files/changes/tests/verification/rollback）；修订轮必须逐条回应 `item_findings` 与上轮 analyst/critic 意见（`responses_to_critic`） |
| 动作→去向 | `FEASIBLE` / `READY_FOR_ANALYST` → stage=ANALYZING；`READY_FOR_CRITIC`（证据充分可跳证据轮）→ stage=REVIEWING；`HUMAN_GATE` → 该 item 挂起（stage 不变，OPEN_HUMAN_GATE）；`BLOCKED` → stage=BLOCKED（blocked_reason） |
| 修订语义 | 收到 analyst/critic 的修订要求后：item 回到 SOLVING，`revision_round+1`，bundle 里带上要求修订的 findings 与 responses |

### 4.2 MENXIA_ITEM_ANALYST（review-analyst，`nexus.menxia.item_analyst.v1`）

| 项 | 规格 |
|---|---|
| 触发 | item.stage == ANALYZING |
| 行为 | `ITEM_EVIDENCE_REVIEW`：对该 item 做证据核验——verified facts / evidence（必须带 source）/ current_behavior / missing_evidence / conflicts / unknowns 分栏；回答 `item_findings`（finding_responses） |
| 动作→去向 | `EVIDENCE_SUFFICIENT` / `READY_FOR_CRITIC` → stage=REVIEWING；`NEEDS_MORE_EVIDENCE` / `REQUEST_SOLVER_REVISION` → stage=SOLVING，`revision_round+1`，缺证清单进入 solver 下一轮 bundle |
| 边界 | analyst 不得修改 plan（envelope 只读 items:<id> 的实现层）；产出只含证据与判断 |

### 4.3 MENXIA_ITEM_CRITIC（review-critic，`nexus.menxia.item_critic.v1`）

| 项 | 规格 |
|---|---|
| 触发 | item.stage == REVIEWING |
| 行为 | `ITEM_OR_GROUP_REVIEW`（逐 item）：decision + findings（必须 validate_scope 通过）+ required_changes（每条可由 verification_plan 观测） |
| 动作→去向 | `APPROVE_ITEM` → stage=APPROVED，item_id 进 `completed_item_ids`（沿用 states.py:577-583 折叠）；`REVISE_ITEM` / `REQUEST_SOLVER_REVISION` → stage=SOLVING，`revision_round+1`；`REMOVE_ITEM` → **stage=REMOVED（一等工作流动作，见下）**；`SPLIT_ITEM` / `MERGE_ITEM` → v1 按"带图编辑许可的修订"处理（stage=SOLVING，envelope 允许增删该 item 的图节点），折叠时经 `_task_graph_projection` 重建（states.py:2709） |
| REMOVE_ITEM 闭环（修 #3） | 折叠即：task_items 投影中除名（写回 plan patch，防止 plan 重建复活）+ 该 item findings 全部关闭 + ledger 行清除（复用 `_discarded_item_ids` 的清理语义，states.py:599-614）。不再经过 solver |

### 4.4 MENXIA_GROUP_GATE（review-critic，`nexus.menxia.group_gate.v1`）

- **本地路由（不派 agent）**：wave 收尾后若还有 pending item，FSM 落到 GATE 状态时由编排器直接按 §6.3 聚合规则选择下一阶段（NEXT_ITEM 语义），**不再为中间过闸花一次 agent 调用**（修 D 的成本项）。
- **真闸（派 agent）**：仅当某 group 的全部 item ∈ {APPROVED, REMOVED, BLOCKED, ESCALATED} 时运行一次：核对 `group_consistency.item_decisions`、blockers（ESCALATED/BLOCKED 的 item 以 blockers 呈现）。`APPROVE_GROUP` → 组完成；`REQUEST_GROUP_REVISION` → 指名 item 回 SOLVING（`revision_round+1`）；对 ESCALATED/BLOCKED 的指名 item，闸的修订决定同时授予新预算（round 清零 un-park），REMOVED 的 item 已脱离 plan 不可重启。
- 全部组完成 → `COMPLETE → DONE`。

## 5. 单 item 生命周期走查（含一轮修订）

```
播种 item-000003 (group-002, deps 满足) → stage=SOLVING
[SOLVER wave]  binding(item-000003) → READY_FOR_ANALYST        → stage=ANALYZING
[ANALYST wave] binding(item-000003) → NEEDS_MORE_EVIDENCE      → stage=SOLVING, round=1
[SOLVER wave]  binding(item-000003) → READY_FOR_CRITIC         → stage=REVIEWING
[CRITIC wave]  binding(item-000003) → REVISE_ITEM(finding F1)  → stage=SOLVING, round=2
[SOLVER wave]  → READY_FOR_ANALYST → [ANALYST wave] → EVIDENCE_SUFFICIENT → REVIEWING
[CRITIC wave]  → APPROVE_ITEM                                  → stage=APPROVED, 进 completed_item_ids
   └─ group-002 全员终态 → [GATE agent] group_consistency → APPROVE_GROUP → 组完成
   └─ 所有组完成 → FSM: GATE + COMPLETE → DONE
```

若 round 达 5：CRITIC wave 折叠时直接 ESCALATED → OPEN_HUMAN_GATE（`MENXIA_ITEM_STALLED`，resume_state=REVIEWING）。同 group 其他 item 继续（continue_and_block_group）；GATE 的 blockers 里呈现该 item。

## 6. 组并发调度

### 6.1 模型：阶段屏障（stage barrier），不是 item 级自由流水线

FSM 仍是线性闭图（transitions.py 不加边）；并发发生在**每个阶段状态内部**——一次 wave 里并发处理"所有处于该阶段的 item"（跨组）。item 的子阶段记录在 `menxia_items.stage`。

选择理由：(a) 完全复用现成 wave 机制（node_dispatch/bindings/fan-out/fan-in，事实 G）；(b) 闭图、合同、准入、fan-out 子 issue 全部不动；(c) 中书省已验证同构模式（task_review wave）。代价：各 item 以阶段为单位推进，快 item 需等当前 wave 收尾（可接受：同阶段耗时相近）。

### 6.2 调度策略（纯函数，进 policies/menxia.py）

```
ready_items(review, stage) = [it for it in menxia_items if it.stage == stage]
排序：组间按 group.order 轮转（group-fair），组内按 item.order
上限：启用时 distinct(groups) <= max_concurrent_groups 且 len(bindings) <= max_concurrent_items
      menxia.enabled=False → 只取第一个 item（等价现状，兼容开关）
```

### 6.3 聚合动作规则（wave fan-in → FSM 转移，全部命中既有边）

每个 menxia 状态 wave 完成后，按"最早阶段优先"选聚合动作：

| FSM 状态 | worker 逐 item 动作折叠后 | 聚合动作（=转移 action） | 命中的边 |
|---|---|---|---|
| ITEM_SOLVER | 任一 item → ANALYZING | READY_FOR_ANALYST | solver→analyst ✓ |
| ITEM_SOLVER | 无 ANALYZING，任一 → REVIEWING | READY_FOR_CRITIC | solver→critic ✓ |
| ITEM_ANALYST | 任一 → SOLVING | NEEDS_MORE_EVIDENCE | analyst→solver ✓ |
| ITEM_ANALYST | 无 SOLVING，任一 → REVIEWING | EVIDENCE_SUFFICIENT | analyst→critic ✓ |
| ITEM_CRITIC | 任一 → SOLVING | REQUEST_SOLVER_REVISION | critic→solver ✓ |
| ITEM_CRITIC | 无 SOLVING（有 APPROVED/others） | APPROVE_ITEM | critic→gate ✓ |
| GROUP_GATE | 有 pending item | NEXT_ITEM（本地，不派 agent） | gate→solver ✓ |
| GROUP_GATE | 全部组终态且 gate agent APPROVE_GROUP | COMPLETE | gate→done ✓ |

未列出的组合按"最早未排空阶段"归约；不可能出现无动作可选拒（每轮 wave 至少推进一个 item，否则触发 §4 预算升级）。 approved/REMOVED item 的折叠发生在 NODE_COMPLETED 的 review fold 中（worker_results → `_review_update` 通道，states.py:458+）。

### 6.4 failure_policy = continue_and_block_group

- 单 binding 失败：走既有节点重试（attempt-scoped request_id，nodes.py:265）；预算耗尽 → 该 item stage=BLOCKED（blocked_reason），**不** FAIL 整个 node；wave 里其余 item 结果正常折叠。
- 一个 group 全部 BLOCKED 或 GATE 发现 blockers → OPEN_HUMAN_GATE（reason=`MENXIA_GROUP_BLOCKED`）。

### 6.5 并发机制细节

- 每 item 一个 binding：`worker_id=f"menxia_item_{stage}-worker-{nn}"`，`fanout_parent_id=task issue`，fan-out 标题 `menxia {role} {item_id}`（确定性，重试加 ` (retry-N)`，nodes.py:282-285）→ 每 item 独立子 issue，一个 agent 身份可并发（与 analyst wave 同构）。
- **实现检查点**：`PARALLEL_WIDTH` 由 distinct `(issue_id, agent_id)` 对计算（agent_effects.py:136-155），fan-out 绑定的 issue_id 在建子 issue 后才产生——须验证 menxia 绑定的宽度计算路径与 analyst wave 一致，加回归测试。
- joiner 放宽：`NODE_ACTION_CONFLICT` 检查只对 `ZHONGSHU_*` 生效（agent_effects.py:1080）；`MENXIA_*` 走新聚合器，允许逐 item 不同动作。
- fan-in 路由：`AgentNodeJoiner` 增加 `MENXIA_*` 分支 → `aggregate_menxia_workers(...)`（新纯函数，policies/menxia.py；输入 worker_payloads + 逐 item 上下文，输出 `{action, item_results, worker_results, findings...}`）。

## 7. 代码修改清单（落点）

| 文件 | 修改 |
|---|---|
| `cmd/orchestrator/domain/context.py` | 新增 `MenxiaItemState`；`ReviewState.menxia_items`；review DTO 序列化/解析（~574/~643 附近）；播种逻辑 |
| `cmd/orchestrator/domain/policies/menxia.py` | 新增纯函数：`ready_stage_items`（§6.2）、`advance_menxia_item`（单 item 折叠：stage/round/指纹/预算）、`aggregate_menxia_workers`（§6.3）、`menxia_group_readiness`（组终态判定）；`validate_scope`/`findings_for_scope` 复用 |
| `cmd/orchestrator/domain/states.py` | ① `_dispatch_effect` 门下省分支重写：`menxia.enabled` 时走 node_dispatch + `_menxia_item_bindings(context, target, effective_review)`（每 binding 带 §4 通用约定的 dispatch_context；复用 `_item_evidence_context`/`build_envelope`）；`enabled=False` 保留单 binding 路径（同样补 dispatch_context，修 #1）② `MenxiaItemSolver/Analyst/CriticState` 增加 `handle()`：NODE_COMPLETED → 聚合动作 + 折叠（menxia_items 更新、APPROVE_ITEM 折 completed、REMOVE 折除名）③ `MenxiaGroupGateState.handle()`：本地路由优先，真闸条件见 §4.4 ④ 预算升级 → `_human_gate_decision(reason=MENXIA_ITEM_STALLED)` |
| `cmd/orchestrator/domain/transitions.py` | **不加边**；仅补契约测试断言 §6.3 表中每条聚合动作都命中既有边 |
| `cmd/orchestrator/runtime/agent_effects.py` | ① `AgentNodeJoiner`：动作冲突检查限定 `ZHONGSHU_*`；新增 MENXIA fan-in 分支 ② 验证 menxia 绑定的宽度/子 issue 路径（§6.5 检查点） |
| `cmd/orchestrator/domain/policies/parallel.py` | `aggregate_zhongshu_workers` 不动；menxia 聚合独立函数，避免污染 zhongshu 契约 |
| `cmd/orchestrator/adapters.py` | 无结构改动；确认 `MulticaCliAdapter.dispatch` 对 `**request.context` 透传 menxia dispatch_context（现成，adapters.py:831-842） |
| `cmd/orchestrator/notifications.py` | v1.1：门下省 wave 进度（item×stage 计数）；v1 沿用现有节点完成通知 |
| `cmd/orchestrator/domain/policies/item_workflows.py` | 不动（中书省专用） |

## 8. 兼容与开关

- `parallel.menxia.enabled=False`（默认）→ 串行单 binding，但 **dispatch_context 与预算修复仍生效**（它们不依赖并发）。
- 旧快照：`menxia_items` 缺省 → 播种；DTO 版本不 bump（新字段可选）。
- `GATE_POLL_INTERVAL_SEC`、HUMAN_GATE 语义不变；每 item 升级产生的 gate 复用现有桥接（app.py `_poll_human_gate_reply`）。

## 9. 测试计划

1. `cmd/test_menxia_pipeline.py`（新）：调度策略（组公平/上限/禁用开关）、单 item 折叠表（§4 每行动作）、预算升级、REMOVE 除名与 findings 关闭、聚合动作归约表（§6.3 全组合）。
2. transitions 契约测试：聚合动作 × FSM 状态 全部命中既有边。
3. joiner：MENXIA 混合动作不触发 NODE_ACTION_CONFLICT；zhongshu 行为不回归。
4. 脚本化 e2e（仿 `test_zhongshu_convergence_e2e.py`）：2 组 × 3 item， scripted adapter 驱动；覆盖修订循环、REMOVE、单 worker 失败后 continue_and_block_group、GATE 本地路由 vs 真闸、全组完成 → DONE。
5. 全量 `python cmd/run_tests.py` 回归（650+ 现有用例）。

## 10. 开放问题（请评审时定夺）

1. `SPLIT/MERGE` v1 按"图编辑许可的修订"处理是否可接受，还是需要一等公民折叠（拆出子 item 独立流水）？
2. GATE 本地路由会改变现有"每 item 都有 gate agent 回执"的可观测性——是否需要一条本地 NEXT_ITEM 的通知/日志替代？
3. `max_concurrent_items` 默认 3 是否合适（agent 池每个 menxia 状态只有 1 个 agent 身份，并发靠子 issue 区分目标）？
