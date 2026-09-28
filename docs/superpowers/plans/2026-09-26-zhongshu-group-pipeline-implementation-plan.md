# 中书省组流水线与闭包投影实施方案

> 需求来源：`docs/2026-09-24-zhongshu-group-convergence-requirements.md`（2026-09-26 修订：组裁决 / 组内 patch 冻结区 / §8 机械投影闭包）
> 本方案所有设计选择已定稿，实施按任务顺序执行，不再需要额外决策。

**Goal：** 中书省以组为流水线原子——每组一个 group_review 任务出整组裁决（APPROVE_GROUP / REVISE_GROUP），组修订波跨组并发派发（每组一个 solver worker）；`acceptance_signals` 由需求文档 §8 机械投影，闭包由构造保证（orphan 类拒绝零发生）；机械形态校验前置到派发前，零 LLM 轮消耗；组面棘轮保证未变组零派发。

**Architecture（定稿决策）：**

- **组审契约**：`zhongshu_critic` 增 `REVIEW_GROUP` mode，动作封闭枚举 `APPROVE_GROUP / REVISE_GROUP / REQUEST_ANALYST_EVIDENCE / HUMAN_GATE / BLOCKED`；finding 保留 `item_id` 坐标（修订定位、证据路由 v3.3、停滞统计用），不构成独立裁决。review job 粒度 = `(revision_id, group_id)`，`review_job_id = zhongshu:{revision_id}:{group_id}`。
- **裁决投影**：`APPROVE_GROUP` ⇒ ledger 全成员机械投影 `APPROVED`、组 stage→`CONVERGED`；`REVISE_GROUP` ⇒ 整组回炉（全成员投影 `CHANGES_REQUIRED`、组 `revision_round+1`、stage→`REVIEWING`）。`task_review_ledger` 降级为投影结果；逐 item verdict 路径仅双轨期保留。
- **组面棘轮**：`group_surface_hash = sha256(doc_hash, sorted[member task_hash])`；hash 未变的组零派发（`ZHONGSHU_GROUP_APPROVAL_HELD`），任一成员正文或文档版本变更即失效。
- **闭包投影（单一事实源）**：§8 按成员 item 分小节（`### <item_id>`）；编排器解析 §8 逐条投影覆盖 plan item 的 `acceptance_signals`（模型所写一律被覆盖）；契约 `_TASK.acceptance_signals` 从 required 降级为可选。缺小节 → `PLAN_HAS_NO_ACCEPTANCE:<item_ids>` 结构违规。闭包校验恒真，`acceptance_orphan` 类拒绝物理消失。
- **机械门禁前置**：九节/措辞/依赖无环/无实现词/投影闭包等 LLM-free 校验在派发前预检；预检不过的组不派审核任务、直接路由修订；机械拒绝反馈必须附期望内容 diff（缺哪行 / 多哪行 / 哪句违规）。
- **组修（group_revise）**：每受影响组一个 solver worker，修订波跨组并发（fanout 子 issue 复用现有机制）；patch = `{items, group_docs}`；**组内 patch 冻结区** = 批次 finding 所属 items + 本组文档（可编辑），其余成员逐字保留（`SOLVER_ITEM_FROZEN:<item_ids>`，retryable）；跨组 items/文档逐字保留（`SOLVER_GROUP_DOC_FROZEN` 语义扩展到 items）。完成定义 = 处置齐 + 投影闭包（构造保证）+ 冻结区未动。
- **组胶囊增量投影**：变更成员带全文，未变成员带 `review_surface` + hash；附组文档全文、归并 finding、各成员 item 证据切片。
- **波次接线**：CRITIC / SOLVER 波的绑定单位改为组（`ready_review_groups` / `ready_revision_groups` 就绪集 + fanout），FSM 顶层边不变；门下省增量放行（9/24 Task 8）不动。
- **双轨开关**：`parallel.zhongshu.review_unit: "group" | "item"`，默认 `group`，item 路径保留一个版本供回退。

**Tech Stack：** Python（cmd/orchestrator），unittest，`python cmd/run_tests.py`（runner 强制 NullNotificationPort）。

**不改动：** Analyst 证据 lenses 与 v3.3 路由；处置账本生命周期（absorbed/rejected/settle）；组记账（预算 5 轮、停滞 2 轮、drainout 人闸、定向解停）；逐组冻结检查与全图校验一次性语义；门下省全部机制；传输（multica/prompt bundle/结果文件）与通知；FSM 顶层边与状态名；fast_track。

---

## 0. 现状与目标行为表

| 场景 | 当前行为（已核实） | 目标行为 | Task |
|---|---|---|---|
| 审核单元 | task_review 逐 item：`build_review_jobs` 每 `(rev, group, item)` 一个 job，`_task_review_bindings`（states.py:3968）一 item 一 binding | 每组一个 group_review job，组胶囊（组文档 + 全成员 item + finding + 证据切片） | 3/6 |
| 裁决粒度 | 逐 item `TASK_APPROVED/CHANGES_REQUIRED` 落 ledger | 整组一个动作；ledger 为机械投影 | 4 |
| 闭包一致性 | solver 双写 `acceptance_signals` 与 §8，折叠时逐字校验，违者重试（f8bde1 `acceptance_orphan`×3，retry=3/3 耗尽） | §8 唯一作者，编排器投影；闭包构造恒真 | 1 |
| 机械校验时点 | 回包折叠时才拒，一拒烧掉整轮 LLM | 派发前 LLM-free 预检 + 拒绝反馈带期望 diff | 2 |
| 修订派发 | 单 solver 串行、批次跨组混装（f8bde1 4 次派发 ×4min 串行） | 每受影响组一个 solver worker 跨组并发 | 5 |
| 修订冻结区 | 组内 `carry_forward_revision_items`（revision_scope 排除已批 item），组文档跨组冻结 | 两层：组内 patch 面（finding 所属 items + 本组文档）+ 跨组（items + 文档） | 5 |
| 重审触发 | 逐项 task_hash 变更只重审该项（`ZHONGSHU_TASK_APPROVAL_HELD`） | 组面 hash 变更重审该组；未变 `GROUP_APPROVAL_HELD` 零派发；增量胶囊 | 4/6 |
| 组规模 | 无上限 | `structural_gate` 增 `GROUP_MAX_ITEMS=6`，超限拆组打回 solver | 3 |

---

## 1. 统一约束（所有任务适用）

1. 每个任务先写测试并确认按预期失败（红），再改生产代码；全量测试经 `python cmd/run_tests.py`。
2. 契约向后兼容演进：`zhongshu_critic` 增 `REVIEW_GROUP` mode 与组动作，item 裁决动作（`TASK_APPROVED/TASK_CHANGES_REQUIRED/REQUEST_TASK_DISCARD`）保留一个版本（双轨开关）；`zhongshu_solver` 的 `acceptance_signals` 只降级不删除。
3. 不新增第三方依赖；新文件 UTF-8 无 BOM；代码注释英文，风格对齐周边。
4. 通知文案结构不变；新 reason code（`ZHONGSHU_GROUP_APPROVAL_HELD`、`SOLVER_ITEM_FROZEN`、`PLAN_HAS_NO_ACCEPTANCE`）走既有 reason 通道。
5. FSM 主线边与状态名不变；波内 job 粒度由 item 改组。
6. 现有防回归护栏继续生效：`SOLVER_SCOPE_ITEM_UNKNOWN` / `SOLVER_REVISION_NOOP`（2026-09-26 修复）在 group_revise 路径同样接线。
7. Git 写操作需用户另行授权。

---

## Task 1：闭包投影——`acceptance_signals` 改为 §8 机械投影

**分类：** 行为变更（单一事实源，先写测试）

**Files：**

- `cmd/orchestrator/domain/zhongshu_doc.py`
- `cmd/orchestrator/domain/policies/solver_plan.py`
- `cmd/orchestrator/contracts/zhongshu_solver.py`
- `cmd/test_zhongshu_doc.py`（扩）

### 1.1 先写测试（红）

| 测试 | 断言 | 预期 |
|---|---|---|
| `ParseSection8ItemSubsections` | §8 下 `### item-000001` 小节解析出 per-item 行集合 | 红 |
| `ProjectSignals_OverwritesPlanField` | plan item 写错 signals → 投影后 == §8 小节行 | 红 |
| `ProjectSignals_MissingItemSection_Rejected` | 成员 item 无 §8 小节 → `PLAN_HAS_NO_ACCEPTANCE:item-xxx` | 红 |
| `MaterializeProjectsSignals` | `materialize_solver_reply` 折叠后 item.acceptance_signals 与 §8 一致 | 红 |
| `ContractAcceptanceSignalsOptional` | 回包 plan item 缺 acceptance_signals 不再是契约违约 | 红 |

### 1.2 实现

1. `zhongshu_doc.py`：§8 解析扩展成员 item 小节（`### <item_id>` 标题，行集合归属该 item）；新增 `project_acceptance_signals(plan, group_docs) -> (signals_by_item, errors)`。
2. `solver_plan.py`：`materialize_solver_reply` 成功路径调用投影并**覆盖** plan items 的 `acceptance_signals`；缺小节的 item 记 `PLAN_HAS_NO_ACCEPTANCE:<item_ids>`，并入 `structural_integrity_errors` 阻断集合。
3. `contracts/zhongshu_solver.py`：`_TASK.acceptance_signals` 移出 `required`（保留可选，折叠即覆盖）。
4. 兼容：无 §8 小节的旧文档在双轨期回退现有逐字闭包校验；新派发一律带小节格式（solver prompt 随 T2 的期望 diff 一起给样例）。

### 1.3 验证

- 1.1 全绿；`test_zhongshu_doc.py`、`test_solver_group_doc_scope.py`、`test_solver_plan_materialization.py` 全量回归。

---

## Task 2：机械门禁前置 + 拒绝反馈带 diff

**分类：** 行为变更（先写测试）

**Files：**

- `cmd/orchestrator/domain/zhongshu_doc.py`（violations 明细结构化）
- `cmd/orchestrator/domain/policies/prompts.py`（retry_feedback 渲染 diff）
- `cmd/orchestrator/domain/states.py`（派发前预检）
- `cmd/test_zhongshu_doc.py`、`cmd/test_retry_feedback_prompt.py`（扩）

### 2.1 先写测试（红）

| 测试 | 断言 | 预期 |
|---|---|---|
| `DocViolationFeedback_CarriesExpectedRows` | 闭包/形态违规明细含 `{kind, item_id, expected, actual}`，错误串带期望行清单 | 红 |
| `ReviewWavePrefilter_SkipsUnfitGroup` | 组面预检不过 → 不派 group_review，路由修订（组 stage 保持 REVIEWING） | 红 |
| `PrefilterEmitsNoDispatch` | 预检路径不产生任何 `agent_dispatch` effect（零 LLM 轮） | 红 |

### 2.2 实现

1. `group_doc_violations` 返回结构化违规明细；`SOLVER_GROUP_DOC_INVALID:` 串尾附期望 diff（裁剪上限沿用前 10 条）。
2. CRITIC 波派发前对每组跑 `structural_gate` + doc-form + 投影闭包复核：不过的组移出 review 就绪集、置入 revision 需求，不派审核任务。
3. `retry_feedback`（prompts.py）渲染期望 diff，禁止只报片段（f8bde1 三连击的直接教训：模型三次修错方向）。

### 2.3 验证

- 2.1 全绿；`test_reply_shape_retry.py`、`test_retry_feedback_prompt.py` 回归。

---

## Task 3：组审契约（REVIEW_GROUP）与组胶囊

**分类：** 新增 + 行为变更（先写测试）

**Files：**

- `cmd/orchestrator/contracts/zhongshu_critic.py`
- `cmd/orchestrator/structured_output.py`
- `cmd/orchestrator/zhongshu_review_queue.py`
- `cmd/orchestrator/domain/states.py`（`_group_review_bindings`）
- `cmd/test_zhongshu_task_review_queue.py`（扩）

### 3.1 先写测试（红）

| 测试 | 断言 | 预期 |
|---|---|---|
| `BuildGroupReviewJobs_OneJobPerGroup` | job 粒度 `(revision_id, group_id)`，`review_job_id=zhongshu:{rev}:{group_id}` | 红 |
| `GroupCapsule_CarriesDocAndMembers` | 胶囊含组文档全文 + 全成员 item（全文或 surface）+ 归并 finding + 各成员证据切片 | 红 |
| `IncrementalCapsule_SurfacesUnchangedMembers` | 未变成员只带 `review_surface` + hash，变更成员带全文 | 红 |
| `GroupMaxItems_StructuralGate` | 7 item 组 → `GROUP_MAX_ITEMS:group-xxx:7` 违规 | 红 |
| `ContractReviewGroupActions` | `REVIEW_GROUP` 动作白名单 5 个；finding 缺 `item_id` 时按 target 归一化（既有 `resolve_finding_item_id`），仍无法归属则契约拒 | 红 |

### 3.2 实现

1. `zhongshu_critic.py`：v bump（`nexus.zhongshu.critic.v2`）——mode `REVIEW_GROUP`，actions=`APPROVE_GROUP / REVISE_GROUP / REQUEST_ANALYST_EVIDENCE / HUMAN_GATE / BLOCKED`；字段增 `group_id`（必填）、`findings`（item_id 必填）、`finding_responses`；item 裁决动作保留双轨一个版本。
2. `structured_output.py`：`zhongshu_dispatch_mode == "group_review"` → `REVIEW_GROUP`。
3. `zhongshu_review_queue.py`：`build_group_review_jobs(plan, revision_id)`；`review_surface` 复用为成员 surface。
4. `states.py`：`_group_review_bindings`（组投影 + 就绪集过滤 + frozen 组跳过沿用）；组胶囊组装（§6 输入自足逐项核对）。
5. `zhongshu_review_queue.structural_gate`：`GROUP_MAX_ITEMS = 6`（常量，超限打回 solver 拆组）。

### 3.3 验证

- 3.1 全绿；`test_zhongshu_task_review_queue_v2.py`、`test_dispatch_envelope.py` 回归。

---

## Task 4：组裁决折叠与组面棘轮

**分类：** 行为变更（本方案核心折叠语义，先写测试）

**Files：**

- `cmd/orchestrator/domain/policies/zhongshu_group.py`
- `cmd/orchestrator/domain/states.py`
- `cmd/orchestrator/zhongshu_review.py`（fan-in 聚合组裁决）
- `cmd/test_zhongshu_group_policy.py`（扩）

### 4.1 先写测试（红）

| 测试 | 断言 | 预期 |
|---|---|---|
| `ApproveGroup_ProjectsLedgerAndStage` | `APPROVE_GROUP` ⇒ 全成员 ledger `APPROVED` + stage `CONVERGED` | 红 |
| `ReviseGroup_ResetsWholeGroup` | `REVISE_GROUP` ⇒ 全成员 `CHANGES_REQUIRED` + stage `REVIEWING` + 组 `revision_round+1` | 红 |
| `GroupSurfaceHash_HeldOnUnchanged` | hash 未变 → `ZHONGSHU_GROUP_APPROVAL_HELD`，零派发 | 红 |
| `GroupSurfaceHash_InvalidatedByMemberOrDocChange` | 任一成员 task_hash 或 doc_version 变 → 棘轮失效 | 红 |
| `FindingItemCoordinate_SurvivesVerdict` | 组裁决后 finding 的 `item_id` 保留（证据路由/停滞统计） | 红 |
| `GroupBudgetStallUnchanged` | 组预算 5 轮 / 停滞 2 轮 / drainout 行为不变 | 红 |

### 4.2 实现

1. `zhongshu_group.py`：`group_surface_hash(review, group_id)` = sha256(doc_hash, sorted[成员 task_hash])。
2. `states.py`：`apply_group_verdict(review, group_id, action, findings)` —— APPROVE/REVISE 两分支投影 ledger、更新 `zhongshu_groups` 行；`_task_review_ledger_update` 逐项路径仅双轨 item 模式保留。
3. 棘轮接入 review 就绪集计算（T6）与 fan-in 裁决折叠；`ZHONGSHU_TASK_APPROVAL_HELD` 语义由 `ZHONGSHU_GROUP_APPROVAL_HELD` 接替（item 路径保留原日志）。

### 4.3 验证

- 4.1 全绿；`test_zhongshu_approval_ratchet.py`、`test_zhongshu_convergence_e2e.py` 按组粒度改写后全绿。

---

## Task 5：group_revise——组修跨组并发 + 组内 patch 冻结区

**分类：** 行为变更（先写测试）

**Files：**

- `cmd/orchestrator/domain/zhongshu/solver.py`（group_revise 派发与 scope）
- `cmd/orchestrator/runtime/agent_effects.py`（`_join_group_revise`，镜像 `_join_item_revise`）
- `cmd/orchestrator/domain/policies/solver_plan.py`（patch 合并 / 冻结区校验）
- `cmd/test_item_revise.py`、`cmd/test_solver_group_doc_scope.py`（扩）

### 5.1 先写测试（红）

| 测试 | 断言 | 预期 |
|---|---|---|
| `GroupRevise_BindingsPerAffectedGroup` | 3 组受修订 → 3 个并发 binding（fanout 子 issue） | 红 |
| `GroupPatch_MergeByItem_PreservesUnowned` | 批次未归属成员逐字保留；改写 → `SOLVER_ITEM_FROZEN:<item_ids>`（retryable） | 红 |
| `GroupPatch_ForeignDocRejected` | 跨组文档改动 → `SOLVER_GROUP_DOC_FROZEN`（既有语义） | 红 |
| `GroupPatch_MemberRewordWithoutFindingRejected` | 组内非 finding 成员措辞改写被拒（棘轮保护） | 红 |
| `RevisionWaveParallelAcrossGroups` | node executor 并发度 = 受影响组数（≤ cap） | 红 |

### 5.2 实现

1. `group_revise` dispatch mode：每组 binding 带本组批次 finding 全文 + 本组 items 正文 + 本组文档全文 + 上轮处置记录；editable = 批次 finding 所属 items + 本组文档。
2. `_join_group_revise`：patch 合并（item 身份不可变）+ `structural_gate` + 完成定义（处置齐 = 既有 `solver_resolution_coverage_error`；投影闭包 = T1 构造保证；冻结区未动 = 本任务校验）。
3. `SOLVER_ITEM_FROZEN:` 加入 `_RETRYABLE_SOLVER_REPLY_PREFIXES`；scope 防线（`SOLVER_SCOPE_ITEM_UNKNOWN` / `SOLVER_REVISION_NOOP`）在 group_revise 路径同样接线。

### 5.3 验证

- 5.1 全绿；`test_solver_plan_materialization.py`、`test_solver_disposition_ledger.py` 回归。

---

## Task 6：组波次接线 + 双轨开关

**分类：** 行为变更（接线层，先写测试）

**Files：**

- `cmd/orchestrator/domain/policies/zhongshu_group.py`（就绪集）
- `cmd/orchestrator/domain/states.py`（波绑定按组）
- `cmd/orchestrator/domain/context.py`（`parallel.zhongshu.review_unit`）
- `cmd/test_linear_fsm_entrypoint.py`、`cmd/test_zhongshu_convergence_e2e.py`（扩）

### 6.1 先写测试（红）

| 测试 | 断言 | 预期 |
|---|---|---|
| `ReviewWave_GroupReadySet` | 就绪集 = REVIEWING 且组面 hash 变（或从未审过）的组；CONVERGED/FROZEN/STALLED 排除 | 红 |
| `RevisionWave_FanoutAcrossGroups` | `REVISE_GROUP` 的组跨组并发派发 | 红 |
| `DualTrackSwitch_ItemMode` | `review_unit="item"` 走旧逐 item 波（回退开关可用） | 红 |
| `EarlyHandover_StillWorks` | 先冻结组进门下省、其余组回中书（ping-pong 语义不变） | 红 |
| `UnfitGroupRoutedToRevisionNotReview` | 预检不过的组进修订波而非审核波 | 红 |

### 6.2 实现

1. `ready_review_groups(review)` / `ready_revision_groups(review)`（镜像 `freeze_ready_groups` 的依赖与顺序语义）。
2. CRITIC / SOLVER 波绑定按就绪集展开（fanout 判定与 `_critic_max_workers` 沿用 app.py 现有路径）；FSM 边不变。
3. `parallel.zhongshu.review_unit` 开关（默认 `"group"`）；item 路径保留一个版本后另行拆除。

### 6.3 验证

- 6.1 全绿；三条 scripted 线（entrypoint / convergence / item workflow）全绿；全量回归。

---

## Task 7：e2e 与实测验收

**分类：** 回归保护 + 验收

**Files：**

- `cmd/test_zhongshu_convergence_e2e.py`（扩）
- `cmd/test_zhongshu_group_pipeline_e2e.py`（新增，如场景过大）

### 7.1 scripted e2e 场景

1. **2 组 ping-pong**：组 A 先 `APPROVE_GROUP` 收敛冻结、门下省提前接手；组 B `REVISE_GROUP` 回炉后冻结，最终 DONE。
2. **单点循环隔离**：组内 1 个 item 顽固 → 组 A 预算耗尽 STALLED 人闸；组 B 照常冻结并在门下省完成（9/24 §13 顽固组隔离）。
3. **闭包零重试**：§8 投影后 orphan 类拒绝零发生（断言无 `acceptance_orphan`、无 `ZHONGSHU_SOLVER_REPLY_RETRY` 因闭包）。
4. **并发修订**：3 组同时 `REVISE_GROUP`，断言同波 3 个 group_revise 派发（并发而非串行）。
5. **组面棘轮**：未变组零派发（`GROUP_APPROVAL_HELD`）；变更成员后重审。

### 7.2 全量回归

- `python cmd/run_tests.py` 全绿（含既有 924 用例按组粒度改写部分）。

### 7.3 实测验收

- 真跑一轮对照 f8bde1 基线：墙钟、orphan 率（目标 0）、reply-retry 率、重审面（组胶囊增量投影生效）；逐条勾 9/24 §13 与 2026-09-26 修订新增的"组裁决与投影闭包"验收。

---

## 验收映射（需求 §13 ↔ Task）

| 需求验收 | Task |
|---|---|
| 组裁决落账（APPROVE/REVISE 投影） | T4 / T7.1 |
| 投影闭包零 orphan | T1 / T7.3 |
| 机械违规零 LLM 轮 | T2 / T7.3 |
| 修订波跨组并发 | T5 / T7.1 |
| 组面棘轮零派发 | T4 / T7.1 |
| 组内 patch 冻结区 + 跨组越界拒绝 | T5 |
| 单组收敛 / 顽固组隔离 / 组级停滞 / 定向解停（既有） | T4/T6/T7.1 不回归 |
| 需求文档形态 / 可实施方案形态（既有） | 不回归（冻结检查沿用） |
| 输入自足（组胶囊 / 组修派发） | T3 / T5 |
