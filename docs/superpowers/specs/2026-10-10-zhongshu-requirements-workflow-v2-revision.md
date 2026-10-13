# 中书省需求工作流 v2 修改文档（补充篇：机械门槛减负）

> **重要：本文已降级为补充篇，主文档是 `docs/superpowers/specs/2026-10-10-zhongshu-scout-draft-first-modification.md`（下称“主文档 B”）。**
> 本文与 B 在拓扑和文档模型上冲突，**冲突处一律以 B 为准**。实施者只做下表标“实施”的部分；标“不实施”的部分保留仅供背景参考，**不要按其中的任务编号（P0–P4、T0.x–T3.9）开工**。

> **执行顺序以 `docs/superpowers/specs/2026-10-10-zhongshu-complete-plan.md`（总纲）为准。** 本文 T7、T8 已在工作区落地，无需再做。

### 取舍表

| 本文章节 | 处置 | 说明 |
| --- | --- | --- |
| 1、2（背景、目标、度量）、3（不变量） | 参考 | 度量指标沿用；不变量必须遵守 |
| 4（角色分工 v2） | 不实施 | 以 B §2 为准（Analyst 仍在 `REQ_ANALYZE` 内，扇出在 effect runner 内部） |
| 5.1–5.4（v2 草案模型：`items`、`AcceptanceCase`、标签渲染、v2 骨架） | 不实施 | B 用现有 markdown 章节 + 枚举扩展，不改持久字段。结构化验收与自动标签推导留作 B 之后的可选项，需 B 全部落地并有度量证据后再评估 |
| 5.5（lint 规则表） | 部分实施 | 以 B T3 的规则表为基础，**追加**三条 advisory 规则：`INTERNAL_ID_IN_TEXT`（正文含 `req-/decision-/fact-` 等内部 ID，已在真实产物中出现）、`HEADING_IN_BODY`（章节 markdown 含 `#` 标题行，会与渲染器加的标题重复，已在真实产物中出现）、`PLACEHOLDER`（`TODO/待补充/TBD/xxx`）。三者按 B 的 D7 一律 advisory。其余 5.5 规则不做 |
| 5.6（Critic 轴重点） | 实施 | 并入 B §6.3 的 Critic skill 文本（B 未覆盖的补一句即可） |
| 6.1–6.3、6.5、6.6（来源片段编号、引用回填、引用对齐、重试提示缩短） | **实施（任务 T7）** | 见下“T7 接入点” |
| 6.4（线索 `clue_id`、`FactRecord` 由线索构造） | 不实施 | B 的探针直接产出 facts，由 `verify_facts` 核验；但 6.5 的 `align_quote` 要用在 `verify_facts` 上（见 T7） |
| 7（预算：格式修复不占 `calls`、`total_calls` 上限） | **实施（任务 T8）** | 与 B 不冲突；本文 7 中关于 `REQ_SCOUT`/`ScoutBoard` 的两行不实施（B 的探针本就不额外扣预算） |
| 8（`REQ_SCOUT`、`flow`、补丁、`NEEDS_SCOUT`、棘轮） | 不实施 | 以 B T2、T5 为准。**不新增状态、不加 `flow`/`ScoutBoard` 等持久字段** |
| 9（持久化与迁移） | 不实施 | B 无持久字段变化，也就不需要迁移；唯一例外是 T8 的 `policy_version`（见 T8） |
| 10（下游适配清单） | 参考 | 其中 `dispatch.py:12 SKILLS` 与 `compat_effects.py:297 _SKILLS` 两处映射、`requirements_test_support.py` 的 `zip` 静默截断两条提醒仍然有效 |
| 11（任务 P0–P4） | 不实施 | 用 B 的 T0–T6，加本文 T7、T8 |
| 12、13（测试命令、运维） | 实施 | 测试只用 `rtk proxy python -X utf8 cmd/run_tests.py`；技能同步由用户执行 |
| 14、15、16（默认值、规格修订、风险） | 以 B 为准 | B §4、§7、§11 取代 |

### 合并后的实施顺序

B 的 T0 → T1 → T2 → T3 → T4 → **本文 T7 → 本文 T8** → B 的 T5 → T6。T7 放在 T5 之前，因为 T5 的探针结果接收要调用 T7 的引用规范化。B 的 T0 另外追加本文 11 节 T0.1（评分表 `rubric.py`）与 T0.2 的 `failure_origin` 分类（把产品失败、机器人驱动失败、基础设施失败分开统计），因为此前观察到的低成功率混有后两类。

### T7 接入点（来源片段编号 + 引用对齐）

按本文第 6 节的算法和文件落点做，并按 B 的架构放置调用：

1. `domain/requirements/spans.py`、`domain/requirements/quote_align.py`：照本文 6.1、6.5。纯函数、不持久化片段。
2. 呈现：`dispatch.py` 的 `build_dispatch` 里，用户来源按 `[{span.id}] {text}` 逐行呈现（6.2）。
3. 规范化函数 `normalize_claim_quotes(payload, sources)`，放 `runtime/requirements_quotes.py`：处理 `span_ids → quote` 回填与 `quote` 对齐（6.3、6.5）。**调用点有三处**：(a) `RequirementsAgentRunner.run_once` 里合约校验之前；(b) B 的 `ScoutFanoutRunner._accept_probe` 在 B §5.1.5 第 2 步合约校验**之前**（探针回复不经过外层 runner 的预校验，漏掉这一处则 intent 探针仍会因空白差异失败）；(c) B 提升出来的 `verify_facts`：事实摘录与文件内容比对失败时，用 `align_quote(文件文本, quote)` 对齐并以文件原文切片替换 `quote`，对齐失败才丢弃。
4. 合约：`REQ_ANALYZE` 的 claim 增加 payload-only 字段 `span_ids`（decode 前弹出，不进数据模型，与 `quote` 同处理）。Analyst skill（B §6.1）追加一句：引用用户原话时填 `span_ids`，不要复制原文。`result_errors`/`consensus.apply_analysis` 里的保护规则在回填后的 `quote` 上照常执行，不得放宽。
5. 重试提示缩短（6.6）。
6. 测试：`test_requirements_spans.py`、`test_requirements_quote_align.py`（用例清单见本文 T2.1）；`test_requirements_agent.py`（核对既有同类文件）增：`span_ids` 路径、对齐路径、伪造引用仍失败、`EXPLICIT_CLAIM_REQUIRES_HUMAN_QUOTE` 仍触发、`verify_facts` 对齐成功与失败各一例。
7. 完成：夹具回放里“引用空白差异”场景由失败变成功；按 T0 指标记录机械失败率变化。

### T8（格式修复不占语义预算）

按本文第 7 节：`kind == 'format'` 的派发只增 `total_calls`、不增 `calls`，每派发最多 2 次格式修复不变，`TOTAL_CALLS_CAP = 40`。**为保证在途任务的回放结果不变**：新语义仅当 `BudgetLedger.policy_version == 'requirements-policy-v2'` 时生效；`BudgetLedger.__post_init__` 同时接受 v1 与 v2；新建任务写 v2，已落盘任务保持 v1 语义。这是 B 之外唯一的持久字段取值变化，测试须覆盖 v1 账本按旧语义计数、v2 按新语义计数、两者都能加载。

---

状态：补充篇的 T7、T8 可开工；其余见上表。以下正文是原 v2 设计稿，**凡与上表冲突处无效**。本文不留待澄清项。

关联文档：

- 现行规格：`docs/superpowers/specs/2026-10-07-zhongshu-requirements-workflow-requirements.md`（以下称“原规格”）
- 现行计划：`docs/superpowers/plans/2026-10-07-zhongshu-requirements-workflow-implementation-plan.md`
- 对标对象：`~/.claude/skills/requirements-doc/`（`SKILL.md`、`assets/requirements-template.md`、`assets/example.md`）
- 代码根：`cmd/orchestrator/`（domain：`domain/requirements/`；runtime：`runtime/requirements_*.py`、`runtime/agent_effects.py`、`runtime/node_effects.py`、`runtime/nodes.py`、`app.py`）

## 0. 阅读方式

- 第 1–9 节是设计（做什么、为什么、规则是什么）。第 10–11 节是实施任务（按顺序做，每个任务可单独测试、单独合入）。第 12–13 节是验证与运维。
- 实施任务分四期：P0 度量、P1 内容与渲染、P2 机械门槛减负、P3 并发侦察与先草案。每期结束时全量测试必须绿，且工作流可用。P4（章节确认棘轮）为可选，P3 验证通过后再启动。
- 凡写“实施者先核对”的地方，是我没有逐行读过的代码；动手前先读，若与本文假设不符，以代码为准并在 PR 说明里写明差异，不要改变本文的设计意图。
- 本文不修改 `openwiki/` 下任何生成页面（项目 CLAUDE.md 禁止）。
- 测试只能用 `rtk proxy python -X utf8 cmd/run_tests.py`（或设置 `NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS=1`）。环境里有真实 `FEISHU_*` 与 `HUMAN_GATE_CHAT_ID`，测试不得真发飞书消息。验证服务用独立端口（如 18766），不得停止或改绑运行中的 8766 服务。工作流机制用脚本化 adapter 验证（零 token）；真实模型只用于脚本回答不了的问题。

## 1. 背景与依据

目标：以 `requirements-doc` 技能的内容方法论为标杆，让中书省稳定、高效地产出可审批的需求方案，并在完整性（摘要绑定、决定保护、恢复、预算）上保持超越单 agent 技能的优势。

### 1.1 现状评估

| 维度 | 现状 | 判断 |
| --- | --- | --- |
| 完整性机制 | 摘要绑定审批、用户决定保护、`ReviewIssue` 账本、七轴覆盖复用、预算、日志与快照恢复 | 强，保留 |
| 内容方法论 | 章节为 background/goals/scope/constraints/behavior/rules/acceptance/non_goals；骨架把原始请求整段放进背景；`validate_draft` 只做结构检查 | 弱：没有“约束 vs 边界 vs 非目标”区分、没有可执行验收结构、没有来源标注规范 |
| 机械门槛摩擦 | 真实运行里约 80% 的失败事件是机械门槛（回复校验、事实证据）；模型要逐字复制原文引用，差一个空白就整轮重来 | 主要效率损失来源 |
| 阶段耗时 | `REQ_ANALYZE` 是最长阶段，约占一次 66 分钟任务的 40% | Analyst 串行做“抽取主张”，不是在加速起草 |
| 产物质量 | 产出文档泄漏 `req-/decision-/fact-` 内部 ID，标题重复 | 渲染层未与账本分离 |

### 1.2 必须声明的限制

- 上面的耗时占比来自有限的真实运行，节省时间是**预期**而非已测量；P0 先建立基线，P3 之后复测，才能下结论。
- 此前观察到的任务成功率偏低（约 18%）受机器人驱动失败和基础设施失败干扰，不能直接当作产品失败率；P0 要把两者分开统计。
- 并发侦察的真实并行度取决于 agent 池或子 issue 能力（见 8.4）；无法并行时退化为串行，仍正确，只是不更快。

## 2. 目标、非目标、度量

### 2.1 目标

1. 产出文档的章节职能与 `requirements-doc` 对齐，并能被确定性规则检查（第 5、6 节）。
2. 去掉模型“抄原文”的机械负担：模型引用片段编号，运行时回填原文（第 6 节）。
3. 把“分析”从串行抽取改为并发侦察 + 先草案（第 4、8 节），缩短到首版草案的时间。
4. 格式修复不再消耗语义预算（第 7 节）。
5. 度量闭环：同一份评分表、同样的三个夹具，改前改后可比（第 11 节 P0）。

### 2.2 非目标

- 不增加第四个 LLM 角色；不引入投票或意见交集；不改变“Solver 是唯一作者”。
- 不改变摘要绑定审批、移交 `MENXIA_REQUIREMENTS_INTAKE` 的语义。
- 不改 multica 的 agent 模型、运行时配置、技能绑定（原验证文档约束：不得修改 Codex 全局配置、worker 配置或模型路由）。
- 不做图形化文档预览、不改飞书卡片视觉风格（卡片仅按第 10 节做必要适配）。
- 不处理 Menxia 之后的流程。

### 2.3 度量（P0 产出基线，之后每期复测）

| 指标 | 定义 | 目标（相对 P0 基线） |
| --- | --- | --- |
| 机械失败率 | 回复校验类事件数 / 总派发数 | 下降 ≥ 60% |
| 到首版草案耗时 | `REQ_INTAKE` 到首次 `DRAFT_READY` 的墙钟时间 | 下降 ≥ 30%（P3 后） |
| 到审批轮数 | 提交确认前的 Critic 轮数 | 不高于基线 |
| 语义调用数 | `budget.calls` | 不高于基线 |
| 评分表得分 | 第 11 节 P0 的 rubric | 不低于基线，P1 后显著提升 |

目标数值是验收门槛的初值；若 P0 基线显示它们不合理，在 P0 的 PR 中调整并说明依据，不得在没有基线时凭感觉改。

## 3. 必须保持的不变量

1. Solver 是文档唯一作者；Critic 不能写正文、不能批准；Analyst/侦察不写主张、决定、问题。
2. 用户决定只能来自用户来源（`SourceRecord.kind in ('request','answer','feedback')`）；线索（clue）永远不进入共识（consensus）。
3. 审批绑定文档摘要；改文档即改摘要，旧审批失效。
4. 所有 I/O 通过 `EffectRequest` 意图提交，状态处理器保持纯函数；文件读取和线索核验放在 runtime 层（joiner、`RequirementsAgentRunner`），domain 不做文件 I/O。
5. 日志与快照可恢复；崩溃后重放得到同一状态。新增状态字段必须参与序列化。
6. 预算有硬上限；停滞检测保留（2 次停滞 → `NO_EFFECTIVE_PROGRESS`）。

## 4. 角色分工 v2

| 角色 | v2 职责 | 明确不做 |
| --- | --- | --- |
| Analyst（侦察员） | 并发、只读、限时地找线索：当前代码、文档/历史、接口与兼容约束、现有测试。输出“线索”：主题、`path:行号`、逐字摘录、相关度、一句说明。事实复核（`REQ_EVIDENCE`）保留 | 不抽取主张、不下结论、不提问、不写决定 |
| Solver | 先草案：读用户原文片段与线索，一次回复里同时给出 `analysis`（主张/事实/假设）和文档；未决处标“待确认”；负责提出澄清问题（重字段由运行时模板补齐）；后续轮次提交章节补丁 | 不抄原文引用（只引用片段编号）；不跳过用户决定 |
| Critic | 意图保真、场景缺口、“Menxia 是否还得替用户拍板”、技能“常见误写”清单 | 不做结构、映射、重复、阈值检查（这些归运行时 lint） |
| Engine（运行时） | 确定性登记员：片段编号、引用回填、线索核验与去重、路由、lint、预算、渲染 | 不做语义判断 |

Analyst 复用现有 `review-analyst` agent 与 `requirements-analyst` 技能，不新增 agent。

## 5. 文档模型（对齐 requirements-doc）

### 5.1 章节

v2 草案（`DraftRevision.schema == 'v2'`）使用以下章节，顺序固定，由渲染器按序编号（1..n）：

| kind | 标题（渲染器拥有） | 空内容写法 | 备注 |
| --- | --- | --- | --- |
| `background` | 背景 | 无 | |
| `goals` | 目标 | 无 | |
| `problems` | 当前问题 | 无 | 可并入 background 或 goals |
| `constraints` | 约束 | 暂无 | 永不删除 |
| `features` | 核心功能清单 | 无 | |
| `boundary` | 责任边界 | 无 | 可并入 features |
| `acceptance` | 验收标准 | 暂无 | 永不删除 |
| `non_goals` | 非目标 | 暂无 | 永不删除 |
| `risks` | 风险 | 整章省略（不写“无”） | 可选，仅有真实风险时存在 |

合并规则：只有 `problems` 和 `boundary` 允许合并。合并时该章的 `merged_into` 填宿主 kind（`problems`→`background`|`goals`；`boundary`→`features`），`merge_reason` 必填；渲染器省略该章标题，并在宿主章末尾追加一行 `（并入说明：当前问题已并入「背景」——{reason}）`。其他章节不得合并或缺失（`SECTION_REQUIRED`）。

v1 草案保持原样：`schema == 'v1'` 的草案继续使用 v1 渲染器、v1 章节集合和 v1 校验。**不得修改 v1 渲染器输出的任何字节**，因为 `RequirementsState.__post_init__` 会重新渲染并核对 `document_digest`（`DOCUMENT_HASH_MISMATCH`），改动会令所有已落盘的已批准任务失效。

### 5.2 章节内容结构

`DraftSection` 新增字段（均有默认值，见第 9 节）：

- `items: tuple[DraftItem, ...]`：结构化条目。v2 章节的正文由 `items` 渲染；`markdown` 字段在 v2 中置空。
- `merged_into: str = ''`、`merge_reason: str = ''`。

`DraftItem`：`item_id`（章节内唯一，运行时分配）、`text`（单行；允许续行缩进）、`basis`（引用的 claim/decision/fact/assumption ID 元组；仅供账本，**不渲染**）、`imposed_by`（仅 `constraints` 使用，枚举见下）。

- `imposed_by ∈ {external_fact, user_decision, regulation, platform}`：回答技能的判断问题“这是现实强加给我们的，还是我们自己选的？”。
- `risks` 条目额外有 `impact`、`mitigation`（均必填）。
- `acceptance` 不使用 `DraftItem`，使用 `AcceptanceCase`：`case_id`、`test_type ∈ {unit, integration, fault_injection, regression, performance, other}`、`scenario`、`pass_condition`、`status ∈ {planned, threshold_pending}`、`basis`。`DraftSection.cases: tuple[AcceptanceCase, ...]`。

### 5.3 渲染规则（新增 `render_markdown_v2`）

- 标题：`# {title}` 后接 `## {n}. {章节标题}`；标题完全由渲染器产生，模型不得提供标题或 `#` 开头文本。
- 条目：`- {text}〔{标签}〕`。标签由运行时按 `basis` 推导，不由模型给：
  - 含 `DecisionRecord` → `〔用户已确认〕`
  - 含 `FactRecord`（observed）→ `〔事实：{path}:{行号范围}〕`（`path` 用相对项目根的路径，不含绝对路径和指纹）
  - 仅含非决定的 claim 或 assumption → `〔建议，待确认〕`
  - 无 `basis` → 仅允许出现在 `background`、`problems`，渲染 `〔推断〕`；其他章节无 `basis` 触发 lint `ITEM_WITHOUT_BASIS`
  - 同时含多类时按优先级：决定 > 事实 > 建议。
- 验收：按 `test_type` 分组，组名加粗（`**单元测试**` / `**集成测试**` / `**故障注入测试**` / `**回归测试**` / `**性能测试**` / `**其他**`），组内每个用例为单行 `- {scenario}：{pass_condition}`；`status == threshold_pending` 时行尾追加 `（阈值待定）`。保持“一行一条”，因为 Menxia 按行比对验收文本（`domain/menxia_doc.py` 约 761 行、`runtime/agent_effects.py` 约 654 行，实施者先核对）。
- 空章节按 5.1 表的空内容写法。
- Markdown 文本中任何形如内部 ID 的串（见 lint `INTERNAL_ID_IN_TEXT`）都是错误，渲染前已被 lint 拦截。

### 5.4 骨架

`new_skeleton`（v2）不再把整段原始请求放进 `background`：产出九章空骨架（`risks` 不含）。原始请求的引用只存在于 `SourceRecord`，在 Solver 的上下文里以编号片段呈现（第 6 节）。

### 5.5 Lint 规则（确定性，放在 `domain/requirements/lint.py`，由 `REQ_VALIDATE` 调用）

`validate_draft` 对 v2 草案先跑原有的结构检查（`SECTION_REQUIRED`、`REFERENCE_UNKNOWN`、`FACT_UNAVAILABLE`、`REQUIREMENT_UNMAPPED`、`ACCEPTANCE_UNMAPPED`、`DECISION_UNMAPPED`，语义保持），再跑下表。产出的 `ReviewIssue` 沿用 `mechanical-` 前缀，owner 为 `solver`，使 `REQ_VALIDATE → REPAIR_CONTENT → REQ_DRAFT` 路径无需改动。

| 码 | 级别 | 规则 |
| --- | --- | --- |
| `THRESHOLD_UNCONFIRMED` | block | 条目或用例文本含“数值+单位/指标词”（词表：`ms 毫秒 秒 s 分钟 % 次/秒 QPS TPS MB GB 并发 吞吐 延迟 P50 P95 P99`），且该数值不出现在任何 `DecisionRecord` / `FactRecord` / 用户来源片段文本中，则必须是 `threshold_pending`（用例）或改写为“待定”。纯计数断言（如“发送次数为 2”）不在词表内，不触发 |
| `CHAPTER_DUPLICATE` | block ≥0.85 / warn 0.70–0.85 | 不同章节任意两条目（去标签、去标点后）的字符二元组 Jaccard 相似度；初值，P1 用夹具调参 |
| `CONSTRAINT_IMPOSED_BY_MISSING` | block | `constraints` 条目缺 `imposed_by` |
| `CONSTRAINT_NOT_ESTABLISHED` | block | `constraints` 条目的 `basis` 全是 assumption/未接受 claim（“把未决方案写成约束”） |
| `CONSTRAINT_LOOKS_LIKE_SCOPE_CUT` | warn | `constraints` 条目含“本次不/暂不/不做/不支持”（范围取舍应在 boundary/non_goals） |
| `CHECKBOX_FEATURE` | block | `features` 条目以 `[ ]`/`[x]` 开头 |
| `VAGUE_ACCEPTANCE` | block | `pass_condition` 命中词表（`正常工作 运行正常 功能正常 符合预期 无问题 正确处理`）或长度 < 6 个字符 |
| `ACCEPTANCE_STRUCT` | block | 用例缺 `scenario` 或 `pass_condition` |
| `VAGUE_GOAL` | warn | `goals` 条目含“提升体验/更好/优化体验/更友好”且不含可判断的结果词 |
| `PLACEHOLDER` | block | 文本含 `TODO` `待补充` `xxx` `XXX` `TBD`（“待确认”“待定”是合法标记，不触发） |
| `INTERNAL_ID_IN_TEXT` | block | 文本匹配 `\b(req|decision|fact|claim|assumption|issue|S)[-_][A-Za-z0-9]{2,}\b` |
| `HEADING_IN_ITEM` | block | 条目文本以 `#` 开头 |
| `ITEM_WITHOUT_BASIS` | block | 5.3 所述 |
| `RISK_INCOMPLETE` | block | `risks` 条目缺 `impact` 或 `mitigation`，或命中通用套话词表（`可能延期 需求可能变化 存在不确定性`） |
| `MERGE_REASON_MISSING` | block | 合并章节缺 `merge_reason`，或合并了不允许合并的章节 |

warn 级别只记入日志与指标，不产生 `ReviewIssue`；block 级别产生 `ReviewIssue`。所有词表放在 `lint.py` 顶部常量，便于后续调参。

同一 lint 码在同一章节命中多次时，合并为一个 `ReviewIssue`（`claim` 里列出全部条目序号），不要每条一个。现有 `DECISION_UNMAPPED` 对每个缺失决定生成同名 issue，P1 的 T1.3 顺带改为按章节合并去重（v1 草案同样适用，保持 issue 稳定身份规则不变，并补测试）。

### 5.6 Critic 轴

七个轴不变（`intent scenarios constraints consistency acceptance decisions facts`，见 `domain/requirements/review.py` 的 `AXES`）；覆盖复用（`basis_digest`）机制不变。只调整每个轴的审查重点（改 `docs/multi/runtime/requirements-critic-skill.md` 与 critic 合约里的共享提示规则）：

- `intent`：文档是否忠实于用户原话；是否擅自扩大/缩小范围；Menxia 是否还得替用户拍板。
- `scenarios`：触发条件、边界场景、失败路径是否缺失。
- `constraints`：技能判断问题——每条约束是不是“现实强加”的；有没有把自己的取舍写成约束，或把外部限制写成边界。
- `consistency`：目标/功能/边界/验收之间是否矛盾；`boundary` 与 `non_goals` 是否分工清楚（边界=已纳入功能的停止点；非目标=整个需求不做的相邻功能）。
- `acceptance`：用例是否真能执行、通过条件是否可判断（lint 只拦明显模糊，语义判断归 Critic）。
- `decisions`、`facts`：保持现状。

删除 Critic 提示中已被 lint 覆盖的项（标题重复、ID 泄漏、占位符、复选框），避免浪费推理。

## 6. 来源原子化与引用协议

### 6.1 片段（span）

新增 `domain/requirements/spans.py`，纯函数 `atomize(source: SourceRecord) -> tuple[Span, ...]`，版本标记 `SPAN_VERSION = 'span-v1'`。

算法（必须逐条照做，保证确定性）：

1. 取 `source.text`，把 `\r\n`、`\r` 统一为 `\n`，得到规范文本 T。所有偏移量针对 T。
2. 按 `\n` 切行；空白行跳过。
3. 每行内按终止符切分：中文终止符 `。！？；` 无条件在其后切；英文终止符 `. ! ? ;` 仅当其后是空白或行尾时在其后切（避免切断 `3.5`、`a.b`）。终止符归属前一个片段。
4. 每个片段去掉首尾空白后若为空则丢弃；记录 `start`、`end`（T 内偏移，针对去空白后的文本）。
5. 按出现顺序从 1 起编号：`span.id = f'{source_id}#{n}'`。列表标记（`- `、`1. `）保留在片段文本里。

不持久化片段，需要时按 `source.text` 重算；因算法确定，结果一致。若将来升级算法，`SPAN_VERSION` 加 1，旧任务保持 `span-v1`（在 `RequirementsState` 记录该版本，见第 9 节）。

### 6.2 提示呈现

`build_dispatch` 的 `dispatch_context` 里，用户来源按 `[{span.id}] {text}` 逐行呈现，原文只出现一次（不再另附整段）。Solver/Analyst 提示规则改为：引用用户原话时填 `span_ids`，不要复制原文。

### 6.3 主张引用

模型主张（claim）新增 payload-only 字段 `span_ids: [str]`（与 `quote` 一样，decode 前弹出，不进入数据模型）。运行时（`RequirementsAgentRunner.run_once`，在合约校验前）规范化：

- 若有 `span_ids`：全部必须属于同一来源，且编号连续（`SPAN_NOT_CONTIGUOUS` 否则）；`quote := T[first.start:last.end]`（原文切片）。未知片段 → `SPAN_UNKNOWN`。
- 若同时有 `quote`：以 `span_ids` 为准，忽略 `quote`。
- 若只有 `quote`（旧路径，保持接受）：先精确包含检查；失败则走 6.5 对齐；仍失败按现有错误码处理。
- `EXPLICIT_CLAIM_REQUIRES_HUMAN_QUOTE` 等规则（`result_validation.py`、`consensus.py`）在回填后的 `quote` 上照常执行，保护逻辑不变。

### 6.4 线索与事实

线索（clue）由侦察员产出、运行时核验，Solver 通过 `clue_ids` 引用：

- 侦察员回复：`clues: [{topic, source: 'path[:l1-l2]', quote, relevance: 'high'|'medium'|'low', note}]`，`gaps: [str]`。
- 运行时（joiner）对每条线索：用 `source_path(source, project_root)` 读文件，验证 `quote` 连续包含（CRLF 等价；失败走 6.5 对齐，对齐成功则以文件原文切片替换 `quote`），计算 `sha256` 指纹；失败的线索丢弃并计数 `clues_rejected`。
- `clue_id = 'clue-' + sha256(f'{规范化相对路径}|{起止行}|{quote}')[:10]`，跨探针去重（保留相关度更高者）。
- Solver 在 `analysis.facts[]` 里写 `{local_key, statement, clue_id}`；运行时据线索构造 `FactRecord(source, quote, source_fingerprint, status='observed')`。Solver 不再复制路径和摘录。`clue_id` 未知 → `CLUE_UNKNOWN`。
- 线索不进入共识；只有被 Solver 引用并成为 `FactRecord` 的才进入账本。`REQ_EVIDENCE`（事实复核）流程与 `fact_updates` 保持原样。

### 6.5 引用对齐（`domain/requirements/quote_align.py`，纯函数）

`align_quote(source_text, quote) -> tuple[int, int] | None`：

1. 构造源文本的“规范视图”：逐字符做 NFKC；丢弃空白（含 `​‌‍﻿`）；保留每个输出字符到源偏移的映射（一个源字符展开成多个输出字符时都映射回该源字符）。
2. 对 `quote` 做同样处理。规范化后长度 < 6（`MIN_ALIGN_CHARS`）→ 不对齐，返回 `None`。
3. 在源视图里找 `quote` 视图的首次出现；映射回源偏移 `[start, end)`，返回。
4. 调用方用 `source_text[start:end]` **替换**模型给的 `quote`（真实原文切片），并记录日志事件 `QUOTE_ALIGNED`（含 `exact|normalized`）。

安全性：替换后的 `quote` 一定是真实原文，伪造的引用仍然匹配不上，决定保护不被削弱。

### 6.6 格式修复的重试提示

`requirements_agent.py` 末尾那段长修复提示保留，但把与已被运行时回填的内容相关的叮嘱（“逐字引用”“省略指纹”）删去，缩短重试负担。

## 7. 预算

`domain/requirements/budget.py`：

- 现状：`charge_dispatch` 对每次派发都 `calls += 1; total_calls += 1`，含 `kind='format'`。
- 修改：`kind == 'format'` 只增 `total_calls`，不增 `calls`；仍受“每次派发最多 2 次格式修复”约束（`format_repairs`）。语义修复（上限 3）、证据修复（上限 2）、外部重试（上限 2）、发布重试（上限 4）、全局 `calls` 上限 12、停滞检测不变。
- `total_calls` 增加一个**总上限** `TOTAL_CALLS_CAP = 40`（安全网，防止格式修复无限累积；超出触发现有的预算耗尽失败路径，错误码沿用现有 `BUDGET_EXHAUSTED` 类，实施者先核对名字）。
- 侦察派发（`REQ_SCOUT`）**不占用** `calls`；它们由 `ScoutBoard.rounds` 与并发上限约束（8.3）。侦察探针的格式修复：每探针最多 1 次，不占任何预算计数，失败则记入 `gaps`。
- `new_work_epoch` 的重置行为不变；`ScoutBoard.rounds` 不重置。
- `BudgetLedger.policy_version` 升为 `requirements-policy-v2`；加载时两个版本都接受（v1 状态按 v1 语义计数，见第 9 节迁移）。

## 8. 状态机与运行时拓扑

### 8.1 新流程

```
REQ_INTAKE --START--> REQ_SCOUT --SCOUT_DONE--> REQ_DRAFT
REQ_DRAFT --DRAFT_READY (无问题)--> REQ_VALIDATE --VALID--> REQ_REVIEW ...（其余不变）
REQ_DRAFT --DRAFT_READY (带问题)--> REQ_CLARIFY_PUBLISH --> REQ_CLARIFY_WAIT --ANSWERS_TO_DRAFT--> REQ_DRAFT
REQ_DRAFT --NEEDS_CLARIFICATION (无法起草)--> REQ_CLARIFY_PUBLISH
REQ_DRAFT --NEEDS_SCOUT--> REQ_SCOUT
REQ_DRAFT --NEEDS_EVIDENCE--> REQ_EVIDENCE（不变）
```

- 本节描述 `flow == 'v2'` 的拓扑。`REQ_ANALYZE`、`ANALYSIS_ACCEPTED`、`ANALYSIS_NEEDS_USER`、`ANSWERS_TO_ANALYZE`、`NEEDS_ANALYSIS` 在 v2 流程里不再使用，但**代码保留**给 `flow == 'v1'` 的在途任务（第 9 节第 4 点），到 T3.9 才删除。`REQ_VALIDATE`/`REQ_REVIEW` 的回边按 `flow` 选目标（v2 指向 `REQ_DRAFT`）。`ClarificationRound.return_to` 枚举保持 `('REQ_ANALYZE','REQ_DRAFT')`，v2 流程只写 `'REQ_DRAFT'`；T3.9 才收缩。
- `REQ_REVIEW` 的 `NEEDS_USER`/`NEEDS_DRAFT`/`NEEDS_EVIDENCE` 不变。
- 新增：`REQ_SCOUT`（业务状态）、事件 `SCOUT_DONE`、`NEEDS_SCOUT`。在 `domain/requirements/transitions.py` 的 `EDGES`、`REQ_BUSINESS`、`WAIT` 等元组中登记；`EFFECT_STATES` 增加 `REQ_SCOUT`。
- 无项目根（`dispatch_context.project_root` 为空）时，`REQ_INTAKE` 的 `START` 直接进入 `REQ_DRAFT`，`ScoutBoard.skipped = True`。`NEXUS_REQUIREMENTS_CONTENT_SAMPLE` 内容样本模式下的行为：实施者先核对 `dispatch.py`，保持与现有 `REQ_ANALYZE` 在该模式下相同的分支（跳过侦察）。

### 8.2 REQ_DRAFT 的新回复

动作：`DRAFT_READY`、`NEEDS_CLARIFICATION`、`NEEDS_EVIDENCE`、`NEEDS_SCOUT`。

- 字段：`analysis{claims, facts, assumptions}`、`document{title, sections}`（首个草案，完整）或 `patches`（之后，按章节）、`questions`、`issue_responses`、`evidence_requests`、`scout_requests`、`change_summary`、`reason`。
- **`analysis` 必填**当且仅当 `state.consensus.pending_source_ids` 非空（有未被登记的新用户来源）。运行时在 `REQ_DRAFT` 处理器里先对 `analysis` 调用 `apply_analysis`（与现 `REQ_ANALYZE` 的 `ANALYSIS_READY` 处理等价，含 `merge_questions`），再 `commit_draft`。`apply_analysis` 的全部保护（`USER_DECISION_PROTECTED` 等）原样生效。
- `patches`：`[{section_id, items | cases | merged_into/merge_reason}]`，按 `section_id` 整章替换；运行时合并成完整 `DraftRevision` 后 `commit_draft`，摘要重算。引用不存在的 `section_id` → `SECTION_UNKNOWN`。首个草案必须是完整 `document`；之后仍允许完整 `document`（兼容）。
- `DRAFT_READY` 且 `questions` 非空：草案提交（作为一个版本），随后 `open_round(state, 'REQ_DRAFT', ...)` 并进入 `REQ_CLARIFY_PUBLISH`，不进 `REQ_VALIDATE`。问题只需 `{topic_key, prompt, options?, depends_on?, why}`，其余字段（revision、provenance、重字段）由运行时模板补齐（实施者先核对 `questions.py`，补齐规则：沿用现有默认值生成函数；没有的字段取空/默认）。
- `NEEDS_CLARIFICATION`（无文档）保留，处理逻辑不变，用于完全无法起草的情形。
- `NEEDS_SCOUT`：`scout_requests: [{facet, question, anchors}]`，非空必填，否则 `DIRECTED_REQUEST_REQUIRED`；每轮最多 3 个探针；累计侦察轮次上限 `SCOUT_ROUND_CAP = 3`（含首轮），超过 → `SCOUT_CAP_EXCEEDED` 作为语义错误返回让 Solver 改用 `NEEDS_CLARIFICATION` 或直接起草。

### 8.3 REQ_SCOUT（并发侦察）

状态数据（新增 `RequirementsState.scout: ScoutBoard`）：

- `ScoutBoard`：`rounds: int`、`skipped: bool`、`clues: tuple[Clue,...]`、`gaps: tuple[ScoutGap,...]`、`probes: tuple[ProbeRecord,...]`。`Clue`：`clue_id, topic, source, quote, relevance, note, fingerprint, facet`。`ScoutGap`：`facet, reason('probe_failed'|'timeout'|'no_result'|'reported'), detail`。`ProbeRecord`：`probe_id, facet, status('done'|'failed'|'timeout'), clue_count, rejected_count, elapsed_seconds`。

探针规划（`domain/requirements/scout_plan.py`，纯函数）：

- 首轮固定四个探针：`code`（相关代码与调用链）、`docs`（文档、历史需求、ADR）、`interfaces`（外部接口、兼容与平台约束）、`tests`（现有测试与验收覆盖）。
- 每个探针附“锚点”：运行时从用户来源文本确定性抽取——文件路径（含 `/` 或 `\\` 且带扩展名）、反引号包裹的标识符、`CamelCase`/`snake_case` 标识符、中英文引号内的术语；去重，最多 20 个，按出现顺序。
- 定向轮：探针由 `scout_requests` 决定（`facet` 取四者之一或 `custom`）。
- 若固定分面被证明不够（P3 验证任务里发现某类需求某分面系统性空手），再评估“规划器”方案；本期不做。

派发（复用 `node_dispatch`）：

- 在 `dispatch.py` 为 `REQ_SCOUT` 生成 `EffectRequest('node_dispatch', payload{..., 'dispatch_mode': 'requirements_scout', 'bindings': [每探针一个 binding], 'role': 'review-analyst', 'target_state': 'REQ_SCOUT', 'timeout_seconds': <探针超时>})`。`bindings` 的形状沿用 Menxia 的 `WorkerBinding`（实施者先核对 `runtime/nodes.py` 的 `NodeContext`/`WorkerBinding`；若字段是 Menxia 专用的，增加可选字段承载 `probe_id/facet/anchors/question`，不要改动既有字段含义）。
- 并发由 `ConcurrentNodeExecutor`（有界 `ThreadPoolExecutor`）负责；上限 `REQUIREMENTS_SCOUT_MAX_WORKERS`，默认 4，且不超过 `NODE_MAX_WORKERS`（默认 6）。
- 汇合：`AgentNodeJoiner` 新增分支 `_join_scout`（与既有 `menxia_item_pipeline`/`menxia_group_pipeline` 并列，按 `dispatch_mode == 'requirements_scout'` 选择）。`_join_scout` 做：线索核验（6.4）、去重、构造 `SCOUT_DONE` 事件载荷 `{clues, gaps, probes}`。**屏障加期限**：所有探针完成或到期（`REQUIREMENTS_SCOUT_DEADLINE_SECONDS`，初值 600）即汇合；失败/超时探针记 `gaps`（原因“未查明”）；全部探针失败也照常进入 `REQ_DRAFT`（Solver 在草案里标“未查明”）。迟到的结果丢弃，仅写日志（不做“补充证据”机制）。
- 探针超时：默认 300 秒（**初值**，P3 验证任务测得真实分布后调整）；通过派发载荷已有的 per-dispatch 超时覆盖字段传入（`runtime/agent_effects.py` 约 140–156 行），不新增按状态的全局表项；`ROLE_DISPATCH_TIMEOUTS['review-analyst']` 仍为 1800 秒，作用于 `REQ_EVIDENCE`。

子 issue 与 agent 池（8.4）：

- `app.py:_build_node_executor` 的扇出条件现为 `state.startswith('MENXIA_') and total>1 and pool_size==1 and child_supported`；扩展为也接受 `state == 'REQ_SCOUT'`。
- `app.py:_agent_pool_by_state()` 为 `REQ_SCOUT` 增加条目，使用 `AGENT_ANALYST_ID`（支持逗号分隔的多 agent 池）。
- 技能映射有**两份**，必须同时加：`domain/requirements/dispatch.py:12` 的 `SKILLS` 与 `runtime/compat_effects.py:297` 的 `_SKILLS`，都增加 `'REQ_SCOUT' → 'requirements-analyst'`（与 `REQ_EVIDENCE` 同技能）；`_skill_lock` 读 `docs/multi/runtime/{skill}-skill.md` 并哈希，哈希进入 `requirements_agent.py` 的缓存键，改技能文本会使 v1 分析缓存失效（正常，不是 bug）。
- `AgentWorkerRunner.parallel_width` 按不同 (issue_id, agent_id) 目标数限并发（外部服务会串行同一目标的运行）。如果既没有多 agent 池也不支持子 issue，探针退化为串行：结果正确，只是不更快。**P3 验证任务必须记录峰值并发**，并在验证报告里如实写明是否真并行。

守卫与过期判定：

- `runtime/requirements_workflow.py:RequirementsRuntime.guard` 已按 `context.parallel.active_node_run_id` 处理 `node_dispatch`，对 `REQ_SCOUT` 沿用。
- `runtime/requirements_event_coordinator.py:flush_one` 的过期判定需增加 `REQ_SCOUT` 分支：以 `target_state`、`active_dispatch.request_id` 与 `node_run_id` 判断；现有的 `node_dispatch` 检查含 Menxia 的 `plan_hash` 假设，对 `requirements_scout` 模式跳过 `plan_hash` 比较（实施者先核对）。
- `runtime/requirements_workflow.py:bind_continuation_effect` 对 `worker_bindings` 的处理沿用。
- `runtime/requirements_notifications.py` 状态标签表增加 `'REQ_SCOUT': '正在并行查找相关资料'`；保留 `'REQ_ANALYZE'` 项（v1 流程仍在用），T3.9 再删。

### 8.4 分析缓存

`analyze-cache/`（`RequirementsAgentRunner._analyze_cache_*`）只缓存 `REQ_ANALYZE`。v1 流程仍在用，P3 不动；T3.9 随 `REQ_ANALYZE` 一起**删除**这部分代码与 `test_requirements_analyze_cache.py`，不要“改造”成侦察缓存（侦察结果依赖文件内容，指纹校验已在线索层完成；缓存留待有度量证据再做）。

### 8.5 章节确认棘轮（P4，可选）

- 用户在 `REQ_CONFIRM_WAIT` 给反馈时可指明 `section_ids`（卡片按钮/文本解析；解析规则 P4 实施者先核对 `requirements_cards.py` 与 `consensus.add_feedback`）。
- 用户明确批准过的章节（审批摘要绑定的草案）记录 `locked_digest = sha256(章节渲染文本)`；其后 Solver 的补丁若触及已锁定章节且该章不在用户反馈 `section_ids` 内 → `SECTION_LOCKED`（语义错误，要求撤回补丁）。
- 前置条件：P3 验证通过。

## 9. 持久化、迁移与切换

事实：`RequirementsState.from_dict` 用 `require_fields`，缺少已持久化的数据类字段会抛错；`BudgetLedger` 强制 `policy_version == 'requirements-policy-v1'`。

决定：

1. **白名单放宽**：对本文新增的字段，`from_dict` 缺失时取默认值（`require_fields` 增加“可选字段 + 默认值”参数，只对下表字段开启；其他字段仍严格）。
2. 新增字段与默认值：
   - `DraftSection.items=()`、`cases=()`、`merged_into=''`、`merge_reason=''`
   - `DraftRevision.schema='v1'`（新草案写 `'v2'`）
   - `RequirementsState.scout=ScoutBoard()`、`span_version='span-v1'`、`doc_schema='v1'`（P1 起新任务写 `'v2'`）、`flow='v1'`（P3 起新任务写 `'v2'`；`doc_schema` 管文档格式，`flow` 管拓扑，两者独立：P1 之后、P3 之前的任务是 `doc_schema='v2'` 且 `flow='v1'`）
   - `BudgetLedger.policy_version` 接受 `requirements-policy-v1|v2`
3. `RECORD_ENUMS['DraftSection']['kind']` 增加 `problems`、`features`、`boundary`、`risks`；v1 的 kind（`scope`、`behavior`、`rules`、`topic` 等）继续可解码。v1 草案走 v1 渲染/校验，v2 草案走 v2。**同一草案不得混用**；`commit_draft` 对 v2 草案拒绝 v1 kind。
4. **进行中任务（P3 切换）：不改写快照，用 `flow` 标记分流。**
   - 新增 `RequirementsState.flow: str = 'v1'`（取值 `v1|v2`）。P3 起新建任务写 `'v2'`；落盘的旧任务缺字段时按默认 `'v1'` 加载。
   - `flow == 'v1'` 的任务**完整保留旧拓扑**：`REQ_ANALYZE`、`ANALYSIS_READY` 等事件与处理器、`return_to == 'REQ_ANALYZE'`、分析缓存、v1 合约，一律不删、不改语义。`flow == 'v2'` 的任务走第 8.1 节的新拓扑。`REQ_INTAKE` 的 `START` 处理器按 `flow` 选择下一状态（`v1` → `REQ_ANALYZE`；`v2` → `REQ_SCOUT` 或 `REQ_DRAFT`）。`REQ_VALIDATE`/`REQ_REVIEW` 里指向 `REQ_ANALYZE` 的回边，对 `flow == 'v2'` 改指 `REQ_DRAFT`，对 `v1` 保持不变（同一处理器内按 `flow` 选目标，`EDGES` 同时登记两套边）。
   - 理由：在途任务的快照里 `progression.state` 可能是 `REQ_ANALYZE`，未完成的 effect 载荷里可能带 `target_state: REQ_ANALYZE`，崩溃恢复会按这些记录继续；改写快照或删除处理器会让这些任务无法恢复。`domain/context.py:context_from_dto`（约 1180 行）是 `RequirementsState.from_dict` 的反序列化入口，所有新字段的默认值都在 `model.py` 里解决，不在此处理。
   - `runtime/migration.py` 是旧版 DTO→快照迁移（`legacy_dto_to_snapshot`），**与本文无关，不要改**。`runtime/requirements_cutover.py` 迁移的是“历史非 requirements 任务 → requirements-v1”（`--migrate-task`），同样不改。
   - 旧路径的清理见 T3.9：仅在运维确认没有 `flow == 'v1'` 的在途任务之后才做。
5. 运维建议（见第 13 节）：P3 部署后让在途 v1 任务自然跑完；全部结束后再触发 T3.9 清理。

## 10. 下游消费者适配清单

| 位置 | 改动 | 期 |
| --- | --- | --- |
| `domain/requirements/handoff.py` `make_approved_input` | `acceptance_markdown`：v2 取 `acceptance` 章渲染文本（保持一行一条）；v1 逻辑不变。当前实现是 `next(s.markdown for s in draft.sections if s.kind == 'acceptance')`，v2 的 `markdown` 为空，必须改 | P1 |
| `domain/requirements/document.py` | `TITLES` 保留供 v1；新增 v2 标题表；`render_markdown` 按 `draft.schema` 分派；`validate_draft` 分派 | P1 |
| `requirements_cards.py` | 卡片 `topic_key → 标签` 映射（constraints/scope/platform/compatibility/acceptance/goal）增加 `boundary`、`features`、`problems`；`review_card` 展示章节时用渲染标题 | P1 |
| `requirements_metrics.py` | 第 37 行硬编码的 8 个旧 kind 改为按草案 `schema` 取；`measure()` 对 `REQ_` 开头状态计数时包含 `REQ_SCOUT`（不计入 `calls`）；`QUALITY_AXES`（intent、scenarios、consistency、acceptance、coherence）保留，增加 rubric 指标（P0） | P0/P1/P3 |
| `cmd/requirements_test_support.py` | 硬编码 8 章、`S_<kind>`、`TITLES`、`make_state`、`complete_draft`、`review_payload`：增加 v2 版本（`complete_draft_v2`），保留 v1。注意文件里有 `zip(CORE_SECTIONS, TITLES, bodies)`，章节数不一致时 `zip` 会**静默截断**；v2 版本必须显式断言长度相等 | P1 |
| `cmd/test_requirements_workflow_e2e.py` `Scripted` | 保留 `REQ_ANALYZE` 脚本分支（v1 流程）；**新增** `REQ_SCOUT`（返回少量线索）与 `REQ_DRAFT`（带 `analysis`）分支供 v2 流程使用，并新增一个 v2 的 e2e 用例（断言 `budget.calls` 为 v2 路径的实际调用数，不沿用 v1 的 3） | P3 |
| `cmd/requirements_bot_round.py`、`evaluate_requirements_workflow.py`、`sample_requirements_workflow.py`、`requirements_port.py`、`requirements_gateway.py` | 我没有逐行读过；实施者先 grep `REQ_ANALYZE`、章节 kind、`TITLES`、`acceptance` 的假设并适配。机器人按用户回答流程驱动，对状态名敏感的地方尤其要查 | P1/P3 |
| `docs/multi/runtime/requirements-{analyst,solver,critic}-skill.md`、`requirements-agent-instructions.md`、`requirements-runtime-protocol.md`、`requirements-workflow-v1.md`、`requirements-workflow-validation-20261007.md` | 按本文改写（角色、协议、拓扑）；新增 `requirements-workflow-v2.md` 说明新流程，v1 文档加“已被 v2 取代的部分”标注，不删除历史 | P1/P3 |
| `contracts/requirements_common.py`、`requirements_analyst.py`、`requirements_solver.py` | 见 11 节各任务 | P1–P3 |
| 规格与计划 | 原规格 4、5.1、6.1–6.3、7、8、9、10、11、12 节按第 15 节对照表修订；计划文档追加 v2 章节（引用本文任务编号） | P1/P3 |

## 11. 实施任务分解

约定：每个任务一个 PR；先写测试（脚本化 adapter，零 token）；任务完成标准里的“全量绿”指 `rtk proxy python -X utf8 cmd/run_tests.py`。下列“测试”是新增或必须更新的测试文件。

### P0 度量（不改行为）

**T0.1 评分表（rubric）。** 新增 `domain/requirements/rubric.py`（纯函数 `score(draft, state) -> RubricResult`），逐条实现技能“完成前检查”中可确定性判断的部分：八章齐全（v1 草案按 kind 映射为近似项）、约束/验收/非目标存在、验收用例结构、无内部 ID、无标题重复、无占位符、无未确认阈值、条目来源可区分。语义项（意图保真等）不在此处，保留给人工/Critic。
- 测试：`cmd/test_requirements_rubric.py`，含一个满分夹具文档和每条规则的反例。
- 完成：对 `cmd/fixtures/requirements/` 三个夹具跑出 v1 基线分数。

**T0.2 指标扩展。** `requirements_metrics.py` 增加：机械失败事件数/派发数（按 `REQUIREMENTS_RESULT_VALIDATION_FAILED` 事件的 `reason` 分类）、到首版草案耗时、rubric 分数；失败归类字段 `failure_origin ∈ {product, bot_driver, infrastructure}`（依据事件来源与错误码；规则写在代码顶部常量并加测试）。
- 完成：`requirements_metrics` 对既有真实运行日志（`runs/` 下，若存在）与夹具运行输出基线报告，存入 `docs/multi/runtime/requirements-workflow-baseline-<日期>.md`（日期用运行当天）。无真实日志时用脚本化 adapter 产出夹具基线并如实标注“脚本化，非真实模型”。

### P1 内容与渲染（拓扑不变）

**T1.1 模型扩展。** `model.py`：`DraftItem`、`AcceptanceCase`、`DraftSection` 新字段、`DraftRevision.schema`、`RECORD_ENUMS` 新 kind、`from_dict` 白名单默认值（第 9 节）。
- 测试：`test_requirements_model.py` 增：v1 快照原样可加载并重算摘要一致；v2 序列化往返；缺新字段的旧 JSON 可加载。
- 完成：所有既有 v1 测试不改动即通过。

**T1.2 渲染与骨架。** `document.py`：`render_markdown_v2`、v2 骨架、标题表、合并规则。
- 测试：`test_requirements_document.py` 增 v2 渲染用例，包括标签推导、合并说明、空章节写法、验收分组、risks 缺省省略、一行一条；加“v1 渲染输出字节不变”的黄金测试（对既有夹具）。

**T1.3 Lint。** `domain/requirements/lint.py` 与 `validate_draft` 分派。
- 测试：`cmd/test_requirements_lint.py`，每个码至少一正一反；`THRESHOLD_UNCONFIRMED` 必须包含“发送次数为 2 不触发”“P99 < 200ms 无依据触发”两个用例。
- 完成：用三个夹具的 v2 示例文档，block 级别无误报；重复阈值 0.85/0.70 在夹具上调参后写入常量并在 PR 说明依据。

**T1.4 合约与提示。** `contracts/requirements_common.py`、`requirements_solver.py`：v2 章节 schema（`items`、`cases`、`imposed_by`、`merged_into`），`REQ_DRAFT` 的 v2 `document`；共享提示规则增加章节职能与“常见误写”摘要；`docs/multi/runtime/requirements-solver-skill.md` 同步。合约按 `state.doc_schema` 选择 v1/v2（两个合约并存）。
- 测试：`test_requirements_contracts.py` 增 v2 schema 校验；脚本化回复由 `contract.example_payload()` 生成的路径要通过。

**T1.5 Critic 重点。** 按 5.6 改写 `requirements-critic-skill.md` 与 critic 合约提示；删除被 lint 覆盖的审查项。
- 测试：合约示例仍通过；`test_requirements_review.py` 不变。

**T1.6 下游适配。** 第 10 节中标 P1 的各项（handoff、cards、metrics、test_support）。
- 测试：`test_requirements_workflow_e2e` 增加一个 v2 草案走到 handoff 的用例，断言 `approved_acceptance_markdown` 每行都是验收用例行，并通过 Menxia 行比对的既有测试路径。

**T1.7 入口开关。** 新任务默认 `doc_schema='v2'`；环境变量 `REQUIREMENTS_DOC_SCHEMA=v1` 可回退（仅供应急，P3 之后删除）。
- 完成：全量绿；用夹具脚本化跑出 v2 rubric 分数，与 T0 基线对比并记录。

### P2 机械门槛减负（拓扑不变）

**T2.1 片段与对齐。** `spans.py`、`quote_align.py` 及单元测试 `test_requirements_spans.py`（中英混排、`3.5`、列表标记、CRLF、空行、连续性）、`test_requirements_quote_align.py`（空白/全半角/零宽差异、短引用拒绝、伪造引用不匹配、多重展开字符）。

**T2.2 引用回填。** `requirements_agent.py`：在合约校验前做 `span_ids` → `quote` 回填与 `quote` 对齐（6.3、6.5）；`dispatch.py` 以编号片段呈现用户来源（6.2）；合约增加 payload-only `span_ids`。
- 测试：`test_requirements_agent.py`（实施者先核对既有同类测试文件名；没有则新建）：`span_ids` 路径、`quote` 对齐路径、伪造引用仍失败、`EXPLICIT_CLAIM_REQUIRES_HUMAN_QUOTE` 仍触发。

**T2.3 格式修复不占语义预算。** `budget.py`（第 7 节）。
- 测试：`test_requirements_budget.py`（核对既有）：格式修复不增 `calls`、增 `total_calls`、每派发上限 2、总上限 40。

**T2.4 重试提示缩短。** 6.6。

完成：全量绿；夹具脚本化回放里“引用空白差异”场景由失败变成功；机械失败率按 T0.2 指标比对记录。

### P3 并发侦察与先草案

**T3.1 数据模型。** `ScoutBoard`、`Clue`、`ScoutGap`、`ProbeRecord`；`RequirementsState.scout`、`RequirementsState.flow`；`from_dict` 默认值；`flow` 只接受 `v1|v2`。
**T3.2 拓扑。** `transitions.py` 的 `EDGES` 等；`states.py` 的 `REQ_INTAKE` 处理器（进入 `REQ_SCOUT` 或在无项目根时跳到 `REQ_DRAFT`）、`REQ_SCOUT` 处理器（`SCOUT_DONE` 登记线索与缺口，进入 `REQ_DRAFT`）、`REQ_DRAFT` 处理器（8.2，`analysis` 仅在 `flow == 'v2'` 时必填，v1 流程的 `REQ_DRAFT` 行为不变）。不删除 `REQ_ANALYZE` 相关处理器与事件（见 T3.6、T3.9）。
- 测试：`test_requirements_workflow.py` 更新；增加“`DRAFT_READY` 带问题走澄清、答案回 `REQ_DRAFT`”“`NEEDS_SCOUT` 回侦察、超过 3 轮报错”。
**T3.3 探针规划。** `scout_plan.py`（锚点抽取、固定分面、定向轮）+ `test_requirements_scout_plan.py`。
**T3.4 派发与汇合。** `dispatch.py`（`REQ_SCOUT` 的 `node_dispatch`）、`app.py`（扇出条件、池）、`compat_effects.py`（`_SKILLS`）、`nodes.py`/`agent_effects.py`（`_join_scout`）、`requirements_event_coordinator.py` 与 `requirements_workflow.py:guard`、`requirements_notifications.py`。
- 测试：`test_requirements_scout.py`：脚本化 adapter 下 4 探针并发、1 探针超时记缺口、全部失败仍进 `REQ_DRAFT`、线索核验丢弃伪造摘录、`clue_id` 去重、迟到结果被丢弃、崩溃恢复后重放同一状态；`test_active_runtime_skill_binding.py` 通过（所有状态都在 `_SKILLS` 和 `_agent_pool_by_state` 中）。
**T3.5 先草案与补丁。** `REQ_DRAFT` 合约增加 `analysis`、`patches`、`scout_requests`；运行时在 `REQ_DRAFT` 处理器里对 `analysis` 调 `apply_analysis`；`clue_id → FactRecord` 构造；问题模板补齐。更新 `requirements-solver-skill.md`、`requirements-analyst-skill.md`（改写为侦察员技能：输出线索与缺口）、`requirements-agent-instructions.md`。
- 测试：`apply_analysis` 在 `REQ_DRAFT` 路径上的全部保护用例（复用 `test_requirements_workflow` 中现有的 `USER_DECISION_PROTECTED` 等测试，改为从 Solver 回复触发）；`analysis` 缺失且 `pending_source_ids` 非空 → 合约/语义错误。
**T3.6 flow 分流。** 第 9 节第 4 点：`RequirementsState.flow`、`REQ_INTAKE`/`REQ_VALIDATE`/`REQ_REVIEW` 按 `flow` 选目标、新任务写 `flow='v2'`。
- 测试：新增 `test_requirements_flow_split.py`：(a) 构造一个 `flow='v1'`、`progression.state='REQ_ANALYZE'`、带未完成 `REQ_ANALYZE` effect 的快照，加载并推进到 `REQ_DRAFT`，行为与 P3 之前逐事件一致；(b) 缺 `flow` 字段的旧 JSON 加载为 `'v1'`；(c) 新任务走 `REQ_SCOUT`；(d) 同一进程内 v1、v2 任务并存互不影响。
**T3.7 保持旧路径可用。** 不删任何 `REQ_ANALYZE` 代码。仅确认：`dispatch.py:SKILLS` 与 `compat_effects.py:_SKILLS` 两处都同时含 `REQ_ANALYZE` 与 `REQ_SCOUT`；`REQUIREMENTS_DOC_SCHEMA` 开关保留（T3.9 删除）。
**T3.8 端到端与指标。** 更新 `test_requirements_workflow_e2e` 及复用它的测试（batching/crash_recovery/evidence/gateway/metrics/observability/progress_notifications）；`requirements_metrics` 记录侦察峰值并发与到首版草案耗时。

完成：全量绿；夹具脚本化运行得到新旧对比报告；然后做**一次**真实验证任务（独立端口 18766，不动 8766），记录：峰值并发、探针超时分布、到首版草案耗时、机械失败率，写入 `docs/multi/runtime/requirements-workflow-validation-<日期>.md`。据此调整探针超时和 `REQUIREMENTS_SCOUT_MAX_WORKERS` 默认值。

### T3.9 清理旧路径（延后；仅在运维确认无在途 `flow == 'v1'` 任务后执行）

触发条件：用户（运维）书面确认 `runs/` 下没有未终结的 `flow='v1'` 任务（或已全部取消）。实施者不得自行判定。内容：删除 `REQ_ANALYZE` 状态、事件、处理器、合约（`requirements_analyst.py` 的 `REQ_ANALYZE` 部分）、分析缓存及 `test_requirements_analyze_cache.py`、`return_to` 枚举收缩为 `('REQ_DRAFT',)`、`REQUIREMENTS_DOC_SCHEMA` 开关；同时删除 `domain/requirements/dispatch.py:12` 与 `runtime/compat_effects.py:297` 两处 `SKILLS` 里的 `REQ_ANALYZE` 项。因 `runtime_fingerprint()`（`runtime/requirements_recovery.py`）按文件哈希计算，代码变更会改变 `runtime-code` 指纹，已记录 `runtime-code` 恢复基线的失败任务需要重新校验，PR 说明里写明。

### P4（可选）章节确认棘轮

见 8.5。任务拆为：`locked_digest` 数据与 `SECTION_LOCKED`、反馈 `section_ids` 解析（卡片+文本）、测试。仅在 P3 验证通过后开始。

## 12. 测试影响与验证命令

- 全量：`rtk proxy python -X utf8 cmd/run_tests.py`。
- 单文件：`rtk proxy python -X utf8 cmd/run_tests.py <模块名>`（实施者先核对 `run_tests.py` 的参数形式；用不了就设置 `NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS=1` 后用 `python -m unittest`）。
- 引用 `REQ_ANALYZE` 的文件（grep 实测，共 27 个）。由于 v1 流程保留，这些测试**默认不改**，并继续验证 v1 流程；P3 只需为 v2 流程**新增**平行用例。测试文件：`test_requirements_analyze_cache`、`_batching`、`_contracts`、`_crash_recovery`、`_evidence`、`_notifications`、`_observability`、`_progress_notifications`、`_questions`、`_recommendation_submission`、`_result_boundary`、`_retry_alignment`、`_run_output`、`_startup`、`_workflow`、`_workflow_e2e`、`test_single_chat_owner`。源码文件：`app.py`、`domain/requirements/{dispatch,model,questions,states,transitions}.py`、`contracts/requirements_analyst.py`、`runtime/{compat_effects,requirements_agent,requirements_notifications}.py`。T3.9 时才逐个改写或删除。P3 开始前先 `grep -rn REQ_ANALYZE cmd/` 复核该清单。
- 默认会被拓扑变化影响、需要补 v2 用例的：`test_requirements_workflow_e2e.py` 的 `Scripted`（新增 v2 脚本分支，不替换旧分支）、`test_requirements_workflow.py`、`test_active_runtime_skill_binding.py`（要求所有状态都在两处 `SKILLS` 映射与 `_agent_pool_by_state` 里）。
- P1/P2 不得修改任何既有 v1 测试的断言；如果需要改，说明你破坏了 v1 兼容，先修实现。
- 真实模型验证只在 P3 末尾做一次（见上），其余一律脚本化。

## 13. 运维步骤（需要用户执行，实施者不得代做）

1. 部署前：让在途需求任务跑完或取消（P3 有迁移兜底，但干净切换更稳）。
2. 同步 multica 的技能与指令文本：运行 `cmd/sync_multica_review_agents.py`。该脚本只改三个 agent 的指令、六个阶段技能正文和中书省技能资源，并校验模型、运行时配置与技能绑定未变。这是外部系统写操作，由用户在合适的时机执行；实施者只负责保证 `docs/multi/runtime/` 下对应文本已更新，并把“需要同步”写在 PR 说明里。P1（Solver/Critic 文本）和 P3（Analyst/Solver 文本）各需同步一次。
3. 若要让侦察真并行：在配置里提供多个分析 agent（`AGENT_ANALYST_ID` 逗号分隔）或确认子 issue 能力可用；这是部署配置，不是代码任务。没有也能运行，只是串行。
4. 验证服务用独立端口（如 18766）。

## 14. 已定默认值（不需要再问）

| 决策 | 默认值 |
| --- | --- |
| 章节集合 | 采用技能的 8 章 + 可选风险；只允许合并“当前问题”“责任边界” |
| 澄清问题的作者 | Solver（运行时模板补齐重字段）；Analyst 不提问 |
| 侦察分面 | 固定四个：code / docs / interfaces / tests；不做规划器 |
| 侦察并发上限 | `REQUIREMENTS_SCOUT_MAX_WORKERS=4`，不超过 `NODE_MAX_WORKERS` |
| 侦察探针超时 / 屏障期限 | 300 秒 / 600 秒（初值，P3 验证后调整） |
| 侦察轮次上限 | 3（首轮 + 2 次定向），每轮最多 3 个定向探针 |
| 迟到侦察结果 | 丢弃，仅日志 |
| 全部探针失败 | 照常进入起草，缺口标“未查明” |
| 格式修复 | 不占 `calls`；每派发最多 2 次；`total_calls` 总上限 40 |
| 引用对齐最短长度 | 规范化后 ≥ 6 字符 |
| v1 草案 | 永远按 v1 渲染/校验，不升级 |
| 分析缓存与 `REQ_ANALYZE` 旧路径 | P3 保留给 `flow='v1'` 在途任务；T3.9 在运维确认无在途 v1 任务后删除 |
| 在途任务处理 | 不改写快照，用 `RequirementsState.flow` 分流 |
| 章节确认棘轮 | P4 可选 |
| 去重阈值 | block ≥ 0.85，warn 0.70–0.85（夹具调参后固化） |

用户需要提供的内容：**无**。唯一需要用户做的事是第 13 节的 multica 技能同步（运维操作）。

## 15. 对原规格的修订对照

| 原规格章节 | 修订 |
| --- | --- |
| 4（阶段语义） | v2 流程不再使用 `REQ_ANALYZE`（代码保留给 v1 在途任务）；增加 `REQ_SCOUT`；起草阶段承担主张登记与澄清提出 |
| 5.1（文档骨架） | 改为本文 5.1 的九章模型与渲染规则；原骨架仅 v1 保留 |
| 6.1（Analyst 主导需求探索与批量澄清） | 改为“Analyst 并发收集线索，Solver 起草并提出澄清” |
| 6.2（Solver） | 先草案、`analysis` 随草案提交、章节补丁、引用用编号 |
| 6.3（Critic） | 重点调整为 5.6；结构类检查移交 lint |
| 7（状态机） | 本文 8.1 的新边 |
| 8（数据模型） | 本文第 5、6、9 节新增记录与默认值 |
| 9（预算） | 本文第 7 节 |
| 10（运行时） | 本文 8.3 扇出、守卫、过期判定 |
| 11（测试与验收） | 本文第 11、12 节 |
| 12（部署与迁移） | 本文第 9、13 节 |

## 16. 风险与待测量项

- 并行度未必真实可得（agent 池/子 issue 依赖部署）；用峰值并发记录说话，不承诺加速。
- “先草案”会让 Solver 的单次回复变大；若出现超时或截断，回退方案是把 `analysis` 与文档拆成同一状态下的两次回复（不改拓扑），在 P3 验证里观察。
- 去重阈值与阈值词表有误报风险；warn 级别只记日志，block 级别要在夹具上证明无误报才能合入。
- v1/v2 两套流程并存期间代码量与测试面更大；这是为在途任务可恢复付出的代价，T3.9 负责收口。未知的 `flow` 取值必须明确报错而不是静默当作 `v1`。
- 本文对 `AgentNodeJoiner`、`ConcurrentNodeExecutor`、`WorkerBinding` 的复用假设：已核实 `join` 有按 `dispatch_mode` 分支的结构，且默认路径对任一失败探针会让整个节点 `FAILED`，所以 `_join_scout` 必须在该默认路径之前自己处理部分失败；`_menxia_infra_failure` 一类判定是 Menxia 专用，不可直接复用。其余（`WorkerBinding` 字段、per-dispatch 超时覆盖字段）仍需实施者在动手前核对。
- 评分表只覆盖可确定性判断的项；意图保真等仍依赖 Critic 与人工抽查，P3 验证任务结束后做一次人工对照评审并记录。
