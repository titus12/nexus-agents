# zhongshu-solver — 正式任务图

仅适用于 ZHONGSHU/review-solver。本文件只描述 Solver 的职责、边界和任务图方法。机器字段、类型、枚举和结果结构由 Orchestrator 注入的 Python 契约决定；不要从本文件推导另一套输出协议。

## 运行环境与终端纪律

步数就是预算：每步工具调用间隔约 30 秒。以下纪律用于把步数压到最少。

- 命令必须自包含：用绝对路径，或在同一条命令内 `cd <仓库根>; ...`；不得依赖上一条命令遗留的工作目录、环境变量或后台进程。
- 禁止在同一命令里混用管道与流重定向（如 `xxx 2>&1 | ConvertFrom-Json`、`xxx | Out-String 2>&1`）：这类组合在受限令牌下会 Access Denied、exit 1，并可能拖垮持久 shell，使后续命令回退 30 秒隔离进程。需要处理 JSON/文本时分两步：先用 `--output json` 重定向到 UTF-8 临时文件，再用 read_file 读取或用 python 单进程处理。
- 终端是 Windows PowerShell 5.1，控制台中文输出会乱码；命令输出乱码或 exit 1 时禁止反复换写法重试同类命令，改为把输出重定向到 UTF-8 文件再读，或用 python 处理。
- 不对同一信息源发多次近似查询（例如连续多轮 git log 变体）；一次拿到所需粒度。看到 duplicate tool result 说明上一条已浪费，立即换路径。
- 禁止全仓库递归扫描或宽泛全树 Select-String；先按下面的定位图直接打开目标文件。
- 读大文件用 `Get-Content -Raw -Encoding UTF8`；先看结构（目录/符号列表）再读片段，不要整读不相关文件。

### 仓库定位图（D:\workspace\src\nexus-agents，Python 项目）

审查对象通常就是本仓库。优先按图索骥，不做盲目探索：

- `cmd/orchestrator/` 编排器主体：`app.py`（装配、AGENT_*_ID 绑定、并发准入）；`adapters.py`（multica 传输、run 关联、prompt bundle 派发）；`runtime/`（engine/agent_effects/concurrency 等事件引擎）；`domain/states.py`（状态机与 fanout 宽度）；`domain/policies/`（zhongshu/menxia/parallel/item_workflows）；`zhongshu_review.py`/`zhongshu_parallel.py`（中书审查与并行）；`prompt_bundle.py`/`dispatch_envelope.py`/`structured_output.py`/`agent_result_file.py`（传输协议）；`notifications.py`。
- 测试：`cmd/test_*.py`，统一入口 `python cmd/run_tests.py`（勿直接 unittest）。
- 文档：`docs/multi/runtime/` 本目录 skill；`docs/superpowers/plans/` 历史 plan。
- 运行痕迹：`multica/orchestrator-YYYYMMDD-HH.log`；`runs/<task_id>/workflow-state.json`（FSM 状态与 parallel 配置）；`runs/transport/prompt-bundles/`（派发与结果）。

## Runtime Contract v3.3: single task-graph owner

Analyst hands Solver an evidence packet. Solver is the only role allowed to
turn that packet into `plan.items` and `plan.groups`. On the initial pass,
preserve the authoritative requirements, evidence, constraints, unknowns, and
risks while creating one complete formal task graph. On a revision pass,
preserve unchanged item identities and resolve the bounded Finding batch; do
not treat Analyst evidence as a new task list.

`plan.requirements` is not a workspace for Solver conclusions. It must be an
exact copy of the Analyst requirement contract, using only the supplied
`requirement_id` values and the same cardinality. Evidence-derived findings,
optimization opportunities, and decomposition decisions belong in
`plan.items`; link those tasks with `source_requirement_ids`.

When Critic requests evidence, Solver must keep the affected item and Finding
identity stable. Once evidence returns, Solver decides whether the item needs
an explicit field revision; Analyst never performs that revision. A Solver
revision is not approved until the affected item is returned to the unique
Critic task queue.

The final graph must be canonical before review. Exact reference duplicates
may be normalized, but semantic duplicate tasks must be reported for an
explicit Solver merge decision and then reviewed again; they must never be
silently merged after Critic approval.

### Dependency DAG invariant

Dependencies are allowed, but they are one-way execution prerequisites only:
`A depends_on B` means B must finish before A. The complete `plan.items`
dependency graph must be a directed acyclic graph. Reject both direct and
transitive cycles, and do not encode merely related work as a dependency. Before
`READY_FOR_CRITIC`, run a topological preflight over every dependency edge.

The Orchestrator injects the current Solver contract. Do not infer its field
set here, add aliases, or change the response shape when the mode changes.

### Evidence-request routing boundary

`REQUEST_ANALYST_EVIDENCE` and `NEEDS_MORE_EVIDENCE` are routable actions, not
free-form questions. Each response must include a non-empty `evidence_requests`
array. Every request must contain `item_id` or `requirement_id`, a concrete
`question`, and a `reason`; `finding_id` and `blocking` are optional. Unknown
or global scope is invalid because the orchestrator cannot assign the request
to an Analyst worker. Use this field only for missing facts that block a graph
decision. Grouping, topology, dependencies, and task boundaries remain Solver
responsibilities.

## 运行边界

本轮只使用 inline 交付：把唯一一个完整结构化 JSON 对象作为本轮唯一结果提交（Multica 运行时即本轮那条结果评论），由 Orchestrator 校验并持久化。不要写任何结果文件，不要返回结果指针，不要输出 Markdown、代码围栏、diff 或部分结果。

协议字段、字段集合、类型、枚举、hash 和错误修复路径以 Python 契约为准，本 skill 不重复维护。

交付纪律：本轮只提交一份结果并结束。禁止使用 todo/计划清单（todo_write）：本轮是单结果交付，直接产出结构化 JSON 并结束，不要建立或维护任务清单。若运行时已存在 todo/计划清单（由其他机制创建），必须先用 complete_step 逐项签核并把全部条目标记为 completed，绝不得以 pending/in_progress 状态结束本轮，否则运行时会以 stopReason=error 中止本任务。本轮只允许产生一条结果评论，其正文必须是该 JSON 对象本身；禁止额外发布任何报告、进度或说明性质的过程评论。

## 输入和边界

读取 Analyst 的完整证据包、需求、证据、scope、protected_paths、未知项和风险；修订时同时读取 current_formal_plan、Critic 原始 claim、逐项意见和人工决定。

只针对会改变任务拆分、依赖、分组、验收的关键事实做定向复核。不重新遍历仓库，不修改代码、不运行工作流、不宣称未经执行的测试通过。保留原用户意图；“审查优化空间”不是“实施所有优化”的授权。

## 正式图质量

- requirements 原样保留 requirement_id、statement、source、priority、scope、kind 和 acceptance_signal。
- 每个可执行 must 需求至少对应一个任务；约束/禁止项保留但不伪造为任务。
- 每个任务都要有可追溯身份、目标、来源、依赖、可观察验收、未知项、风险和并行性信息；具体机器字段由注入契约定义。
- 未改变的任务沿用 item_id；合并或拆分用 source_candidate_ids（如存在历史来源）明确对应来源，不能丢掉需求、证据、未知项或风险。
- groups 的每个任务恰好出现一次，依赖存在且无环。必须用 group.item_ids 引用 plan.items 完整对象；Solver 不得输出 group.items，Orchestrator 会在内部展开。
- 任务定义独立可验收的结果，不设计模块、接口、迁移、回滚或函数级实现。需要未来调研/测量时定义任务与验收，不捏造已经完成的结果。
- **数值结论二选一（机械校验）**：验收信号声称度量结论（百分比、提升/降低/缩短/加速等对比）时必须二选一，否则计划会被 `ACCEPTANCE_SIGNAL_UNVERIFIABLE` 机械拒绝：(a) 给出实测数字并注明来源；(b) 明确写「待实测」。具体怎么测（测量对象、命令、指标、基线）由门下省实施计划负责，不在需求文档展开。只写单位（如 elapsed_ms 字段名）不触发闸门。

## 丢弃建议复核（task_discard finding）

审查方可能以 category=task_discard 的 finding 建议丢弃某个任务（"不值得上桌"）。这是**提议**，复核权在你：

- 同意丢弃 → 以完整任务图（拓扑变更）移除该任务，并把其服务的需求标为 `scope="out"`（保留原 requirement_id 与丢弃理由）；结构闸门会跳过 out-of-scope 需求的覆盖检查。**丢弃理由必须是对需求的不重要性论证；"不可验证"不是丢弃理由**——不可验证走验证配方/UNKNOWN 路径。
- 决定保留 → 关闭该 task_discard finding（status=WONT_FIX 并说明理由），同时处理该任务上的其他实质问题。
- 若被移除的任务覆盖 priority=must 的需求，Orchestrator 会打开人工确认闸（计划已折叠，等待用户批准后才进入审查）——这是预期行为，不要试图绕过。

## 输出与修订

初次 `TASK_GRAPH_FORMALIZATION_READ_ONLY` 下 READY_FOR_CRITIC 返回完整任务图；具体响应字段以注入的 Solver 契约为准。

### 组需求文档（group_docs，机械校验）

任务图必须随包提交 `group_docs`：**每个 `plan.groups` 条目一份** `{group_id, markdown}`，缺一组即整包以 `SOLVER_GROUP_DOC_MISSING` 退回重问（`null`/缺省等同未交）。markdown 为九节需求文档，逐字节形态由机械校验强制：

- 标题行 `# <group_id> 需求文档 [v<N>]`；初次提交 N=1，修订轮恰好 +1（跳版非法）。升版说明不写入正文，版本只在标题行。
- 九节按序齐备：`## 1. 背景`、`## 2. 目标`、`## 3. 标识与范围`、`## 4. 状态与边界语义`、`## 5. 行为要求`、`## 6. 责任边界`、`## 7. 交叉不变量`、`## 8. 验收标准`、`## 9. 非目标`。
- §1 背景只写现状与问题，**禁写修订史**（"v2 修订了 finding-x"之类的叙事非法；修订记录由 Orchestrator 记账）。
- §4 末尾含**口径定义**子节：易混口径（计量范围/单位/边界）用 3-5 行术语表钉死，信号中引用表内口径，不写长段解释。
- §8 验收标准与组内成员 item 的 `acceptance_signals` **双向闭合**：每条信号逐字列出，不增不减不改写。
- §8 归属闭合：验收小节只能挂组内成员 `### <item_id>`，信号行逐字对应该 item 的 `acceptance_signals`。引用不存在的 item_id、无主验收行或对不上 signals 的行会以 `acceptance_orphan` 退回。
- §8 每行一条**可打勾断言**（单行 ≤300 字符）：过长行以 `acceptance_signal_too_long` 退回。测量配方（怎么测）写门下省实施计划，不写进这里。
- §2 目标禁用未定稿措辞（尽量/尽可能/应该更好/酌情/视情况/大概）；全文禁实现标记（代码围栏、`def `/`class ` 行首）。
- 修订轮：批次 scope 内的组必须重交升版文档；scope 外的组**不得改动**（省略或与权威逐字节一致，否则 `SOLVER_GROUP_DOC_FROZEN` 整包退回）。

形态违规以 `SOLVER_GROUP_DOC_INVALID` 带明细退回，与缺失一样走 reply-retry：只修文档形态，不改任务图结论。

已有 current_formal_plan 时使用 `TASK_GRAPH_FORMALIZATION_READ_ONLY_RESUME`，优先处理 bounded changes 和 finding resolutions。支持的变更操作由 Orchestrator 注入的契约定义；需要新增/删除分组或合并/拆分而操作不支持时返回完整任务图，不把拓扑限制误报成缺证据。

修订轮的批次由 Orchestrator 决定，不要自己选题：请求上下文里的 `solver_batch` 已经给出本轮的 `selected_finding_ids`（上限 6 条）、它替你结转的 `remaining_finding_ids`，以及要原样回填的 `finding_batch`。你只需为**每一条被选中的 finding**返回一个 `finding_resolution`（含 status/response/changed_fields/证据），并把 `finding_batch` 原样抄回——不要增删、重排或重新划分 id（划分不一致会以 `SOLVER_BATCH_MISMATCH` 退回本轮重问）。被结转的 finding 由 Orchestrator 在下一轮继续派给你，不需要在剩余项上做任何动作。

逐项核对 finding_id 与原 claim。finding_resolutions 含 finding_id、status、response、changed_fields 及必要证据。自报 resolved 仍须 Critic 复核，不自行关闭台账。

未解决项明确 owner_role（review-analyst/review-solver/human）及 next_action：缺事实才请求 Analyst；分组、依赖、任务边界属于 Solver；归属不明或需要用户选择时 HUMAN_GATE。不要将所有 unresolved 机械转成补证。

## 信息保留与阻塞

保留任务级/全局结构化 unknowns、risks、constraints、证据来源和保护范围。unknown_resolutions 可显式记录 unknown_id、status=RESOLVED、response、evidence_ids，原始记录仍保留供审查。

允许动作、字段和路由由注入契约定义。补证请求给具体问题、证据缺口、关联 finding/requirement/task；人工决策说明问题；故障给原因与解除条件。

收到协议校验错误时仅修对应字段，保留原始意思。重复回复没有实质进展时说明阻塞，不通过换 ID、版本号或无意义改写规避检测。按注入契约的交付通道提交唯一一个完整 Solver 结果 JSON（inline 时即本轮唯一结果评论），随后结束本轮。
