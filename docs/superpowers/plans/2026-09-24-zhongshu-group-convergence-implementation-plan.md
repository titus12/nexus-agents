# 中书省组级收敛与产物形态 实施方案

> 需求来源：`docs/2026-09-24-zhongshu-group-convergence-requirements.md`
> 本方案所有设计选择已定稿，实施按任务顺序执行，不再需要额外决策。

**Goal：** 中书省以组为收敛单元——每组独立修订预算（5 轮）与停滞判定（open 计数连续 2 轮不降），一组停滞不阻塞他组；批次内 finding 逐条处置对账，未授权组的需求文档逐字保留；冻结产物为每组一份九节需求文档（机械校验），门下省 body 为六部分可实施方案（收敛校验）；首个组冻结即进门下省，未冻结组经 gate 回边返回中书省。

**Architecture（定稿决策）：**

- **组记账行** `ZhongshuGroupState`：镜像 `MenxiaGroupState`（context.py:441），字段 `group_id / stage / revision_round / last_open_count / stalled_rounds / last_verdict / blocked_reason / doc_version / doc_hash / doc_markdown / doc_source_hash`；stage 封闭枚举 `REVIEWING|CONVERGED|FROZEN|STALLED|BLOCKED`；`max_zhongshu_group_rounds = 5`。
- **轮次归属（attempt-scoped）**：一轮修订的 scope = `attempted_item_ids`（context.py:159，既有字段）所属组；scope 内非终态组 `revision_round+1`；scope 外组不消耗预算，仅重算 CONVERGED。总修订轮次上界 = 组数 × 5（每轮至少 bump 一个组），全局轮次 fuse 退役。
- **停滞规则**：组内 open P0/P1 计数连续 2 轮不降 → STALLED（首轮只记基线），逐字镜像 `MENXIA_GROUP_STALL_LIMIT = 2`（menxia_group.py:60、170-173）。全局 `blocker_fingerprint` no-progress fuse 退役为观测记录；finding 级 stuck fuse（`stuck_blockers`，zhongshu.py:259）保留。
- **修订对账**：solver 协议已有 `finding_resolutions`（contracts/zhongshu_solver.py:55-56），新增机械校验——batch 内每个 selected finding 必须有 `response ∈ {absorbed, rejected}` 的处置记录，缺即整包拒绝。P0/P1 驳回 → `REJECTED_PENDING` 待提出方确认；P2/P3 驳回一轮无再提出自动销。
- **冻结区**：item 级既有语义保留（`revision_scope` + `carry_forward_revision_items`，solver.py:128/171、solver_plan.py:405-463），扩展到组文档——非授权组提交的需求文档与权威不一致 → `SOLVER_GROUP_DOC_FROZEN` 拒绝（retryable），不静默携带。
- **需求文档**：九节 markdown，solver 随 plan 一并提交（contract 增可空 `group_docs` 字段，v1 不 bump），编排器 `verify_doc` 机械校验（九节齐备 / 目标节措辞黑名单 / §8 验收标准与 `acceptance_signals` 双向闭合 / 无实现标记），fold 重算 `doc_hash`、版本单调。
- **提前接手**：单条新回边 `MENXIA_GROUP_GATE + REQUEST_NEXT_GROUP → ZHONGSHU_CRITIC`；`seed_menxia_groups` 播种门控改为仅 FROZEN 组；无主线边变更。
- **门下省 body 形态**：body 节内六个固定 `###` 子标题（目标与设计决策 / 现状与目标行为 / 统一约束 / 任务分解 / 验收映射 / 风险与兼容），`verify_group_reply` solver 分支结构校验；critic APPROVE_GROUP 前置检查扩展：验收映射左侧 ⊆ 组需求文档 §8、无未定稿标记、例外行含替代验证与补测触发。

**Tech Stack：** Python（cmd/orchestrator），unittest，`python cmd/run_tests.py`（强制 NullNotificationPort）。

**不改动：** 门下省组流水线机制语义（文档生命周期、销账规则、stall 阈值、GATE 行为）；fast_track 分支；MENXIA_ITEM_* 退役代码；通知文案结构；合同 ID 版本；GATE/ESCALATED 既有语义；`HybridCLRData/`（不适用本仓）。

---

## 0. 现状与目标行为

| 场景 | 当前行为（已核实） | 目标行为 | Task |
|---|---|---|---|
| 组间预算 | 全局 `zhongshu_revision_round=8`（context.py:137-138）；任一修订消耗全局一轮 | 每组独立 5 轮；已 CONVERGED 组不消耗预算 | 1/2/6 |
| 顽固 item | `stalled_item_ids`（changes_rounds≥5）触发全局人闸（zhongshu/critic.py:115-120）；9/10 过审仍 `ZHONGSHU_FREEZE_INCOMPLETE` 人闸（states.py:1810-1818） | 顽固组 STALLED 隔离人闸；其余组照常冻结 | 2/6/7 |
| 无进展判定 | 全图 `blocker_fingerprint` + `no_progress_count`（zhongshu/critic.py:108-112） | 组级 open 计数停滞规则；指纹 fuse 退役；stuck fuse 保留 | 2/6 |
| 修订对账 | `finding_resolutions` 协议字段存在，但仅要求"存在任一响应"（solver_plan.py:99-129） | batch 内逐 finding 处置记录，缺记录整包拒绝 | 3 |
| 驳回销账 | 驳回 finding 保持 OPEN，critic 重审是唯一确认手段 | P0/P1 待确认销账；P2/P3 一轮无再提出自动销 | 3 |
| 冻结区 | `revision_scope` 排除已批准 item；`carry_forward_revision_items` 逐字保留未授权 item | 语义保留并扩展到组文档：非授权组文档改动即拒 | 4 |
| 需求产物 | plan JSON（items/groups/requirements），无成文需求文档；`acceptance_signals` 由 solver 自由撰写 | 每组九节需求文档随 plan 提交；§8 与 acceptance_signals 双向闭合；冻结检查机械校验 | 5/7 |
| 冻结粒度 | 全图 unapproved 检查（zhongshu.py:410-439）→ 全局人闸 | 逐组冻结检查（形态 + 依赖）；组置 FROZEN | 7 |
| 人闸恢复 | gate 恢复按 resume_state 重入，无定向解停 | 指名组 budget_reset：round 归零、stall 清零，他组不动 | 7 |
| 门下省接手时点 | 全图冻结后一次性进入 | 首个组 FROZEN 即进入；未冻结组经 gate 回边返回 | 8 |
| 门下省正文 | 五节文档 body 内容自由（menxia_doc.py:61） | body 六部分固定子标题；验收映射闭合、零遗留决策、例外三要素机械校验 | 9 |

---

## 1. 统一约束（所有任务适用）

1. 每个任务先写测试并确认按预期失败（红），再改生产代码；全量测试经 `python cmd/run_tests.py`（环境强制 `NullNotificationPort`，防止真实飞书外发）。
2. 合同 ID 不 bump：`zhongshu_solver` 增可空 `group_docs` 字段、`menxia_group_solver` 校验 body 子标题，均为向后兼容演进；worker 回包缺新字段 → retryable 形状违约，走 reply-retry 预算（menxia 首轮适配同款风险，见 `2026-09-23` 验收文档 §7）。
3. 不新增第三方依赖；新文件 UTF-8 无 BOM；代码注释英文、风格对齐周边。
4. 通知文案结构不变；新增 reason code（`ZHONGSHU_GROUP_STALLED`、`ZHONGSHU_DOC_FORM_INVALID`）走既有 reason 通道。
5. FSM 主线边不变；Task 8 仅新增一条 role 边；MENXIA_ITEM_* 代码不触碰。
6. fast_track 分支（states.py:1785-1799）不动；组记账与 `menxia.enabled` 无关（zhongshu 阶段先于 menxia 门控运行）。
7. 验证用独立端口实例（18766），不动 8766 服务；Git 写操作需用户另行授权。

---

## Task 1：组级记账数据基座

**分类：** 新增（纯结构，先写测试）。

**Files：**

- `cmd/orchestrator/domain/context.py`
- `cmd/test_zhongshu_group_state.py`（新增）

### 1.1 先写测试（红）

1. `SeedZhongshuGroups_CoversEveryTaskGroup`：3 组 plan → `seed_zhongshu_groups()` 返回 3 行，stage 全 `REVIEWING`，round=0。
2. `ZhongshuGroupState_RoundTripsThroughDict`：全字段行 to_dict/from_dict 往返相等；缺省字段取默认值。
3. `LegacySnapshotWithoutZhongshuGroups_SeedsEmpty`：旧 review DTO（无 `zhongshu_groups` 键）from_dict 成功，字段为空元组；`apply_review_update` 传递 `zhongshu_groups`。

### 1.2 实现

1. `context.py` 在 `MenxiaGroupState`（:441-500）之后新增 `ZhongshuGroupState`，字段见 Architecture；`to_dict`/`from_dict` 仿 `MenxiaGroupState`（:470-500），`_int` 容错同款。
2. `ReviewState` 增两个字段：`zhongshu_groups: tuple[ZhongshuGroupState, ...] = ()`（`menxia_groups` :186 之后）、`max_zhongshu_group_rounds: int = 5`（`max_menxia_group_rounds` :146 旁）。
3. `ReviewState.seed_zhongshu_groups()`：为 `task_groups` 每组播种（zhongshu 阶段全组在审，无 dispatchable 过滤；已有行不重播）。
4. `ReviewUpdate` 增 `zhongshu_groups`（:543 后）；`apply_review_update` 增传递分支（:670 后，仿 `menxia_groups` :670-674）。
5. review DTO 的 `to_dict`/`from_dict` 区（文件尾序列化段，仿照 `menxia_groups` 键的既有读写）补 `zhongshu_groups`。

### 1.3 验证

- 1.1 三测试转绿；`python cmd/run_tests.py` 全量无回归。

---

## Task 2：组级折叠与停滞策略（纯函数）

**分类：** 新增（纯策略，先写测试）。

**Files：**

- `cmd/orchestrator/domain/policies/zhongshu_group.py`（新增）
- `cmd/test_zhongshu_group_policy.py`（新增）

### 2.1 先写测试（红）

| 测试 | 断言 | 预期红 |
|---|---|---|
| `ApplyRound_BumpsOnlyScopeGroups` | 3 组，attempted items 属组 A/B：仅 A/B round+1，C 不变 | 红或不实现 |
| `ApplyRound_ConvergedGroupSkipsBudget` | 组 A 全员 ledger APPROVED 且无 open blocker：round 不增、stage=CONVERGED | 红 |
| `StallRule_FirstRevisionRound_OnlyBaseline` | 组首轮修订 open 计数 3：stalled_rounds=0（基线） | 红 |
| `StallRule_TwoRoundsNoShrink_Escalates` | 第二轮 open 计数仍 3 → stage=STALLED | 红 |
| `StallRule_OpenCountDrops_ResetsCounter` | 计数 3→1 → stalled_rounds=0 | 红 |
| `BudgetExhausted_EscalatesEvenWithProgress` | round 达 5 且计数在降 → STALLED | 红 |
| `FreezeReady_RequiresDependencyGroupsFrozen` | B 依赖 A：A=REVIEWING 时 B 不 ready；A=FROZEN 后 ready | 红 |
| `Drainout_AllRemainingParked_True` | 剩余组全 STALLED/BLOCKED → True；有 REVIEWING → False | 红 |
| `OutOfScopeGroup_ReconvergesWithoutBudget` | scope 外组因 ledger ratchet 达成收敛 → stage=CONVERGED、round 不变 | 红 |

### 2.2 实现

`policies/zhongshu_group.py`（镜像 `menxia_group.py` 的纯函数风格）：

1. 常量 `ZHONGSHU_GROUP_STALL_LIMIT = 2`；`_ACTIVE_STATUSES`/`_BLOCKING_SEVERITIES` 从 `zhongshu.py:45-48` 复用导入。
2. `group_item_ids(review, group_id)`、`group_of_item(review, item_id)`、`group_dependencies(review, group_id)`（组成员 item.dependencies → 依赖 item 所属组集合）。
3. `group_open_blockers(review, group_id)`：open P0/P1 且 item_id ∈ 组成员的 finding 元组。
4. `zhongshu_group_converged(review, group_id)`：`approved_item_ids`（zhongshu.py:533）覆盖全成员 且 `group_open_blockers` 为空。
5. `apply_zhongshu_round(review, *, attempted_item_ids, max_rounds) -> tuple[ZhongshuGroupState, ...]`：scope = attempted items 的组集合；scope 内非 CONVERGED/FROZEN 组 round+1，停滞判定逐字镜像 menxia_group.py:170-183（`row.revision_round > 0 and open_count >= row.last_open_count` 基线规则）；达预算或停滞 → STALLED；scope 内 + scope 外全部组按 `zhongshu_group_converged` 重算 stage（scope 外不计预算、不清停滞计数）；返回按 `seed_zhongshu_groups()` 顺序排列（镜像 menxia_group.py:217-219）。
6. `freeze_ready_groups(review)`：CONVERGED 且 `group_dependencies` 全 FROZEN，按 `task_groups.order` 排序。
7. `zhongshu_drainout_parked(review)`：非 FROZEN 组全部 ∈ {STALLED, BLOCKED} 且至少一组存在。

### 2.3 验证

- 2.1 全绿；全量回归。

---

## Task 3：修订对账——处置记录强制与销账生命周期

**分类：** 行为变更（先写测试）。

**Files：**

- `cmd/orchestrator/domain/policies/solver_plan.py`
- `cmd/orchestrator/domain/findings.py`
- `cmd/orchestrator/domain/policies/zhongshu.py`
- `cmd/orchestrator/domain/zhongshu/solver.py`
- `cmd/orchestrator/domain/states.py`
- `cmd/test_solver_disposition_ledger.py`（新增）

### 3.1 先写测试（红）

| 测试 | 断言 | 预期红 |
|---|---|---|
| `RevisionWithoutDispositions_Rejected` | batch selected=[F1,F2]，`finding_resolutions=[]` → error `SOLVER_RESOLUTION_COVERAGE_INCOMPLETE:missing=['F1','F2']` 且 retryable | 红 |
| `PartialDispositions_RejectedWithMissingIds` | 仅 F1 有记录 → missing=[F2] | 红 |
| `DispositionResponseUnknownValue_Rejected` | response="ignored" → 同上（合法值仅 absorbed/rejected） | 红 |
| `AbsorbedFinding_ClosesWithResolution` | `apply_dispositions` 后 finding status=CLOSED、resolution 含处置说明 | 红 |
| `RejectedP0_GoesRejectedPending_NotClosed` | P0 rejected → status=REJECTED_PENDING、disposition=rejected | 红 |
| `RejectedP2_AutoClosesAfterSilentRound` | P2 REJECTED_PENDING，下一轮无再提出 → CLOSED（resolution 注明 auto） | 红 |
| `RejectedP1_ConfirmedByCritic_Closes` | critic finding_responses 确认维持驳回 → CLOSED；critic 再提出（同 canonical_key）→ 复活 OPEN | 红 |

### 3.2 实现

1. `solver_plan.py`：
   - 新增 `solver_resolution_coverage_error(selected_finding_ids, payload) -> str`：`finding_resolutions` 中每个条目经 `resolve_finding_ids` 规范化（复用 ：177-213）后必须覆盖全部 selected，且 `response` 规范化小写后 ∈ {"absorbed","rejected"}；缺 → `SOLVER_RESOLUTION_COVERAGE_INCOMPLETE:missing=[...]`。
   - 前缀加入 `_RETRYABLE_SOLVER_REPLY_PREFIXES`（:59-77）。
2. `zhongshu/solver.py`：`ReviseSolverLogic.validate`（:219-232）在 batch 覆盖检查后调用新检查（selected 取 `self._batch.selected_finding_ids`）。
3. `findings.py`：`Finding` 增 `disposition: str = ""`（:69 `resolution` 旁）；`from_dict` 已按 `__dataclass_fields__` 过滤（:132-133），旧快照兼容，无迁移。
4. `policies/zhongshu.py` 新增：
   - `apply_dispositions(findings, resolutions, round)`：absorbed → status=CLOSED、`disposition="absorbed"`、resolution 追加处置说明；rejected → `disposition="rejected"`、P0/P1 与 P2/P3 均 status=`REJECTED_PENDING`（销账由 settle 决定）。
   - `settle_rejected_findings(findings, critic_responses, reraised_keys)`：REJECTED_PENDING 且 critic responses 确认维持驳回 → CLOSED；P2/P3 的 REJECTED_PENDING 在本轮折叠无再提出（canonical_key 不在 reraised）→ 自动 CLOSED（`disposition="rejected-auto"`）；再提出 → 复活 OPEN、清 disposition。
5. `states.py`：`_review_update`（:596）findings 折叠链在 `merge_findings`（zhongshu.py:82）之后接入 `apply_dispositions`（solver 轮）与 `settle_rejected_findings`（critic 轮，reraised = 本轮新 findings 的 canonical_key 集合）。

### 3.3 验证

- 3.1 全绿；既有 `test_solver_batch_handoff.py`、`test_finding_identity_merge.py` 全量回归（销账不得破坏 merge 的 content-identity 语义）。

---

## Task 4：冻结区强化——组文档逐字保留与越界拒绝

**分类：** 行为变更（依赖 Task 1/5 的组行与文档字段；先写测试）。

**Files：**

- `cmd/orchestrator/domain/zhongshu/solver.py`
- `cmd/orchestrator/domain/policies/solver_plan.py`
- `cmd/test_solver_group_doc_scope.py`（新增）

### 4.1 先写测试（红）

| 测试 | 断言 | 预期红 |
|---|---|---|
| `RevisionSubmittingForeignGroupDoc_VariantRejected` | 组 A 不可编辑，回包 group_docs 带组 A 改版文档 → error `SOLVER_GROUP_DOC_FROZEN:['group-000001']` 且 retryable | 红 |
| `ForeignGroupDocIdentical_Accepted` | 与权威逐字节一致 → 通过 | 红 |
| `ForeignGroupDocOmitted_AuthoritativeKept` | 未提交 → 权威保留（折叠行 doc_markdown 不变） | 红 |
| `EditableGroupDoc_ReplacesAndBumpsVersion` | 组 B 可编辑，提交新版 → doc_markdown 更新、doc_version+1、doc_hash 重算 | 红 |
| `FrozenGroupNeverEditable_EvenIfBatchNamesIt` | batch finding 属 FROZEN 组（防御路径）→ editable 组集合排除之 | 红 |

### 4.2 实现

1. `zhongshu/solver.py`：
   - `batch_group_scope(review, batch)`：`batch_item_scope`（:103-125）的组级版本——batch findings 的 owner 组 − approved − FROZEN 组。
   - `ReviseSolverLogic.materialize`（:239-246）：对 `payload["group_docs"]` 执行 `carry_forward_group_docs`（新纯函数，放 solver_plan.py）——非 scope 组的提交文档与 `ZhongshuGroupState.doc_markdown` 逐字节比对，不一致收集为 `SOLVER_GROUP_DOC_FROZEN:<sorted group_ids>`；scope 组文档替换进折叠结果。error 走既有 `SolverReplyOutcome.error` 通道。
2. `build_solver_dispatch` REVISE 分支（:333-372）：`dispatch_context` 增 `group_docs_authoritative`（全部组当前权威 markdown 的 dict，供 solver 无读档修订）。
3. `solver_plan.py`：新增 `carry_forward_group_docs(submitted_docs, current_rows, editable_group_ids)`；`SOLVER_GROUP_DOC_FROZEN:` 与 `SOLVER_GROUP_DOC_MISSING:`（Task 5 引入）加入 retryable 前缀表。

### 4.3 验证

- 4.1 全绿；全量回归。

---

## Task 5：组需求文档模型（九节）

**分类：** 新增（模型 + 契约字段，先写测试）。

**Files：**

- `cmd/orchestrator/domain/zhongshu_doc.py`（新增）
- `cmd/orchestrator/contracts/zhongshu_solver.py`
- `cmd/orchestrator/domain/zhongshu/solver.py`
- `cmd/test_zhongshu_doc.py`（新增）

### 5.1 先写测试（红）

| 测试 | 断言 | 预期红 |
|---|---|---|
| `DocRoundTrips_ParseRender` | 九节文档 parse→render 往返逐字节相等 | 红 |
| `MissingSection_Violation` | 删去 §6 → 违规 `missing_section:6` | 红 |
| `SectionOrderEnforced` | §8 出现在 §3 前 → 违规 `section_order` | 红 |
| `UndecidableWordingInGoals_Rejected` | §2 含"尽量" → 违规 `undecidable_wording:2` | 红 |
| `AcceptanceClosure_BothDirections` | §8 行与组内 items acceptance_signals 互为子集 → 通过；孤儿验收行 / 无验收 item 各判违规 | 红 |
| `ImplementationMarkers_Rejected` | §5 含代码围栏 → 违规 `implementation_marker:5` | 红 |
| `TitleVersionArithmetic` | 标题 `# group-000001 需求文档 [v2]` 解析版本 2；v1→v2 合法、跳版非法 | 红 |
| `SolverReplyWithoutGroupDocs_MissingError` | FORMALIZE 回包无 group_docs → `SOLVER_GROUP_DOC_MISSING:all` 且 retryable | 红 |
| `FoldRecomputesHashAndBumpsVersion` | 接受的 group_docs 折叠后 doc_hash==sha256(markdown)、doc_version 单调 +1 | 红 |

### 5.2 实现

1. `zhongshu_doc.py`（镜像 menxia_doc.py 结构，:28-66 常量区、:130 类区、:593 verify 区）：
   - `SECTION_TITLES = ("背景","目标","标识与范围","状态与边界语义","行为要求","责任边界","交叉不变量","验收标准","非目标")`；节标题 `## <n>. <名>`；文档标题 `# <group_id> 需求文档 [v<N>]`（正则仿 menxia_doc.py:28）。
   - `UNDECIDABLE_PHRASES = ("尽量","尽可能","应该更好","酌情","视情况","大概")`；`IMPLEMENTATION_MARKERS`：代码围栏（```）与 `def `/`class ` 行首标记。
   - `ZhongshuRequirementDoc`：parse/render、`verify(review, group_id)`（返回违规明细：九节齐备有序、目标节黑名单、§8 行集合与组内 items `acceptance_signals` 双向闭合——规范化空白后逐条互配、实现标记）、`version` 解析。
   - `_MAX_REPLY_DOC_CHARS = 131_072`（沿用 menxia_doc.py:589 上限，超长即违规）。
2. `contracts/zhongshu_solver.py`：FIELDS 增 `"group_docs": array(object_schema({"group_id": string(), "markdown": string()}, required=("group_id", "markdown")), nullable=True)`（:67-71）；`contract_id` 不变。
3. `zhongshu/solver.py`：
   - FORMALIZE：回包缺任一组的 group_docs → `SOLVER_GROUP_DOC_MISSING:<group_ids 或 all>`（retryable）。
   - REVISE：scope 组缺文档 → 同上；非 scope 组走 Task 4 冻结区检查。
   - 通过后每份文档 `verify(...)`，违规 → `SOLVER_GROUP_DOC_INVALID:<明细前 10 条>`（retryable）。
   - 折叠（`process_solver_reply` :259-289 成功路径）：写入对应 `ZhongshuGroupState`（doc_markdown、doc_hash=sha256 重算不信任 worker、doc_version+1、doc_source_hash=当前 plan 投影 sha256）。
4. `ZHONGSHU_DOC_FORM_INVALID` 作为冻结检查（Task 7）对外的 reason code，本任务只定义文档侧明细格式。

### 5.3 验证

- 5.1 全绿；全量回归。

---

## Task 6：ZhongshuCriticState 组级接线

**分类：** 行为变更（本方案风险最高的任务；先写测试，保留原路径直至绿）。

**Files：**

- `cmd/orchestrator/domain/states.py`
- `cmd/orchestrator/domain/zhongshu/critic.py`
- `cmd/test_zhongshu_critic_group_gate.py`（新增）

### 6.1 先写测试（红）

| 测试 | 断言 | 预期红 |
|---|---|---|
| `RevisionRound_FoldsGroupRows` | 一轮 REQUEST_SOLVER_REVISION 后 ReviewUpdate.zhongshu_groups 的 scope 组 round+1 | 红 |
| `ConvergedGroup_NotConsumedByOtherGroupsRevisions` | 组 A CONVERGED 后组 B 再磨 3 轮：A 的 round 恒 0? （A 冻结前 round 保持、stage 保持） | 红 |
| `ApprovalHold_IgnoresFrozenGroupItems` | 组 A FROZEN、组 B 有 unapproved：APPROVE_CRITIC 不再被 A 的 items 拦截（pending 只算 B） | 红 |
| `Drainout_ParkedOnly_OpenHumanGateWithGroupList` | 剩余组全 STALLED → OPEN_HUMAN_GATE，reason=`ZHONGSHU_GROUP_STALLED`，payload 列各组 open findings 与 unapproved items | 红 |
| `StuckFindingFuse_Retained` | 同一 finding stuck_rounds 超限 → 仍 `ZHONGSHU_STUCK_FINDING` 人闸（不受组状态影响） | 红 |
| `FollowupFreeze_StillBounded` | 残余 P1 + 全 expected reviewed → FREEZE_WITH_FOLLOWUPS 语义不变 | 红 |
| `GlobalFingerprintFuse_Retired` | blocker_fingerprint 连续不变但各组 open 计数在降 → 不再 BLOCKED（ZHONGSHU_NO_PROGRESS 不触发） | 红 |
| `RevisionBudget_GroupScoped` | 组 A 磨满 5 轮 STALLED；组 B 第 6 轮修订仍被允许（不再受全局 8 轮限制） | 红 |

### 6.2 实现

1. `zhongshu/critic.py`：`evaluate_gate` 增参 `use_fingerprint_fuse: bool = True`；为 False 时跳过 no_progress 累计分支（:108-112 与 :162-168），stuck 分支（:152-161）与 FREEZE_WITH_FOLLOWUPS（:126-144）不动。
2. `states.py` `ZhongshuCriticState`：
   - 三个分支（APPROVAL :1800-1825 / REVISION :1826-1867 / EVIDENCE :1868-1908）统一在折叠后调用 `apply_zhongshu_round(review_after, attempted_item_ids=review.attempted_item_ids, max_rounds=review.max_zhongshu_group_rounds)` 并写入 `ReviewUpdate.zhongshu_groups`。
   - APPROVAL hold 的 pending 计算（:1803）改为过滤 FROZEN 组 items（新 helper `_pending_unfrozen_items(review, round_after)`）。
   - `_gate_verdict`（:1946-1956）：`evaluate_gate(..., use_fingerprint_fuse=False)`；`revision_allowed` 改传"任一非终态组有剩余预算"（`row.stage in ("REVIEWING","CONVERGED") and row.revision_round < max_rounds`）。
   - `_apply_gate_verdict`（:1958-2050）：HUMAN_GATE 分支前插入 drain-out 拦截——`zhongshu_drainout_parked(review_after)` 为真 → `_human_gate_decision(reason="ZHONGSHU_GROUP_STALLED")`，gate request payload 列出各组未决（open finding ids + unapproved items）与可解停标记（供 Task 7 的定向解停）。`ZHONGSHU_ITEM_STALLED` 分支保留在其后。
   - `zhongshu_revision_round` 字段保留累计（观测用），不再参与 gate。
3. 兼容：组行缺失（旧快照）时 `apply_zhongshu_round` 先播种（`seed_zhongshu_groups`），行为等价。

### 6.3 验证

- 6.1 全绿；`test_zhongshu_convergence_e2e.py`、`test_linear_fsm_entrypoint.py`、`test_zhongshu_convergence_e2e.py` 三条 scripted 线全绿（全量测试命令见 §验证命令）。

---

## Task 7：组级冻结检查与定向解停

**分类：** 行为变更（依赖 Task 5/6；先写测试）。

**Files：**

- `cmd/orchestrator/domain/states.py`（ZhongshuFreezeCheckState :2052-2100）
- `cmd/orchestrator/domain/policies/zhongshu_group.py`
- `cmd/test_zhongshu_freeze_groups.py`（新增）

### 7.1 先写测试（红）

| 测试 | 断言 | 预期红 |
|---|---|---|
| `FreezeApproval_MarksReadyGroupsFrozen` | A CONVERGED 且依赖满足 → FROZEN；B 未收敛保持 CONVERGED/REVIEWING | 红 |
| `FreezeWithUnfrozenGroups_StaysInFreezeCheck` | 存在非 FROZEN 组时不进入门下省（等待下一轮 critic 波次） | 红 |
| `DocFormViolation_RejectsWithDetails` | 组文档缺节 → FREEZE_REJECTED，payload 带组 id 与 `ZHONGSHU_DOC_FORM_INVALID` 明细，该组 stage 回 REVIEWING | 红 |
| `DependencyNotFrozen_RejectsFreeze` | B CONVERGED 但 A 未 FROZEN → B 不冻结（`freeze_ready_groups` 已保证，此处验证接线） | 红 |
| `GlobalChecksRunOnceBeforeFirstFreeze` | 依赖环（structural_gate 类）在首个组 FROZEN 前拦截一次；已有 FROZEN 行时不再重复全图校验 | 红 |
| `GateReset_NamedGroupOnly` | 恢复事件 payload 带 `zhongshu_group_resets=[{group_id: A, budget_reset: true}]` → A round=0、stalled_rounds=0、stage=REVIEWING；B 的 round 不变 | 红 |

### 7.2 实现

1. `ZhongshuFreezeCheckState.handle`（:2062-2100）：
   - freeze approval actions（`MENXIA_FREEZE_ENTRY_ACTIONS`）先取 `freeze_ready_groups(review)`；逐组 `verify_doc`；全过 → 全部置 FROZEN（fold `zhongshu_groups`），随后按既有出边进入 MENXIA_GROUP_SOLVER；存在未 ready 组时不放行出边——decision：仍走 APPROVE 语义但目标出边仅当"全部组 FROZEN"才触发（Task 8 改为部分放行），否则落入 `REQUEST_SOLVER_REVISION` 边回到 solver 继续磨剩余组（payload 指名剩余组）。
   - 形态违规组 → `FREEZE_REJECTED`，`freeze_check_attempt` 预算沿用（:2066-2087），payload 带违规明细；违规组 stage 折回 REVIEWING。
   - 全局校验项（`structural_gate` 的依赖环等）在无任何 FROZEN 行时执行一次，已有时跳过。
2. 定向解停：human gate 恢复事件（resume 到 ZHONGSHU_SOLVER/ZHONGSHU_CRITIC）payload 支持 `zhongshu_group_resets`（gate agent 回包格式镜像 menxia `REQUEST_GROUP_REVISION` + `budget_reset`，states.py:2307-2322 模式）；两个 state 的 handle 在折叠前处理 resets → 对应行 round=0、stalled_rounds=0、stage=REVIEWING。

### 7.3 验证

- 7.1 全绿；全量回归。

---

## Task 8：提前接手——FSM 回边与 FROZEN 门控播种

**分类：** FSM 边界变更（独立 PR 粒度；依赖 Task 7；先写测试）。

**Files：**

- `cmd/orchestrator/domain/transitions.py`
- `cmd/orchestrator/domain/context.py`（seed_menxia_groups）
- `cmd/orchestrator/domain/states.py`（MenxiaGroupGateState、_task_review_bindings）
- `cmd/test_menxia_pipeline.py`、`cmd/test_zhongshu_convergence_e2e.py`

### 8.1 先写测试（红）

| 测试 | 断言 | 预期红 |
|---|---|---|
| `FirstFrozenGroup_EntersMenxiaWhileOthersReviewing` | A FROZEN、B REVIEWING：freeze approval → MENXIA_GROUP_SOLVER 波次只派发 A（bindings 仅含 A） | 红 |
| `UnfrozenGroupsNotSeeded` | B 未 FROZEN：`seed_menxia_groups()` 不含 B 行 | 红 |
| `GateRoutesBackWhenZhongshuWorkRemains` | menxia 波次收尾、B 非 FROZEN → 聚合动作 `REQUEST_NEXT_GROUP` → ZHONGSHU_CRITIC | 红 |
| `CriticWaveSkipsFrozenGroupItems` | 回到 ZHONGSHU_CRITIC 后 bindings 不含 A 的 items | 红 |
| `GateApprovesDone_OnlyWhenAllGroupsComplete` | 全部组 menxia APPROVED 且无未 FROZEN 组 → APPROVE_GROUP → DONE；否则回边 | 红 |
| `PingPongTerminates` | scripted 2 组场景：回边次数 ≤ 组数 × 每组预算（无死循环），最终 DONE | 红 |

### 8.2 实现

1. `transitions.py`：`_ROLE_EDGES` 增 `("MENXIA_GROUP_GATE", "REQUEST_NEXT_GROUP", "ZHONGSHU_CRITIC")`（:126 前后）；契约测试补断言该边注册。
2. `context.py` `seed_menxia_groups()`（:641-669）：播种条件追加——该组的 `zhongshu_groups` 行 stage == FROZEN（无 zhongshu 行的旧快照沿用现行为）。
3. `states.py`：
   - `MenxiaGroupGateState`（:2206-2353）：`supported_actions` 增 `REQUEST_NEXT_GROUP`；APPROVE_GROUP/COMPLETE 分支（:2229-2277）在 pending 折叠后、`NEXT_ITEM` 判定前插入：`review.zhongshu_groups` 存在非 FROZEN 组 → `action = "REQUEST_NEXT_GROUP"`（parked 全拦的人闸判定 :2253-2258 仍优先）。
   - `_task_review_bindings`（:3403-3544）：`_select_review_jobs` 选出的 jobs 过滤掉 FROZEN 组的 items（frozen zone 不重审）。
4. 核实项（不改动）：`app.py` `_agent_pool_by_state` 与 `compat_effects._SKILLS` 无新状态名（复用既有 ZHONGSHU_CRITIC / MENXIA_GROUP_*），登记表不需变更；notifications 无新事件类型。
5. scripted e2e：`test_zhongshu_convergence_e2e.py` 增 2 组场景（复用 `test_group_doc_loop.py::GroupWaveScripting` 文档演进 mixin）：组 A 先收敛冻结 → menxia 接手 A → gate 回边 → 中书省继续 B → B 冻结 → 门下省完成 → DONE。

### 8.3 验证

- 8.1 全绿；三条 scripted 线全绿；全量回归。

---

## Task 9：门下省 body 六部分形态与收敛校验

**分类：** 行为变更（依赖 Task 5 的需求文档随行；先写测试）。

**Files：**

- `cmd/orchestrator/domain/context.py`（MenxiaGroupState 增字段）
- `cmd/orchestrator/domain/menxia_doc.py`
- `cmd/orchestrator/domain/states.py`（`_menxia_group_dispatch_context` :2968、冻结折叠点）
- `cmd/orchestrator/runtime/agent_effects.py`（`_join_menxia_group`）
- `cmd/test_menxia_doc.py`、`cmd/test_menxia_group_join.py`

### 9.1 先写测试（红）

| 测试 | 断言 | 预期红 |
|---|---|---|
| `SolverBodyMissingPart_Violation` | body 缺"验收映射"子节 → `verify_group_reply` 违规 `body_missing_part:验收映射` | 红 |
| `BodySixPartsPresent_Passes` | 六个 `###` 子标题齐备 → solver 回包通过 | 红 |
| `AcceptanceMapLeftColumnBeyondRequirement_Rejected` | critic APPROVE_GROUP 的验收映射行引用需求文档 §8 之外的条目 → joiner 折为 REQUEST_SOLVER_REVISION | 红 |
| `UndecidedMarkerInBody_BlocksApproval` | body 含"待定/TODO/待决策" → 不得 APPROVE_GROUP | 红 |
| `ExceptionRowWithoutThreeElements_BlocksApproval` | 例外行缺"替代验证"或"补测触发"字样 → 不得 APPROVE_GROUP | 红 |
| `RequirementMarkdownTravelsToMenxia` | 冻结折叠后 MenxiaGroupState.requirement_markdown == 组需求文档全文；dispatch_context 携带 | 红 |

### 9.2 实现

1. `menxia_doc.py`：
   - 常量 `BODY_PART_TITLES = ("目标与设计决策","现状与目标行为","统一约束","任务分解","验收映射","风险与兼容")`；`UNDECIDED_MARKERS = ("待定","待决策","TODO")`。
   - `_verify_solver_reply`（:649-744）：body 节内必须含六个 `### <名>` 子标题（按常量序）。
   - `verify_group_reply` critic 分支（:745-800）：APPROVE_GROUP 时校验——验收映射子节行格式 `- <验收条目> -> <覆盖>`，左侧条目 ⊆ `requirement_markdown` 的 §8 行集合（解析复用 zhongshu_doc 的 §8 提取）；例外行（含"例外"字样）必须同时含"替代验证"与"补测触发"。
2. `context.py`：`MenxiaGroupState` 增 `requirement_markdown: str = ""`（:463 旁）；from_dict/to_dict 同步。
3. `states.py`：ZHONGSHU_FREEZE_CHECK 的组 FROZEN 折叠点与 `seed_menxia_groups` 播种点把 `ZhongshuGroupState.doc_markdown` 复制进 `requirement_markdown`；`_menxia_group_dispatch_context`（:2968）增 `requirement_markdown` 透传。
4. `agent_effects.py` `_join_menxia_group`：critic APPROVE_GROUP 的既有收敛前置检查（`open_blocking()`/`pending_rejections()`，验收文档 §7.1-2a）旁接入 §9.2-3 的两条新拒绝路径，折为 `REQUEST_SOLVER_REVISION`。

### 9.3 验证

- 9.1 全绿；`test_group_doc_loop.py`（GroupWaveScripting 的文档样例需补六部分与验收映射）全绿；全量回归。

---

## 验收映射

| 需求验收项（§13） | 覆盖测试 |
|---|---|
| 单组收敛（B 先冻结、不耗 A 预算） | `test_zhongshu_group_policy.py::ApplyRound_ConvergedGroupSkipsBudget`、`test_zhongshu_freeze_groups.py::FreezeApproval_MarksReadyGroupsFrozen` |
| 顽固组隔离 | `test_zhongshu_group_policy.py::BudgetExhausted_EscalatesEvenWithProgress`、`test_zhongshu_critic_group_gate.py::RevisionBudget_GroupScoped`、scripted 顽固组场景 |
| 组级停滞（2 轮不降、首轮基线） | `test_zhongshu_group_policy.py::StallRule_*` 三条 |
| 修订对账（缺记录拒绝、P0/P1 待确认、P2/P3 自动销） | `test_solver_disposition_ledger.py` 七条 |
| 冻结区保护 | `test_solver_group_doc_scope.py` 五条（item 级既有覆盖：`test_solver_batch_handoff.py` 回归） |
| regroup 重播种 | `test_zhongshu_freeze_groups.py` 补一条（见 7.2 注：regroup 清空 zhongshu_groups 后 seed 重播）——`Regroup_ReseedsGroupRows` |
| 定向解停 | `test_zhongshu_freeze_groups.py::GateReset_NamedGroupOnly` |
| 需求文档形态（九节/措辞/闭合/实现词汇） | `test_zhongshu_doc.py`、`test_zhongshu_freeze_groups.py::DocFormViolation_RejectsWithDetails` |
| 可实施方案形态（六部分/映射闭合/零决策/例外三要素） | `test_menxia_doc.py`、`test_menxia_group_join.py` 新增用例 |
| 输入自足 | `test_solver_group_doc_scope.py::`（dispatch_context 携带权威文档）、`test_menxia_doc.py::RequirementMarkdownTravelsToMenxia` |
| 提前接手与乒乓终止 | `test_menxia_pipeline.py`、`test_zhongshu_convergence_e2e.py` 新场景 |

覆盖例外（显式记录）：需求 §13"输入自足"中"无 artifact 读取依赖"的运行时断言依赖派发 bundle 检查（静测覆盖 dispatch_context 构造；线上验收以 `workflow-events.jsonl` 无 artifact 读事件佐证），替代验证 = 代码审查派发路径 + 线上观察；补测触发 = 引入派发 bundle 快照测试夹具时补静测断言。

---

## 测试设计记录

```text
Test decision: new（Task 1/2/3/4/5/6/7 各自新文件），extend（Task 8/9 并入既有 menxia/e2e 文件）。
Behavior and risk: 全局 fuse 退役后无进展死循环；乒乓回边不收敛；worker 首次适配 group_docs 回包错误率高；
  组级 gate 重写破坏既有收敛路径。
Existing coverage: 三条 scripted 线（entrypoint/convergence/stubborn-blocker）+ test_group_doc_loop 覆盖主线；
  test_solver_batch_handoff/test_finding_identity_merge 覆盖批次与 finding 归并；不动这些断言。
Unique protection: 组预算隔离；drain-out 人闸带组清单；处置对账整包拒绝；冻结区组文档逐字节比对；
  验收映射双向闭合；乒乓回边次数上界。
Selected layer: 纯策略层单测（折叠/门判定/文档校验）+ 全 app scripted e2e（零模型驱动）；
  不做 live 模型验证（属线上验收，见验证命令）。
Red evidence / exception reason: 各任务 1.x/… 表预期红列明；Task 8 PingPongTerminates 为回归保护（修复前为红：
  回边不存在时场景无法走通）。
Actual validation: 实施时逐任务记录命令与结果（追加到本文档 §Actual validation 段）。
Retain, merge, or delete decision: 全部保留；无删除测试。
Follow-up trigger: 引入派发 bundle 快照夹具时补输入自足静测；item 流水线退役 PR（既有遗留，不在本方案）。
```

---

## 验证命令

```powershell
# 全量测试（强制 NullNotificationPort，防真实飞书外发；独立进程，不影响 8766 服务）
python cmd/run_tests.py
# 或显式：
$env:NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS="1"; python cmd/run_tests.py
```

顺序：每任务先新测试（红）→ 实现 → 同文件（绿）→ 全量回归。Task 6/8 完成后额外跑三条 scripted 线对应文件。

线上验收（全部任务完成后，独立执行）：18766 起验收实例跑 `task-menxia-t1` 类任务，观察 `runs/<task>/workflow-events.jsonl` 的组级波次、`ZHONGSHU_GROUP_STALLED`、`SOLVER_GROUP_DOC_FROZEN`（若有，须带明细）、回边次数与 token 账（cache_read 占比对比基线）。

---

## 风险与兼容性

| 项 | 说明 | 缓解 |
|---|---|---|
| worker 首次适配 `group_docs` | 回包缺字段/格式错误率高（menxia 先例：首轮 worker 全灭） | 全部新错误码入 retryable 前缀表，走 reply-retry 预算；不污染文档链 |
| Task 6 重写 critic gate | 触碰收敛核心，回归面大 | 分两步提交：先加组折叠（不改 gate），再切 gate 判定；每步全量绿；既有 scripted 线为回归网 |
| 全局 fuse 退役 | 无进展死循环风险 | stuck-finding fuse 保留；总轮次上界 = 组数 × 5；drain-out 拦截兜底 |
| 乒乓回边不收敛 | zhongshu↔menxia 交替空转 | 每次回边由"组冻结数增加"或"menxia 波次推进"驱动；`PingPongTerminates` 断言回边次数上界；drain-out 人闸兜底 |
| 停滞规则误杀 | open 计数受 merge 语义影响波动 | 首轮只记基线（menxia 同款）；`budget_reset` 定向解停 |
| 组文档 token 膨胀 | 文档随行走 bundle | 沿用 131072 字符上限；吸收即删语义不在需求文档（无账本），文档篇幅由 verify 的结构校验间接约束 |
| 旧快照兼容 | 新字段缺失 | 全部新字段可选 + 播种兜底；`Finding.disposition` 经 `__dataclass_fields__` 过滤天然兼容（findings.py:132-133） |
| regroup 清账 | 重划组后行失效 | regroup 折叠点清空 `zhongshu_groups` 并重播种（Task 7 测试覆盖）；需求文档随新版任务图重出 |

**回滚**：Task 8 删除回边即恢复"全冻结后进入"；Task 6 单独 revert 恢复全局 gate（组行保留不读）；无数据迁移，全部新字段可选。单 PR 粒度对应单任务。

---

## Actual validation

逐任务验证命令与结果（一律经 `python cmd/run_tests.py`，环境强制 `NullNotificationPort`；2026-09-24 执行）：

| 范围 | 命令 | 结果 |
|---|---|---|
| Task 1 组记账基座 | `python cmd/run_tests.py test_zhongshu_group_state` | 9 绿 |
| Task 2 组折叠与停滞策略 | `python cmd/run_tests.py test_zhongshu_group_policy` | 9 绿 |
| Task 3 修订对账 | `python cmd/run_tests.py test_solver_disposition_ledger` | 11 绿 |
| Task 4 冻结区 | `python cmd/run_tests.py test_solver_group_doc_scope` | 9 绿 |
| Task 5 组需求文档 | `python cmd/run_tests.py test_zhongshu_doc` | 17 绿 |
| Task 6 critic 组级接线 | `python cmd/run_tests.py test_zhongshu_critic_group_gate` | 8 绿 |
| Task 7 组级冻结与定向解停 | `python cmd/run_tests.py test_zhongshu_freeze_groups` | 7 绿（含 `Regroup_ReseedsGroupRows`） |
| Task 8 提前接手 | `python cmd/run_tests.py test_menxia_pipeline`（`ZhongshuMenxiaPingPongTests` 六条：提前接手/不播种/回边/跳冻结项/全完成才 DONE/边注册断言） | 50 绿 |
| Task 9 门下省 body 形态 | `python cmd/run_tests.py test_menxia_doc test_menxia_group_join` | 65 绿 |
| 三条 scripted 线 | `test_zhongshu_convergence_e2e`（5，含 `PingPongTerminates`）、`test_linear_fsm_entrypoint`（1）、`test_group_doc_loop`（4） | 全绿 |
| 全量回归 | `python cmd/run_tests.py` | 897 tests OK（约 19s） |

收尾发现与修复（本轮实施）：

1. `GroupWaveScripting`（test_group_doc_loop.py）文档样例未随 Task 9 六部分形态更新：`verify_group_reply` 判 `MENXIA_GROUP_DOC_BREACH: body_missing_part:*`，组波次折为 `MENXIA_GROUP_BLOCKED` 后 scripted 线在人闸挂起（收敛 e2e 顽固 P1 用例与 group_doc_loop 首条）。按 9.3 补齐样例：solver 首轮填六部分 body，验收映射左列取派发 `requirement_markdown` 的 §8 行（缺省回退 items' `acceptance_signals`），闭合规则与 `menxia_approval_blockers` 同源；修订轮跟进说明并入"风险与兼容"节，`touched_scope` 仍由 `changed_body_sections` 机械推导。
2. 验收映射点名的 `Regroup_ReseedsGroupRows` 缺失：先补测（红：旧行随合并漏存、预算不清零），后于 `apply_review_update`（context.py）落 regroup 清账语义——fold 变更组切分（非空新切分 ≠ 旧切分）即清空 `zhongshu_groups` 并按新切分重播种，同折携带的需求文档链（doc_markdown/doc_version/doc_hash/doc_source_hash）保留于新行，收敛记账归零；fold 不带组或切分不变走既有合并。
3. Task 8 契约断言已在 `test_menxia_pipeline.py`（`REQUEST_NEXT_GROUP` 边注册与聚合动作）覆盖，无需另补。

线上验收（未运行，按计划独立执行）：18766 起验收实例跑 `task-menxia-t1` 类任务，观察 `runs/<task>/workflow-events.jsonl` 的组级波次、`ZHONGSHU_GROUP_STALLED`、`SOLVER_GROUP_DOC_FROZEN`（若有，须带明细）、回边次数与 token 账（cache_read 占比对比基线）。
