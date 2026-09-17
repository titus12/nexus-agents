# A2：数据-行为解耦与 ItemWorkflow（per-item 修订流水线）

状态：已确认（两班厨师轮替方案），分四阶段实施。
前置：A1 验收规范前移已落地（`cmd/orchestrator/acceptance_standards.py`）。
关联：`docs/multi/architecture/zhongshu-review-loop.md`（现有中书省机制）、
menxia_parallel 设计文档（闸门模式与本方案的特性开关同构）。

## 1. 设计原则

做菜比喻的实质是**数据与行为解耦**：食材=数据，厨师/工具/做法=行为，做菜=任务。

- **数据层（食材仓库）**：声明式、可寻址、有生命周期（持久 | 会话内 | 临时），不含行为。
  现有雏形 = ReviewState + DTO（PlanGraph / FindingLedger / EvidencePacket / AttemptRecords）。
- **行为层（加工单元）**：无状态 ProcessingUnit，输入清单 + 纯逻辑 + 类型化产物。
  现有已隔离的逻辑类（FormalizeSolverLogic / ReviseSolverLogic / fold_round /
  evaluate_gate / _task_review_ledger_update）缺的是输入输出声明。
- **编排层（厨房调度）**：大 FSM 只做三件事——分单（派菜谱）、收菜（fold 产物）、
  全图一致性（主厨校验）。

**菜谱（Recipe）= 数据 × 行为的绑定声明**，一个任务单元的完整定义。

## 2. 现状违背点（本方案要消除的）

1. states.py 的 6 个管道函数：行为长在数据折叠管道里（已知残留，P4 一并归位）。
2. 证据曾搭 `plan` 字段旅行（已修：`ReviewState.evidence_packet`），但派发切片
   （item_evidence、批次、retry feedback）仍是隐式依赖，无声明。
3. dispatch = 全量 context.json：厨师搬整个厨房。
4. Solver 单写入者串行处理所有 item：行为未单元化。

## 3. ItemWorkflow（一道菜的完整生命周期）

```
大 FSM 分单（task_review fanin 改造点）
  → 为每个争议 item 实例化菜谱，开一个子 issue 灶台
  → 灶台内两班厨师轮替（保对抗性，已确认）：
      critic 身份：按胶囊+证据+标准初审 → verdict
      fold（纯函数）
      solver 身份：CHANGES 时在刀具边界（revision_scope）内改稿 → patch
      critic 身份（换 pool 内另一身份，防和稀泥）：复审
      循环，≤ item 预算
  → 出锅：verdict + patch 提交大 FSM
大 FSM 收菜
  → 全部出锅：主厨（Solver）跨菜一致性检查（依赖图/口径/canonical_plan_hash）
      → FREEZE_CHECK
  → 有 ESCALATE：带完整拉锯记录 → HUMAN_GATE
```

菜谱示例：

```json
{
  "dish": "item-000001 的评审-修订-出锅",
  "ingredients": {
    "task_capsule": "persisted:plan.task_items[item-000001]",
    "evidence": "slice:review.evidence_packet@item-000001",
    "findings": "filter:review.findings@item-000001+active",
    "knife_boundary": "revision_scope(item-000001)",
    "standard": "acceptance_standards(7条)",
    "history": "review.item_workflows[item-000001].rounds"
  },
  "behavior": "REVIEW → (CHANGES) REVISE → RE-REVIEW * → APPROVE | ESCALATE",
  "budget": "max_item_revision_rounds=5（现成）",
  "product": "{verdict, patch(限刀具边界内), evidence_gaps}"
}
```

## 4. 持久化边界

| 层 | 内容 | 载体 |
|---|---|---|
| 持久 | 菜谱状态表（item→phase/rounds/verdict）、出锅 verdict+patch、ESCALATE 拉锯记录 | ReviewState.`item_workflows` + DTO |
| 会话内 | 改稿草稿、灶台内对话、复审中间产物 | 子 issue 生命周期 |
| 临时 | 每次派发的切片（item_evidence、批次、fingerprint） | dispatch payload |
| 崩溃恢复 | 状态表在 state；子 issue 幂等键 `request_id:attempt-N` | 现有机制组合 |

## 5. 实施步骤

### Phase 1 — DispatchEnvelope（食材清单规范，1-2 天）【已完成】
- 新模块 `cmd/orchestrator/dispatch_envelope.py`：build/validate。
- 四个派发点写入 envelope（进 dispatch_context，经拍平到 bundle manifest）：
  - Solver（formalize/revise，`build_solver_dispatch`）：plan、finding batch 切片、
    验收规范、刀具边界（revise 时 = 选中 finding 所属 item − 已批准）。
  - Critic task_review（`_task_review_bindings`）：任务胶囊、item_evidence 切片、
    active findings 切片、验收标准提示；tools.editable=none。
  - Analyst 契约派发（requirement_contract）。
  - Analyst lens 绑定。
- `prompt_bundle.py`：manifest 增加 `envelope` 段；畸形 envelope fail-fast；
  新日志 `PROMPT_BUNDLE_ENVELOPE`。
- 验证：manifest 可机读；一致性测试（envelope 声明 vs 实际 dispatch 内容）。

### Phase 2 — ItemWorkflow 数据模型（1-2 天）【已完成】
- ReviewState.`item_workflows` 状态表 + DTO 往返（旧快照缺 key → 空表）。
- task_review fanin 折叠时写分单表：`policies/item_workflows.py` 的计数规则与
  ledger ratchet 同源（attempt 范围、预算耗尽 → ESCALATED）。

### Phase 3 — ItemWorkflow 执行器（2-3 天，核心）【已完成：FSM 级灶台】
- 实现取"大 FSM 状态对"拓扑：REVISE 边 + 开关开启时派 item_revise 节点
  （每争议 item 一个 Solver binding），joiner 物化/合并 patch 并重算 plan_hash，
  NODE_COMPLETED 携带合并后 plan 回 ZHONGSHU_SOLVER（跳过 reply 校验、写 plan artifact），
  复审由 task_hash 变化自动触发。两班厨师 = 审查节点与 item 修订节点经大 FSM 交替。
- 开关 `zhongshu.item_workflow_enabled`（env `ZHONGSHU_ITEM_WORKFLOW_ENABLED`，默认 off）。
- scripted e2e：test_zhongshu_item_workflow_e2e.py（拒绝→灶台修订→复审批准→DONE）。

### Phase 4 — 主厨一致性 + 切换（1-2 天）【已完成】
- joiner 合并后跑 `structural_integrity_errors` + `structural_gate`（含
  DEPENDENCY_CYCLE）：违反 → `NODE_ITEM_REVISION_INVALID`（reply 预算，可重试，
  带门禁问题清单作为 retry feedback）。
- 配置参数全层对齐：global=6 / per_task=6 / analyst=3 / critic=4 /
  lease_ttl=1020（代码默认 = env 默认 = context 层）。
- 开关 `zhongshu.item_workflow_enabled`（env `ZHONGSHU_ITEM_WORKFLOW_ENABLED`，默认 off）。
- 真实 A/B：下次测试设 `ZHONGSHU_ITEM_WORKFLOW_ENABLED=1`，对比争议项收敛轮数与墙钟。
- 维护规则：skill 源 = docs/multi/runtime/*.md，改完必须同步 multica 服务端
  （`multica skill update <id> --content-file`），两条通道内容需逐字节一致。

## 6. 风险与对策

1. 并行改 plan 的合并冲突 → patch 限 revision_scope + 主厨终审 + 特性开关回退。
2. 对抗性稀释 → 复审强制换 critic 身份（pool 轮换）+ ESCALATE 带完整拉锯记录。
3. token 成本 → 每 item 上下文更小，总量近似不变，墙钟下降。

## 7. 预期

争议 item 并行处理：修订环 2 轮串行（~20min）→ 1 轮并行（~8-10min）；
验收争议在灶台内解决，不阻塞全图；配 A1 压返工轮数；中书省 60min → 30min 档；
门下省照搬此模式（menxia_parallel 闸门同构）。
