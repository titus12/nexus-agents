# 门下省组级流水线（共享文档模型）验收文档

- 日期：2026-09-23
- 状态：待验收
- 范围：`cmd/orchestrator/domain/*`、`cmd/orchestrator/contracts/*`、`cmd/orchestrator/runtime/agent_effects.py`
- 基线：`task-menxia-t1`（5 轮未过审，全程 125.5M tokens，其中 93% 为 cache_read；单轮墙钟 ~30min）

---

## 1. 背景与目标

### 1.1 问题（有实测证据）

item 波次流水线对同一组任务**全量重做**：

- 每个 item 独立跑 SOLVER→ANALYST→CRITIC，任一 item 修订就把同组其它 item 的方案一起重写（seq60 快照不一致 P1 实证：同组方案变形）；
- 收敛判据是"全部 item 过审"，一轮 30min，group-000001 磨 5 轮 0 过审；
- token 账：cache_read 125.5M（93%）≫ input 8.8M ≫ output 0.65M —— 重复读 prompt bundle 是大头。

### 1.2 目标

以**组为粒度**重构门下省流水线：

- 同组任务共享一份版本化方案文档（plan.md vN 模型），修订只改文档版本，不做全量重做；
- 组间并发、组内单执行者；item 降级为文档正文小节 + 需求映射；
- 目标：单轮 30min → 证据轮 ~10min，cache_read 大幅下降，收敛判据 / GATE / ESCALATED 语义不变。

---

## 2. 方案概要

```
ZHONGSHU_FREEZE_CHECK --FREEZE_APPROVED--> MENXIA_GROUP_SOLVER
MENXIA_GROUP_SOLVER   --FEASIBLE/READY_*--> MENXIA_GROUP_ANALYST
MENXIA_GROUP_ANALYST  --EVIDENCE_SUFFICIENT/...--> MENXIA_GROUP_CRITIC
MENXIA_GROUP_CRITIC   --APPROVE_GROUP--> MENXIA_GROUP_GATE --APPROVE_GROUP--> DONE
                             |_{NEXT_ITEM, REQUEST_GROUP_REVISION}--> MENXIA_GROUP_SOLVER
```

**文档生命周期**（`MenxiaGroupDoc`，纯 markdown，五节结构）：

1. SOLVER 出一版可执行方案（v1 骨架由 `render_initial` 从需求基线生成）；
2. ANALYST/CRITIC 只在"建议段"追加审核内容（其余节冻结，逐字节比对）；
3. SOLVER 吸收：改正文 + 吸收记录留痕即删；不采纳则内联回复，P0/P1 驳回必须提出方确认才销，P2/P3 一轮无异议自动关闭；
4. 版本号每轮 +1（SOLVER 推进）/ 不变（评审者），直到 analyst/critic 双签通过。

**防御纵深**（与 item 流水线镜像）：

| 层 | 机制 |
|---|---|
| 文档规则 | `verify_group_reply`：版本算术、越权改动、销账规则、冻结区比对，纯机械可执行 |
| 派发 | joiner 重算 `doc_hash`、权威提取 `open_count`；校验失败降级 `BLOCKED`（`MENXIA_GROUP_DOC_BREACH`） |
| 状态机 | 波次 drain-out 拦截：仅剩 parked 组 → `OPEN_HUMAN_GATE`（reason `MENXIA_GROUP_BLOCKED`） |
| 预算 | 组级 `revision_round` 共用 5 轮上限；stall 规则：open 计数连续 2 轮不降即 ESCALATED（首轮只记基线防误判） |

---

## 3. 交付清单（按模块）

### 3.1 纯文档模型 — `cmd/orchestrator/domain/menxia_doc.py`

- `MenxiaGroupDoc`：parse/render 往返、版本算术、五节结构、额外节透传；
- 生命周期：`absorb / reject / confirm_rejection / auto_close_stale_rejections / defer / prune_closed / sign_doc`；
- `converged()`：无 open P0/P1、无待确认驳回、analyst+critic 双签当前版本；
- `verify_absorption`：销账对账（堵住 SOLVER 偷删待确认驳回的漏洞）；
- `verify_group_reply`：按角色的机械校验（见 §2 表）；
- `render_initial`：v1 骨架种子。
- **测试**：`cmd/test_menxia_doc.py`，39 例。

### 3.2 上下文与序列化 — `cmd/orchestrator/domain/context.py`

- `MenxiaGroupState`：`stage / revision_round / doc_version / doc_hash / doc_ref / doc_markdown（权威正文随行）/ fingerprint / blocked_reason / last_open_count / stalled_rounds`；
- `ReviewState.menxia_groups[]` + `seed_menxia_groups()`（只为有 dispatchable item 的组播种）；
- `max_menxia_group_rounds = 5` 预算字段；
- `ReviewUpdate` / `apply_review_update` / `to_dict` / `from_dict` 全链路序列化（含旧快照兼容）。

### 3.3 组级策略 — `cmd/orchestrator/domain/policies/menxia_group.py`

- stage 映射：`MENXIA_GROUP_STAGE_BY_TARGET/BY_ACTION`（含 `REQUEST_EVIDENCE → ANALYZING`，critic 可直达 analyst）；
- `ready_stage_groups`：组间并发，`max_concurrent_groups` 封顶，按组序排序；
- `apply_menxia_group_results`：fold（`doc_hash` 由 fold 用 sha256 重算，不信任 worker；预算达上限或 stall 触发 → `ESCALATED`；`budget_reset` 解停清计数）；
- `menxia_group_wave_action`：HUMAN_GATE 优先，其余按 census 最早未排水 stage 路由；
- `menxia_group_pipeline_readiness`：组行版 gate readiness（报告形状与 item 版一致，envelope 不变）。
- **测试**：`cmd/test_menxia_group_policy.py`，22 例。

### 3.4 契约 — `cmd/orchestrator/contracts/menxia_group_{solver,analyst,critic}.py`

- envelope = 编排信号 + `doc_markdown` 全文随包（权威正文随 ReviewState 走，joiner/派发免异步 artifact 读）；
- 已注册进 `contracts/__init__.py`。

### 3.5 Joiner — `cmd/orchestrator/runtime/agent_effects.py`

- `_join_menxia_group`（`dispatch_mode="menxia_group_pipeline"` 路由）：
  - `verify_group_reply` 不过 → 降级 `BLOCKED`，带违规明细；
  - `doc_hash` joiner 重算；`open_count` 从解析后文档权威提取；
  - infra 失败返回 None 走标准重试；content 失败 → `BLOCKED` 带 error_code；
- `_sha256_hex` helper、`hashlib` 顶层导入。
- **测试**：`cmd/test_menxia_group_join.py`，7 例。

### 3.6 状态机 — `cmd/orchestrator/domain/{states,transitions}.py`

- 三个组状态类挂 `_menxia_group_wave_decision`（drain-out 拦截镜像 item 版 `_menxia_wave_decision`）；
- 组级 dispatch：三段式 bundle、`dispatch_mode="menxia_group_pipeline"`、binding context 携带 `group_id + doc_markdown`；首轮 SOLVER 用 `render_initial` 种子 v1 骨架；
- **入口切换（本轮）**：`FREEZE_APPROVED/APPROVE_FREEZE/FREEZE_OK → MENXIA_GROUP_SOLVER`；GATE 出边 `NEXT_ITEM/REQUEST_GROUP_REVISION → MENXIA_GROUP_SOLVER`；
- GATE 组级分支：组行优先（`menxia_items` 存在时走 item 遗留分支）；`APPROVE_GROUP` 整组折算完成项；`REQUEST_GROUP_REVISION` 支持 `group_ids` + item→group 映射 + `budget_reset` 解停；
- serial（menxia 禁用）路径：组目标附带 `dispatch_context`（含共享文档）；
- `_payload_update`：`APPROVE_GROUP` 事件按 `active_group_id` 整组折算 `completed_item_ids`（serial 组循环收口的关键，曾由此修复 GATE 无限轮询）；
- item 状态/边保留注册但已不可达（退役前保持矩阵完整），item 流水线代码与单测全部保留。

### 3.7 测试同步

- scripted e2e（entrypoint / convergence / stubborn-blocker）actions 切组状态名，一票通到 DONE；
- `MenxiaTransitionContractTests` 补全组级边；contract 主线测试更新为组主线；
- 两个 gate revision 测试移植到组级行（`budget_reset` 解停 ESCALATED→SOLVING、round 归零）；
- `cmd/test_group_doc_loop.py`（本轮新增，4 例）：全 app 零模型驱动并行组管线的完整共享文档闭环——v1 骨架 → SOLVER v2 起草（版本算术 + touched_scope）→ 双评审各追加 P1 建议（冻结区规则）→ REQUEST_SOLVER_REVISION → SOLVER v3 吸收（`verify_absorption` 销账对账）→ 双评审收敛 → GATE 一票 → DONE；断言 doc_version 链（3）、账本留痕、`revision_round ≤ 1`、整组完成项折算、波次 binding 数量。

---

## 4. 自动化验证（已通过）

| 项 | 结果 |
|---|---|
| 全量测试 `python cmd/run_tests.py` | **791 全绿**（本轮工作起点 719，新增 72 个用例） |
| 新增测试 | test_menxia_doc 39 + test_menxia_group_policy 22 + test_menxia_group_join 7 + test_group_doc_loop 4 |
| scripted 全程零模型 | entrypoint / convergence / stubborn-blocker 三条 scripted 线均 DONE，无超时空转；test_group_doc_loop 覆盖并行组管线的文档全生命周期 |

测试环境约束：`NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS=1`，经 `python cmd/run_tests.py` 执行（强制 NullNotificationPort，防止真实飞书外发）。

---

## 5. 需要人工审查的决策点

1. **GATE 语义变更**：组级 `APPROVE_GROUP` 一次折算整组完成项（含 `_payload_update` 按 `active_group_id` 的折叠）。人工组批准（gate agent 回 `REQUEST_GROUP_REVISION` + `budget_reset`）会整组重开、round 归零。是否符合运营预期？
2. **stall 规则阈值**：`MENXIA_GROUP_STALL_LIMIT = 2`（open 计数连续 2 轮不降即升级）。是否过激/过松？
3. **P2/P3 自动关闭**：一轮无异议即自动销账（`auto_close_stale_rejections`）。低优先级建议的"沉默即通过"策略是否接受？
4. **冻结区定义**：评审者不可改 baseline/body/ledger/signoff/extra 五节，只能在建议段追加。若评审者需要纠正正文错误，只能提建议让 SOLVER 改——多一轮交互，是否接受？
5. **item 流水线退役节奏**：本次只切入口，item 状态/边/契约/joiner 代码保留（不可达）。退役是独立清理 PR，避免大爆炸。
6. **anchor 盲区**：文档校验是结构性的，不判方案质量；"锚点盲区"靠 objective 级 amendment + GATE steelman 缓解，非本文档机制保证。

---

## 6. 线上验收步骤（待执行）

```powershell
# 1. 启动验收实例（独立端口，不动现有 8766 服务）
python cmd/run_menxia_test.py --task-id task-menxia-t1 --force   # 端口 18766
```

对比基线（task-menxia-t1）：

| 指标 | 基线 | 目标 |
|---|---|---|
| rounds-to-approve | 5 轮 0 过审 | 组级文档轮内收敛，预期 ≤2 轮 |
| 全程 tokens | 125.5M（93% cache_read） | 显著下降（观察 cache_read 占比与绝对值） |
| 单轮墙钟 | ~30min | 证据轮 ~10min |
| 方案一致性 | seq60 快照 P1（同组方案变形） | 同组共享一份文档，结构上消除 |

观察点：

- `runs/task-menxia-t1/workflow-events.jsonl`：组级波次、`menxia_stage_census`、`MENXIA_GROUP_DOC_BREACH`（若有，明细会写入 blocked_reason）；
- usage：`multica issue usage <run-id> --output json`（JSON 非法时需正则兜底，输出可能 UTF-16）；
- 组文档版本链：`doc_version` 单调、`doc_hash` 与文档内容一致（fold 重算）。

通过标准：三条 scripted 线全绿 + 线上跑通至 DONE + token/墙钟改善达到上表目标 + 无 `MENXIA_GROUP_DOC_BREACH` 反复出现。

---

## 7. 风险与回滚

| 风险 | 缓解 |
|---|---|
| worker 首次适配新契约，回包格式错误率高 | joiner 降级 BLOCKED 带 `MENXIA_GROUP_DOC_BREACH` 明细，走标准重试/升级，不会污染文档链 |
| 新状态漏登记 runtime skill binding | **线上已发生并修复**（task-menxia-t1 首轮 FAILED：`MENXIA_GROUP_SOLVER/ANALYST/CRITIC` 未登记进 `compat_effects._SKILLS`，派发被 `ACTIVE_RUNTIME_SKILL_BINDING_MISSING` 拒绝）；已补登记 + `test_active_runtime_skill_binding.py` 3 条防回归（全状态 locked、复用 item 技能文件、transport 出站请求带锁） |
| 组级入口基线 journal 污染 | **线上已发生并修复**：基线从失败 run 复制时带入了旧波次的 `effect_result`（node:25 SUCCEEDED / dispatch:26 FAILED）与 ack，resume 时 `pending_effects()` 按"已有终态结果"过滤掉待执行 effect、事件又全部已确认 → app 空转直至 `EVENT_INPUT_TIMEOUT`。修复：基线 journal 精准剔除失败波次的 7 条记录（保留 seq-25 transition），`JsonWorkflowRepository` 验证 `pending_effects` 恰为 `node:…:MENXIA_GROUP_SOLVER:25`、无积压事件。教训：**构建基线必须保证"待执行 effect 无终态结果、待投递事件无 ack"** |
| 新状态漏登记 agent 身份池（第二类登记遗漏） | **线上已发生并修复**：`app.py` 的 `agent_ids`（状态→multica 规范 agent UUID，env `AGENT_*_ID`）漏了三个 `MENXIA_GROUP_*` 状态 → `resolve_worker_agent` 返回空 → 回落 payload 里的 worker 名（`menxia-solver-01`），multica CLI 拒绝：`resolve assignee: expected a canonical UUID`。solver 波次 worker 全灭（joiner 降级 BLOCKED/RUNTIMEERROR 后聚合仍推进）+ ANALYST 单发直败，run FAILED@seq27。修复：提取 `_agent_pool_by_state()` 补齐三键；防回归升级为按 `_ROLE_BY_STATE` 全状态覆盖断言（skill 表 + agent pool 表各一条），新状态进 `_ROLE_BY_STATE` 而不登记任何一张表都会测试失败 |
| stall/预算规则误杀 | 首轮只记基线不判 stall；gate `budget_reset` 可人工解停 |
| 组文档膨胀 | 吸收即删（留痕账本）；建议段生命周期自动清理 P2/P3 |
| 入口切换影响存量任务 | 旧快照含 `menxia_items` 时 gate 自动走 item 遗留分支；旧 menxia 快照无需迁移（`baselines/zhongshu-final` 冷启动） |

### 7.1 审查问题修复（2026-09-23，803 测试全绿）

代码审查确认的 4 个问题全部修复，各配防回归：

| # | 问题 | 修复 | 防回归 |
|---|---|---|---|
| 1 | menxia disabled（默认 False）时冻结批准仍无条件进入组流水线：绑定/joiner/文档折叠全部被 `enabled` 闸住 → 静默按"无文档串行"退化 | `ZhongshuFreezeCheckState.handle` 拦截：disabled 时冻结批准（`APPROVE_FREEZE/FREEZE_OK/FREEZE_APPROVED`，`policies/zhongshu.py::MENXIA_FREEZE_ENTRY_ACTIONS`）→ 显式 BLOCKED（`MENXIA_PARALLEL_DISABLED`），不再静默退化、也不复活已退役的 item 串行链（`NEXT_ITEM` 边已改指 GROUP_SOLVER，item 串行回路本身是断的） | `test_menxia_pipeline.py` 两条（disabled→BLOCKED / enabled→组波次）；3 个脚本化 e2e（entrypoint、convergence、item workflow）全部改为启用 menxia 走**真实组契约波次**（复用 `test_group_doc_loop.py::GroupWaveScripting` 文档演进 mixin，`critic_demands_revision=False` 短回路），e2e 从"串行空转"升级为主线实测 |
| 2a | `converged()` 死语义（签核无调用方）→ critic 可在文档仍有未决阻断建议时 APPROVE_GROUP 直通 gate | joiner 侧收敛前置检查（最小版）：critic 的 APPROVE_GROUP 若文档 `open_blocking()`/`pending_rejections()` 非空 → 折叠为 `REQUEST_SOLVER_REVISION` 回 solver；完整版（orchestrator 盖签核 + gate 校验 `converged()`）留验收后 | `test_menxia_group_join.py::TestGroupCriticJoin` 两条（有 open P1 → 折回；干净文档 → 放行） |
| 3 | `_menxia_gate_revision_group_ids` 只读顶层 `group_ids/groups`，gate 修订要求若只写在 `required_changes` 里 → 无目标 → 误转人工闸 | 解析 `required_changes` 中的组目标（裸 `group-…` 字符串 / 对象的 `target`、`group_id` 字段）；契约 `menxia_group_gate.py` FIELDS 补声明 `group_ids`（`array(string())`）一级通道 | `test_menxia_pipeline.py::MenxiaGateRevisionTargetTests` 3 条（required_changes 提取与去重排序 / 显式 group_ids 优先 / 不可执行要求回空列表→人工闸） |
| 4 | joiner 组身份取回包 `group_id` 优先于派发绑定（schema `additionalProperties: True` 拦不住伪造），首轮 solver 校验最松 | 绑定优先：`group_id = context["group_id"]`（binding_contexts 的 dispatch_context），回包声明别的 group_id → BLOCKED 行 `MENXIA_GROUP_IDENTITY_MISMATCH`（注明回包声称值与绑定值），永不折入任何文档链 | `test_menxia_group_join.py::test_reply_group_id_mismatch_blocks` |

**回滚**：入口边（transitions.py 三处 + GATE 两处）改回 `MENXIA_ITEM_SOLVER` 即恢复 item 主线；组级代码不可达、无副作用。单 PR 粒度，无数据迁移。

---

## 8. 审查建议路径

按依赖顺序读代码：

1. `menxia_doc.py`（纯模型，无外部依赖）→ `test_menxia_doc.py`；
2. `policies/menxia_group.py`（纯策略）→ `test_menxia_group_policy.py`；
3. `contracts/menxia_group_*.py`（envelope 形状）；
4. `agent_effects.py::_join_menxia_group`（防御纵深）→ `test_menxia_group_join.py`；
5. `states.py`：`_menxia_group_bindings / _menxia_group_dispatch_context / _menxia_group_wave_decision / MenxiaGroupGateState.handle`；
6. `transitions.py` 入口边 diff。
