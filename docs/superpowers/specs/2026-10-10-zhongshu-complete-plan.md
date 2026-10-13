# 中书省完整方案：产出稳定、读得懂、与原意一致（总纲）

- 日期：2026-10-10
- 状态：可开工。无待澄清项；§1 的决定是使用者已确认或本文已定，实施者不得更改。
- **本文是中书省改造的总纲与唯一执行顺序。** 细节分布在下列"附件"，附件中被本文点名的章节**逐字有效**，冲突处以本文为准：
  - **附件 R** `docs/superpowers/specs/2026-10-10-zhongshu-output-stability-roadmap.md`：§S0 核实、§S1 章节契约写入 skill、§S2 Critic 章节职能审查、§S3 阅读摘要、§S5 稳定性度量。其 §0 第 3、4 条（"逐章确认暂不做""并发侦察不做"）和 §6（延后表）**作废**，由本文替代。
  - **附件 B** `docs/superpowers/specs/2026-10-10-zhongshu-scout-draft-first-modification.md`：§4 决定 D1–D18、§7 规格修订（尚未落到原规格，由 P7 做）、§8 回滚、§11/§12。B 的 T0–T6（含 T5 Scout 并发探路、T6.2 fixtures、T6.3 度量、§6.1–6.3 三份 skill 文本）**已落地并提交**（提交 `ea1e6b6`，见 §2）；实施者只在需要理解设计意图时查阅，不要重做。
  - **附件 A** `docs/superpowers/specs/2026-10-10-zhongshu-requirements-workflow-v2-revision.md`：仅 T7（引用对齐）、T8（预算）已落地，其余已被取代；实施者无需阅读。
  - 附件 R §1"现状核实"、§2 执行顺序、§S1 第 1 条（"skill 仍是旧版"）**已过期**，以本文 §2、§3 为准。
- 代码根：`cmd/orchestrator/`。所有命令加 `rtk` 前缀；单个测试 `rtk proxy python -X utf8 cmd/run_tests.py <模块>`，全量 `rtk python cmd/run_core_tests.py`。
- 禁止手改 `openwiki/`。实施者**不要提交**，除非使用者要求；也不要整理工作区里与本文无关的未提交改动。
- 凡写"实施者核实"处，是我没有读到或只读了一部分的代码；以代码为准，差异写进 PR 说明，不得改变本文设计意图。

## 0. 这套改造要解决什么

使用者的手工流程：用 `requirements-doc` skill 和单个 agent 谈需求 → 换别的 agent 会审 3–4 轮（意见追加在文末，再复审、再吸收）→ 把文档交给另一个 agent 出执行方案（门下省的活）。单 agent 的问题是：产出不稳定、随机性强、缺少审核、过度依赖模型能力。

中书省的目标只有一个：**产出一份使用者读得懂、与使用者原意一致、跨次运行结构与内容稳定的需求文档**，因此文档分多个章节，每章只回答一个问题。门下省因此受益，但本文不度量、不优化门下省。

本文给出五件事，对应五类问题：

| 问题 | 手段 | 任务 |
|---|---|---|
| 内容放错章、章间重复，跨次差异大 | 章节契约写进 Solver/Critic skill，Critic 审章节职能 | P3 |
| 使用者不知道该看哪、哪些是模型替他想的 | 阅读摘要 `review-brief.md` | P2 |
| 已看过确认的章，下一轮被悄悄改掉 | 逐章确认 + 章节上锁（棘轮） | P4 |
| 起草前的资料线索慢、不全 | Analyst 并发探路（Scout） | **已落地**（`ea1e6b6`），P5 只做核对与一处 skill 措辞补丁 |
| 没数字就不知道变没变稳 | 稳定性度量 + 前后对比 | P1、P6 |

## 1. 已确认的决定（不得更改）

1. 聚焦中书省自身产出稳定，第一优先级。
2. 章节是"与需求一致"的检查单位：每章只回答一个问题（skill 八章职能）。
3. **确认方式 = 整篇批准为唯一有约束力的关卡；逐章确认是可选的增量。** 使用者可以一次批准整篇（等同选项 A），也可以先逐章确认再批准整篇。批准整篇不要求所有章都已确认。逐章确认的作用只有一个：**被确认的章在之后的修订中被上锁，不得被悄悄改动**。
4. 阅读摘要是阅读辅助，不是正文，不参与批准摘要、不交给门下省（见 R §S3 的理由）。
5. 提问权只在 Solver；Analyst 只读、不提问（B D4、D9）。
6. 本文不做：导入单 agent skill 产出的文档、会审闭环自动化、门下省方案分歧探针、飞书逐章卡片（见 §9）。

## 2. 现状（2026-10-10 17:50 核实；HEAD = `80f9176`）

下列均已**提交**（`ea1e6b6`、`80f9176`），**不要重做**。核心测试 290 个通过（`rtk python cmd/run_core_tests.py`）。工作区里与本文无关的未提交改动（`internal/`、`web/`、`design/`、若干 docs）不要碰。

| 任务 | 落地证据 |
|---|---|
| B T0 rubric（仅度量） | `domain/requirements/rubric.py` |
| B T1 章节模板 | `document.py` 的 `KIND_TITLES`/`CANONICAL_ORDER`/`PENDING_MARKER_RE`；`model.py` 的 `CORE_SECTIONS`（7 项）、`problem`/`risks`/`boundary` 枚举 |
| B T2 草案优先 | `states.py`（`cover_blocking_assumptions` 在 DRAFT_READY 之后）；`contracts/requirements_common.py`、`requirements_solver.py`、`dispatch.py` |
| B T3 lint | `domain/requirements/lint.py`（十条 advisory） |
| B T4 溯源 | `provenance.py`，`materialize` 写 `provenance.md` |
| **B T5 Scout 并发探路** | `runtime/requirements_scouts.py`（`ScoutFanoutRunner`、`ScoutConfig`；环境变量 `REQUIREMENTS_SCOUT`/`_FACETS`/`_TIMEOUTS`/`_ATTEMPTS`/`_FACT_CAP`、`ANALYST_MAX_WORKERS`）；`app.py:284` 接入；`cmd/test_requirements_scouts.py` |
| B T6 fixtures 与度量 | `cmd/fixtures/requirements/README.md` 的三个新场景；`requirements_metrics.py` 的 `first_complete_draft_seconds` 与 scout 统计 |
| **三份 skill 新文本** | `docs/multi/runtime/requirements-solver-skill.md`（46 行：草案优先、`【待确认：<question_id>】`、八章职能、三章速辨）、`requirements-critic-skill.md`（31 行：五项审查重心、lint 说明）、`requirements-analyst-skill.md`（41 行：探针规则）；`contracts/requirements_analyst.py` 已含"questions 必须是空数组" |
| A T7 / A T8 | `spans.py`、`quote_align.py`、`runtime/requirements_quotes.py`；`budget.py`（policy-v2，`TOTAL_CALLS_CAP=40`） |

**未落地（本文要做的）**：
- 八章职能在两份 skill 里**没有单一来源**：Solver skill 有"八章职能/三章速辨"但无标记块，Critic skill 只有一句"一致性与职能"，两处文字不保证一致，也无测试（P3）。
- Critic 没有 `chapter-misplaced`/`chapter-duplicate`/`chapter-function-violation` 三个 rule 值，`contracts/requirements_critic.py` 没有对应规则（P3）。
- 阅读摘要 `review-brief.md`、稳定性度量工具（P2、P1）。
- 逐章确认与章节上锁（P4）。
- Analyst skill 在派发里没有 `scout` 键时的措辞（P5 第 2 条）。
- 原规格 `2026-10-07-zhongshu-requirements-workflow-requirements.md` 尚未按 B §7 修订（P7）。

**部署状态未知**：skill 文件在仓库里已是新文本，但线上 multica agent 用的是否已是新文本，取决于使用者是否已运行 `cmd/sync_multica_review_agents.py`。本文不假设；见 §4 O1。

## 3. 依赖与执行顺序

```
P0 核实
 ├→ P1 稳定性度量工具 ──→ [运维 O1] 采集"改前"基线（在同步 P3/P5 改过的 skill 之前）
 ├→ P2 阅读摘要（与 P1 并行）
 ├→ P3 章节契约 + Critic 章节职能审查
 │     └→ P4 逐章确认 + 章节上锁（改同一份 Solver/Critic skill，故在 P3 之后）
 └→ P5 Scout 核对 + Analyst skill 措辞补丁（独立，可随时）
P7 规格修订（文档，随时）
[运维 O2] 同步 skill → 采集"改后" → P6 判定
```

**关键约束**：skill 文件合并进仓库不改变线上行为，`cmd/sync_multica_review_agents.py`（使用者执行）才会。所以"改前"基线必须在 O2 之前采集，否则无法归因。

每个任务完成即跑本任务测试 + `rtk python cmd/run_core_tests.py`，红则停在该步修复，不得跨步堆积。

## P0 核实现状

R §S0。另外在开工前记录：`git status --short` 与核心测试通过数，写进 PR 说明。

## P1 稳定性度量工具

R §S5a（`domain/requirements/stability.py`、`cmd/measure_requirements_stability.py`、`cmd/test_requirements_stability.py`）。逐字有效。

## P2 阅读摘要 `review-brief.md`

R §S3（含 3.1 结构、3.2 接入、3.3 测试）。逐字有效。**P4 会再追加一节内容**（见 P4.8），实施 P2 时先按 R 实施，P4 时增量改。

## P3 章节契约与 Critic 章节职能审查

目标：两份 skill 的八章职能来自**同一份逐字文本**，Critic 能按章职能报告问题。Solver skill 现有文本（草案优先、八章职能、三章速辨）**保留**，R §S1 第 1 条"全文替换"**不要做**。

1. **标记块**：在 `requirements-solver-skill.md` 里，把现有"八章职能（顺序固定…"到"三章速辨"最后一条（`非目标："断点续传"…`）整段，用下列标记包住，**文字不改**（R §S1.2）：
   ```
   <!-- chapter-contract:start -->
   ...
   <!-- chapter-contract:end -->
   ```
   再把该块（含标记）逐字复制进 `requirements-critic-skill.md`，放在"审查重心"之前。
2. **Critic 审查重心**按 R §S2 扩写：现有第 4 项"一致性与职能"改成对每一章判断是否在回答它的那个问题；三个固定 rule 值 `chapter-misplaced`、`chapter-duplicate`、`chapter-function-violation` 及各自 `claim`/`acceptance` 写法、严重度默认值（只有放错/重复会改变下游对范围、行为或验收的理解才 blocking，否则 advisory）、不重复 lint 已报告问题，逐条照 R §S2 写入。
3. `contracts/requirements_critic.py` 的 `prompt_rules` **追加**两条英文规则（既有规则一字不动）：
   - `Judge each chapter against the chapter contract in the skill. Report misplaced, duplicated or function-violating content with rule chapter-misplaced, chapter-duplicate or chapter-function-violation; mark blocking only when it changes how downstream readers understand scope, behavior or acceptance.`
   - `Chapter problems already reported by a lint-* advisory issue must not be reported again.`
4. `review.AXES` 不变；`ReviewIssue.rule` 若有枚举/格式限制（R V1），把三个值加入允许集合并附测试。
5. 保留 skill 里的字面串：`ACTIVE_RUNTIME_SKILL:` 头、`too large to post safely`、`超长输出必须写入结果文件。`（skill 绑定测试依赖，见 §8 V-Skill）。
6. 测试：新增 `cmd/test_requirements_skills.py`（R §S1.4 与 §S2 的断言：两份 `chapter-contract` 块逐字相等；块内含 `document.KIND_TITLES` 全部标题；Solver skill 含 `【待确认：`；Critic skill 含 `【待确认`、`lint-` 与三个 rule 值）；在 `cmd/test_requirements_review.py` 增：含 `chapter-misplaced` 的 Critic 回复可被 `merge_review` 接受并走与其他 Solver blocking issue 相同的修复路径。更新受 skill sha 影响的既有测试（实施者核实）。
7. Analyst skill 与 `requirements_analyst.py` 本任务**不改**（Analyst 的"questions 必须为空"规则已落地；措辞补丁归 P5）。
8. P4 要在这两份 skill 上再增补"已确认章节"一段（P4.7），所以 P3 完成时两份 skill 先不含该段。

## P4 逐章确认与章节上锁

### P4.0 语义（先读懂再写代码）

- 使用者在 `REQ_CONFIRM_WAIT`（评审已通过、等待批准）时，可对**当前版本**的若干章发"章节确认"。被确认的章记为"已确认"，并记录当时的内容摘要。
- 之后任何一次修订（使用者反馈 → Solver 重写 → 重新评审），**已确认章的正文必须与确认时逐字一致**，否则机械校验判定为阻塞问题，退回 Solver 修复。
- 解锁只有三种途径：(a) 使用者对该章**明确提意见**（针对该章的反馈）；(b) 使用者给出**不指定章节的整篇意见**——解除全部章节确认；(c) 某章引用的事实来源变化（`FACTS_CHANGED`）——解除引用了该事实的章。
- Solver 认为必须改已确认章时，不能直接改；它在 `change_summary` 里用固定前缀声明申请，使用者在阅读摘要里看到后决定是否针对该章提意见（见 P4.6）。
- 批准整篇不改变任何确认状态；批准之后进入发布/交接，上锁不再起作用（批准后的变更走"需求变更"入口，本文不改）。
- 逐章确认**不触发任何工作流迁移、不消耗预算、不触发重新评审**（草案没变）。

### P4.1 持久字段与迁移（最需谨慎）

`RequirementsState.from_dict` 要求持久化数据含全部字段、`decode` 拒绝未知字段，所以新字段必须带迁移，否则**进行中的旧任务一加载就失败**。

在 `domain/requirements/model.py`：

```python
@dataclass(frozen=True)
class ChapterLock:
    kind: str               # 九个规范 kind 之一，见 P4.2
    title: str
    content_digest: str     # sha256(规范化(title, markdown))，见 P4.3
    document_version: int   # 确认时的草案版本号
    actor_id: str
    event_id: str
    confirmed_at: str
```
`RequirementsState` 末尾追加 `chapter_locks: tuple[ChapterLock, ...] = ()`。`__post_init__` 增加校验：`kind` 不重复、`kind in document.CANONICAL_ORDER`（延迟导入，避免循环）。

迁移：`from_dict` 在 `require_fields` 之前，对**顶层**字典做 `value = {**{'chapter_locks': []}, **value}`（只补缺失的顶层新字段；嵌套记录不补）。常量集中放在 `_BACKFILL_TOP_LEVEL = {'chapter_locks': []}`，注释一行说明"为旧快照补默认值"。`to_dict` 总是写出该字段。

实施者核实：
- **V-P4a** 哪些地方对整个 `RequirementsState` 做摘要或比较，导致新字段改变既有摘要（`last_progress_digest`、`runtime_fingerprint`、事件幂等键、`Context` 摘要）。已知：`review_input_digest`、`document_digest`、`consensus.digest` 只由草案/共识计算，不受影响；`runtime_fingerprint()`（`requirements_recovery.py:27`）哈希 `domain/requirements/*.py` 源码，只用于失败恢复前提校验，代码改动本来就会改变它，无需处理，但在 PR 说明里提醒"升级后卡在 REQ_BLOCKED 的旧任务需按既有流程重新校验前提"。
- **V-P4b** `domain/context.py:1174 context_from_dto` 对旧 `schema_version in ('4.0','5.0')` 的上下文，其 `requirements` 经 `RequirementsState.from_dict`，补默认值即可；**不要**升 schema 版本。
- 测试：构造不含 `chapter_locks` 的旧快照字典（从现有测试夹具里取一份 `to_dict()` 后删掉该键）→ `from_dict` 成功且 `chapter_locks == ()`；往返 `to_dict/from_dict` 相等；未知字段仍被拒绝；一份旧 `WorkflowContext` DTO 能经 `context_from_dto` 加载。

### P4.2 可上锁的章

锁的键是 `kind`，仅限 `document.CANONICAL_ORDER` 的九个：`background, goals, problem, constraints, behavior, scope, acceptance, non_goals, risks`。`topic`、`rules` 章不可确认（kind 可重复，无稳定身份）。

### P4.3 内容摘要

`domain/requirements/chapter_lock.py`（新，纯函数）：

```python
def section_digest(section) -> str
    # sha256 of json: {'title': title.strip(), 'markdown': normalized(markdown)}
    # normalized: 统一 \r\n→\n，去首尾空白，行尾空白去掉；其余字节不变
def lockable_sections(draft) -> dict[str, DraftSection]   # kind -> section，仅九个规范 kind 且恰一份且非空
def confirm_sections(state, kinds, *, actor_id, event_id, occurred_at) -> RequirementsState
def release_locks(state, kinds=None, *, fact_ids=None) -> RequirementsState
def locked_violations(draft, locks) -> tuple[tuple[str, str], ...]   # (kind, 'changed'|'missing')
def locked_payload(state) -> list[dict]   # 给派发用：[{'kind','title','markdown'}]，按 CANONICAL_ORDER
```
引用 id（`requirement_ids` 等）**不入摘要**：Solver 因共识重编号而改引用不算改章。

`confirm_sections` 的拒绝条件（抛 `ValueError`，错误码作消息）：
- `SECTION_UNKNOWN`：kind 不在 `lockable_sections(state.draft)`；
- `SECTION_HAS_PENDING_MARKER`：该章含 `PENDING_MARKER_RE`；
- 已确认且摘要相同 → 幂等返回原 state；已确认但摘要不同 → 以新摘要覆盖（正常发生于该章被解锁后重新确认）。

### P4.4 事件与处理

新事件 `REQUIREMENTS_SECTION_CONFIRMED`，payload：
```
{'actor_id', 'gate_id', 'document_digest', 'section_kinds': [kind, ...]}
```
- 实现位置：`RequirementsWorkflowState._handle`（`states.py:176`，受 `try/except` 保护），放在 `REQ_CONFIRM_WAIT` 的 `REQUIREMENTS_APPROVED` 分支（`states.py:317`）**之前**。**不要**放在 `try` 之前与 `REQUIREMENTS_FEEDBACK`（`states.py:132`）同层：那里没有异常保护，`ValueError` 会直接冒出。事件不迁移状态，所以不需要 `transitions.py` 的边。
- 每个业务状态都有一个 `RequirementsWorkflowState` 实例，所以所有业务状态都会走到这个分支：仅当 `self.name == 'REQ_CONFIRM_WAIT'` 才生效；其他状态返回 `StateDecision(update=ContextUpdate(requirements=replace(state, reason='当前不能确认章节：…')))`，不迁移。
- 校验顺序：`authorized_actor(actor, context.request.decision_actor_ids, payload)`（同批准，错误 `UNAUTHORIZED_SECTION_CONFIRMATION`）；`document_digest == state.draft.document_digest` 且 `gate_id == state.review_gate_id`（错误 `STALE_SECTION_CONFIRMATION`）；`review_stamp.passed` 且输入摘要一致（同批准的 `VALID_REVIEW_REQUIRED`）；再 `confirm_sections`。
- **必须**把 `'REQUIREMENTS_SECTION_CONFIRMED'` 加进 `states.py:171` 的元组 `('ANSWERS_SUBMITTED','REQUIREMENTS_APPROVED','RECOVERY_INPUT')`：否则校验失败的 `ValueError` 会落入 `self.fail(...)` 把任务打入失败路径，而这里只应把原因写进 `state.reason`。
- 成功后返回 `StateDecision(update=ContextUpdate(requirements=updated))`，`updated.reason = ''`；无 effect、无迁移。
- `ContinuationState.handle`（`runtime/requirements_workflow.py:78`）加分支：该事件在交接之后 → 只写 `reason='需求已交接；章节确认不再适用。'`，同 `REQUIREMENTS_APPROVED` 的处理。
- 实施者核实 **V-P4c**：事件准入——`RequirementsEventCoordinator`（`runtime/requirements_event_coordinator.py`）、`NotifyingRequirementsState`、`RequirementsTransitionRegistry` 是否对事件名有白名单或必须有边；有则登记该事件（无迁移）。再核实 `requirements_bot_round.py`、`requirements_gateway.py` 里没有对事件名做穷举分支会误伤新事件。

### P4.5 反馈针对章节（解锁 a/b）

`REQUIREMENTS_FEEDBACK` payload 新增可选键 `section_kinds: [kind, ...]`。在 `states.py:132` 分支内，`add_feedback` **返回新状态之后**（`updated == state` 即重复事件，直接 `StateDecision()`，不得解锁）：

- 有 `section_kinds`：对每个 kind 校验合法（不合法 → 忽略该 kind 并在 `reason` 提示，不拒绝整条反馈）；反馈文本改写为 `'【针对章节：' + '、'.join(KIND_TITLES[k]) + '】' + 原文`（**在调用 `add_feedback` 之前改写**，使来源原文带上章节，Solver 与 provenance 都能看到）；`release_locks(updated, kinds)`。
- 无 `section_kinds`：`release_locks(updated, None)`（解除全部）。
- 该分支在 `REQ_BLOCKED/REQ_ENGINE_ERROR/REQ_RETRY_WAIT`（`states.py:141`）与正常路径都要先解锁再分支；`REQ_PUBLISH/REQ_HANDOFF/MENXIA_REQUIREMENTS_INTAKE` 分支（批准之后）**不解锁**。
- `ANSWERS_SUBMITTED` 与 `ANSWER_CONFLICT`（经 `propose_answer_changes` 调 `add_feedback`）**不解锁**：回答问题填的是未确认章里的 `【待确认】`；若某个回答确实需要改已确认章，走 P4.6 的申请路径。
- `FACTS_CHANGED`（`states.py:176` 的 `_handle`）：改写事实状态后，`release_locks(state, fact_ids=changes)`：解除所有 `DraftSection.fact_ids` 含这些事实的章。

### P4.6 机械强制与申请

`document.validate_draft(draft, consensus, decisions=(), locks=())` 新增参数；三处调用点都传 `state.chapter_locks`：`states.py:250`、`requirements_effects.py:43`、`requirements_effects.py:139`。

对 `locked_violations(draft, locks)` 的每一项发一条机械阻塞问题：
- `rule='LOCKED_SECTION_CHANGED'`（或 `LOCKED_SECTION_MISSING`），`section_ids` 为该章 id（缺失时为空），`owner='solver'`，`severity='blocking'`；
- `claim` 写"第X章（<标题>）已被使用者确认，正文必须保持与确认版本逐字一致"，`acceptance` 写"恢复该章的已确认原文"。需要给内部 `issue()` 助手增加可选的 `claim`/`acceptance` 覆盖参数（现为 `rule` 同时当 claim）；
- 它走既有的 `REQ_VALIDATE → REPAIR_CONTENT → REQ_DRAFT` 修复路径，与 `SECTION_REQUIRED`、`PENDING_MARKER` 同类，预算语义同它们（实施者核实：该路径消耗 `semantic_repairs`，写进 PR 说明）。修复预算耗尽时按既有行为进入阻塞，使用者处理办法是对该章提意见（解锁）。

**已知死结与处理**：若 Critic 提出的 blocking 问题只能靠改已确认章才能解决，Solver 既不能改（锁）又必须改（Critic），会反复被打回直到修复预算耗尽进入阻塞。本批不做自动仲裁；处理办法是使用者对该章提意见（解锁）。实施者在 P4.7 的 Critic 一句里已要求"不要对已确认章措辞提意见"，这一句要保留；并在 PR 说明里记录该风险。

**申请修改已确认章**：Solver 在 `change_summary` 里写一行 `【申请修改已确认章节：<章标题>】<原因>`（不改该章正文）。运行时不解析、不校验它；它只出现在阅读摘要（P4.8）。使用者看到后若同意，对该章提意见即解锁。

### P4.7 Solver 与 Critic 看到的内容

1. 派发：在 Solver 与 Critic 的派发上下文（`dispatch.py` 构造 `dispatch_context` 处，实施者定位）追加 `'locked_sections': locked_payload(state)`；为空列表时**不追加该键**（保持旧派发字节不变，缓存与既有测试不受影响）。
2. Solver skill（P3 产出的文件）在 `<!-- chapter-contract:end -->` 之后追加一段**不在**契约块内的"已确认章节"：
   ```
   ## 已确认章节（逐字保持）
   - 派发上下文里的 locked_sections 是使用者已确认的章。修订时这些章的标题与正文必须与给出的原文逐字一致，不要润色、不要重排、不要顺手修错别字。
   - 如果你认为必须改动其中某章，保持原文不动，并在 change_summary 单独写一行：`【申请修改已确认章节：<章标题>】<原因>`。
   - 其他章可以照常修改；如果修改会使已确认章显得矛盾，也用上面的申请行说明，不要改已确认章。
   ```
3. Critic skill 追加一句：`locked_sections 中的章已由使用者确认，不要对其措辞提出修改意见；只有当它与其他章矛盾时，对"其他章"提 issue。`
4. 契约块 `<!-- chapter-contract -->` 不变，两份 skill 的逐字一致测试仍成立。

### P4.8 阅读摘要增补

在 `review_brief.render_review_brief` 的"一、先看这里"追加一条：`Solver 申请修改你已确认的章节：`+ 逐行列出 `change_summary` 中以 `【申请修改已确认章节：` 开头的行（按出现顺序；没有则不输出该条）。摘要文件按草案目录写一次、之后不可变，所以**不显示**实时的确认状态；实时状态由 P4.9 的状态命令给出。

### P4.9 本地入口（`app.py`，约 682–825 行）

当前本地通道是命令行（审查文档是本地发布，提交 `321ff38`）；飞书 `review_card` 未启用，本文不接（§9）。新增：

- `--confirm-sections DOCUMENT_SHA256 KIND [KIND ...]`（`nargs='+'`）：提交 `REQUIREMENTS_SECTION_CONFIRMED`，`gate_id` 取 `app.ctx.requirements.review_gate_id`，`actor_id='local-operator'`，事件键 `'local-section-confirm:'+uuid`。KIND 接受英文 kind 或中文章节标题（用 `KIND_TITLES` 反查），无法识别 → `parser.error`。
- `--feedback-sections KIND [KIND ...]`：**修饰参数**，必须与 `--feedback-file` 同用，附加 `section_kinds`；单独使用 → `parser.error`。它**不**进入 `inputs` 列表（否则与 `--feedback-file` 同用时会被 "use exactly one requirements input" 拒绝）。
- `--requirements-status TASK_ID`：只读，独立动作，与 `--inspect-task` 同级：加入 `actions` 元组（`app.py:701`，"choose exactly one of" 的校验），在 `inspect_task` 附近的分支里处理并 `return 0`，不创建 `OrchestratorApp`。读取方式沿用 `inspect_task` 的原始 JSON 读取（不走 `from_dict`，因此旧快照也能看）。打印草案版本、`document_digest`、`review_gate_id`、每章：标题、状态（已确认 / 未确认 / 含待确认标记）、条目数（用 `rubric._entries`）。
- 把 `args.confirm_sections` 加入 `app.py:718` 的 `inputs` 列表（它在 826、828 行被引用），否则 `--resume` 会把它当成"没有输入"而去恢复整个任务；并在 796–825 行的 `if/elif` 链里加一支。`--feedback-sections` 在 `--feedback-file` 那一支里读取。
- 事件消费沿用 `app.requirements_runtime.submit(name, payload, event_id)`。

### P4.10 测试 `cmd/test_requirements_chapter_lock.py`

- `section_digest`：CRLF/行尾空白不改变摘要；改一个字改变摘要；引用 id 不入摘要。
- `confirm_sections`：未知 kind、含 `【待确认：` 的章被拒；重复确认幂等；改章后重新确认覆盖。
- 事件：只在 `REQ_CONFIRM_WAIT` 生效；错误 actor、过期 `document_digest`、过期 `gate_id` 只写 `reason`，**不进入失败路径**（断言状态名不变且 `outcome` 未变）；成功后无 effect、无迁移、`budget` 不变、`review_stamp` 不变。
- 反馈：带 `section_kinds` 只解锁指定章，且来源文本带 `【针对章节：…】`；无 `section_kinds` 解锁全部；重复事件 id 不解锁；批准之后的反馈不解锁；`ANSWERS_SUBMITTED`、`ANSWER_CONFLICT` 不解锁；`FACTS_CHANGED` 解锁引用该事实的章。
- 强制：改动已确认章 → `validate_draft` 出 `LOCKED_SECTION_CHANGED` 阻塞问题，REQ_VALIDATE 走 `REPAIR_CONTENT`；只改引用 id 不触发；删除该章 → `LOCKED_SECTION_MISSING`；改其他章不触发；解锁后同样改动通过。
- 端到端（沿用 `test_requirements_workflow_e2e.py` 的 FakeAgent 模式）：确认第 X 章 → 使用者反馈针对第 Y 章 → Solver 回复"改了 X"→ 被打回 → Solver 回复"只改 Y"→ 通过并进入评审；批准后 `requirements.md` 的字节与 `document_digest` 不受锁影响。
- 迁移：见 P4.1。
- 派发：存在锁时 Solver/Critic 派发含 `locked_sections`；无锁时派发字节与改动前相同。
- 本地入口：`--confirm-sections` 参数解析、中文标题反查、`--feedback-sections` 缺 `--feedback-file` 报错。

## P5 Scout 核对与 Analyst skill 措辞补丁（探针主体已落地）

探针主体（B T5/T6）已提交，**不重做**，不新增探针种类，不改汇总规则。本任务只做两件事：

1. **核对（只读，结果写进 PR 说明）**：
   - B §12 的 V1（传输与请求细节）、V6（指标）、V7（环境变量）、V9（答案路径，`claims` 非空则直通）与已落地的 `requirements_scouts.py`、`requirements_metrics.py` 是否一致；不一致处记录，不改。
   - `REQUIREMENTS_SCOUT=0` 时行为与旧单 Analyst 一致：`cmd/test_requirements_scouts.py` 若已有对应用例，在 PR 说明里点名；没有则补一个。
   - 默认值保持不变：`REQUIREMENTS_SCOUT` 默认 `1`；`ANALYST_MAX_WORKERS` 代码默认 `3`（`app.py:222`），线上推荐 `5`，由运维设置（B D13），不改代码默认值。
2. **Analyst skill 措辞补丁**：`requirements-analyst-skill.md` 第一段现为"Stay inside your facet (dispatch_context.scout.facet / focus)"。派发里没有 `scout` 键时（`REQUIREMENTS_SCOUT=0`、已有 claims 的后续分析、REQ_EVIDENCE 点名补证）这句语义不明。改为：若 `dispatch_context.scout.facet` 存在则只做该焦点，否则按完整分析处理；其余探针规则、"只读、不提问"、字面串与 `ACTIVE_RUNTIME_SKILL:` 头不变。在 `test_requirements_skills.py` 增断言：该句存在。注意：skill 内容变化后同样需要运维同步（O2）。

## P6 前后对比与判定

R §S5b + 下面的运维步骤。只比两栏：

| 阶段 | 配置 | 产物 |
|---|---|---|
| 改前 | 采集时线上已部署的 skill（O1 记录是哪一版）+ 当前代码 | 3 需求 × 3 次 |
| 改后 | P1–P5 合并，P3/P5 改过的 skill 已同步（`REQUIREMENTS_SCOUT` 保持默认 `1`） | 同上，两栏都记 `first_complete_draft_seconds`（`requirements_metrics.py` 已有） |

写入 `docs/superpowers/specs/2026-10-10-zhongshu-stability-baseline.md`。判定（不预设数字阈值）：结构一致率不降；各章条目数波动不增；lint 数不增；字面一致度不降。**逐章确认与上锁不进入稳定性对比**（它是行为保证，由 P4.10 的测试证明）。

若"改后"相对"改前"变差，且怀疑探针是原因，再补一次 `REQUIREMENTS_SCOUT=0` 的"改后-无探针"栏做归因；否则不跑。若无改善，回头查模型是否真的采用了 skill 文本，**不要**靠加规则硬补。首稿耗时只作参考，不是验收门槛。

## P7 规格修订

B §7 的 R-4…R-11 落到原规格 `docs/superpowers/specs/2026-10-07-zhongshu-requirements-workflow-requirements.md`（先读对应章节、删冲突句，按 B §7 的方法）。再追加：

- **R-12（§4 阶段语义/§11 验收，追加）**：评审通过后，使用者可对若干章单独确认；已确认章在后续修订中必须逐字保持，直至使用者对该章提出意见、提出不指定章节的整篇意见，或该章引用的事实来源变化。整篇批准是唯一有约束力的批准，不要求所有章都已确认。阅读摘要是辅助文件，不参与批准摘要，不交给门下省。

## 4. 运维步骤（使用者执行，实施者不得代做）

- **O1 基线**：先确认并记下 multica 上三个 agent 当前用的 skill 是否已是仓库 `ea1e6b6` 的版本（即是否运行过 `cmd/sync_multica_review_agents.py`），写进基线文档。选 3 个真实需求（建议含你手工会审过的，用其最终版作人工对照）。在**同步 P3/P5 改过的 skill 之前**，每个需求跑 3 次中书省到批准，得 9 个 approved 目录，按需求分组各跑一次 `cmd/measure_requirements_stability.py`，写入基线文档"改前"栏。
- **O2**：P1–P5 合并后运行 `cmd/sync_multica_review_agents.py`（同步脚本会一并同步三份 skill）；线上建议 `ANALYST_MAX_WORKERS=5`；按同样方式采集"改后"栏。
- **O3**：基线文档写完后，P6 判定。

## 5. 验收（整体）

| # | 条件 | 验证 |
|---|---|---|
| R1 | 核心测试全部通过，无新增红 | `run_core_tests.py` |
| R2 | 两份 skill 的 `chapter-contract` 块逐字一致，含全部章节标题；Solver skill 含 `【待确认：` 与"已确认章节"段；Critic skill 含三个 `chapter-*` rule 值与 lint 说明；Analyst skill 含"没有 scout 焦点时按完整分析"一句 | `test_requirements_skills.py` |
| R3 | `review-brief.md` 确定性、无内部 ID、不改变 `requirements.md` 字节与 `document_digest`；`REQ_CONFIRM_WAIT` 通知含摘要前两节 | `test_requirements_review_brief.py` |
| R4 | 稳定性度量工具对多个 approved 目录出报告，能报告缺章/换序 | `test_requirements_stability.py` |
| R5 | 旧快照（无 `chapter_locks`）可加载、校验、发布；新字段往返一致 | `test_requirements_chapter_lock.py` |
| R6 | 章节确认只在 `REQ_CONFIRM_WAIT` 生效，不迁移、不耗预算、不进失败路径；整篇批准不依赖其状态 | 同上 |
| R7 | 已确认章被改动时机械阻塞并退回 Solver；解锁三途径各有测试 | 同上 |
| R8 | Scout 已有测试保持通过；`REQUIREMENTS_SCOUT=0` 回到单 Analyst 的用例存在；P5 核对结果已写入 PR 说明 | `test_requirements_scouts.py` |
| R9 | 两栏基线报告存在并有结论：改后相对改前稳定性指标不变差（运维完成后） | 人工 |

## 6. 提交点（仅供参考，使用者要求才提交）

1. `docs(requirements): add the complete zhongshu plan and mark older plans as annexes`（本文与三份旧文档横幅）
2. `feat(requirements): add stability measurement for approved drafts` — P1
3. `feat(requirements): publish a reader brief next to the draft` — P2
4. `feat(requirements): write the eight-chapter contract into solver and critic skills` — P3
5. `feat(requirements): let the user confirm chapters and lock them against silent edits` — P4
6. `fix(requirements): make the analyst skill valid without a scout facet` — P5
7. `docs(requirements): amend the workflow spec for chapter confirmation and scouts` — P7

提交信息末尾按系统要求附 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。

## 7. 回滚

- P1/P2：删除工具；P2 删 `materialize` 一行与 `progress_text` 扩展。无持久数据变化。
- P3：还原两份 skill 与两条 `prompt_rules`。无持久数据变化。
- P4：**有持久数据变化。** 回滚前须确认没有进行中的任务带 `chapter_locks`（旧代码会因未知字段拒绝解码），或先让其终结；`REQUIREMENTS_SECTION_CONFIRMED` 事件在旧代码里未知，也不能留在待处理队列里。仅需关闭行为而不回滚代码时：不执行 `--confirm-sections` 即可，无锁则无任何额外行为。
- P5：还原 Analyst skill 一句措辞；探针本身随时可用 `REQUIREMENTS_SCOUT=0` 关闭。
- 草案优先/模板/lint（已落地部分）的回滚注意事项见 B §8（含 `boundary/problem/risks` 的解码限制）。

## 8. 实施者核实清单（汇总）

沿用 R §7 的 V1–V6（本文称 R-V1…R-V6）；B §12 只有 V1、V6、V7、V9 用于 P5 核对（其余随 T5 已落地）。本文新增：

- **V-P4a/b** 新持久字段对摘要与上下文加载的影响（P4.1）。
- **V-P4c** 新事件的准入（P4.4）。已核对：`RequirementsRuntime.submit` 直达 `store.local`，未见事件名白名单；`NotifyingRequirementsState.handle` 对无 transition 的决定直接返回，不产生通知。实施者再确认 `store.local`、`RequirementsEventCoordinator`、`requirements_gateway.py` 对事件名无穷举。
- **V-P4d** 已核对：没有只读状态入口；`--inspect-task` 只吐整份原始 context（`runtime/requirements_cutover.py::inspect_task`），不适合阅读。P4.9 因此新增独立入口。
- **V-P4e** `dispatch.py` 构造 Solver/Critic `dispatch_context` 的位置；加 `locked_sections` 后是否影响分析缓存键或派发幂等键（只应影响 Solver/Critic 派发，不影响 REQ_ANALYZE 缓存）。
- **V-P4f** `validate_draft` 的修复路径对预算的消耗（P4.6）。
- **V-Skill** skill 绑定测试：`compat_effects._skill_lock`、`test_active_runtime_skill_binding.py` 对字面串（`too large to post safely`、`超长输出必须写入结果文件。`、`ACTIVE_RUNTIME_SKILL:` 头）的断言，改 skill 后须满足。

## 9. 本文不做及触发条件

| 项 | 原因 / 何时再做 |
|---|---|
| 飞书逐章确认卡片 | 审查文档目前是本地发布，`review_card` 未启用。恢复飞书审查后，在 `requirements_gateway.py` 增 `kind=='confirm_section'` 分支，复用 P4.4 的事件即可 |
| 与上一版的逐章差异展示 | 状态里只保留当前草案；上锁已避免已确认章被改，阅读摘要用 `change_summary`。仍不足时再决定是否持久化上一版 |
| 导入单 agent skill 产出的文档 | 使用者要中书省自己出需求方案 |
| 会审闭环自动化（吸收检查、去重、3–4 轮收敛） | 对应使用者手工流程，属下一批；P3 的 `chapter-duplicate` 是其基础 |
| 门下省方案分歧探针 | 度量的是下游 |
| 来源原子化、结构化验收用例、约束 `imposed_by`（B X1/X3） | 需新增持久字段；先观察 lint 与摘要是否足够 |
| 晚到线索合并、探针结果缓存（B X4/X6） | 见 B §11 |
