# 00 设计初衷与关键设计点（AI 快速回忆用）

> 用途：给 AI 助手在进入本仓库任何中书省/门下省相关工作前快速重建心智模型。
> 冲突裁决顺序：当前代码契约 > 本文档 > 01/02/03 协议文档 > docs/ 历史设计稿。
> 已知过期点：`01/02` 中 "Analyst 提供 candidate_items/candidate_groups" 的描述已过时，
> 当前契约（`nexus.zhongshu.analyst.v1`）把这三个字段定为 `max_items=0`（强制为空）。

## 1. 设计初衷（用户原话级目标）

通过机制化的多 agent 合作，把**复杂、模糊的需求**变成**稳定的、可执行（可冻结、可逐项执行、可验证）的方案**。

一句话本质：**内容由模型生成，决策权在确定性代码。**
模型只负责产出证据、任务图、裁决意见；一切流程推进、放行、打回、升级都由编排器 FSM 的确定性规则决定。这是对多 agent 系统四大死法（角色漂移、范围膨胀、无限循环、悄悄放行）的直接防御。

做菜比喻（记忆锚）：**中书省不炒菜，只把"我想吃点好的"变成一份品审签字、可以照着开火的定稿菜单；门下省是灶台。**

## 2. 六条核心设计原则（全部有代码落点）

1. **职责隔离用机器契约硬编码，不是提示词请求**。
   例：Analyst 的 `task_proposals/candidate_items/candidate_groups` 在 schema 层就是 `array(max_items=0)`。
   → `cmd/orchestrator/contracts/zhongshu_analyst.py:75-77,100-106`
2. **阶段由转移边决定，不由数据推断**。
   Solver 的 FORMALIZE/REVISE 两通道在代码上隔离（`domain/zhongshu/` 包）；dispatch 看来源状态
   （CRITIC/FREEZE_CHECK → REVISE），reply 先看 mode 再回退 plan。这修复过 `role mode mismatch`
   把合格回复整条丢弃的真实事故。
   → `cmd/orchestrator/domain/zhongshu/stages.py`
3. **编排器拥有批次，单写手修订**。SolverBatch（每轮 ≤6 条 findings）由编排器计算并随派发下发，
   模型只"逐条解决 + 原样回填"；划分不一致 = `SOLVER_BATCH_MISMATCH`（一次重问）。
   修订范围 scoped：editable = 选中 finding 所属 item − 已批准 item。
   → `cmd/orchestrator/domain/zhongshu/solver.py`
4. **必然收敛**：修订预算（默认 8 轮）、无进展检测（no_progress）、stuck finding 公平计数
   （只在被排进批次后增长）、达到上限进 `BLOCKED` 或 `HUMAN_GATE`，**宁可卡住也不脑补、不默默放行**。
   → `cmd/orchestrator/domain/zhongshu/critic.py` 的 `evaluate_gate` 决策表
5. **证据纪律**：Analyst 每条证据必须带 `decision_relevance`（boundary/coverage/dependency/acceptance/risk
   五选一，带别名规范化防整条拒收）；Critic 的 finding 必须可追溯证据；自称"实测"的结论必须附
   「验证方法：」五要素配方（目标 file:line、步骤、指标+单位、基线来源、预期观察）；本轮验不了的标
   `WONT_VERIFY`，不许永久挂起。
   → `contracts/zhongshu_analyst.py:28-63`、`contracts/zhongshu_critic.py:27-41`
6. **三条冻结不变量**：批准棘轮（已批准 item 不因后续修订无声回退，approve+blocker 自相矛盾
   裁决会被纠正）、P0 不放行、全员评审完成才可带 P1 跟进冻结（P1 标 DEFERRED）。
   → `cmd/orchestrator/domain/zhongshu/critic.py` 的 `fold_round` / `evaluate_gate`

## 3. 三个角色的"手铐"（谁被限制做什么）

| 角色 | 比喻 | 被限制成什么 | 关键契约 |
|---|---|---|---|
| Analyst | 采购验货员 | **捆手**：只产出证据包/边界/未知项/提问，禁止提案任务；两段式——先单独产出权威需求契约（`requirement_contract`），再多 lens worker 绑定同一份 canonical requirements 并行取证，不许各自发明 requirement_id | `nexus.zhongshu.analyst.v1`；`states.py:785-788` |
| Solver | 菜谱设计师 | **框手**：`plan.requirements` 是不可变的 Analyst 契约，只许引用已有 requirement_id；依赖必须是 DAG（"相关但不阻塞"不许写成 depends_on）；groups 只许用 `item_ids` 引用，禁止复制任务对象；禁止实现级设计字段（留给门下省） | `nexus.zhongshu.solver.v1` |
| Critic | 品审团 | **扎眼**：逐任务审查（`REVIEW_ONE_TASK`，任务级 Critic 队列）；固定五个审查面 requirement_coverage/boundary/dependencies/acceptance/risks；每个结论要证据链 + 验证配方；`REQUEST_TASK_DISCARD` 只许以"不服务任何需求"为由，不许拿"验不了"当丢弃理由 | `nexus.zhongshu.critic.v1` |

修订加速（可选）：`item_workflow_enabled` 开启后，REVISE 边可按被争议 item 各派一个 Solver worker
并行改（`dispatch_mode: item_revise`）；FORMALIZE 始终单写手。→ `states.py:886-931`

## 4. 冻结门决策表速记（evaluate_gate）

按序判定：
1. 修订预算耗尽 → `BLOCKED(ZHONGSHU_REVISION_BUDGET_EXHAUSTED)`
2. 已评审全批准且无 blocker → `APPROVE_CRITIC`（FREEZE_WITHOUT_REVISION）
3. blocker 在收缩但单 item 连续被拒 → `HUMAN_GATE(ZHONGSHU_ITEM_STALLED)`
4. blocker 停止收缩：无 P0 残留 + 有 P1 + 全员已评审 → 带跟进冻结（P1=DEFERRED）；否则 `HUMAN_GATE`
5. stuck finding（两分支都查，批次内 stuck 优先排序）→ `HUMAN_GATE(ZHONGSHU_STUCK_FINDING)`
6. no_progress ≥ max → `BLOCKED(ZHONGSHU_NO_PROGRESS)`

通过后：`ZHONGSHU_FREEZE_CHECK → MENXIA`。

## 5. 中书省 → 门下省边界

- 门下省只接收**冻结方案**（带版本 revision_id + plan_hash）。
- 门下省逐 item 固定顺序 `MENXIA_ITEM_SOLVER → ANALYST → CRITIC`，不并发跳过；组门禁在组内全过后执行。
- 门下省只描述实现方案，**不写业务代码**；发现菜单本身有问题必须退回中书省出新版本，不许灶台私自改谱。
  → `docs/multi/menxia-solver-skill.md:352`

## 6. 已知代价与既定方向（不要当成缺陷重复"发现"）

- **小任务快速通道**：当前固定开销重（每 worker 每轮约 10 万 token prompt 束，一次真实运行可达数亿
  prompt token），小任务仪式成本大于收益。**用户已明确：方向正确，但等整个机制真实测试跑通后再做快速通道。**
- **效率验收指标**（机制跑通后衡量，不靠感觉）：
  1. 冻结后返工率：门下省打回且归因"菜单本身错"的比例是否下降；
  2. HUMAN_GATE 兑现率：人工决策实际改变菜单走向的比例（人不是橡皮图章）。
- 已做的降本：REVISE 通道上下文裁剪（实测 -46%）；机制验证用脚本化 adapter 测试零 token 完成
  （`test_linear_fsm_entrypoint.py`、`test_zhongshu_convergence_e2e.py`），真实运行只留给脚本答不了的模型行为问题。

## 7. 权威来源指针

- 代码契约（最高权威）：`cmd/orchestrator/contracts/zhongshu_*.py`
- 阶段/批次/折叠/门控纯逻辑：`cmd/orchestrator/domain/zhongshu/{stages,solver,critic,contracts}.py`
- FSM 管道与派发：`cmd/orchestrator/domain/states.py`
- 近期重构说明（为什么两条通道代码隔离）：`docs/multi/architecture/zhongshu-review-loop.md`
- 协议知识域（部分描述滞后于代码）：本目录 `01`~`08`
