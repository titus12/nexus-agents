# 中书省效率优化实施方案（冻结机械化 / finding 身份重铸 / prompt 计量与切片）

> 需求来源：2026-09-28 对话——6 轮实测显示中书省典型耗时 60–140 min（理想路径 50–60 min），用户判定"体验偏慢"。三项优化按性价比排序为：②冻结检查机械化 → ①finding_id 命名空间 → ③prompt bundle 瘦身。
> 范围澄清：优化 1、2 是中书省段内改造；优化 3 是全流水线改造（中书省每跳受益，门下省跳数更多、绝对收益更大）。本文档按实施顺序编排（Task 1 = 优化 2，Task 2 = 优化 1，Task 3 = 优化 3）。
> 修订：2026-09-28 评审对照代码核验全部锚点后修订（Task 1 条件谓词、Task 2 触发形态与接线、Task 3 计量点），并接入 bc58c1 进行中实测证据，详见 §6 修订记录。
> **评审修订（2026-09-28，已并入正文）**：①机械路径组行终态条件改为 `FROZEN 或 ratchet 持有`（`group_approval_held` 对 FROZEN 行返回 False，zhongshu_group.py:373 只认 CONVERGED）；②账本闭合改用带 task_hash/dependency_hash 新鲜度的既有谓词语义（states.py:4853-4866），弃用 `completed_item_ids`（门下省侧记账，states.py:3044，冻结时刻基本为空）；③写明 `_group_freeze_decision`（states.py:2720）是机械路径的防御纵深；④非组（legacy）轮无条件禁用机械路径；⑤补脚本化测试回归面；⑥finding 重铸补"次轮旧 id 复活"rebind、critic-only 作用域、stuck_rounds 继承语义、自定义 claim 归一化（不复用 `finding_semantic_key`，zhongshu_review.py:370 刻意排除 claim）；⑦修正"静默丢失"的适用分支描述；⑧计量点改挂 bundle build（adapters.py:888）并承认传输层既有计量；⑨对照验收按 hop 聚合/多轮；⑩收益表按"Task1+2 保底 / Task3 增量"拆分。

**Goal：** Task 1+2 落地后：干净轮（无打回）~**40–50 min**、坏轮削峰（撞号/unstructured 类废波消失）；Task 3 对照达标后进一步压到 **35–45 min**。收益承诺按"保底 / 增量"拆分，见文末收益表。

**背景与实测数据（6 轮）：**

| 轮次 | 中书省耗时 | 废波/废跳主因 |
|---|---|---|
| c0cd83 | 144 min | 6 个 critic 波仅 1 个裁决波（unstructured×3、验收语义、finding_id 撞号） |
| 616863 | ~109 min | ITEM_PATCH_IDENTITY×4、文档重试 |
| eaea40 | 至闸 | unstructured 波 + 预算跨波（均已修） |
| bc58c1 | 进行中，修订时已 ~140 min 仍在中书省 | SOLVER 首跳 acceptance_orphan 打回（投影 bug，已修）→ critic 首波 unstructured×2 重问 → SOLVER 修订波 ITEM_PATCH_IDENTITY 整波重跑（时间线见 §6.1） |

时间构成：每跳固定成本 = agent 读 ~100k token bundle + 思考 + 写结构化回包 = 10–24 min；中书省最少 3 跳串行（Solver→Critic 裁决→冻结检查）。**打回循环是放大器**：每打回一次 ≈ +10–20 min + 重读全量 bundle。

**Architecture（定稿决策）：**

- **冻结检查机械化（Task 1）**：冻结条件全部是进程内可判定的客观事实（组行终态、带新鲜度的账本闭合、依赖组 FROZEN）。条件全满足时**跳过 agent 派发**，直接合成 `FREEZE_APPROVED` 事件走 `fast_track` 同款合成事件通路；任一条件不满足才派发 agent 冻结检查，并把失败项注入其 prompt（`[Freeze precheck]` 块）。**机械路径仅限组轮**（zhongshu_groups 非空）；合成事件仍进 `ZhongshuFreezeCheckState.handle` → `_group_freeze_decision` 重做图检查 + 文档形式校验 + ready 计算——这是机械路径的主要安全网（最坏部分释放，不会误冻）。既有语义保护：四轴 critic 已逐组裁决 + 方案预审闸（默认开）兜底人工复核。
- **finding_id 身份重铸（Task 2）**：critic 轮回包带来的 finding 按**claim 级内容哈希**先行指认：内容与账本既有条目相同 → rebind 到既有 id（吸收"次轮旧 id 复活"，不产生并行重复条目）；结构键撞号且主张确系不同 → 重铸确定性新 id，两条意见都保留；其余 id 原样不动。**仅对 critic 轮生效**（`_merge_and_close_findings` 被 solver/critic 轮共用，solver 轮的 disposition 按 finding_id 引用，无条件重铸会扑空）。与现有 `canonical_key` 内容身份、跨语言重述 rebind、缩写 id 容错**叠加而非替代**。
- **prompt 计量先行、切片在后（Task 3）**：计量点在 **bundle 构建处**（`adapters.py:888 prompt_bundle_builder.build`——`_dispatch_request` 处 `prompt_ref` 只是 manifest 路径字符串，量不到大头）+ node 侧 capsule 长度 + 按状态聚合报告；传输层已有 `AGENT_DISPATCH_TRANSPORT`（full_prompt_bytes/manifest）与 `AGENT_DISPATCH_PAYLOAD_FINGERPRINT`（chars/bytes）日志，缺的是 **bundle 内部分项**与**跨跳聚合**。切片默认关、带回归门槛与线上对照验收（按 hop 聚合、多轮对比），防止"裁过头 → 打回率上升 → 修复循环比延迟更贵"。

**Tech Stack：** Python（cmd/orchestrator），unittest，`python cmd/run_tests.py`（runner 强制 NullNotificationPort）。验证用脚本化 adapter 测试（`test_linear_fsm_entrypoint.py` / `test_zhongshu_convergence_e2e.py`），零模型 token。

**不改动：** FSM 拓扑与转移表（`PLAN_REVIEW` 闸边保留）；组流水线机制全部（裁决投影、组面棘轮、patch 冻结区、§8 闭包投影、partial salvage、预算与停滞记账）；worker 级 re-ask 与预算清偿；契约 JSON 形态；`fast_track` 测试捷径行为；门下省机制本体；传输层（multica/飞书）。

---

## 1. Task 1：冻结检查机械化（对应优化 2，每轮必省 10–15 min）

**现状**：`ZHONGSHU_CRITIC --APPROVE_CRITIC--> ZHONGSHU_FREEZE_CHECK` 后固定派发一跳 critic 角色 agent（`contracts/zhongshu_freeze_check.py`），由它判断"全部任务批准、无活动 P0/P1、证据与方案对齐"。c0cd83 实测该跳 ~10 min 且首跳通过；`max_freeze_check_attempts` 预算存在意味着它可失败重试（再 +10 min/次）。

**实现细节：**

1. 新增纯函数 `mechanical_freeze_ready(context) -> tuple[bool, list[str]]`（放 `domain/policies/zhongshu.py`），**仅当 `review.zhongshu_groups` 非空（组轮）时评估**；逐条判定并返回失败项列表：
   - **组行终态**：每行 `stage == "FROZEN"`，或 `stage == "CONVERGED"` 且 `group_approval_held(review, group_id)` 成立。**不得裸用 `group_approval_held`**：它对 FROZEN 行返回 False（zhongshu_group.py:373 只认 CONVERGED），多层依赖图下第一批冻结后部分行已是 FROZEN，裸用会使第 2+ 次冻结检查永远进不了机械路径；
   - **账本闭合（带 hash 新鲜度）**：对每个待审 job，账本存在 `status == "APPROVED"` 且 `task_hash`、`dependency_hash` 与当前 job 一致的记录——即 states.py:4853-4866 既有谓词的语义（"无需要重审的项"）。**不得用 `completed_item_ids` 等价替代**：那是门下省侧完成记账（states.py:3044 在门下省组批准时更新），冻结时刻基本为空；stale APPROVED（hash 不匹配）必须判为未闭合；
   - **依赖闭合**：每组的依赖组全部 `stage == "FROZEN"`——与 `freeze_ready_groups` 的语义对齐（zhongshu_group.py:159），严于"held/frozen"的宽泛表述。
2. 插入点：states.py 中 `target == "ZHONGSHU_FREEZE_CHECK"` 的 EffectRequest 构建分支（现有 `fast_track` 合成分支旁，~line 1087）。组轮且 `mechanical_freeze_ready` 全过 → 返回与 `fast_track` 同形的合成 EffectRequest（`effect_type="mechanical_freeze"`，payload `action="FREEZE_APPROVED"`，同 request_id 规则），并打日志 `ZHONGSHU_FREEZE_MECHANICAL_APPROVED task_id=... skipped_agent_hop=1`；否则（含全部非组轮）按现状构建 agent 派发，且在 prompt 末尾追加 `[Freeze precheck] 未满足条件: <失败项列表>`（agent 带着具体缺口进场，更可能一跳解决）。
3. **防御纵深（机械路径的主要安全网，必须有）**：合成事件照常进 `ZhongshuFreezeCheckState.handle` → `_group_freeze_decision`（states.py:2720）——它重新做依赖图检查、文档形式校验与 ready 计算，机械预判只是提前过滤；最坏情形是**部分释放**（部分组被扣回继续磨），不会误冻。v1 只写了 `_with_plan_review`，漏了这层；`_with_plan_review`（方案文档 + PLAN_REVIEW 闸）在 `_group_freeze_decision` 通过后才发生。
4. **非组（legacy）轮无条件禁用机械路径**：`zhongshu_groups` 为空时上述组条件空洞成立，且 handler 无 `_group_freeze_decision` 校验（返回 None）、无逐组 critic 前置裁决；闸关闭时"证据与方案对齐"将彻底无检查。故 legacy 轮一律保持 agent 派发（该跳的主观判断在非组场景没有其他兜底）。
5. 配置：`parallel.zhongshu.mechanical_freeze: bool = True`（env `ZHONGSHU_MECHANICAL_FREEZE`），默认开；`fast_track` 优先级不变（测试捷径仍最先命中）。
6. 语义取舍：机械化路径放弃 agent 的"证据与方案对齐"主观判断——组轮兜底链 = 四轴 critic 逐组裁决（前置）+ `_group_freeze_decision` 机械复核（中置）+ 方案预审闸（后置人工）。闸关闭时前两层仍然有效，这是仅组轮开放机械路径的原因。

**测试与回归面：**

- 新增 `cmd/test_mechanical_freeze.py`：
  - 组轮全条件满足 → 无 agent 派发、合成 `FREEZE_APPROVED`、`_group_freeze_decision` 照常执行、方案文档 effect + PLAN_REVIEW 闸产出；
  - 多层依赖图：第一批组已 FROZEN + 其余 CONVERGED 持棘轮 → 仍进机械路径（FROZEN 行不误判）；
  - 任一条件失败（活动 P1 / stale APPROVED（hash 不匹配）/ 依赖组未 FROZEN / FROZEN+CONVERGED 之外的 stage）→ 照常 agent 派发且 prompt 含 `[Freeze precheck]`；
  - 非组轮（zhongshu_groups 空）→ 无条件 agent 派发；
  - `mechanical_freeze=False` → 照旧派发；
  - `fast_track=True` → 行为与今日完全一致。
- **既有脚本化测试回归面（默认开会批量变调，必须显式处理）**：`test_linear_fsm_entrypoint.py` / `test_zhongshu_convergence_e2e.py` / `test_group_doc_loop` 均脚本回复 `ZHONGSHU_FREEZE_CHECK`——机械路径默认开后该派发消失，派发计数/目标状态断言与"冻结打回（REQUEST_SOLVER_REVISION）"路径的脚本不再被覆盖。处理：这三处夹具显式设 `mechanical_freeze=False`（保持原覆盖），另以"构造失败条件"的新用例覆盖打回路径（上面的失败分支用例）。

**验收：** 脚本化 e2e 全绿（含迁移后的三处夹具）；干净组轮冻结阶段跳数 = 0；非组轮派发行为不变。

## 2. Task 2：finding_id 身份重铸（对应优化 1，坏轮削峰 0–30 min）

**现状与事故形态（精确化）**：finding 折叠身份 = `canonical_key`（内容哈希，优先）否则结构键 `group|item|finding_id`（`policies/zhongshu.py::_finding_identity`）。**"静默丢失一条意见"只发生在双方皆无 canonical_key 的分支**（结构键同 → 字典覆盖）；双方 canonical 都存在且不同时 `merge_findings` 本就保留两条——该分支的问题是 **id 寻址歧义**（同一 id 指两条主张，solver 批次/处置引用无法区分），重铸解决的是这个。c0cd83 实测 14 条 finding 仅 8 个 id；多 finding 轮次 100% 复现。

**实现细节：**

1. **claim 级归一化（自定义，不复用 `finding_semantic_key`）**：`zhongshu_review.py:370` 的语义键刻意排除 claim 文本（同一 target 的两个不同主张共享语义键），直接复用会把不同主张误判为"同主张"。新增 `_claim_text_key(claim) -> str`：NFKC → lower → 去列表标记/标点 → 空白折叠（与 `zhongshu_doc._normalize` 口径对齐），精确规则由测试校准。
2. **统一身份规则**（新增 `remint_incoming_finding_ids(existing, incoming) -> tuple[tuple, list[dict]]`，`policies/zhongshu.py`；对每条 incoming finding 依序判定）：
   - **(a) 内容指认（吸收旧 id 复活，v1 缺口）**：`h = sha256(group|item|_claim_text_key(claim))`；账本已有条目的内容哈希等于 h（含其 canonical_key 形态与重铸 id 形态）→ **rebind** `incoming.finding_id → 该条目 id`。这一条同时覆盖"次轮 critic 以原 id X 复活同主张"（X 已不在账本、结构键不撞，v1 规则漏掉 → X 与重铸后的 Y 并存两条同主张）与"跨轮换 id 重提"；
   - **(b) 结构键撞号重铸**：结构键 `group|item|finding_id` 与账本某条目相同、但内容哈希不同（且 canonical 判定不同）→ 重铸 `finding-{h[:12]}`，循环确保与账本及本批次不撞；
   - **(c) 其余情形**（无撞号、同 id 同主张重提、canonical 命中的合并）→ id 原样保留。
3. **作用域：仅 critic 轮**。`_merge_and_close_findings`（states.py:852）被 solver/critic 轮共用，后续 `apply_dispositions` 按 finding_id 引用处置——共用入口无条件重铸会让 solver 轮的 disposition 引用扑空。实现：给 `_merge_and_close_findings` 加 `remint_findings: bool = False` 形参，仅 critic 轮调用方（有 `task_reviews` 的折叠路径）传 True；solver 轮零行为变化。
4. **接线与顺序**：在 `_merge_and_close_findings` 内、`rebind_restatement_findings` **之前**调用（先清洗 id，跨语言 rebind 才不会绑到撞号的孪生条目上）；重铸/指认结果打日志 `FINDING_ID_REMINTED task_id=... item_id=... from=... to=... rule=a|b`。
5. **计龄语义（必须显式保证）**：`age_unresolved_findings`（zhongshu.py:225-245）按 `_finding_identity` 在 prior 中找前值，重铸/指认后的新 id 若在 prior 无条目 → stuck_rounds 归零，慢性 finding 的停滞升级被洗白。规则 (a) 的 rebind 发生在 merge 之前，同主张合并到既有 id 后 prior 命中，计数自然继承/递增 ✓；规则 (b) 只对"确系新主张"触发，从 0 计龄是正确语义。测试必须断言：同一主张换 id/复活旧 id 重提时 stuck_rounds 继承既有条目计数，不归零。
6. **确定性**：rebind/重铸 id 由内容哈希派生，同一 payload 重放得到同一 id（断言进测试）。

**测试（新增 `cmd/test_finding_id_remint.py`）：**

- 双无 canonical、同 group|item|id、不同 claim → 两条俱全、第二条按 (b) 重铸、日志 from/to；
- **旧 id 复活**：账本有重铸 id Y（原 X 的主张），次轮 incoming 用 X 带同主张 → 按 (a) rebind 到 Y，无并行条目；
- 同一主张换 id 重提 → stuck_rounds 继承（不归零）；
- critic-only 作用域：solver 轮 payload（findings + finding_resolutions 引用既有 id）→ 无重铸、disposition 引用全部命中；
- claim 归一化不并主张：同 target 两条不同主张（共享 semantic key）→ 不被 (a) 误并；
- 同 canonical_key 异 id → 仍合并（现有行为不变，回归 `test_finding_identity_merge.py` 全绿）；
- 重放稳定：同一输入两次折叠 id 相同。

**验收：** 构造 6 finding/4 id 的批评波折叠后账本 6 条、引用可达；脚本化 e2e 无回归。

## 3. Task 3：prompt bundle 计量与角色切片（对应优化 3，全流水线每跳 -25%+）

**现状（精确化）**：每跳 agent 读 ~100k token bundle。bundle 在 **`adapters.py:888 prompt_bundle_builder.build`** 处落盘（`_dispatch_request` 的 `prompt_ref` 只是 manifest 路径字符串，在那里计量量到的是小文本——v1 计量点选错）。传输层**已有**计量：`AGENT_DISPATCH_TRANSPORT`（comment_payload_bytes / full_prompt_bytes / manifest_path / manifest_hash，adapters.py:960-976）与 `AGENT_DISPATCH_PAYLOAD_FINGERPRINT`（chars/bytes/sha）。**缺口是：bundle 内部分项（哪段占多少）与跨跳按状态聚合**，没有分项就无法定切片的刀口。

**实现细节（计量先行，切片置后）：**

1. **T3.1 分项计量（无条件上线）**：
   - `prompt_bundle_builder.build` 内按 section 打点：`PROMPT_BUNDLE_SECTIONS task_id=... request_id=... state=... sections={name: bytes}`（builder 不感知 state 时由调用方补传 target_state）；
   - node 侧补 capsule 长度：`nodes.py` 绑定构建处打 `WORKER_CAPSULE_BYTES task_id=... node_run_id=... worker_id=... bytes=N`；
   - 冻结/闸触发时汇总 `PROMPT_BUNDLE_REPORT task_id=... by_state={state: {hops, bytes_avg, top_sections}}`。
   - 与 bc58c1 任务里的 item-000004（get_compiled_content 出口字节日志）互补：一个量 orchestrator 出口分项、一个量 agent 侧编译产物。
2. **T3.2 角色切片（默认关，拿到分项数据后定案）**：配置 `parallel.prompt_slicing: bool = False`（env `ZHONGSHU_PROMPT_SLICING`）。候选切法（按分项结果裁剪，此处为预设）：
   - 中书省组审 worker：只附本组 doc + 本组 items/findings/signals（组胶囊已组内化，需核实 plan/evidence 是否全量随包）；
   - 门下省 item worker：只附本 item 的 doc 小节 + item findings + 依赖摘要；
   - analyst / solver formalize：全量保留（跨组视野是职责需要）。
3. **T3.3 回归门槛（切片的放行条件，写死在验收里）**：
   - 脚本化 e2e（`test_linear_fsm_entrypoint` / `test_zhongshu_convergence_e2e`）全绿；**`test_critic_evidence_context` 等对 prompt 内容的断言是切片的现成回归网**，必须一并通过；
   - 新增 binding 字节预算断言：切片开启时组审 binding bundle ≤ 阈值（以 T3.1 分项实测的 50% 定阈，进 `test_prompt_slicing.py`）；
   - 线上对照：**按 hop 聚合、跨 ≥2 轮对比**（单轮单跳噪声大，不作结论依据）：切片开启后 hop 级平均耗时下降 ≥25% 且打回率不升（对照 bc58c1 基线）；不达标关开关即可，无需回滚代码。

**验收：** 分项日志在任意一轮运行可见 per-section 字节与 `PROMPT_BUNDLE_REPORT`；切片开启后 e2e + 内容断言全绿 + 预算断言通过；多轮对照达标。

---

## 4. 发布顺序与回滚

| 顺序 | 内容 | 开关 | 回滚方式 |
|---|---|---|---|
| 1 | Task 1 冻结机械化 | `ZHONGSHU_MECHANICAL_FREEZE`（默认开，仅组轮） | 关开关即回 agent 派发 |
| 2 | Task 2 finding 重铸 | 无开关（critic-only，行为严格增广：只多不少） | 无需回滚 |
| 3 | T3.1 分项计量 | 常开（纯日志） | — |
| 4 | T3.2 切片 | `ZHONGSHU_PROMPT_SLICING`（默认关） | 关开关即回全量 bundle |

**重启时机约束**：三项落地后一次性重启，重启点必须在人工预审闸（`PLAN_REVIEW`）停等处——只有此时运行不持有 in-flight 派发，RESUME 零损失。正在运行的 bc58c1 若尚未到闸，**等它到闸或跑完再重启**，不中断在途轮次。

## 5. 预期收益汇总（保底 / 增量拆分）

| 层级 | 内容 | 单轮提效 | 生效条件 |
|---|---|---|---|
| **保底（Task 1+2）** | 冻结机械化 | 10–15 min，每轮必省 | 干净组轮（critic 收敛后无 P0/P1）；非组轮不适用 |
| | finding 重铸 | 0–30 min（坏轮削峰，多 finding 轮均值 ~15 min） | 仅撞号/复活场景触发，触发即免整波重审 |
| **增量（Task 3）** | 分项计量 | 0（决策依据） | 常开 |
| | 角色切片 | 每跳 -25%+，中书省 6–8 跳计 30–45 min；门下省更多 | 切片开 + 多轮对照达标 |

**结论：** Task 1+2 落地即可承诺干净轮 ~**40–50 min**、坏轮削峰；Task 3 对照达标后再到 **35–45 min**。

---

## 6. 修订记录（2026-09-28 评审）

### 6.1 bc58c1 实测时间线（数据来源：`runs/task-20260928-bc58c1/workflow-events.jsonl`，时间为事件流 UTC）

| 时刻 | 步骤 | 结果 | 问题与原因 |
|---|---|---|---|
| 06:11:31 | START → 需求契约跳（ANALYST#1） | 06:15:07 契约就绪（3.6 min） | 无 |
| 06:15→06:18 | ANALYST 证据波（3 worker） | READY_FOR_SOLVER | 无（3.6 min） |
| 06:18:42→06:32:43 | SOLVER#3 首跳 | **打回**：`SOLVER_GROUP_DOC_INVALID:acceptance_orphan:item-000001..3` | 机械组文档闸拒绝。被拒 plan 的 items 实际带有 `source_requirement_ids`（req-001..004）——与"投影 bug 假孤儿（已修）"的定性一致；重跑通过。**浪费 ~14 min + 整跳重跑 17.5 min**（SOLVER 阶段合计 32 min，净需求 1 跳） |
| 06:32:44→06:50:15 | SOLVER#5 重跑 | READY_FOR_CRITIC | 无 |
| 06:50:15→07:32:27 | CRITIC 波#6（2 组） | **首问两跳全废 → 重问**：两份回包均为 143 字节占位文本 `__UNSTRUCTURED_REPLY__`（"output was too large to post safely"，multica 评论通道拒绝投递超大结果，原始产物只在 issue Execution log） | 传输层丢大回包（c0cd83 "unstructured×3" 同类）。worker 实际完成了工作但回包通道失败；re-ask 后 attempt-2 有效。**波耗时 42 min（干净估计 ~23 min），re-ask 开销 ~19 min + 两次全量 bundle 重读**。折叠：双组 REVISE_GROUP，findings=6（2×P1、4×P2），**跨组复用同号**（finding-000001×2：item-000002/item-000004）——Task 2 形态 B 实证 |
| 07:32:27→08:08:59 | SOLVER 修订波（#7→#9） | **#7 node_join 失败 → 整波重跑**：`NODE_ITEM_REVISION_INVALID`，worker-02（group-000002）patch 缺 `group_id`（`ITEM_PATCH_IDENTITY:item-000004 group_id expected=group-000002 actual=none`） | 与 616863 "ITEM_PATCH_IDENTITY×4" 同类：worker 输出缺 identity 字段，join 闸拒绝后**整波重跑**（含回包有效的 group-000001）。**#7 浪费 ~13 min，#9 重跑 ~23 min** |
| 08:08:59→08:23:14 | CRITIC 波#10 | REQUEST_ANALYST_EVIDENCE（14 min，干净） | findings=2 **同为 finding-000002**（P1/item-000002 新主张："实证时 prompt.txt 的 Requirement document 为空"——prompt 装包缺料信号，支撑 Task 3 计量先行；P2/item-000004 与 #6 同 id 同主张重提，正常合并）；group-000001 要证据、group-000002 REVISE_GROUP |
| 08:23:14→08:27:52 | ANALYST 证据跳（#11） | READY_FOR_SOLVER（4.6 min，干净） | 无 |

截至日志末尾：**已 136 min 仍在中书省**（等待第 3 轮 Solver），`no_progress_count=1/3`。干净路径估计 ~91 min，三处硬失败合计引入 **~45 min（33%）纯开销**，且每次失败都以整波重跑 + 全量 bundle 重读收场——与 §1/§3 的优化动机一致。

### 6.2 评审修订对账（v1 → v2）

正文已并入的修订与代码依据：①机械条件组行终态 `FROZEN 或 ratchet 持有`（zhongshu_group.py:373）；②账本闭合带 hash 新鲜度、弃用 `completed_item_ids`（states.py:3049 更新点、states.py:4860-4866 新鲜度谓词）；③`_group_freeze_decision`（states.py:2720）为防御纵深；④legacy 轮禁用机械路径；⑤脚本化测试回归面（三处夹具显式 `mechanical_freeze=False`）；⑥重铸三形态（内容指认吸收旧 id 复活 / 结构键撞号重铸 / 其余不动）+ critic-only 作用域 + stuck_rounds 继承 + 自定义 claim 归一化（zhongshu_review.py:370 的语义键刻意排除 claim，不可复用）；⑦"静默丢失"分支精确化（仅双无 canonical）；⑧计量点移至 `prompt_bundle_builder.build`（adapters.py:888）+ node capsule，承认传输层既有日志（adapters.py:960-976）；⑨对照验收按 hop 聚合、跨 ≥2 轮；⑩收益表拆"保底/增量"。

### 6.3 本轮新暴露、超出三项任务范围的候选问题（仅记录，不承诺）

1. **传输层丢弃超大回包**（#6 波，~19 min）：critic 结构化结果超过 multica 评论投递上限即整体丢失，只能靠 re-ask 重做。候选方向：critic 类大回包改走 result-file 通道（solver 已用 `nexus-agent-result-file-v2`）或传输层回读 Execution log。属传输层改造（本文档"不改动"范围），需单独立项。
2. **join 闸失败整波重跑**（#7→#9，~23 min）：单 worker 的 `ITEM_PATCH_IDENTITY` 失败惩罚了回包有效的同波 worker。candidate：对 identity 类机械失败优先走"只重问失败 worker"的局部 re-ask（worker 级 re-ask 机制已存在），需评估 join 语义是否允许部分折叠。
3. **prompt 装包缺料**（#10 波 P1："Requirement document 为空"）：切片尚未实施就出现了缺料打回——T3.1 分项计量应优先核实 requirement document 在各角色 bundle 中的在场性。
