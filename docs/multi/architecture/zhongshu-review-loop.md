# 中书省评审循环：Solver / Critic 逻辑隔离设计

> 状态：已实施（2026-09-17）。测试护栏：`cmd/test_zhongshu_logic_isolation.py` 及全部回归。

## 1. 为什么重构

重构前，Solver 的两个输入通道共用一条代码路径，通道靠**数据形态**推断：

- `has_current_plan` 决定"建图还是修订"——但分析师阶段可能已经把 plan 落进状态，
  导致第一次 Solver 调用就被判成"修订"（`task-20260916-fc2fbf` 实测如此）；
- `zhongshu_dispatch_mode` 硬编码 `"solver_revision"`，初次建图也是这个标签；
- `solver_resume_mode` 是死键（无人写入），mode 判定失效，引发过
  `role mode mismatch` 把合格修订回复整条丢弃的事故；
- 聚合（fold）与门控（gate）逻辑内嵌在 `ZhongshuCriticState.handle`，无法单测。

原则：**代码隔离，而不是数据区分；阶段由转移边决定，而不是由数据推断。**

## 2. 模块布局

```
cmd/orchestrator/domain/zhongshu/
  stages.py     # SolverStage（FORMALIZE/REVISE）与阶段判定
  solver.py     # SolverBatch + FormalizeSolverLogic + ReviseSolverLogic
                # + process_solver_reply + build_solver_dispatch
  critic.py     # fold_round / evaluate_gate / mark_followups（纯计算）
  contracts.py  # 分阶段响应契约片段 + 阶段解析（带旧键回退）

cmd/orchestrator/domain/states.py   # 只剩 FSM 管道：决策/效果/预算/日志
```

## 3. 流程

```
┌ A. FORMALIZE（分析师 → 首图）
│   INTAKE/ANALYST ──边──▶ ZHONGSHU_SOLVER
│   dispatch = build_solver_dispatch(stage=FORMALIZE)
│     prompt: 无 [Revision task] 段；context: 无 solver_batch / active_findings
│     contract: required_by_action = {READY_FOR_CRITIC: [action, plan]}，无 batch 规则
│   reply(mode=..._READ_ONLY) → FormalizeSolverLogic：结构门 → 完整 plan
└───────────▶ ZHONGSHU_CRITIC

┌ B. CRITIC 评审（fanout，每 item 一个 worker）
│   NODE_TASK_REVIEW_FANIN → 逐项裁决
│   fold_round(...): 账本折叠（批准棘轮）+ 裁决自洽（approve+blocker → CHANGES_REQUIRED）
└───────────▶ APPROVE_CRITIC 或 REQUEST_SOLVER_REVISION

┌ C. REVISE（审核员 → 修订）
│   ZHONGSHU_CRITIC ──边──▶ ZHONGSHU_SOLVER
│   dispatch = build_solver_dispatch(stage=REVISE)
│     SolverBatch.for_findings(findings, cap=6)   ← 编排器定批次
│     prompt: [Revision task] 逐条列任务 + 原样回填 finding_batch
│     context: solver_batch + 仅批次的 findings（上下文裁剪，-46%）
│     contract: required_by_action = {READY_FOR_CRITIC: [action]}，含 batch 规则
│   reply(mode=..._RESUME 或 ..._READ_ONLY，均宽容接受)
│     ReviseSolverLogic：batch 回显 / overlap / coverage / SOLVER_BATCH_MISMATCH
│     物化：scoped（editable = 选中 finding 所属 item − 已批准 item）
└───────────▶ 回到 ZHONGSHU_CRITIC

┌ D. FREEZE GATE（evaluate_gate 决策表）
│   1. 修订预算耗尽            → BLOCKED(ZHONGSHU_REVISION_BUDGET_EXHAUSTED)
│   2. 已评审全批准且无 blocker → APPROVE_CRITIC（FREEZE_WITHOUT_REVISION）
│   3. blocker 仍在收缩：单 item 连续被拒 → HUMAN_GATE(ZHONGSHU_ITEM_STALLED)
│   4. blocker 停止收缩：
│        无 P0 残留且有 P1 且全部 item 已评审 → 带跟进冻结（P1 标 DEFERRED）
│        否则 stalled → HUMAN_GATE
│   5. stuck finding（两分支都查）→ HUMAN_GATE(ZHONGSHU_STUCK_FINDING)
│      stuck_rounds 只在 finding 被排进批次后增长（attempted_finding_ids 公平计数）；
│      批次内 stuck 优先排序，未被点名过的 finding 不会被升级
│   6. no_progress ≥ max       → BLOCKED(ZHONGSHU_NO_PROGRESS)
└───────────▶ ZHONGSHU_FREEZE_CHECK → MENXIA
```

## 4. 关键接口

```python
# stages.py
class SolverStage(Enum): FORMALIZE = "formalize"; REVISE = "revise"
def resolve_dispatch_stage(source_state: str) -> SolverStage   # 边决定
def resolve_reply_stage(payload, review) -> SolverStage        # mode 优先，plan 回退

# solver.py
@dataclass(frozen=True)
class SolverBatch:
    selected_finding_ids: tuple[str, ...]
    remaining_finding_ids: tuple[str, ...]
    max_findings: int
    # for_findings(findings, cap) / as_dispatch_context() / as_reply_template() / as_expected()

class FormalizeSolverLogic:      # 分析师通道
    def validate(payload, review) -> str
    def materialize(payload, review) -> (plan|None, error)
class ReviseSolverLogic:         # 审核员通道
    def __init__(batch: SolverBatch)
    def validate(payload, review) -> str
    def editable_item_ids(payload, review) -> set[str] | None
    def materialize(payload, review) -> (plan|None, error)

def process_solver_reply(payload, review) -> SolverReplyOutcome(stage, payload, materialized, error)
def build_solver_dispatch(context, *, request_id, prompt, revision_id, plan_hash, next_item) -> EffectRequest
def plan_artifact_effect(task_id, sequence, plan, revision_id) -> EffectRequest

# critic.py（纯计算；StateDecision 由状态类组装）
def fold_round(*, ledger, findings, expected_item_ids, max_item_rounds) -> ReviewRound
def evaluate_gate(round, *, revision_allowed, previous_fingerprint,
                  no_progress_count, max_no_progress, max_stuck_rounds) -> GateVerdict
def mark_followups(findings, followup_ids, note) -> tuple

# contracts.py
def resolve_solver_stage(context) -> SolverStage          # 显式键优先，旧键回退
def solver_contract_fragments(stage) -> dict              # 分阶段契约片段
```

## 5. 设计要点与不变量

1. **阶段由边决定**：dispatch 的 stage 看来源状态（CRITIC/FREEZE_CHECK → REVISE）；
   reply 的 stage 先看 agent 声明的 mode，再回退 plan。数据只做回退，不做主判。
2. **编排器拥有批次**：`SolverBatch` 由编排器计算并随派发下发；模型只"逐条解决 +
   原样回填"。划分不一致 = `SOLVER_BATCH_MISMATCH`（回复预算重问一次）。
3. **mode 是信息位**：容错接受任一声明模式（见 `accepted_role_modes`），阶段断言不依赖
   mode——避免重回 `role mode mismatch` 死锁。
4. **上下文裁剪（REVISE 专属）**：只随本轮批次的 findings（紧凑字段集）与批次 id，
   实测 context.json 39.4KB → 21.4KB（-46%）；完整 findings/plan 始终留在编排器状态里，
   回复物化仍对全量 plan 进行，裁剪不丢数据。
5. **单写手**：修订轮仍是一个 agent 一个回复，避免并发补丁冲突；DAG/需求保持等全局
   不变量由 `structural_integrity_errors` 在物化后统一把关。
6. **批准棘轮 / P0 不放行 / 全员评审才可带跟进冻结**：三条冻结不变量原样保留。
7. **契约同源**：批次规则只有 `ZHONGSHU_SOLVER_BATCH_RULE` 一份，两阶段契约与规则元组
   都引用它。

## 6. 效率与后续（fanout 触发条件）

- 修订通道的 token 大头已裁掉；每轮上限 6 条保持不变（回复有界，避免重回过载 slip）。
- 按 item 的 Solver fanout **暂不实施**，触发条件：
  1. 本轮批次跨 ≥3 个 item 且全部 finding 为 item 内（`item_id` 非空、无跨 item）；
  2. `ZHONGSHU_SOLVER` agent 池 ≥3 个身份（否则并行无加速）；
  3. 合并用逐 item `apply_solver_changes` 确定性完成，不得引入"总结 agent"；
  4. 跨 item finding 或合并后结构校验失败 → 整批回退单写手重做。
- 观测点：真机日志应出现 `solver_stage=revise` 的 context、含 `[Revision task]` 的
  prompt.txt；出错时是 `SOLVER_BATCH_MISMATCH`（一次重问）而非
  `DEFERS_BLOCKERS`/`OVERLAP`/三次盲重试。
