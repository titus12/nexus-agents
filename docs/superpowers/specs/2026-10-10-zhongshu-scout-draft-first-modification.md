# 中书省修改文档：Analyst 探路并发化 + Solver 草案优先 + 单 agent 需求文档方法内化

- 日期：2026-10-10
- 状态：定稿，可直接实施。无待澄清项；残留决定均已在 §4 给出默认值，实施者不得自行更改默认。
- **执行顺序以 `docs/superpowers/specs/2026-10-10-zhongshu-complete-plan.md`（总纲）为准。** 本文 T0（rubric）、T1–T4 与 A 的 T7、T8 已在工作区落地；T5（探针并发）、T6.2（fixtures）、§6.1–6.3 skill 文本、§7 规格修订由总纲 P3/P5/P7 点名采用，其余按总纲取舍。
- **本文是主文档。** 补充篇 `docs/superpowers/specs/2026-10-10-zhongshu-requirements-workflow-v2-revision.md`（下称“补充篇 A”）只提供两个增量任务，须在本文 T4 之后、T5 之前做：A 的 **T7**（来源片段编号 + 引用对齐，解决模型逐字抄引用的机械失败，其中 `verify_facts` 与 `_accept_probe` 要调用它）和 **T8**（格式修复不占语义预算）。A 的其余内容与本文冲突处，以本文为准。本文 T0 另需追加 A 的评分表（`rubric.py`）与失败来源分类（`failure_origin`）。本文 T3 的 lint 另需追加 A 取舍表里说明的三条 advisory 规则。
- 基线：`docs/superpowers/specs/2026-10-07-zhongshu-requirements-workflow-requirements.md`（下称"原规格"）与 `docs/superpowers/plans/2026-10-07-zhongshu-requirements-workflow-implementation-plan.md`（下称"原计划"）。本文是对二者的增量修订，冲突处以本文为准。
- 代码根：`cmd/orchestrator/`（下称 `orch/`）。所有命令加 `rtk` 前缀（用户全局规则）；测试入口见 §9。
- 禁止手改 `openwiki/` 下生成页（仓库 CLAUDE.md）。
- 实施者约束：不要提交（commit）除非使用者另行要求；本文 §8 给出建议的提交点，仅供参考。

---

## 0. 一页摘要

三件事，按依赖顺序：

1. **文档方法内化**：把单 agent skill `requirements-doc`（`~/.claude/skills/requirements-doc/`）的"八章职能 + 常见误写 + 验收写法"变成中书省正文骨架，以及 Solver 写作、Critic 审查的标准；其中可机械检查的部分下沉为运行时 **lint**（advisory，不占 LLM 审查预算）。
2. **草案优先（draft-first）**：REQ_ANALYZE 之后**不再先批量问用户**。Solver 先出带 `【待确认：<question_id>】` 标记的第一版完整草案，并由 Solver 统一提问；Critic 聚焦意图/场景/缺口。
3. **Analyst 探路并发化**：REQ_ANALYZE 内部由 `ScoutFanoutRunner` 并发派出多个只读探针（意图/代码/文档/约束…），汇总成**一个** `ANALYSIS_READY`，对状态机、guard、coordinator 完全透明。

配套：`provenance.md` 溯源产物（正文保持干净，来源另文件呈现）。

**不改**：状态集合与转移表、`EFFECT_STATES`、审批绑定（document_digest + review_input_digest + gate id）、预算 `policy_version`、`RequirementsState` 持久字段集合（只做枚举取值扩展）、Menxia 交接、飞书端口回读哈希。

## 1. 背景与目标

**问题**
- REQ_ANALYZE 是单 agent 串行探索；`states.py:208` 的 `cover_blocking_assumptions` 会在**出任何草案前**把阻塞假设批量变成问题，用户在没看到成稿时回答抽象问题，慢且问不准。
- 正文骨架 `document.py:TITLES = ('背景','目标','对象与范围','约束与责任边界','场景与行为要求','关键规则与专题边界','验收标准','非目标')` 把约束、责任边界、非目标混在两章里，与单 agent skill 的八章职能不一致；写作和审查缺少"约束=外部强加 / 责任边界=我们自己划定 / 非目标=整体不做"的判据。
- Critic 同时承担结构检查与意图审查；结构类缺陷（空泛验收、把计划写成已执行结果、未确认数字当阈值）靠 LLM 发现，贵且不稳。
- 来源只能从 `traceability.json` 反查，正文读者看不到"这句话出自哪"。

**目标（可验收，见 §10）**
- G1 `first_complete_draft_seconds`（从 REQ_INTAKE 起到首个 DRAFT_READY 提交）相对 T0 基线下降；
- G2 首轮向用户提出的问题 100% 锚定在草案中的 `【待确认：qid】` 标记上；
- G3 正文按八章职能写作；机械可查的质量问题由 lint 以 advisory 给出；
- G4 每份发布产物含 `provenance.md`；
- G5 全部新行为可经环境变量回退，且仅对**新任务**生效。

**非目标（本批不做）**：见 §11。另：不新增 effect 类型；不改 `RequirementsState` 持久字段；不引入投票/交集；不让 Analyst 写正文或提问；不让探针间互相通信。

## 2. 角色分工（修订后）

| 角色 | 一句话职责 | 能产出 | 不能产出 |
|---|---|---|---|
| 编排器（引擎） | **登记官**：确定性地登记、校验、路由；不替用户做业务选择 | 校验结果、lint、路由、溯源产物 | 业务结论 |
| Analyst（探针集合） | **探路者**：并发、只读、限时，找"线索"，帮 Solver 提高第一版草案效率 | claims（仅 intent 探针，需逐字引用）、facts（逐字引用+来源文件）、assumptions（proposed）、gaps（→ unavailable fact） | 问题、正文、实现任务、用户决定 |
| Solver | **唯一正文作者 + 唯一问题作者**：先出完整草案，未决处加标记并统一提问 | 草案、问题、issue 响应、`evidence_requests`/`analysis_requests` | 用户批准、DecisionRecord |
| Critic | **意图/场景/缺口审查**：以用户原始意图审草案；结构类问题交给 lint | issues（含 owner/severity）、coverage | 改写正文、替用户决定 |
| 用户 | 业务取舍与批准 | 答案、飞书卡片批准 | — |

关键变化：① Analyst 不再提问（聚合 payload 的 `questions` 恒为 `[]`）；② Analyst 由"一个 agent 串行"变为"多探针并发 + 汇总"；③ 提问权完全归 Solver；④ Critic 的结构检查职责下沉为 lint。

## 3. 总体流程变化

```
旧：INTAKE → ANALYZE(单Analyst) → [阻塞假设→批量问用户→等待] → DRAFT → VALIDATE → REVIEW → …
新：INTAKE → ANALYZE(并发探针→汇总) → DRAFT(完整草案+待确认标记+Solver提问)
        ├─有问题 → CLARIFY(用户在已成稿的锚点上回答) → DRAFT(修订) → …
        └─无问题 → VALIDATE(blocking机械检查 + advisory lint) → REVIEW → …
```

状态机的状态集合与转移表**不变**。变化只在：

1. `states.py` REQ_ANALYZE `ANALYSIS_READY` 分支不再调用 `cover_blocking_assumptions`（§5.2）；
2. `states.py` REQ_DRAFT `DRAFT_READY` 分支在合并问题后调用 `cover_blocking_assumptions`，保证阻塞假设**不会被悄悄放过**（§5.2）；
3. `states.py` REQ_VALIDATE 分支同时产出 advisory lint，路由只看 blocking 机械问题（§5.3）；
4. REQ_ANALYZE 的 agent 派发在运行时层展开为并发探针（§5.5），状态机无感知。

## 4. 已定设计决定与默认值（实施者不得改动）

| # | 决定 | 默认值/取值 | 理由 |
|---|---|---|---|
| D1 | 正文章节模板 | 采用 skill 八章：背景、目标、当前问题、约束、核心功能清单、责任边界、验收标准、非目标，另可选"风险"；映射到现有 kind（见 §5.1），不新增持久字段 | 与单 agent 方法一致，且仅需枚举扩展 |
| D2 | `problem`（当前问题）、`risks`（风险）是否必需 | 均**可选**，不进 `CORE_SECTIONS` | skill 允许"当前问题"并入背景/目标；"风险"仅在真实风险时才有 |
| D3 | 核心章节集合 | `CORE_SECTIONS = ('background','goals','constraints','behavior','scope','acceptance','non_goals')`（7 项，去掉 `rules`） | `rules` 的"专题边界"职能由 `topic` 章节承接 |
| D4 | 提问权 | 仅 Solver；Analyst 聚合 `questions=[]` | 提问需要成稿上下文；避免问题散落 |
| D5 | 草案优先的门槛 | 只要 REQ_ANALYZE 结束即进入 REQ_DRAFT；"目标无法判定"时由 Solver 返回 `NEEDS_CLARIFICATION` 先问意图问题 | 引擎不做语义判断，只做路由 |
| D6 | 阻塞假设 | 不在 ANALYZE 阶段变问题；在 Solver 的 `DRAFT_READY` 之后由 `cover_blocking_assumptions` 兜底成问题 | 保留"阻塞选择不被悄悄放过"的安全性 |
| D7 | lint 的严重度 | 全部 advisory（`severity='advisory'`，`owner='solver'`）；唯一新增 blocking 机械规则是 `PENDING_MARKER` | 质量检查不得变成新的机械门失败 |
| D8 | 探针集合 | 默认 `intent,code,docs,constraints`；可选 `history`（默认关）；至多 5 个；`intent` 强制存在 | 覆盖"意图/实现线索/资料/外部限制" |
| D9 | 探针权限 | 仅 `intent` 探针可产出 claims 与 blocking assumption；其余探针的 claims 被丢弃、assumption 强制 `blocking=false` | 保持 claim 的逐字引用门；防止线索探针越权 |
| D10 | 探针超时/尝试次数 | intent：600s / 2 次尝试；其余：300s / 1 次；整体墙钟上限 660s | 原 analyst 角色超时 1800s 足以兜住 |
| D11 | 探针失败策略 | intent 失败 → 原样返回 intent 的失败 outcome（走现有修复/预算路径）；非 intent 失败 → 容忍并登记为 unavailable fact | 单点必需、其余尽力 |
| D12 | 扇出触发条件 | 仅当 `validation_state.consensus.claims` 为空（首次分析）时扇出；之后的 REQ_ANALYZE（解释用户答案/反馈）走原单 Analyst 直通 | 避免每轮答案都重跑全套探针 |
| D13 | 探针并发度 | `min(探针数, ANALYST_MAX_WORKERS)`，由 runner 自行限流；代码默认值 3 不改，推荐线上设为 5 | 不依赖 admission 的等待语义 |
| D14 | 回退开关 | `REQUIREMENTS_SCOUT=0` 回退为单 Analyst 派发（draft-first 与模板为代码行为，回退靠回滚提交） | 纯域层不读环境变量，保持纯函数 |
| D15 | 迁移 | 仅新任务；旧任务持久化数据必须仍可解码、校验、发布 | 与原规格 §12 一致 |
| D16 | 溯源 | 新增 `provenance.md` 为独立产物，不参与 `document_digest` | 飞书回读按 `document_digest` 哈希正文，正文必须不含运行时附注 |
| D17 | 缓存 | 分析缓存 `'v'` 2→3，键纳入探针配置盐 | 探针配置变化必须使旧缓存失效 |
| D18 | 提示语言 | 新增 skill/契约规则用中文，保留既有英文原句不动，仅追加/替换指明的段落 | 减少无关 diff |

## 5. 工作包详述

依赖：T0 → T1 → T2 → T3；T4 在 T1 之后可并行；T5 依赖 T2；T6 最后。

### T0 基线测量（先做，改动前）

目的：得到"改前"数据，用于 G1/G2 对比。

1. 跑 `rtk python cmd/run_core_tests.py` 记录通过基线（若已有红，先记录，不修）。
2. 用 `cmd/evaluate_requirements_workflow.py` 在 `cmd/fixtures/requirements/` 现有三个场景（`evolving-scope`、`singleflight`、`vague-goal`）上各跑一遍；用法以该脚本 `--help` / `cmd/fixtures/requirements/README.md` 为准。
3. 记录并写入 `docs/superpowers/specs/2026-10-10-zhongshu-scout-baseline.md`（新建，简短表格即可）：每个场景的 `first_complete_draft_seconds`（若脚本尚无此指标，T6 增加后用同一数据重算，或以"INTAKE 时间戳到首个 DRAFT_READY 事件时间戳"人工计算）、首次提问前的轮次数、Analyst 派发次数、首轮问题数。
4. 若本地不能真实调度 agent（无 multica），T0 改记录"fixture 回放可得到的指标"，并在基线文档标注"无墙钟数据"。**不得因此跳过 T0**。

### T1 章节模板迁移（枚举扩展 + 骨架 + 机械规则）

**1.1 `domain/requirements/model.py`**
- `RECORD_ENUMS['RequirementClaim']['kind']` 改为 `('goal','behavior','constraint','non_goal','boundary')`（新增 `boundary`：我们自己划定的责任边界）。
- `RECORD_ENUMS['DraftSection']['kind']` 改为 `('background','goals','problem','scope','constraints','behavior','rules','acceptance','non_goals','risks','topic')`。
- `CORE_SECTIONS` 改为 `('background','goals','constraints','behavior','scope','acceptance','non_goals')`。
- 其余字段不动；`from_dict`/`decode` 的严格校验保持。旧任务的 `rules` 章节、旧 claim kind 仍合法。

**1.2 `domain/requirements/document.py`**
- 用映射替换 `TITLES` + `zip(CORE_SECTIONS, TITLES)`：

```python
KIND_TITLES = {
    'background': '背景', 'goals': '目标', 'problem': '当前问题', 'constraints': '约束',
    'behavior': '核心功能清单', 'scope': '责任边界', 'acceptance': '验收标准',
    'non_goals': '非目标', 'risks': '风险',
}
CANONICAL_ORDER = ('background', 'goals', 'problem', 'constraints', 'behavior', 'scope',
                   'acceptance', 'non_goals', 'risks')
```
- `new_skeleton`：按 `CORE_SECTIONS` 的**规范顺序**（背景、目标、约束、核心功能清单、责任边界、验收标准、非目标）生成 7 个章节，`section_id = 'S_' + kind`，仅 `background` 预填原始请求引用（保持现有行为：`'> ' + raw_request...`）。`problem`/`risks` 不在骨架里，由 Solver 按需追加（`section_id` 约定 `S_problem`、`S_risks`）。
- `render_markdown` **不改**（它使用各 section 自带的 `title`，所以旧文档哈希不变）。
- `commit_draft` 不改（`CORE_SECTIONS` 子集校验自动适配新集合）。
- `TITLES` 常量：保留导出但标注废弃，或在确认全仓库无引用后删除。**实施者必须先 grep `TITLES`（排除 `menxia_doc.py`、`zhongshu_doc.py`、`states.py` 中的 Menxia/旧 zhongshu 同名常量）确认引用点再决定**。

**1.3 `validate_draft`（同文件）新增 blocking 规则 `PENDING_MARKER`**
- 任一章节 `markdown` 命中正则 `【待确认[^】]*】` → `issue('PENDING_MARKER', (section_id,), owner='solver')`。
- 含义：草案进入校验/审批时不得残留未决标记；用户答复后须替换，或改写为普通"待定"（无方括号标记）。
- `ACCEPTANCE_UNMAPPED` 维持仅对 `goal`/`behavior` 生效；`boundary` 类 claim 只要求被某章节引用（`REQUIREMENT_UNMAPPED`，现状逻辑自动覆盖）。
- 注意：`requirements_effects.py:~44` 发布时调用 `validate_draft` 并在非空时抛 `MECHANICAL_DOCUMENT_INVALID`，因此 `PENDING_MARKER` 同时是发布闸门——这是期望行为。

**1.4 `contracts/requirements_common.py`**
- `SECTION` schema 的 kind 枚举来自 `RECORD_ENUMS`，自动更新；确认 `CLAIM` schema 同样自动带上 `boundary`。
- 注意：契约 schema 变化会改变 `schema_hash`，见 §9 测试更新。

**1.5 全仓库核对（实施者必做）**：grep `'non_goal'`、`'constraint'`、`'behavior'`、`CORE_SECTIONS`、`section.kind`、`.kind ==` 在 `review.py`、`requirements_metrics.py`、`handoff.py`、`requirements_port.py`、`consensus.py`、`result_validation.py` 的使用，确认新增枚举值不会触发 `KeyError`/未知分支。

### T2 草案优先的状态机变更

**2.1 `states.py` REQ_ANALYZE（原 202-212 行）**

```python
if self.name == 'REQ_ANALYZE' and action == 'ANALYSIS_READY':
    aliases = {}
    state = apply_analysis(state, payload, aliases=aliases)
    state = replace(state, work_queue=...)            # 保持不变
    state = merge_questions(state, payload.get('questions', ()), aliases)  # 保持：兼容 REQUIREMENTS_SCOUT=0 的旧 Analyst
    # 删除：state = cover_blocking_assumptions(state)
    if eligible_questions(state):                      # 仍保留（旧 Analyst 回退路径可能提问）
        state = open_round(state, 'REQ_ANALYZE', context.request.decision_actor_ids)
        return self.goto(context, state, 'ANALYSIS_NEEDS_USER')
    return self.goto(context, state, 'ANALYSIS_ACCEPTED')
```

**2.2 `states.py` REQ_DRAFT `DRAFT_READY`（原 214-225 行）**
- 在 `state = merge_questions(state, payload.get('questions', ()))` 之后插入 `state = cover_blocking_assumptions(state)`。
- 其余（`eligible_questions` → `open_round(..., 'REQ_DRAFT')` → `NEEDS_CLARIFICATION`）不变。现状已先 `commit_draft` 再开轮，所以"草案先落库、问题后提"天然成立。
- `NEEDS_CLARIFICATION`（Solver 因目标不可判定而无法成稿）分支不变；Solver skill 规定只有该情形才使用它（见 2.4）。

**2.3 `contracts/requirements_common.py` 共享规则修订**
- 删除并替换原规则 "Batch clarification by topic. Before the first round, cover applicable scope, compatibility, runtime/platform, allowed API changes and acceptance criteria together. …" 与 "Link blocking assumptions to questions…Do not leave known independent basic constraints for a later round."，改为：

```
'Draft first. Questions are authored only by the Solver together with a complete first draft; '
'every question must point to a pending marker 【待确认：<question_id>】 in that draft. '
'Group questions by topic in one round; do not hold the draft back for questions. '
'Do not invent questions for constraints already explicit in human sources.',
'Link blocking assumptions to questions using default_assumption_id.',
```
- 保留 "After a clarification round, ask additional questions only when…" 一条不变。
- `requirements_analyst.py`：规则追加"Analyst 不得提问：`questions` 必须为空数组"（见 T5.6 具体文本）。

**2.4 Solver 契约与 skill（`contracts/requirements_solver.py`、`docs/multi/runtime/requirements-solver-skill.md`）**
- 契约 `prompt_rules` 追加：

```
'Use the injected skeleton section kinds and the eight-chapter functions in the skill. '
'Unconfirmed items are written inline as 【待确认：<question_id>】 and every such marker needs a matching question; '
'unconfirmed numbers are written as 待定 (no brackets). Never cite an unavailable fact in fact_ids.',
```
- skill 全文替换见 §6.2。

**2.5 `dispatch.py` `build_dispatch`（REQ_DRAFT 提示文案）**
- 在 REQ_DRAFT 的中文 prompt 中加入一句："首轮直接完整成稿；未决处用 `【待确认：qid】` 标记并在本次回复的 `questions` 中给出对应问题（含 reason/impact/recommendation）。"
- REQ_ANALYZE 的 prompt 文案改为"探索线索，不提问，不写正文"（见 T5.7）。

**2.6 测试**：`cmd/test_requirements_draft_first.py`（新建，见 §9）。`cmd/test_requirements_batching.py` 直接调用 `cover_blocking_assumptions`，函数本身不改，应继续通过。

### T3 lint 与 REQ_VALIDATE 路由

**3.1 新建 `domain/requirements/lint.py`**（纯函数，无运行时导入）

```python
def lint_draft(draft, consensus, sources=()) -> tuple[ReviewIssue, ...]:
    """确定性、advisory。issue_id 形如 lint-<rule>-<n>（n 从 1 递增，按规则表顺序、章节顺序）。"""
```
每条 issue：`ReviewIssue(issue_id, rule='lint-'+slug, section_ids, requirement_ids=(), claim=<中文一句>, impact=<中文一句>, severity='advisory', owner='solver', acceptance=<中文一句>)`。

规则表（slug → 判定）：

| slug | 作用章节 | 判定 | claim 文案 |
|---|---|---|---|
| `vague-phrase` | goals、acceptance | 某条目（按行/列表项切分）含 `VAGUE_PHRASES` 之一，且不含数字、不含"待定" | "该条使用了无法判定的表述" |
| `acceptance-no-test-type` | acceptance | 章节正文不含任何 `TEST_TYPES` 词 | "验收标准没有写明测试类型" |
| `plan-as-result` | acceptance | 含 `PLAN_AS_RESULT = ('已通过','已执行','已验证','测试通过','全部通过')` | "把计划中的测试写成了已执行的结果" |
| `unconfirmed-number` | goals、acceptance | 条目匹配 `NUMBER_RE`，不含"待定"，所在章节无 `decision_ids` 且无 `fact_ids`，且该数字文本未出现在任何 `sources[*].text` 中 | "数字未经用户或事实来源确认，应标为待定" |
| `constraint-is-scope-cut` | constraints | 条目（去列表符后）以 `('不做','不负责','不包含','不支持','不提供','暂不')` 之一开头 | "本次范围取舍不属于约束，应归责任边界或非目标" |
| `non-goal-duplicates-scope` | non_goals vs scope | 两章存在规范化后（`normalized_quote`）相等的条目 | "非目标与责任边界重复" |
| `section-order` | 全部 | 已知 kind（`CANONICAL_ORDER` 内）的出现顺序偏离规范顺序 | "章节顺序偏离规范" |

常量：
```python
VAGUE_PHRASES = ('正常工作','工作正常','运行正常','稳定','合理','完善','良好','友好','提升体验','用户体验','尽快','及时','较快','高效','灵活')
TEST_TYPES = ('单元测试','集成测试','故障注入','回归测试','性能测试','端到端','E2E','契约测试','冒烟测试','验收测试','人工验证','手工验证')
NUMBER_RE = r'\d+(?:\.\d+)?\s*(?:ms|毫秒|秒|分钟|小时|%|％|次|个|条|MB|GB|KB|QPS|TPS|倍)'
```
- 条目切分：按换行；去掉前导 `- `、`* `、`1. `、`> ` 等列表/引用符。
- 空章节（`markdown.strip()==''`）不产生 lint（`SECTION_REQUIRED` 已负责）。
- 不对 `topic`/`rules` 章节做任何 lint。
- 排序与 id 必须确定性（同输入同输出），以便进度摘要稳定。

**3.2 `states.py` REQ_VALIDATE（原 242-248 行）改为：**

```python
if self.name == 'REQ_VALIDATE' and action in ('VALID', 'REPAIR_CONTENT'):
    mechanical = validate_draft(state.draft, state.consensus, state.decisions)
    lint = lint_draft(state.draft, state.consensus, state.sources)
    issues = tuple(i for i in state.issues
                   if not i.issue_id.startswith(('mechanical-', 'lint-'))) + mechanical + lint
    state = replace(state, issues=issues)
    if any(i.owner == 'analyst' for i in mechanical):
        return self.goto(context, state, 'NEEDS_EVIDENCE', kind='evidence')
    return self.goto(context, state, 'REPAIR_CONTENT' if mechanical else 'VALID',
                     kind='semantic' if mechanical else 'initial')
```
路由仍只由 `mechanical`（全部 blocking）决定；`lint` 仅入库。

**3.3 审批/发布不受 advisory 影响**：`RequirementsState.__post_init__` 只拒绝 *open blocking* issue，advisory lint 允许在批准时仍为 open，不改。

**3.4 Critic 如何使用 lint**：dispatch 已把 `state.issues` 提供给 Critic；Critic skill 规定见 §6.3（lint 是机器提示，Critic 认为损害意图/场景/验收时，才以自己的 issue 提出，且可为 blocking）。`merge_review`/`next_repair_route` **不改**；实施者须核对（§12 V5）`merge_review` 对输入中既有 `lint-` issue 的处理：不得因为 Critic 回复未提及而把 lint issue 标为 `resolved`，也不得因其 open 而影响 `next_repair_route`（后者只看 blocking）。如核对发现 `merge_review` 会处理全部 open issue，则在 `merge_review` 入口把 `lint-` 前缀 issue 排除在"需要显式关闭的旧缺陷"之外（改动限于 `review.py`，附测试）。

**3.5 进度摘要核对**：`budget.record_progress`（NO_EFFECTIVE_PROGRESS 判定）若把 `state.issues` 纳入摘要，因 lint 为确定性且每次 VALIDATE 全量替换，不会造成虚假"进展"或"停滞"；实施者须用一个测试确认（§9 `test_requirements_lint.py::test_progress_digest_stable`）。

### T4 溯源产物 `provenance.md`

**4.1 新建 `domain/requirements/provenance.py`**（纯函数）

```python
def render_provenance(state) -> str:
```
内容（Markdown，中文，确定性排序）：
1. `# 溯源说明`（含 document_digest 前 12 位、共识 digest 前 12 位、草案版本）。
2. `## 章节与来源`：每个章节一小节：列出其 `requirement_ids` → 对应 claim 的 `statement`（截断 120 字）+ `origin` + 来源 id 与原文引用摘要（取 `sources[*].text` 中与 `source_ids` 对应的首 120 字）；`decision_ids` → 决定结论与答案来源；`fact_ids` → 事实观察 + `source`（路径/行号）+ 引用 + `source_fingerprint` 前 12 位。
3. `## 未能核实的线索`：所有 `status='unavailable'` 的 fact（含探针 gap、探针失败登记），写 `source` 与 `observation`。
4. `## 假设`：每条 assumption 的 statement、blocking、disposition。
5. `## 未决问题`：当前 eligible 问题 id + 文案。
- 不含时间戳、不含随机值，保证同状态同输出。

**4.2 `runtime/requirements_effects.py`（~47-48 行 `files = {...}`）**
- 追加 `'provenance.md': render_provenance(state).encode('utf-8')`（草案与批准两种目录都写）。
- **不得**把其内容拼入 `requirements.md`；`requirements_port.py:69` 的回读哈希只对正文生效，保持不变。
- `handoff.py` 的 `ApprovedRequirementsInput` 是否枚举产物文件：实施者核对（§12 V4）；若是白名单，不要加入 `provenance.md`（Menxia 不需要它）。

**4.3 测试**：`cmd/test_requirements_provenance.py`（见 §9）。

### T5 Scout 并发探路

#### 5.0 总体

在 `app.py:~287` 的 runners 字典中，把 `RequirementsAgentRunner(self.runner, ...)` 的 `inner` 由 `self.runner` 换成 `ScoutFanoutRunner(self.runner, ...)`：

```
EffectManager → RequirementsAgentRunner(校验/缓存/修复反馈)
                  → ScoutFanoutRunner(扇出/汇总，仅对 REQ_ANALYZE 首次分析)
                      → AgentWorkerRunner（现有，每探针一次 run_once）
```
外层 `RequirementsAgentRunner` 把汇总结果当作"单个 Analyst 的回复"做契约校验、`result_errors`、事实引用核验、`apply_analysis`/`merge_questions` 试跑——因此 guard / coordinator / 预算 / 缓存语义全部不变。

#### 5.1 新文件 `runtime/requirements_scouts.py`

接口（同 `AgentWorkerRunner.run_once(request) -> EffectOutcome`）：

```python
class ScoutFanoutRunner:
    def __init__(self, inner, *, config: ScoutConfig | None = None, report_dir=None,
                 max_parallel: int | None = None): ...
    def run_once(self, request) -> EffectOutcome: ...
    def cache_salt(self) -> str: ...           # 供外层缓存键使用
    def __getattr__(self, name): return getattr(self.inner, name)   # 透传 agent_pool_summary 等
```

**5.1.1 `ScoutConfig`**（`from_env()`）：

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `REQUIREMENTS_SCOUT` | `1` | `0` → `run_once` 直接 `return self.inner.run_once(request)` |
| `REQUIREMENTS_SCOUT_FACETS` | `intent,code,docs,constraints` | 逗号分隔；去重；`intent` 缺失则自动前置；超过 5 个截断并记录；未知名称忽略并记录 |
| `REQUIREMENTS_SCOUT_TIMEOUTS` | `intent=600,*=300` | 秒；`*` 为其余默认 |
| `REQUIREMENTS_SCOUT_ATTEMPTS` | `intent=2,*=1` | 每探针最大尝试数（仅传输/格式失败重试） |
| `REQUIREMENTS_SCOUT_FACT_CAP` | `12` | 每探针最多保留的 fact 数；汇总后总量上限 40 |

解析失败（非法数字等）回落到默认并 `record('SCOUT_CONFIG_INVALID', ...)`；不得抛异常。

**5.1.2 触发判定** `_should_fanout(request)`：同时满足
- `request.payload.get('target_state') == 'REQ_ANALYZE'`；
- `config.enabled`；
- `request.payload.get('validation_state')` 是 dict 且其 `consensus.claims` 为空（D12）。
否则 `return self.inner.run_once(request)`。

**5.1.3 探针请求构造** `_probe_request(request, facet, index)`：用 `dataclasses.replace(request, effect_id=..., idempotency_key=..., payload={...})`（`EffectRequest` 字段名以 `runtime/` 中实际定义为准，§12 V1 核实）。payload 在原基础上覆盖：

| key | 值 |
|---|---|
| `worker_id` | `f'scout-{facet}-{index}'`（`index` 为 1 基；`AgentWorkerRunner.resolve_worker_agent` 用 `rsplit('-',1)[-1]-1` 取池下标，必须以 `-<数字>` 结尾） |
| `fanout_parent_id` | 原请求的 issue id（取法同 `AgentWorkerRunner._dispatch_request`；§12 V1） |
| `fanout_title` | `f'[scout:{facet}] {task_id}'` |
| `timeout_seconds` | `config.timeout(facet)`（`AgentWorkerRunner._effective_dispatch_limits` 已支持 payload 覆盖） |
| `request_id` | `f'{orig_request_id}-scout-{facet}'` |
| `idempotency_key` / `effect_id` | `f'{orig}-scout-{facet}'` |
| `dispatch_context` | 深拷贝原值，并追加 `'scout': {'facet': facet, 'focus': FACET_FOCUS[facet], 'allow_claims': facet == 'intent', 'fact_cap': config.fact_cap}` |
| `prompt`（或原 prompt 字段，名称以 `build_dispatch` 为准） | 原 prompt + `FACET_PROMPT[facet]`（见 5.1.7） |

`validation_state`、`target_state`、`role` 等原样保留。探针不修改原请求。

**5.1.4 并发执行**：`ThreadPoolExecutor(max_workers=min(len(facets), max_parallel or ANALYST_MAX_WORKERS 解析值))`；`intent` 最先提交。每探针循环 `attempts` 次：

```
outcome = inner.run_once(probe_request)
ok, payload = self._accept_probe(outcome, request, facet)   # 见 5.1.5
if ok: break
```
重试时（仅 intent 的第 2 次尝试）把上次的校验错误文本追加到 prompt 末尾（"上次回复被拒：…，请只修复这些路径"）。全部异常（含线程内异常）捕获为该探针失败，不得向外抛。

**5.1.5 探针结果接收** `_accept_probe(outcome, request, facet)`：
1. `outcome.status != 'SUCCEEDED'` 或 `event_name != 'ANALYSIS_READY'` → 失败（若 `PREREQUISITE_MISSING`，记录原因）。
2. 取 `payload = deepcopy(outcome.event_payload)`；用 `contract_for_state('REQ_ANALYZE', workflow='requirements-v1').validate(payload 去掉运行时键)`（运行时键集合与 `requirements_agent._RUNTIME_KEYS` 一致，**复用而非复制**：把它提升为模块级公共常量或从该模块导入）。不通过 → 失败。
3. 对 `analysis.facts` 做**非变更**引用核验：把 `RequirementsAgentRunner.run_once` 中 197-215 行的循环抽成模块级纯函数 `verify_facts(facts, project_root) -> (verified_facts, errors)`（放在 `runtime/requirements_agent.py` 或新建 `runtime/fact_evidence.py`；外层原循环改为调用该函数，**行为逐字不变**，另补回归测试）。核验失败的 fact 被**丢弃**并计入报告，不构成探针失败。
4. 非 intent 探针：丢弃 `analysis.claims`（计数入报告）；每条 assumption 强制 `blocking=False`；每条 fact 的 `requirement_ids` 置空。
5. 所有探针：丢弃 `questions`（计数入报告）；`analysis.facts` 超过 `fact_cap` 时按出现顺序截断。
6. intent 探针：额外调用 `result_errors(payload, dispatch_context)`（claims 逐字引用门，`result_validation.py`）；有错 → 视为本次尝试失败（可重试）。

**5.1.6 汇总** `_merge(request, results)`（确定性，与探针完成先后无关；按 `FACETS` 固定顺序遍历）：
- 基底 = intent 探针的 `outcome`/`payload`（保留其运行时键）；
- `analysis.claims` = intent 的 claims（原样）；
- `analysis.facts` = 各探针已核验 facts 依固定顺序拼接，按 `(规范化 source, normalized_quote(quote))` 去重（保留先出现者）；非 intent 探针的 `local_key`（以及同探针内被引用处）统一加前缀 `f'{facet}.'` 以满足"local_key 全局唯一"；总量上限 40，超出截断并入报告；
- gaps → `analysis.facts` 追加 `{'status':'unavailable','observation':<gap 文本>,'source':f'scout:{facet}','quote':'', 'local_key':f'{facet}.gap-{n}'}`；失败/超时探针追加一条 unavailable fact：`observation=f'探针 {facet} 未完成：{原因}'`；
- `analysis.assumptions` = 各探针 assumptions，按规范化 statement 去重，`local_key` 加前缀（intent 不加）；
- `analysis.gaps` = `[]`（已转为 unavailable facts；`apply_analysis` 本来就丢弃 gaps）；
- `questions` = `[]`；
- `action='ANALYSIS_READY'`；`reason='探索汇总：<探针数>个探针，成功<k>，失败<m>'`；
- **重新绑定**：`task_id`、`request_id`、`role`、`state` 取原请求值（同 `RequirementsAgentRunner._analyze_cache_hit` 134-137 行做法）；返回 `replace(intent_outcome, event_payload=merged, request_id=原 request_id)`；
- 若 intent 探针最终失败：**原样返回 intent 的最后一次 outcome**（不合并），由外层现有路径转成修复反馈/失败。
- 若 intent 返回 `PREREQUISITE_MISSING`：原样返回该 outcome。

**5.1.7 探针提示词（`FACET_PROMPT`）**——追加到原 REQ_ANALYZE prompt 之后，中文：

- 通用尾注：`你是只读探针，限时完成；只找线索，不提问、不写需求正文、不设计实现。每条 fact 必须含来源文件与逐字连续引用；找不到的内容写入 analysis.gaps，不要猜。最多返回 {fact_cap} 条 fact。questions 必须为空数组。`
- `intent`：`从用户原始来源中提取明确的目标、行为、约束、非目标与范围取舍，作为 claims（kind 可为 goal/behavior/constraint/non_goal/boundary，必须逐字引用用户原文）。区分"用户明确说的"与"你推断的"，推断只能进 assumptions（proposed，必要时 blocking=true）。同时可返回与意图直接相关的 facts。`
- `code`：`在项目内定位与需求直接相关的代码、模块、接口、配置的现状，返回 facts（来源=文件路径[:行号]）。只读，不改，不通读整个仓库；覆盖面以"足以说明现状与影响"为准。`
- `docs`：`查找与需求相关的既有文档、规格、计划、README、说明，返回 facts；特别标出已有的相反/重叠规定。`
- `constraints`：`查找外部强加、无法靠我们的决定改变的限制（既有接口能力、平台/版本兼容、合规、已定技术栈、禁止引入的依赖），返回 facts。不要把"本次范围取舍"当约束。`
- `history`（默认关）：`查找与该需求相关的历史任务、已批准需求、评审记录，返回 facts。`

`FACET_FOCUS` 为上述各句首的一行摘要（≤40 字），写入 `dispatch_context.scout.focus`，供 prompt 构建和排障。

**5.1.8 子 issue 与关闭**：探针经 `fanout_parent_id`/`fanout_title` 走现有扇出机制由传输层创建子 issue。探针结束（成功/失败/异常）后必须关闭其子 issue：参照 `app.py:_finalize_fanout_children`（~393-403）的做法，对 `transport.close_issue(child_id, 'done'|'cancelled')`。把这段"按 worker 结果关闭子 issue"抽成可复用函数，`ScoutFanoutRunner` 通过构造参数 `close_child=callable(child_id, status)` 注入，`app.py` 提供实现；探针结果里取子 issue id 的字段以 `AgentWorkerRunner` 实际输出为准（§12 V1：`worker.external_issue_id` 是 Menxia 节点的取法，单次 `run_once` 的取法需核实）。**子 issue 创建失败时探针回落为在父 issue 上直接运行**，并 `record('SCOUT_CHILD_ISSUE_FALLBACK', ...)`，不视为失败。

**5.1.9 报告与指标**
- 写 `<report_dir>/<task_id>/<原 request_id>.json`：`{'facets':[{'facet','status','attempts','seconds','facts_accepted','facts_rejected','claims_dropped','questions_dropped','failure'}], 'merged': {...计数}}`。`report_dir = allowed_root / 'scout-reports'`（`allowed_root` 即 `self.root.parent`，与 analyze-cache 同级）。写入失败静默忽略。
- 事件：`record('SCOUT_FANOUT_START'|'SCOUT_PROBE_DONE'|'SCOUT_FANOUT_DONE', ...)`（`record` 取法同 `requirements_agent.py`）。
- `requirements_metrics.py`：新增 `first_complete_draft_seconds` 与探针计数（实施者按该文件现有指标注册方式补充；§12 V6）。

**5.1.10 缓存盐**：`cache_salt()` 返回 `sha256(canonical({'enabled','facets','fact_cap','facet_prompt_version':1}))`；探针超时/尝试次数**不**入盐（不影响结果语义）。

#### 5.2 `runtime/requirements_agent.py` 改动
- `_analyze_cache_path` 的 `material` 改为 `{'v': 3, 'skill_sha': ..., 'schema_hash': ..., 'scout': getattr(self.inner, 'cache_salt', lambda: '')(), 'validation_state': key_state}`。
- 把 197-215 行 fact 核验循环抽成函数 `verify_facts`（见 5.1.5.3），外层行为不变；`_RUNTIME_KEYS` 保持，同时被 scouts 模块复用。
- 汇总回复来自缓存时，`_analyze_cache_fresh` 对 `status='unavailable'` 的 fact 已跳过（第 98 行），无需改动。

#### 5.3 `app.py` 接线（~287 行）
```python
from .runtime.requirements_scouts import ScoutFanoutRunner
scout = ScoutFanoutRunner(self.runner, report_dir=self.root.parent / 'scout-reports',
                          close_child=<复用 _finalize_fanout_children 的关闭逻辑>)
runners = {'agent_dispatch': RequirementsAgentRunner(scout, allowed_root=self.root.parent), ...}
```
- `ANALYST_MAX_WORKERS`（`app.py:222`，默认 3）不改；部署文档建议设为 5（见 §8 发布）。
- `ScoutFanoutRunner` 取 `max_parallel` 的方式：从同一环境变量解析，缺省 3，与 `ConcurrencyLimits.analyst_max` 一致（避免两处默认不同）。

#### 5.4 Analyst 契约与 skill
- `contracts/requirements_analyst.py`：契约字段**不变**（保持 `analysis`、`questions`，避免动 schema）；`prompt_rules` 追加：`'Analyst 是只读探针：questions 必须是空数组；非 intent 探针的 claims 会被丢弃；找不到的内容写入 gaps。'`（英文原句不动，仅追加该条；D18）。
- skill 文件 `docs/multi/runtime/requirements-analyst-skill.md`：见 §6.1。
- skill 锁：`compat_effects._skill_lock` 以文件 sha256 锁定；改 skill 后 `schema_hash`/`skill_sha` 均变化，相关测试需同步（§9）。

#### 5.5 与现有机制的相容性核对（已分析，实施者无需重做，只需通过测试证明）
- `EffectManager.execute_pending` 串行、一个 `active_dispatch`：扇出发生在单个 effect 的 `run_once` 内部，天然满足。
- 预算：`build_dispatch` 对一次逻辑派发只扣 1 次 `calls`；探针是该派发内部的传输尝试，不额外扣预算；外层修复路径（`evidence_repairs`/`semantic_repairs`）语义不变。
- guard/coordinator：事件 request_id 已被重绑为原 request_id，stale 规则不变。
- 缓存：命中时跳过整个扇出；写缓存的是汇总后的 payload。
- 超时：外层角色超时 1800s（`ROLE_DISPATCH_TIMEOUTS`）> 扇出墙钟上限 660s。

### T6 Critic 重心调整、fixtures、评估、规格修订

**6.1** Critic 契约与 skill：`contracts/requirements_critic.py` 的 `prompt_rules` 追加 §6.3 中的两条规则（英文原句不动）；skill 全文替换见 §6.3。`AXES` **不变**（`ReviewStamp.coverage` 必须覆盖全部 AXES）。

**6.2** fixtures（`cmd/fixtures/requirements/`，格式照现有三个目录/文件）新增：
1. `draft-first-explicit-goal`：请求有明确目标但含 ≥2 个阻塞未知 → 期望首个 Solver 派发先于任何用户问题；首个提问卡片的问题均带草案标记。
2. `draft-first-vague-goal`：请求目标不可判定 → Solver 返回 `NEEDS_CLARIFICATION`，先问意图问题。
3. `scout-partial-failure`：非 intent 探针超时 → 汇总带 unavailable fact，流程照常到 DRAFT。
（若 fixture 格式要求录制 agent 回复，参照 `result-boundary-20261007.json` 的结构手工构造；无法构造真实回放的，以单元测试代替并在 `cmd/fixtures/requirements/README.md` 注明。）

**6.3** 评估：重跑 T0 的三场景 + 新场景，把对比写入 `docs/superpowers/specs/2026-10-10-zhongshu-scout-baseline.md` 的"改后"栏。

**6.4** 规格/计划修订：见 §7。

## 6. 角色 Skill 与契约文本（可直接落盘）

### 6.1 `docs/multi/runtime/requirements-analyst-skill.md`

保留文件头两行（`# requirements-v1 Analyst`、`ACTIVE_RUNTIME_SKILL: requirements-analyst`）；将首段 "Read the bound dispatch context … under the injected state contract. Only an explicit platform overflow … normal inline results." 替换为下列内容；"Fact protocol"、"Assumption write permissions"、"too large to post safely"、"超长输出必须写入结果文件。" 三段**逐字保留**（`test_active_runtime_skill_binding` 与相关测试依赖这些字面串）。

```
Read the bound dispatch context and current consensus. You are a read-only scout (探针): your job is
to quickly find clues that help the Solver write the FIRST COMPLETE DRAFT. Stay inside your facet
(dispatch_context.scout.facet / focus), finish within the time box, and return exactly one complete
inline JSON result under the injected state contract. Only an explicit platform overflow enables the
bound result-file fallback. The orchestrator materializes normal inline results.

探针规则：
1. 只找线索：目标/行为/约束/非目标的原文依据，相关代码与文档现状，外部强加的限制。不写需求正文、不设计实现、不提出问题（questions 必须为 []）。
2. 只有 intent 探针可以返回 claims；claims 必须逐字引用用户原文，且不得凭空制造用户决定。其余探针即使返回 claims 也会被丢弃。
3. 每条 fact 必须有可读的 UTF-8 来源文件与一段连续逐字引用；一条 fact 只说一件事；不要为凑数量而写 fact，数量上限见 dispatch_context.scout.fact_cap。
4. 找不到、读不了或无权限的内容写入 analysis.gaps（一句话说明"找了什么、为什么没找到"），不要猜测，不要写成 observed fact。
5. 推断只能写成 assumptions（disposition=proposed）。只有 intent 探针的 assumption 才可标 blocking=true；其他探针的 blocking 会被忽略。
6. 复用已验证事实；REQ_EVIDENCE 时只查被点名的缺口，不重做全面调研。
7. 区分"约束"（外部强加）与"范围取舍"（我们自己划定）：不要把范围取舍写成约束线索。
```

（随后接原文 "Fact protocol: …"、"Assumption write permissions: …"、"If the platform reports …"、"超长输出必须写入结果文件。"。）

### 6.2 `docs/multi/runtime/requirements-solver-skill.md`（全文替换，保留末尾两段字面串）

```
# requirements-v1 Solver

ACTIVE_RUNTIME_SKILL: requirements-solver

You alone author the complete requirements document and you alone ask the user questions.
Read current consensus, human sources, scout facts (including unavailable ones), the one bound
document and all open issues. Preserve valid unrelated text and user choices. Return every
section under the exact injected contract as one complete inline JSON result. Only an explicit
platform overflow enables the bound result-file fallback. Human approval is a verified card
callback bound to the current document digest, not a text reply or agent action.

草案优先：
1. 首轮直接写出完整草案，不要等问题先被回答。能从用户原文、已验证事实、已确认决定写实的就写实；
   尚未确认且会改变目标/范围/行为/责任边界/验收的内容，在正文对应位置写 `【待确认：<question_id>】`，
   并在本次回复的 questions 中给出同名问题（reason、impact、options、recommendation 齐全）。
   每个标记必须有对应问题；不影响草案的细节只写"待定"（不带方括号）。
2. 仅当用户的目标完全无法判定、无法成稿时，才返回 NEEDS_CLARIFICATION 先问意图问题。
3. 提问按主题集中为一轮；不重复问用户已回答或资料已能确定的内容。
4. 用户答复后，把对应 `【待确认】` 替换为已确认内容；用户选择"待定/延后"的，改写为普通"待定"。
5. 不得在 section 的 fact_ids 中引用 status 为 unavailable 的事实；这类线索只能转成"待定"或提问。
6. 引擎不会替你做业务选择；缺事实用 NEEDS_EVIDENCE，缺解释用 NEEDS_ANALYSIS。

八章职能（顺序固定；sections 的 kind 与章节对应如下，title 用括号内标题，section_id 保持稳定）：
- background（背景）：当前情况与需求缘由，回答"问题从哪来"。误写：提前写方案；把目标或功能塞进来。
- goals（目标）：期望的结果或能力变化。误写：把实现步骤当目标；写成功能列表；"提升体验"之类无法判断的话。
- problem（当前问题，可选）：现有能力缺口及实际影响。与背景/目标重复时不要单列。误写：把目标反过来说一遍。
- constraints（约束）：外部强加、无法靠我们决定改变的限制（已有接口能力、平台/版本兼容、合规、已定技术栈、"不引入新依赖"）。
  判据：问"这是现实强加给我们的，还是我们自己选的？"强加的才是约束。误写：把还没决定的方案写成约束；把本次范围取舍（如"不做后台重试"）写成约束。
- behavior（核心功能清单）：系统应提供的能力与可观察行为（触发条件、系统行为、可观察结果）。
  误写：写成开发任务看板；写实现方式；写成验收测试。行为写结果，不擅自指定实现。
- scope（责任边界）：我们自己划定的范围——每项核心功能做到哪里为止、哪里交给别人。
  误写：把外部限制写成边界；把整体不做的功能写进来（那是非目标）；默认罗列所有参与方职责（只写真实交接关系）。
- acceptance（验收标准）：将执行的测试类型 + 场景/输入 + 明确通过条件（单元/集成/故障注入/回归/性能按适用）。
  误写：把核心功能换句话重述；"正常工作"；把计划测试写成已执行结果；没确认的数字当阈值——一律写"待定"。
- non_goals（非目标）：整个需求明确不做的相邻能力。与责任边界的区别：责任边界限定已纳入功能的范围，非目标是整体不含的功能。误写：与责任边界重复；写没人期待的无关功能。
- risks（风险，可选）：仅在存在会影响范围/实现/验收的真实风险时添加，放在最后；每条写"风险是什么、影响哪里、如何确认或缓解"。不写"可能延期"之类套话；不写"无"。
空章节：constraints、acceptance、non_goals 写"暂无"，其余写"无"。来源贴近结论：关键结论用 requirement_ids/decision_ids/fact_ids 绑定来源，不要把来源附注写进正文。

三章速辨（以"网络请求失败后自动重试"为例）：
- 约束："上传接口已支持幂等键""不增加第三方依赖"（外部给定）。
- 责任边界："重试到成功或返回最终错误为止，不负责界面展示"（已纳入功能的停止点）。
- 非目标："断点续传"（整个需求不做的相邻功能）。

If the platform reports "too large to post safely", write full UTF-8 JSON to
`agent-result.json` and return the bound `result_path` and `result_sha256` reference.
超长输出必须写入结果文件。
```

### 6.3 `docs/multi/runtime/requirements-critic-skill.md`（全文替换）

```
# requirements-v1 Critic

ACTIVE_RUNTIME_SKILL: requirements-critic

Review the same bound full document independently against all original human sources and
current consensus. Check intent, scenarios, constraints, consistency, acceptance, decisions
and facts. Reuse unchanged coverage except global consistency; do not repeat valid research.
Each defect names a stable rule and section/requirement/source, its claim, consequence, owner,
severity and fixed acceptance condition. Business tradeoffs belong to the user; facts belong
to Analyst; body defects belong to Solver; runtime defects belong to the engine. Resolve old
defects explicitly with evidence; omission never closes them. Do not rewrite the document,
change user decisions or block on implementation design. Return the exact contract through
one complete inline JSON response; only explicit platform overflow enables the bound file
fallback. Use the injected enum values and field names, not legacy findings/voting menus.
The orchestrator verifies human approval by card callback and current document digest.

审查重心（运行时 lint 已覆盖的结构问题不要重复审）：
1. 意图：草案是否忠实于用户原始意图？有没有遗漏、曲解、擅自扩大？
2. 场景：核心功能是否覆盖关键场景与异常/边界场景？每个场景的可观察结果是否明确？
3. 缺口：是否存在会改变目标/范围/行为/验收却没被标为待确认、也没有对应问题的空白？
4. 一致性与职能：目标、功能、责任边界、验收、非目标之间是否矛盾、越界或重复？约束章是否混入了范围取舍？
5. 验收可执行性：每个目标/行为是否有可执行的测试类型、场景与通过条件？

运行时 lint（issue_id 以 `lint-` 开头，severity=advisory）是机器提示：词句空泛、验收无测试类型、
计划写成已执行、数字未确认、章节顺序等。它们每次校验都会重算，你不需要关闭它们。只有当你认为某条
lint 实质损害了意图/场景/验收时，才用你自己的 issue（可标 blocking）提出；否则忽略。
草案中仍存在 `【待确认：…】` 标记时，不要要求其"已解决"——那是等待用户的问题，不是 Solver 的缺陷。

If the platform reports "too large to post safely", write full UTF-8 JSON to
`agent-result.json` and return the bound `result_path` and `result_sha256` reference.
超长输出必须写入结果文件。
```

`contracts/requirements_critic.py` 的 `prompt_rules` 追加（英文，一条）：
`'Do not repeat structural checks already reported as lint-* advisory issues; judge intent, scenarios and gaps. A pending marker 【待确认】 is a user-owned open question, not a Solver defect.'`

## 7. 规格与计划修订条款（实施时落到原文）

原规格章节号以标题为准（`## 4 中书省阶段语义`、`## 5 文档骨架与填充要求`、`## 6 三个角色的职责`、`## 7 飞书批量澄清要求`、`## 11 验收标准`）。**实施者先阅读这些章节，删除与下列条款冲突的句子**，再按下列文字写入；不要改动无冲突部分。文件头加一行：`> 修订：2026-10-10 见 2026-10-10-zhongshu-scout-draft-first-modification.md。`

- **R-4（§4 阶段语义，追加）**：REQ_ANALYZE 为"探索"阶段：由编排器并发派出只读探针寻找线索，汇总为一份分析结果；该阶段不向用户提问。
- **R-5.1（§5.1 骨架来源，覆盖）**：正文骨架采用八章职能：背景、目标、约束、核心功能清单、责任边界、验收标准、非目标为必备章节；"当前问题"与"风险"为可选章节。章节标题可调整，章节 ID 保持稳定。
- **R-5.2（§5.2 填充规则，追加）**：约束仅记录外部强加、无法靠本次决定改变的限制；责任边界记录本次自行划定的范围；非目标记录整体不做的相邻能力；三者不得互相重复。验收标准写明测试类型、场景与通过条件，未确认的数字写"待定"，不得把计划中的测试写成已执行结果。正文不携带来源附注，来源见同目录 `provenance.md`。
- **R-6.1（§6.1 Analyst，覆盖）**：Analyst 是并发只读探针集合，目的是快速理解需求并找到线索，帮助 Solver 提高首版草案效率。探针只返回带逐字引用的事实、用户原文依据的主张（仅意图探针）、待确认假设与未找到项；不提问、不写正文。
- **R-6.2（§6.2 Solver，覆盖）**：Solver 是唯一正文作者与唯一提问者；首轮直接产出完整草案，未决处用 `【待确认：问题ID】` 标记并给出对应问题；仅在目标完全无法判定时先提意图问题。
- **R-6.3（§6.3 Critic，覆盖）**：Critic 审查意图、场景与缺口；机械可查的结构质量问题由运行时 lint 以建议级别给出，Critic 不重复审查。
- **R-7.1（§7.1，覆盖）**：问题在首版完整草案之后提出，并锚定在草案的待确认位置。目标尚不明确时，Solver 先集中确认痛点、期望结果、优先方向和范围；目标明确后，再围绕草案中的待确认点集中确认关键场景、行为取舍、约束和验收预期。允许多轮澄清，每轮围绕一组相关决策；已回答的问题不重复询问，除非出现影响原决定的新情况。三个角色发现的问题由编排器统一汇总、去重和呈现（Analyst 不直接提问）。
- **R-11（§11 验收标准，追加）**：① 首个完整草案先于首轮用户问题产生（目标可判定时）；② 草案无残留 `【待确认】` 标记才能进入审批；③ lint 建议级别问题不阻止审批；④ 每份发布产物含 `provenance.md` 且不影响正文哈希；⑤ `REQUIREMENTS_SCOUT=0` 时行为回退为单 Analyst 派发。
- **计划（原计划 Task 6/Task 7 前加一节）**：加"Task 6A–6F：见 2026-10-10 修改文档 §5（T1–T6）"，并在 Task 6 的 6.3 契约表中把 REQ_ANALYZE 行注明"`questions` 恒为空，提问权归 REQ_DRAFT"。

## 8. 提交点与发布/回滚

建议提交点（每点测试绿后）：

1. `test(requirements): record baseline for draft-first and scouts` — T0 基线文档
2. `feat(requirements): align draft sections with eight-chapter template` — T1
3. `feat(requirements): draft first and let the solver own questions` — T2
4. `feat(requirements): advisory draft lint with blocking pending markers` — T3
5. `feat(requirements): publish provenance.md next to approved drafts` — T4
6. `feat(requirements): fan out analyst scouts under a single analysis result` — T5
7. `docs(requirements): refocus critic and amend workflow spec` — T6

提交信息末尾按系统要求附 `Co-Authored-By: Claude Code <noreply@anthropic.com>`；**仅在使用者要求提交时才提交**。

**发布**
- 先部署全部代码（环境变量不变：`REQUIREMENTS_SCOUT` 默认 `1`）。
- 部署前建议设置 `ANALYST_MAX_WORKERS=5`（默认 3 也可工作，探针会排队）。
- 仅新任务生效；已有进行中任务保持旧骨架/旧状态数据（见 §12 V2 的兼容测试）。

**回滚**
- 探针：`REQUIREMENTS_SCOUT=0`（立即生效，无需改代码）。
- draft-first / 模板 / lint：回滚对应提交（T1–T3）；因仅新增枚举值且不改持久字段，回滚后新任务产生的数据中若含 `boundary`/`problem`/`risks`，旧代码会拒绝解码——**因此回滚前须确认没有进行中的新任务，或先让其终结**。
- `provenance.md`：回滚 T4 仅少一个文件，无兼容问题。

## 9. 测试计划

运行入口：`rtk proxy python -X utf8 cmd/run_tests.py <模块名...>`（原计划用法）；全量回归：`rtk python cmd/run_core_tests.py`。新增测试需登记到 `cmd/CORE_TESTS.md` 约定的核心集合（按该文件现有写法追加）。

**新增**
- `cmd/test_requirements_document_template.py`：骨架 kind/标题/顺序；`render_markdown` 确定性；含 `rules` 的旧式 8 章草案仍可 `decode`、`validate_draft`、渲染且摘要不变；`PENDING_MARKER`。
- `cmd/test_requirements_draft_first.py`：`ANALYSIS_READY` 含阻塞假设时进入 REQ_DRAFT（无 `ANALYSIS_NEEDS_USER`）；Solver `DRAFT_READY` 未带问题但存在 blocking-proposed 假设时自动补问题；`DRAFT_READY` 带问题 → 草案已提交且开轮 `return_to='REQ_DRAFT'`；`NEEDS_CLARIFICATION` 路径；旧 Analyst 回退路径（analyst 返回 questions）仍可进入 `ANALYSIS_NEEDS_USER`。
- `cmd/test_requirements_lint.py`：每条规则正/反例；id 与顺序确定性；severity/owner；仅 lint 时 REQ_VALIDATE 返回 `VALID`；有 blocking 机械问题时仍 `REPAIR_CONTENT`；lint 每次 VALIDATE 被全量替换；`test_progress_digest_stable`；`merge_review` 对 `lint-` issue 不产生副作用。
- `cmd/test_requirements_provenance.py`：确定性；章节-claim-来源-事实映射；unavailable 事实列入"未能核实的线索"；发布目录含 `provenance.md` 且 `requirements.md` 字节不变、`document_digest` 不变。
- `cmd/test_requirements_scouts.py`（沿用 `test_requirements_analyze_cache.py` 的 FakeInner 模式）：
  1. 并发（用 `threading.Barrier` 证明至少 2 个探针同时在飞）；
  2. 汇总确定性（打乱探针完成顺序，输出逐字节相同）；
  3. 非 intent 探针 claims 被丢弃、blocking 被强制 false、`requirement_ids` 被清空；
  4. 引用核验失败的 fact 被丢弃而非整体失败；
  5. gaps → unavailable facts；失败/超时探针 → unavailable fact，流程继续；
  6. intent 失败 → 原样返回其失败 outcome；intent `PREREQUISITE_MISSING` 透传；
  7. `local_key` 全局唯一且引用被一致改写；fact 去重；总量上限；
  8. 汇总 payload 经外层 `RequirementsAgentRunner` 能通过契约校验与 `apply_analysis`/`merge_questions` 试跑；`request_id` 等已重绑；
  9. `REQUIREMENTS_SCOUT=0` 直通；已有 claims 时（D12）直通；配置解析（intent 强制、上限 5、未知名称、非法数字回落）；
  10. 子 issue 关闭在成功/失败/异常路径都被调用；创建失败回落；
  11. `cache_salt` 随探针集合变化而变化，外层缓存键 v3；
  12. 每探针超时覆盖 payload `timeout_seconds` 与 `worker_id` 以 `-<数字>` 结尾。

**必须更新**
- `cmd/test_requirements_analyze_cache.py`：硬编码 `SCHEMA_HASH` 与 `'v': 2` → 重新计算/改为 3；增加盐参与键的断言。
- `cmd/test_active_runtime_skill_binding.py`、`cmd/test_requirements_contracts.py`、`cmd/test_requirements_dispatch.py`：skill sha、契约 schema、提示文案断言。
- 任何断言 `TITLES`/`CORE_SECTIONS`/章节数量 8 的测试（实施者先 `grep -rn "CORE_SECTIONS\|TITLES\|'non_goal'" cmd/test_*.py`）。
- 回归：`fact` 核验函数抽取后，原 `RequirementsAgentRunner` 的 `FACT_EVIDENCE_INVALID`、CRLF 等价、行号后缀处理的既有测试必须无改动通过。

## 10. 验收标准（整体）

| # | 条件 | 验证方式 |
|---|---|---|
| A1 | 新增与更新的测试全部通过，`run_core_tests.py` 无新增红 | §9 |
| A2 | 目标可判定的 fixture 中，首个 Solver 派发先于任何用户问题 | fixture `draft-first-explicit-goal` |
| A3 | 目标不可判定的 fixture 中，Solver 返回 `NEEDS_CLARIFICATION` 先问意图 | fixture `draft-first-vague-goal` |
| A4 | 草案含残留 `【待确认】` 无法发布（`MECHANICAL_DOCUMENT_INVALID`） | `test_requirements_document_template` |
| A5 | 仅有 advisory lint 时流程进入 REQ_REVIEW，不触发 `REPAIR_CONTENT` | `test_requirements_lint` |
| A6 | 汇总分析与单 Analyst 回复在外层校验链路上不可区分 | `test_requirements_scouts` #8 |
| A7 | `REQUIREMENTS_SCOUT=0` 与改动前行为一致（派发次数、payload 结构） | `test_requirements_scouts` #9 |
| A8 | 每份发布产物含 `provenance.md`，正文字节与摘要不受影响 | `test_requirements_provenance` |
| A9 | `first_complete_draft_seconds` 相对 T0 下降（有墙钟数据时）；无墙钟数据则报告探针并发度与派发数变化 | T0/T6 基线文档 |
| A10 | 旧任务数据（含 8 章 `rules` 旧草案）仍可加载、校验、发布 | `test_requirements_document_template` |

## 11. 延后项（本批明确不做，及原因）

| # | 项 | 原因 / 触发再做的条件 |
|---|---|---|
| X1 | 来源原子化（把用户长原文切成可寻址片段） | 需要新增持久字段；先观察 `provenance.md` 是否足够 |
| X2 | 章节锚定的反馈棘轮（用户对某章的修改意见只回到该章） | 需新增反馈-章节绑定字段 |
| X3 | 结构化验收用例（`AcceptanceCase`）与约束的 `imposed_by` 字段 | 需新增持久字段；lint 先行验证价值 |
| X4 | 晚到线索（非 intent 探针超时后到达的结果后续合并） | 需要引入异步合并与 stale 规则；当前以 unavailable fact 登记 |
| X5 | 草案预览随澄清卡片展示给用户 | 涉及飞书卡片与回读，另立需求 |
| X6 | 探针结果缓存（修复重试时复用已成功探针） | 重试频率低时收益小；若 T6 评估显示修复重跑成本高再做 |
| X7 | `analyst_max` 代码默认值改为 5 | 部署侧环境变量即可，避免改全局默认影响 Menxia |
| X8 | 用户答案进入共识的路径优化（答案 → NEEDS_ANALYSIS → 再成稿的额外一圈） | 属既有行为，需先在 T6 用指标确认是否为瓶颈 |

## 12. 实施者必须核实的事项（读代码后确认，结论写入 PR 描述）

- **V1 传输/请求细节**：`EffectRequest` 的字段名（`effect_id`、`idempotency_key`、`payload`…）；`AgentWorkerRunner._dispatch_request`（`agent_effects.py:342-362`）如何取父 issue id、如何创建子 issue、单次 `run_once` 结果里子 issue id 的字段；admission 在槽位满时是排队还是拒绝（若拒绝，D13 的自限流是必须的，已满足）；`resolve_worker_agent` 对 `scout-<facet>-<n>` 形如的 worker_id 取池下标是否正确。
- **V2 旧数据兼容**：旧草案含 `rules` 章节与旧 `TITLES`；确认 `RequirementsState.__post_init__`（`model.py:505-510`）只用 section 自带 title 渲染摘要（`render_markdown` 现状如此），升级后旧任务无需迁移。
- **V3 枚举扩展的下游**：§T1 1.5 的 grep 清单；`result_validation.py`、`consensus.py`、`review.py:coverage_basis`、`requirements_metrics.py`、`handoff.py`、`requirements_port.py` 对 claim/section kind 的分支。
- **V4 handoff/飞书**：`handoff.py` 的 `ApprovedRequirementsInput` 是否枚举发布目录文件；`requirements_port.py:69` 回读逻辑只对 `requirements.md` 计算 `document_digest`；`provenance.md` 是否需要上传到飞书（本批不要求，保持仅落本地）。
- **V5 Critic/review 合并**：`review.py:merge_review` 与 `next_repair_route` 对输入里已有的 advisory `lint-` issue 的行为（§T3 3.4）。
- **V6 指标注册方式**：`requirements_metrics.py` 现有指标的登记方式；`cmd/evaluate_requirements_workflow.py` 的参数与输出，以便补 `first_complete_draft_seconds`。
- **V7 并发环境变量命名**：`app.py:220-223` 的 `GLOBAL_MAX_WORKERS/PER_TASK_MAX_WORKERS/ANALYST_MAX_WORKERS/CRITIC_MAX_WORKERS`；本文新增变量统一 `REQUIREMENTS_SCOUT*` 前缀（与既有 `REQUIREMENTS_ANALYZE_CACHE` 一致）。
- **V8 skill 锁测试**：`compat_effects._skill_lock` 与 `test_active_runtime_skill_binding` 对字面串（"too large to post safely"、"结果文件"）和 `ACTIVE_RUNTIME_SKILL:` 头的断言，改 skill 后须全部满足。
- **V9 答案路径**：`accept_answers` 如何把答案变成 `pending_source_ids` 并触发 `NEEDS_ANALYSIS`；确认 D12 的"claims 非空则直通"在该路径下命中（避免答案触发整套探针）。
- **V10 其他下游消费者**：后台调研 agent"Tests, cards, downstream consumers"（afef18ab551d642df）若在你开工时已有结果，以其清单补充 V3/V4；没有则自行 grep。

## 13. 实施顺序速查

```
T0 基线 → T1 模板(model/document/validate_draft/契约枚举) → T2 draft-first(states/契约规则/solver skill/dispatch 文案)
       → T3 lint(+VALIDATE 路由) ─┐
       → T4 provenance ───────────┤（T3/T4 可并行）
                                  └→ T5 scouts(requirements_scouts.py / requirements_agent.py 抽函数与缓存盐 / app.py / analyst skill+契约)
                                       → T6 critic 重心/fixtures/评估/规格修订
```
每步完成即跑对应新增测试 + `rtk python cmd/run_core_tests.py`；任何一步红则停在该步修复，不得跨步堆积。
