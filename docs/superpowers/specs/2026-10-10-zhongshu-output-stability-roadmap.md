# 中书省产出稳定化：合并开工顺序（主线）

- 日期：2026-10-10
- 状态：可开工。无待澄清项；用户已确认的决定见 §0，实施者不得更改。
- **【已并入总纲】** 执行顺序以 `docs/superpowers/specs/2026-10-10-zhongshu-complete-plan.md` 为准。本文仅 §S0–§S5 作为该总纲的附件 R 继续有效；本文 §0 第 3、4 条与 §6 延后表（不做逐章确认/探针/上锁）已作废。
- **【§1 现状已过期】** 本文 §1 写的"skill 仍是旧版、探针未做"已不成立：探针（B T5/T6）和三份新 skill 文本已提交（`ea1e6b6`）。现状以总纲 §2 为准；§S1 第 1 条"全文替换 skill"不要做，改为总纲 P3 的"加标记块"。
- ~~本文是执行顺序的唯一依据。~~ 它取代两份旧文档里的任务排序，但不取代其细节：
  - 主文档 B：`docs/superpowers/specs/2026-10-10-zhongshu-scout-draft-first-modification.md`
  - 补充篇 A：`docs/superpowers/specs/2026-10-10-zhongshu-requirements-workflow-v2-revision.md`
  - 引用 "B §x / B Tn / A Tn" 即指上述文档对应处。冲突处以本文为准。
- 代码根：`cmd/orchestrator/`。所有命令加 `rtk` 前缀；测试用 `rtk proxy python -X utf8 cmd/run_tests.py <模块>`，全量用 `rtk python cmd/run_core_tests.py`。
- 禁止手改 `openwiki/`。实施者**不要提交**，除非使用者要求。
- 凡写"实施者核实"的地方，是我没有读过或只读了一部分的代码；以代码为准，差异写进 PR 说明，不要改变本文设计意图。

## 0. 目标与已确认的决定

**目标**：中书省产出的需求文档（1）每次跑结构一致、内容放对章；（2）用户读得懂、容易判断；（3）与用户原意一致。下游门下省的稳定性由此受益，但**本文不度量、不优化门下省**。

用户已确认：
1. 聚焦中书省自身产出稳定，这是第一优先级。
2. 章节是"与需求一致"的检查单位：每章只回答一个问题（skill 八章职能）。
3. 确认方式选 **A（整篇确认）**：用户一次批准整篇，文档之外附一份"阅读摘要"，让用户先看需要判断的点。逐章确认暂不做。
4. 并发侦察（B T5）、导入模式、门下省方案分歧探针、会审闭环：本批**不做**，见 §6。

## 1. 现状核实（2026-10-10，工作区未提交状态）

已核实：`rtk python cmd/run_core_tests.py` 通过（272 个测试，OK）。以下 B/A 任务**已在工作区落地**（未提交），不要重做：

| 任务 | 落地证据 |
|---|---|
| B T1 章节模板 | `document.py` 有 `KIND_TITLES`、`CANONICAL_ORDER`、`PENDING_MARKER_RE` 并在 `validate_draft` 里发 `PENDING_MARKER`；`model.py` 的 `CORE_SECTIONS` 为 7 项，枚举含 `problem`/`risks`/`boundary` |
| B T2 草案优先（状态机、契约规则、dispatch 文案） | `states.py` ANALYSIS_READY 不再调用 `cover_blocking_assumptions`，DRAFT_READY 之后调用；`contracts/requirements_common.py`、`requirements_solver.py` 有 "Draft first…" 规则；`dispatch.py:75` 有首轮成稿文案 |
| B T3 lint | `domain/requirements/lint.py` 十条 advisory 规则（含 A 追加的 `internal-id-in-text`、`heading-in-body`、`placeholder`） |
| B T4 溯源 | `domain/requirements/provenance.py`，`requirements_effects.materialize` 写 `provenance.md` |
| B T0 的 rubric | `domain/requirements/rubric.py`（仅度量，非门槛） |
| A T7 引用对齐 | `spans.py`、`quote_align.py`、`runtime/requirements_quotes.py` |
| A T8 格式修复预算 | `budget.py`：`requirements-policy-v2`、`TOTAL_CALLS_CAP = 40` |

**未落地**（本文要做的）：
- Solver/Critic/Analyst 的 **skill 文本仍是旧版**（`docs/multi/runtime/requirements-solver-skill.md` 只有 18 行，不含八章职能、`【待确认】` 规则；critic skill 无职能审查与 lint 说明）。结果是：状态机和契约已按"先草案"运行，但给模型看的 skill 没跟上，**这是当前最大的不一致**。
- 无"阅读摘要"；无对中书省自身稳定性的度量。
- B T5（探针并发）、B T6（fixtures、评估）未做，本批仍不做。

## 2. 执行顺序

```
S0 核实现状（5 分钟）
 → S5a 稳定性度量工具  ─→ [运维] 改 skill 同步前跑"改前"基线
 → S3 阅读摘要（与 skill 无关，可与 S5a 并行）
 → S1 章节契约写入三份 skill（合并代码，但不同步到 multica）
 → S2 Critic 的章节职能审查
 → [运维] 同步 skill → 跑"改后"测量 → S5b 对比、判定
```

**关键约束**：skill 文件合并进仓库不会改变线上行为，**同步到 multica agent 才会**（`cmd/sync_multica_review_agents.py`，由用户执行）。所以基线必须在用户同步前采集；S1/S2 的代码可以先合并。

## S0 核实现状

- 跑 `rtk python cmd/run_core_tests.py`，记录通过数（预期 272，OK）。如果已红，先记录，不修。
- `git status --short` 记录哪些文件是未提交状态，写进 PR 说明。工作区含大量与本文无关的改动和删除（`zhongshu-*` 旧 skill 等），不要整理它们。

## S1 章节契约写入三份 skill（解决"放错章"的根因）

**目的**：让起草者和审查者拿同一份"每章回答什么、不能写什么、常见误写"，这是 skill 里最能降低跨次差异的部分。

1. 按 B §6.2 全文替换 `docs/multi/runtime/requirements-solver-skill.md`；按 B §6.3 全文替换 `requirements-critic-skill.md`；Analyst skill 暂**不改**（探针并发不做，B §6.1 的探针语义不适用；只在其"不得提问"一项上与 B §6.1 第 1 条对齐：把"questions 必须为 []"加进 `contracts/requirements_analyst.py` 的 `prompt_rules`，英文原句不动，仅追加一条；这与 `states.py` 已落地的"提问权归 Solver"一致）。
2. **单一事实来源**：八章职能（B §6.2 中"八章职能"和"三章速辨"两段）放在两份 skill 里，且各用一对标记包住，内容必须逐字相同：
   ```
   <!-- chapter-contract:start -->
   ...
   <!-- chapter-contract:end -->
   ```
   B §6.3 的 Critic 文本没有这一块：把 Solver 的这一块原样复制进 Critic skill，放在"审查重心"之前。
3. 保留 B §6.2/§6.3 注明要逐字保留的字面串（`too large to post safely`、`超长输出必须写入结果文件。`、`ACTIVE_RUNTIME_SKILL:` 头），它们被 skill 绑定测试依赖。
4. 测试：新增 `cmd/test_requirements_skills.py`：
   - 两份 skill 的 `chapter-contract` 块逐字相等；
   - 块内出现 `document.KIND_TITLES` 的每个标题（背景、目标、当前问题、约束、核心功能清单、责任边界、验收标准、非目标、风险）；
   - Solver skill 含 `【待确认：`；Critic skill 含 `【待确认` 和 `lint-`。
   - 更新受 skill sha 影响的既有测试：`test_active_runtime_skill_binding.py`、`test_requirements_contracts.py`、`test_requirements_dispatch.py`（实施者核实具体受影响项）。
5. 运维：合并后提示用户"改 skill 后需运行 `cmd/sync_multica_review_agents.py`；**请先完成 S5a 的基线采集再同步**"。实施者不得代做。

## S2 Critic 的章节职能审查（解决"内容放错章 / 章节间重复矛盾"）

**不新增轴、不改 schema。** `review.AXES` 保持 7 项；章节职能审查归入既有的 `consistency` 和 `constraints` 轴。

在 Critic skill（S1 产出的文件）"审查重心"里确保有下列要求（B §6.3 第 4 条已有雏形，本文把它写具体）：

- 对**每一章**判断它是否在回答它的那个问题，发现下列情形时用 `ReviewIssue` 报告，`rule` 取固定三个值之一：
  - `chapter-misplaced`：内容属于别的章（例：本次范围取舍写进约束；功能复述写进验收；整体不做的功能写进责任边界）。`claim` 写明"哪一行、应归哪一章"，`acceptance` 写"移到X章后本章仍成立"。
  - `chapter-duplicate`：两章表达同一件事。写明两处位置，要求保留一处。
  - `chapter-function-violation`：本章整体没有履行职能（例：验收没有任何可执行的测试条件；目标写成功能列表）。
- 严重度：只有当"放错/重复会让下游对范围、行为或验收的理解改变"时才标 `blocking`，否则 `advisory`。这是默认值，S5b 数据出来后再调。
- 运行时 lint 已报告的结构问题不要重复（已在 B §6.3 要求）。
- 全部章节确认后做一次全篇去重对齐：对应 skill 步骤 6。Critic 的整篇一致性检查属于 `consistency` 轴，现有"除全局一致性外可复用覆盖"的规则保持不变。

实施者核实：`ReviewIssue.rule` 是否有枚举或格式限制（读 `model.py` 与 `review.py::merge_review`）；若有，把三个值加入允许集合，附测试。`merge_review`/`next_repair_route` 对 blocking 的 `chapter-*` issue 应走与其他 Solver blocking issue 相同的语义修复路径，核实后写进 PR 说明。

测试：`test_requirements_skills.py` 增断言：Critic skill 含三个 rule 值；`test_requirements_review.py`（核对既有文件名）增：含 `chapter-misplaced` 的 Critic 回复可被 `merge_review` 接受并进入 Solver 修复路径。

## S3 阅读摘要 `review-brief.md`（解决"用户读不懂、不知道看哪"）

**为什么单独成文件**：`requirements.md` 的字节必须等于 `document_digest`（`LocalRequirementsPort.verify_approval` 按文件哈希校验），且 `compiler.py` 要求门下省方案里的 `requirement_markdown` 与已批准正文相等；摘要放进正文会改批准摘要并流入门下省。所以摘要是并列的独立产物，与 `provenance.md` 同级，不参与摘要。审查文档是本地发布（提交 `321ff38`），所以摘要也走本地文件 + 进度通知，不依赖飞书卡片。

### 3.1 新建 `domain/requirements/review_brief.py`

```python
def render_review_brief(state) -> str:
```
纯函数，同状态同字节，无时间戳、无随机值。中文。**不得出现内部 ID**（`req-`/`decision-`/`fact-`/`S_` 等）；内部 ID 只用于内部查表，输出用章节标题和语句。截断用 `provenance._clip`（120 字）。

结构固定：

```
# 阅读摘要｜需求草案 v{version}

> 本文件不是需求正文，不参与批准，也不会交给门下省。

## 一、先看这里：需要你判断的
- 正文里的“待定”条目：按章列出 `章标题：条目前 80 字`；没有则写“无”。
- 你选择暂缓的问题：AnswerRecord.disposition == 'defer' → 问题文案；没有则“无”。
- 采用了默认假设的问题：disposition == 'accept_default' → 问题文案 + 对应假设语句；没有则“无”。

## 二、我替你做的推断
- 每条 status == 'active' 且 origin in ('derived','proposal') 的主张：`[章标题] 语句`，下一行缩进写依据（取其 source_ids 对应来源文本的前 120 字；无来源写“无直接原话”）。
- 每条 disposition in ('proposed','accepted') 的假设：`假设：语句（阻塞/非阻塞）`。
- 都没有则写“无”。

## 三、没能核实的线索
- status == 'unavailable' 的事实：`observation（source）`；没有则“无”。

## 四、各章来源构成
| 章 | 来自你的原话或你确认的决定 | 推断 | 资料事实 |
每章一行，按文档章节顺序。“来自你”= 章节 requirement_ids 里 origin in ('explicit','choice') 的主张数 + decision_ids 数；“推断”= origin in ('derived','proposal') 的主张数；“资料事实”= fact_ids 数。三项全为 0 的章在行尾标“未绑定来源”。

## 五、机器检查提示（建议级）
- state.issues 里 status == 'open' 且 severity == 'advisory' 的 issue：`[章标题] claim`；没有则“无”。

## 六、本版修改说明
- draft.change_summary 原文；为空写“首版”。
```

排序：全部按文档章节顺序、再按记录在状态里的出现顺序，保证确定性。

### 3.2 接入

1. `runtime/requirements_effects.py::materialize` 的 `files` 字典追加 `'review-brief.md': render_review_brief(state).encode('utf-8')`（草案与批准目录都写；清单摘要自动包含，因为 manifest 对 `files` 全部算哈希）。**不得**改动 `requirements.md`。
2. `runtime/requirements_notifications.py::progress_text`：`target == 'REQ_CONFIRM_WAIT'` 分支现为一行 `需求评审已通过，请查看需求文档和确认卡片。`，改为该行 + 空行 + 阅读摘要的"一、二"两节（截断到 1200 字符，超出加 `…（完整见 review-brief.md）`）。它是纯函数输出，进入已日志化的 effect payload，所以必须确定。
3. `handoff.py` 的 `make_approved_input` 不读发布目录文件，保持不变（已核实：它从 `state` 构造）。

实施者核实：（a）`progress_text` 的 `after.requirements` 在 `REQ_CONFIRM_WAIT` 时是否已含 `review_document_ref`，用于在通知里写摘要路径（`Path(review_document_ref).with_name('review-brief.md')`）；若没有则只写"见同目录 review-brief.md"；（b）本地批准通道用户是怎么看到文档的，若有别的展示入口也要显示摘要，写进 PR 说明。

### 3.3 测试 `cmd/test_requirements_review_brief.py`

- 确定性：同状态两次输出字节相同。
- 不泄漏内部 ID：用 `rubric.INTERNAL_ID_RE` 检查输出。
- 推断：含 `derived` 主张时出现在"二"；全是 `explicit` 时"二"为"无"。
- 暂缓与默认假设分别出现在"一"。
- 来源构成表数字与构造的状态一致；全 0 的章带"未绑定来源"。
- `materialize` 后 `requirements.md` 字节与 `document_digest` 不变；目录含 `review-brief.md`；`manifest.json` 的 `files` 含它的哈希。
- `progress_text` 在 `REQ_CONFIRM_WAIT` 时含摘要前两节，且 ≤ 1200 字符 + 提示。

## S5 中书省自身稳定性的度量

**为什么需要**：没有数字，S1–S3 是否让产出更稳无从判断。这里度量的是**中书省的产出**，不是门下省。

### S5a 工具（先做）

新建 `domain/requirements/stability.py`（纯函数）与薄入口 `cmd/measure_requirements_stability.py`：

```
rtk python cmd/measure_requirements_stability.py <approved目录1> <approved目录2> ... [--out stability-report.md]
```
每个目录是同一需求跑出来的一次结果（含 `requirements.md`，可选 `manifest.json`）。读 `requirements.md`，按 `## ` 标题用 `KIND_TITLES` 反查 kind 构造 `DraftRevision`（未识别标题当 `topic`）。输出 Markdown 报告：

1. **结构一致性**：每次的章节 kind 序列是否一致；不一致列出差异。
2. **各章条目数**：每章每次的条目数（`rubric._entries`），给出最小/最大。
3. **lint 数**：对每次 `lint_draft(draft, ConsensusRevision())`，按 slug 统计 advisory 数。无来源文本可对照，`unconfirmed-number` 因此按"无来源"处理，报告里注明并对比三次相对值即可。
4. **验收的测试类型集合**：每次 `TEST_TYPE_WORDS` 命中的集合，及两两 Jaccard。
5. **条目字面一致度（粗）**：对约束、责任边界、验收、非目标四章，两两比较条目集合：每个条目取与对方最相似条目的字符二元组 Jaccard，取均值；并列出相似度 < 0.5 的条目对。**此项只衡量字面接近，低分不等于语义不同**，报告须这样注明，列表交给人或审查 agent 判断是否同义。
6. 若目录含 `manifest.json`：记录 `policy_version`、文档版本号。

不引入第三方依赖。测试 `cmd/test_requirements_stability.py`：两份相同文档 → 结构一致、字面一致度 1.0；构造一份缺章/换序/改条目的 → 对应项被报告；未识别标题不崩。

### 运维采集（需要用户执行，实施者不得代做）

- 选 3 个真实需求（建议含你历史上手工会审过的文档对应的需求，用它的最终版作人工对照）。
- **基线**：在同步 skill 之前，每个需求跑 3 次中书省到审批，收集 9 个 approved 目录，按需求分组各跑一次度量，存为 `docs/superpowers/specs/2026-10-10-zhongshu-stability-baseline.md`。
- **改后**：S1/S2/S3 合并并同步 skill 后，同样 3×3 再跑，写入同一文件的"改后"栏。

### S5b 判定（不预设数字阈值）

只在"改后"对"改前"逐项比较：结构一致率不降；各章条目数波动不增；lint 数不增；字面一致度不降。把相似度低的条目对交人工判断。若无改善，说明 S1/S2 没有起作用，回头查 skill 文本是否被模型采用，**不要**靠加规则硬补。

## 3. 验收（整体）

| # | 条件 | 验证 |
|---|---|---|
| R1 | 核心测试全部通过，无新增红 | `run_core_tests.py` |
| R2 | 两份 skill 的 `chapter-contract` 块逐字一致，含全部章节标题 | `test_requirements_skills.py` |
| R3 | Solver skill 含 `【待确认：` 规则；Critic skill 含职能审查三个 rule 值与 lint 说明 | 同上 |
| R4 | `review-brief.md` 确定性、无内部 ID、不改变 `requirements.md` 字节与 `document_digest` | `test_requirements_review_brief.py` |
| R5 | `REQ_CONFIRM_WAIT` 通知含阅读摘要前两节 | 同上 |
| R6 | 稳定性度量工具可对多个 approved 目录出报告，且对构造的缺章/换序输入能报告差异 | `test_requirements_stability.py` |
| R7 | 基线与改后的 3×3 报告存在（运维完成后）并有 S5b 结论 | 人工 |

## 4. 提交点（仅供参考，使用者要求才提交）

1. `docs(requirements): add the output-stability roadmap and mark its precedence over the older modification docs`（本文及 B/A 横幅）
2. `feat(requirements): add stability measurement for approved drafts` — S5a
3. `feat(requirements): publish a reader brief next to the draft` — S3
4. `feat(requirements): write the eight-chapter contract into solver and critic skills` — S1
5. `feat(requirements): review chapter function in the critic` — S2

提交信息末尾按系统要求附 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。

## 5. 回滚

- S1/S2：还原两份 skill 与 `requirements_analyst.py` 一条规则；无持久数据变化。
- S3：删除 `materialize` 一行与 `progress_text` 的扩展；无持久数据变化，已写出的 `review-brief.md` 无害。
- S5：独立工具，删除即可。

## 6. 本批不做及触发条件

| 项 | 原因 / 何时再做 |
|---|---|
| B T5 探针并发、`ScoutFanoutRunner` | 解决速度而非稳定。S5b 之后若"首稿耗时"成为主要问题再做 |
| B T6 fixtures 与评估、`first_complete_draft_seconds` | 同上；稳定性度量先于速度度量 |
| 逐章确认（飞书逐章卡片） | 用户选了 A。若阅读摘要仍不足以让用户判断，再升级 |
| 已确认章节上锁（章节确认棘轮，B X2、A 8.5） | 需要新增持久字段。A 方案下用户只批准整篇，没有章节粒度的确认事件。若反馈修订后出现"悄悄改了没让改的章"，再启动 |
| 与上一版的逐章差异展示 | 状态里只保留当前草案（无上一版正文）；阅读摘要先用 `change_summary`。需要时再决定是否持久化上一版 |
| 导入 skill 产出的文档 | 用户要中书省自己出需求方案，非主线 |
| 会审闭环自动化（吸收检查、去重、3–4 轮收敛） | 与用户手工流程对应，属下一批；S2 的 `chapter-duplicate` 是其基础 |
| 门下省方案分歧探针 | 度量的是下游 |

## 7. 实施者核实清单

- **V1** `ReviewIssue.rule` 是否受限（S2）。
- **V2** `progress_text` 在 `REQ_CONFIRM_WAIT` 时可取到的 `review_document_ref`（S3.2a）。
- **V3** 本地批准通道的展示入口（S3.2b）。
- **V4** skill 绑定测试的字面串断言（`compat_effects._skill_lock`、`test_active_runtime_skill_binding.py`），改 skill 后须满足。
- **V5** `dispatch.py::SKILLS` 与 `compat_effects._SKILLS` 两张映射在本批不新增状态，无需改；如发现不一致写进 PR 说明。
- **V6** `requirements_metrics.py` 现有指标（调用数、轮数、交互次数）能否在度量报告中引用，作为"成本"栏的来源（可选）。
