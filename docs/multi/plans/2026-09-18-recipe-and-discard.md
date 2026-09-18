# 2026-09-18 验收配方 + 丢弃建议机制

## 背景与动机

来自 task-20260918-0b7867 的复盘：6 个 item 连续 4-5 轮不收敛，全部是"证据不够硬"
型争议。第一性分析发现循环把三种性质不同的证据缺口当成同一种在重试：

1. 检索缺口（事实已存在，粒度不够）→ 重试有用
2. 测量缺口（需要跑基准/埋点，本轮无人有权执行）→ 重试无用，须授权或降级
3. 认识论缺口（静态审查根本产不出）→ 重试纯属浪费

同时品菜师缺乏"这任务不值得做"的合法表达通道，只能无限打回。

## 决策（用户已确认）

- **配方载体 = 文本标记（方案 A）**：验收信号仍是字符串；不可核验信号改写为
  `"<结论> UNKNOWN（验证方法：<五要素>）"`。五要素 = 测量对象含 file:line、
  具体命令/步骤、指标与单位、基线来源、预期观测量。
- **丢弃 = 建议-复核两级**：品菜师新动作 `REQUEST_TASK_DISCARD`（必须论证到需求，
  "不可验证"不构成丢弃理由）；规划师复核后执行（删项 + 需求标 out-of-scope）或
  保留（关闭相关 finding 并说明理由）。
- **must 级需求的丢弃 = 规划师复核后仍需人审确认**（HUMAN_GATE）。

## Phase 1：验收配方

1. `acceptance_standards.py`：检查清单加第 8 条（可测性闸门）；新增
   `acceptance_signal_gate(plan)`：信号含度量触发词（%/％/提升/降低/缩短/加速/
   减少/提高/吞吐/倍）且不含 `UNKNOWN`/`验证方法` 逃生标记 → 计划不合格。
2. `zhongshu_review_queue.structural_gate` 接入信号闸门；
   `solver_plan._BLOCKING_STRUCTURAL_ISSUES` 加 `ACCEPTANCE_SIGNAL_UNVERIFIABLE`
   → 同时约束 solver 物化（SOLVER_PLAN_STRUCTURE_INVALID，可重试）与
   item_revise 合并（NODE_ITEM_REVISION_INVALID）。
3. finding 生命周期新增 `WONT_VERIFY` 关闭状态（决策=WONT_VERIFY → 状态映射、
   resolved 集合、consolidate accepted 集合、通知关闭集合）。
4. critic 合同 prompt 规则：信号含验证方法时评五要素完整性；缺素打回、齐全则
   原声称按 UNKNOWN-已挂方法处理，decision=WONT_VERIFY 关闭，不作 blocker。

## Phase 2：丢弃建议

1. critic 合同动作加 `REQUEST_TASK_DISCARD`；worker 结论必须以 finding 形式
   承载论证（category=task_discard，claim=服务哪条需求/为何不关键/损失什么，
   owner_role=Solver，severity=P1 → 进批次）。
2. 聚合层把该动作折叠为 finding + 轮动作 REQUEST_SOLVER_REVISION；
   弃用建议不新增 payload 通道，复用 finding 管线（批次/stuck/通知全兼容）。
3. structural_gate 覆盖检查跳过 `scope=="out"` 的需求（丢弃删项的合法路径）。
4. 规划师复核：删项走 full-plan（拓扑变更）；states 折叠时 diff 新旧 task_items，
   检出被删 item 的需求 priority=="must" → HUMAN_GATE（不进 critic）。
5. 通知：task_discard finding 渲染加"品菜师建议丢弃"前缀；人审 gate 原因可读化。

## Phase 3：skill 同步

`docs/multi/runtime/zhongshu-solver-skill.md`（配方撰写/降级义务/丢弃复核协议）
与 `zhongshu-critic-skill.md`（配方评估/WONT_VERIFY/丢弃建议协议）更新后推
multica 服务器并校验 byte-identical。

## 测试（零 token）

- acceptance_signal_gate 单元（触发/逃生/误报防护：含 ms 字段名的静态信号不触发）
- structural_gate：scope=out 需求不判 UNCOVERED
- 聚合折叠：REQUEST_TASK_DISCARD → task_discard finding + 轮动作
- solver 删 must 覆盖项 → HUMAN_GATE；删非 must 项 → 正常进 critic
- consolidate：WONT_VERIFY 关闭不计 blocker、stuck 归零
- 全量回归 `python cmd/run_tests.py`
