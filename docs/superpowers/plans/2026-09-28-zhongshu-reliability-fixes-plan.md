# 中书省可靠性修复方案（2026-09-28，v5 线上复盘修订版）

依据：task-20260928-835a07 实测故障链（18:05–19:05），全部有日志实据。
目标：消除三类可靠性损耗——确定性超时、死循环无刹车、证据绕路；评估每项的效率收益。

v2 修订（按代码评审）：Fix 1 改为修复既有重问反馈通道（不新增字段/通道）；Fix 2 补 max_polls 耦合与 role 表作用域；Fix 3 重设计（封闭图 1:1 边不可复用同 action，须新 action + fan-in 同步）；Fix 4 改为扩展既有 salvage 到 worker 失败波。

v3 修订（按落盘产物审计）：**v2 的 Fix 1 实证前提被推翻**——SOLVER:8 的 prompt.txt 实际含有 `[Retry feedback]`（4352B > 4162B，逐一核实落盘文件），v2 引用的"16111B 逐字节相同"是 comment payload 字节数，与 prompt.txt 是不同产物。重问反馈通道对 reply 类失败**已在工作**，Fix 1 缩小为回归测试 + 陈旧反馈清理；真正的盲重问只发生在超时类失败（AGENT_TIMEOUT ∉ REPLY_FAILURE_CODES），但其对症药是 Fix 2 而非反馈。新增 **Fix 5（P0）：同 state 连续 node-FAIL 刹车**——本轮 SOLVER:6→8→10→12 连续整波 FAIL 无任何刹车，须等 external 预算 3 次跑满才落 FAILED 终态。

v4 修订（实施后 + Fix 3 设计定案）：Fix 2 / Fix 5 / Fix 1 残份已实施（1084 测试全绿）：Fix 2 实现为 role 表 + env `ZHONGSHU_DISPATCH_TIMEOUT_OVERRIDES` + payload 级覆盖，max_polls 按生效 timeout 同步放大，仅默认 900s 配置注入 role 表；Fix 5 实现为 `node_fail_streak/state/max_node_fail_streak`，第 2 次同 state FAIL → HUMAN_GATE（不扣预算），clean join 重置；Fix 1 残份以 `RecoveryUpdate.clear_last_failure` 哨兵实现（显式新失败 > 清除 > 保留）。**Fix 3 原第 4 点"设计定案"已完成调查**（详见 Fix 3 节 v4 补充）：证据持久化已存在，真正的缺口是请求方追踪、按目标 action 重标、plan 键覆写防护三件事；worker 契约无需新增动作。

v5 修订（2026-09-29 线上复盘，task-20260929-c261a8）：Fix 3 Phase 1 已上线并实证有效（证据直通审查员、无 solver 绕行，证据轮仅 3.5–6min）；Fix 4 已实施（`_salvage_failed_group_revision`，1105 测试全绿）。但该轮暴露新故障模式：评审期 73min 在 critic↔analyst 间打转 8 波——**混合裁决饿死**（REVISE_GROUP 连续四轮被同波 REQUEST_ANALYST_EVIDENCE 压制，solver 修订波零次派发）+ **需求在证据与修订通道均不可闭合**（critic capsule 的 [Requirement document] 为空；requirement_contract 只注入 analyst bundle，critic/solver 没有——装包不对称，states.py:1482）+ **analyst 事实错误**（wave 9/11 把超限拒绝读成"写 result-file"，存活两轮才被 critic 抓出）+ **findings 波内去重失败**（同一 claim ×3）。新增 Fix 6–11（见二）；完整复盘与归因见第六节。Fix 7/8/9 的详细设计与验收见配套文档 `2026-09-29-analyst-evidence-quote-gate-and-requirement-contract-visibility-plan.md`。

## 一、故障链实证（为什么修）

| 时刻 | 事件 | 损失 |
|---|---|---|
| 18:05 | 组审 CRITIC:4 打回 `REQUEST_ANALYST_EVIDENCE`（806/900s，余量仅 94s） | – |
| 18:11 | 证据到手后 `EVIDENCE_PACKET_READY` 只能回 SOLVER（transitions.py:77，分析员无直通审查员的边） | 强制多跑一跳 solver |
| 18:26/18:50/19:05 | group-01 solver 修订**三次**在恰 900.0s 撞 `AGENT_TIMEOUT` | 3×15min 白烧 |
| 18:35 | SOLVER:6 两回复被接受，但折叠判 `PLAN_HAS_NO_ITEMS` → 整波 FAIL | 23.7min 全废 |
| 18:35 | SOLVER:8 重问 bundle **实际含有** `[Retry feedback] …PLAN_HAS_NO_ITEMS`（prompt.txt 4352B vs 4162B，落盘文件核实；此前"16111B 逐字节相同"是 comment payload 口径，非 prompt.txt） | 反馈通道工作正常；但 group-01 修订本身 >900s，告知也救不了 |
| 18:35 起超时类重问 | SOLVER:10/12 的 prompt.txt（4162B）与 SOLVER:6 完全相同——`AGENT_TIMEOUT ∉ REPLY_FAILURE_CODES`（domain/errors.py:91），反馈为空 | 超时无"可纠正的错"，对症药是 Fix 2，不是反馈 |
| 19:05 | group-01 末次重试超时 → 整波 FAIL → SOLVER:10 **全量重跑**，18:44 已成功的 group-02 被丢弃重付 | 每周期 ~30min |
| 19:05–20:35（预测） | waves 10→12→14 连续 FAIL，无刹车：`AGENT_TIMEOUT` 计 external 预算（context.py:134 `max_external_retries=3`），**须第 4 次 FAIL 预算耗尽才落 FAILED 终态**（states.py:302-310 预算耗尽后 action 保持初始 "FAIL"，transitions.py:176 `(state,"FAIL")→FAILED`） | 确定性超时下每周期纯烧 ~30min |

clean-path 基准：intake→组审完成仅 34min（2.5+4.1+14+13.4）；实际 94min+ 仍未到冻结检查，差值全部来自上述故障。

## 二、修复项

### Fix 1（P2，缩水后）重问反馈通道：回归锁定 + 陈旧反馈清理
**v3 审计结论**：通道已存在且对 reply 类失败工作正常——`retry_feedback()`（domain/policies/prompts.py:33）读 `recovery.last_failure`（domain/context.py:142），挂在全部 binding 组装点（states.py:4857 group_revise、states.py:4764 group_review、prompts.py:229 单发路径），event_inbox.py:75 透传 failure dict，reducer.py:183 保留 last_failure。SOLVER:8 落盘 bundle 已验证反馈送达。v2 列的三个致空假设 (a)/(b)/(c) 经核实**均未发生**（错误码 NODE_ITEM_REVISION_INVALID 在 REPLY_FAILURE_CODES 内、state 匹配、消息非空）。

**剩余真实缺口与改法**：
1. **陈旧反馈清理（保留 v2 改法 3）**：成功折叠（NODE_COMPLETED/:SUCCEEDED）不清除 `last_failure`——同 state 后续派发会带上上一轮的 `[Retry feedback]`。在 states.py:423 的 NODE_COMPLETED 分支补 `RecoveryUpdate(last_failure=None)`（reducer.py:183 需同步支持 None 语义：现"None 即保留"要改为显式清除信号，或用哨兵字段）。
2. **回归测试锁定通道（原改法 1/2 降级为测试）**：fold 判 invalid → bundle 含 `[Retry feedback]` 与错误文本；state 不匹配 → 不泄漏到其他状态；成功后 → 无陈旧反馈。不再改动 gate/出参代码。
3. **超时类反馈（可选，P2）**：若要让 `AGENT_TIMEOUT` 也带反馈（"上次尝试超时，请更直接地给出结果"），把超时码加入反馈白名单并给通用文案——价值存疑（模型没交卷，无错可纠），默认不做。

**测试**：上述三组回归用例；不新增字段、无 migration 变更。

**效率**：不直接省时间（通道已在工作）；价值是防回归 + 消除陈旧反馈误导模型的隐患。

### Fix 2（P0）角色差异化派发超时
**问题**：15 个派发全是 `timeout_seconds=900`；group-01 solver 修订需要 >15min，重试同 payload 同上限 → 确定性超时；组审 806s 已贴脸。

**改法**：
1. `AgentRequest`（transport/external.py:25）增加 `timeout_seconds: float | None = None`；deadline 计算处（runtime/agent_effects.py:196）优先用请求级值，回退现全局值。取值从 EffectRequest payload → dispatch 一路带下（`_dispatch_request` 组装时填入）。
2. **max_polls 同步换算**：轮询次数上限由全局 timeout 推导（app.py:305 `max_polls = max(30, timeout/interval + 2)`），请求级超时若不同步放大 max_polls，轮询循环（agent_effects.py:273）会先耗尽次数、提前落 `AGENT_TIMEOUT`——不同步则本修复无效。实现上按生效 timeout 重算 max_polls，或循环内以 deadline 为唯一终止条件。
3. 角色默认表（模块常量）：`review-analyst` 900s / `review-critic` 1200s / `review-solver` 1800s；env `ZHONGSHU_DISPATCH_TIMEOUT_OVERRIDES`（JSON role→sec）可覆盖。注意 role 名与 MENXIA 派发共用（`review-solver` 同时是门下省 solver，_ROLE_BY_STATE states.py:207）：键取 (phase, role) 或明确宣布门下省同享该超时，实现时二选一并写进测试。
4. 派发日志行照旧打印生效值（可观测）。

**测试**：单测：请求级覆盖到达成 deadline 计算**与 max_polls**；未覆盖时全局值不变；env 覆盖生效；ZHONGSHU/MENXIA 各自的生效超时分别断言。

**效率**：消除确定性超时重试风暴（本轮 3×15min+整波重跑均由此起）；不采用全局上调（会加重 f13561 型挂起白等）。

### Fix 3（P1，动图谱）证据包直通审查员
**问题**：审查员要证据 → 分析员补证 → 证据只能回 SOLVER（transitions.py:77）→ 整合修订后再交审查员。门下省组循环已有直通边（transitions.py:117-118，注释明言 "without touching the solver"），中书省没有。

**v4 设计定案（原第 4 点调查结论，已完成）**：

1. **证据持久化已存在，无需新增落库代码**。证据波折叠链：worker 发 `EVIDENCE_PACKET_READY`（adapters.py:2346 worker 契约）→ `aggregate_zhongshu_workers`（policies/parallel.py:40，唯一调用点 agent_effects.py:1254）→ `merge_analyst_evidence` 返回 aggregate，其中 `evidence_packet` 键装合并包 → 通用 `_review_update`（states.py:1041）经 `_menxia_evidence_packet_update` 第一分支（"Zhongshu folds carry the packet directly"）直写 `review.evidence_packet`；DTO roundtrip 已覆盖（context.py:1120/1158）。v3 担心的"结果仅存在于回复 payload"不成立。
2. **但发现新缺口：`plan` 键覆写**。merge 结果同时把 packet 塞在 `"plan"` 键下（task_proposals 恒空）→ `_review_update` 会把 `review.plan` 覆写成空图 packet。今天被"强制回 SOLVER 重建 plan"掩蔽；直通后 critic 将面对 plan 槽里没有 plan 的快照。**必须**：merge 按 target action 决定是否带 `plan` 键（solver 目标保留——stages.py:52 证实 solver 端按 `READY_FOR_SOLVER` 双通道分流消费；critic 目标不带 plan）。
3. **请求方追踪缺失**。三个请求方：SOLVER（transitions.py:79）/ CRITIC（:81）/ FREEZE_CHECK（:89）。SOLVER 的回流（证据回 solver）现状即正确；被错路由的只有 CRITIC 与 FREEZE_CHECK。方案：`ReviewState` 新字段 `evidence_requester: str | None = None`（reducer None=保留 + 显式 `clear_evidence_requester` 哨兵，复用 v4 已实施的 `clear_last_failure` 同款语义：显式新值 > 清除 > 保留）；`_payload_update` 在 `action == "REQUEST_ANALYST_EVIDENCE"` 时写 `evidence_requester=self.name`（一处通用改动覆盖三个请求方；已核实 critic 分支 states.py:2306 从 `_payload_update` 结果继续 replace，注入可存活；实施时须同样核实 SOLVER/FREEZE_CHECK 两处 handle 不丢弃 review_update）；分析员折叠（agent_effects.py:1254）读 `context.review.evidence_requester` 传 `target_action` 给 `aggregate_zhongshu_workers`/`merge_analyst_evidence`；**用后必须清除**（分析员 clean join 时置 clear 哨兵），否则陈旧 requester 会把后续 SOLVER 请求的证据错路由到 critic。

**改法（定案后）**：
1. 新增 action `EVIDENCE_PACKET_READY_FOR_CRITIC`，`_ROLE_EDGES` 增 `("ZHONGSHU_ANALYST", "EVIDENCE_PACKET_READY_FOR_CRITIC", "ZHONGSHU_CRITIC")`（新 action 无碰撞，1:1 registry 安全）；原 `EVIDENCE_PACKET_READY` 边保留专供 SOLVER。
2. **worker 契约不动**（v3 第 3 点范围修正）：证据波 worker 仍只发 `EVIDENCE_PACKET_READY`（adapters.py:2346 不改）；新 action 只存在于 aggregate/FSM 层。需要同步的只有：contracts/zhongshu_analyst.py:8 `_ACTIONS`（若其校验 aggregate action）、states.py `ZHONGSHU_ANALYST.supported_actions`、notifications.py 标签、transitions 边。
3. **critic 侧行李（新增工作项）**：直通后 critic 重派必须能看到新证据。group/task-review critic 已读 `review.evidence_packet`（`_item_evidence_context` states.py:4325 → `_task_review_bindings` :4535、group-review :4805 evidence_rows）；**plan 级 critic prompt = `build_prompt`（policies/prompts.py:167），今天无证据节**——须为 `ZHONGSHU_CRITIC` 增加有界证据节（复用 `_item_evidence_context` 切片规则），否则 critic 看不到证据会再次要证据（虽有 stall 熔断兜底但纯浪费）。
4. FREEZE_CHECK 直通需第二条边+action（1:1 registry 限制），**第一期只做 CRITIC**，freeze 维持现状（经 solver 回流），后续按需复制同款模式。
5. 审查员在证据轮的合法动作不变（APPROVE_CRITIC / REQUEST_SOLVER_REVISION / 再次 REQUEST_ANALYST_EVIDENCE，均有既有出边）；反复要证据由既有 stall 检测兜底（states.py:2285-2292 已有先例熔断）。
6. 主线不受影响：常规分析员轮仍出 `READY_FOR_SOLVER`（transitions.py:63）；`evidence_requester` 为空或 `ZHONGSHU_SOLVER` 时 merge 行为与今天逐字节一致。

**测试**：脚本化 e2e（复用 scripted-adapter 模式，零 token）：critic `REQUEST_ANALYST_EVIDENCE` → 断言 `review.evidence_requester=="ZHONGSHU_CRITIC"` → 分析员补证 → 断言下一跳 ZHONGSHU_CRITIC、`review.plan` 未被覆写、`evidence_requester` 已清除、critic 重派 prompt 含新证据；SOLVER 请求证据回流 ZHONGSHU_SOLVER 不变（回归）；无 requester 时主线 `READY_FOR_SOLVER` 路由不变（回归）；陈旧 requester 清除后 SOLVER 请求不被误路由（回归）；reducer 哨兵优先级三态（显式值 > 清除 > 保留）。

**效率**：每次证据需求省一整跳 solver（实测该跳 13–24min）；本轮若有此边，18:05 要证据后 ~19:00 前即可回到审查员桌上（实际路径现已拖到 SOLVER:10 且未回头）。

### Fix 4（P1，可选，需设计评审）worker 失败波结转已成功组
**问题**：本轮损失路径是 **worker 超时走 failed 分支**（agent_effects.py:999-1072），到不了 `_join_group_revise` 里已有的 salvage（`_salvage_group_revision`，agent_effects.py:2133——它只救 fold 阶段 patch 失败时的合法组）。group-02 18:44 已成功、19:05 整波 FAIL 后全量重跑重付。

**改法（草案）**：把既有 salvage 扩展到 worker 失败波——failed 分支聚合时，对已 SUCCEEDED worker 的组结果按 `_salvage_group_revision` 同款校验（patch+文档过全量后处理管线才折叠），FAIL aggregate 携带 `salvaged_group_ids`/`salvaged_group_rows`，重跑波跳过已接受组（binding 不派发）。不新造 `group_revision_accepted` 台账，复用既有 salvage 通道，避免与并发租约/审批棘轮的第二套状态交互。与并发租约/审批棘轮的交互仍需设计评审。

**效率**：每周期省已成功组的重付（本轮 group-02 ~9min/周期）；若 Fix 2 到位则触发场景大幅减少，优先级可降。

### Fix 5（P0）同 state 连续 node-FAIL 刹车
**问题**：FAIL→RETRY_WAIT→auto-resume 循环对确定性失败无刹车。`AGENT_TIMEOUT` 记 external 预算（context.py:134 `max_external_retries=3`），但预算耗尽前每次 FAIL 都整波重跑（transitions.py:176 预算耗尽后 `(state,"FAIL")→FAILED` 终态）。本轮实证：waves 6→8→10→12 连续 FAIL，前两次 FAIL（18:35、19:05）已足以证明 group-01 的超时是确定性的（同 payload 同上限），后两个周期纯烧 ~60min；若无 Fix 2，任何"需要 >900s 的活"都会以 3 个完整周期为代价才停。

**机制定位**：
- FAIL 事件预算路由在 `_ConcreteWorkflowState.handle`（states.py:281-312）：reply 预算耗尽已有升级 HUMAN_GATE 的先例（states.py:296-301），external/convergence 耗尽只是不再改写 action（保持初始 "FAIL"→FAILED 终态），没有中间刹车。
- 落点现成：每个非终态已自动生成 `(state,"HUMAN_GATE")→HUMAN_GATE`、`(state,"BLOCKED")→BLOCKED` 边（transitions.py:176-183），HUMAN_GATE 在各 state 的 supported_actions 中（states.py:2111/2991 等）；app.py:666 保证 HUMAN_GATE 永不自动恢复；states.py:1693-1706 已有带 reason 的 HUMAN_GATE decision helper 模式。

**改法**：
1. `RecoveryState`（domain/context.py:129 一带）新增字段：`node_fail_streak: int = 0`、`node_fail_state: str = ""`、`max_node_fail_streak: int = 2`（镜像既有 `max_*_retries` 风格，可配置）。
2. FAIL 事件处理（states.py:281 预算路由**之前**）：事件家族按 `{"FAIL", "AGENT_FAILED", "NODE_FAILED"}`（与 states.py:599 的 last_failure 写入口径一致）计算 `streak = streak+1 if node_fail_state == 当前state else 1` 并写回；`streak >= max_node_fail_streak` 时 action 强制 `"HUMAN_GATE"`（reason_code=`NODE_FAIL_STREAK_EXHAUSTED`，payload 沿用 :1693 helper 模式），不再进预算分支。
3. 成功重置：NODE_COMPLETED / `:SUCCEEDED` 分支（states.py:423）补 `RecoveryUpdate(node_fail_streak=0, node_fail_state="")`。
4. 阈值语义：按 state 计数、**不按错误码**（本轮 wave 6 是 fold 无效、wave 8 是超时，码不同但同属确定性失败，state 键控才能在 wave 8 刹住）。若实操中发现带反馈的 reply 重试需要更完整预算，上调 `max_node_fail_streak` 即可。
5. migration.py 在 RecoveryState 恢复路径（~:158）回读三个新字段——吸取 review_unit 恢复缺口教训。

**测试**：脚本化 FSM 测试：① 同 state 两次 FAIL（不同错误码）→ 第二次路由 HUMAN_GATE 且 payload 带原因，不再进 RETRY_WAIT；② 两次 FAIL 之间夹一次成功 → streak 重置、第二次 FAIL 走正常预算路由；③ 跨 state 的 FAIL 不累计；④ migration roundtrip 保留新字段；⑤ HUMAN_GATE 后 operator RESUME 走既有恢复路径。

**效率**：本轮事故若已上线：19:05 的 wave 8 FAIL 即触发刹车，省去 waves 10/12/14 ≈ **90min**；把此类"确定性失败循环"的最坏损耗从 N×30min 封顶为 2×30min。

### Fix 6（P1，需设计评审）混合裁决分流：修订组与证据组各自成波
**问题**：聚合动作取单一最严值——`aggregate_reviews`（zhongshu_review.py:842-843）与组级 overall（agent_effects.py:1938-1939，注释 :1832 明言优先级）都让 `REQUEST_ANALYST_EVIDENCE` 压过修订类动作，FSM 每轮只执行一个动作。本轮实证 seq 8/10/12 三轮 group-01 与 group-03 均投 REVISE_GROUP、group-02 投 REQUEST_ANALYST_EVIDENCE → 聚合每轮取 evidence，修订诉求原地踏步、findings 反复重提，第 4 轮触发 stuck-finding 守卫（states.py:2225）。修订波**零次派发**，评审期 73min 全部耗在 critic↔analyst 交替。

**改法**：
1. 聚合 payload 已带 per-group 裁决（`group_reviews`，events 实证），分流数据现成。critic 处理分支（states.py:2233 起）读到"evidence 需求 + REVISE_GROUP 并存"时，**同一轮内先派 group_revise 波（只绑定 revise 组的 active findings），折叠后接着派证据波（只绑定 evidence 组的 findings）**——两条既有派发机器不改语义，新增的只是按组过滤。
2. `_analyst_evidence_demand`（states.py:4389）增加组过滤参数，只收集 evidence 组的 findings；group_revise 波 bindings 按 revise 组构建（既有机器按 active findings 组装，天然支持子集）。
3. stuck/identity 计数不得把本轮已派发修订组的 findings 继续累加（否则分流当轮就误触刹车）；以 finding 在本轮是否被消费为准。
4. 回退方案（若与审批棘轮/并发租约交互过深）：仅翻转两处优先级，REVISE_GROUP 胜于 REQUEST_ANALYST_EVIDENCE，证据需求顺延一轮再答——一行改动，仍严格优于饿死，但每轮多付一个 critic 波。设计评审时二选一。

**测试**：脚本化 e2e（复用 GroupWaveScripting）：混合裁决 → 断言同一轮先后出现 group_revise 波与证据波、两组 findings 各自正确入波、下一 critic 波两组诉求均被消费、stuck 计数不误增；纯 evidence / 纯 revise 轮行为不变（回归）。

**效率**：本轮若 seq 8 即分流，可比实际省 ≥2 个 critic 波 + 2 个证据波 ≈ 40–50min；评审期收敛轮数从 4 降至约 2。

### Fix 7（P1）需求权威原文注入 critic/solver bundle 与 critic capsule
> v5.1 勘误（2026-09-29，按逐 bundle 扫描修正）：本节最初写"requirement_contract 不在产物 context.json / analyst 出处瞎猜"——**不准确**。实证：analyst 证据波 bundle 15/15 含该字段（5 条需求全文），critic 13/13、solver 2/2 **没有**；同名 `context.json` 每角色各一份、内容不同。analyst 的引用在其自己的包里为真，critic 按自己的包核对才得出"不存在"。病根是**装包不对称（states.py:1482 只注入 analyst）**，不是 analyst 幻觉。详细设计与验收见 `2026-09-29-analyst-evidence-quote-gate-and-requirement-contract-visibility-plan.md` §2（B1/B2/B3/B4）。

**问题**：group 评审 capsule 的 [Requirement document] 槽（zhongshu_review_queue.py:290）填的是组文档 `row.doc_markdown`（states.py:4693/4886）——组文档尚未成文时为空，critic 便索要"req 权威原文"。执行方 solver 的 bundle 同样没有契约（无法权威转载），critic 自己的包里也没有（无法核对 must/非目标）→ "要原文"在证据与修订两条通道都不可闭合；再叠加 Fix 6 的饿死（修订波零派发），形成死锁闭环。

**改法**：
1. group 评审 capsule 的 [Requirement document] 槽在组文档为空/未成文时回填**需求契约权威原文**（req_id + statement + source，取自需求契约 fold 的 canonical 副本，有界截断）；组文档已成文时维持现状。
2. group_review 波的 dispatch context 复用契约波的注入模式（states.py:1484-1487）带上 `requirement_contract`，使 bundle 的 context.json 真实含该字段——analyst 的出处引用从此可验证、可抄写。
3. 两处注入均受既有 bundle 体量护栏约束，不新增无界文本。

**测试**：单测：组文档为空时 capsule 含 req 原文与 source；已成文时不覆盖；group_review bundle 的 context.json 含 `requirement_contract` 键；脚本化 e2e 断言注入后"索要原文"类 finding 不再复现。

**效率**：直接消除本轮四轮 evidence 环中 group-01/02 的主要诉求来源；配合 Fix 6，评审期收敛预计 2 轮内。

### Fix 8（P2）evidence 增加 quote 字段 + 传输门机械校验
> 详细设计（含双调用点、行漂移两级查找、语义残留边界声明）见 `2026-09-29-analyst-evidence-quote-gate-and-requirement-contract-visibility-plan.md` §1.6。

**问题**：analyst 契约的 evidence_updates 只要求 source（path:line）+ conclusion（转述）（contracts/zhongshu_analyst.py:59-61），转述即失真点。本轮方向性错读首现于 wave 9（wave 7 的 worker-01 方向读对、worker-03 幻觉出函数名 `_inline_result_from_payload`），把 `_persist_inline_result` 的抛错行为（adapters.py:1314-1316 超 64KiB 抛 AgentResultFileError）曲解为"超限写 result-file"，存活至 wave 12 才被 critic 抓出——检查离场太远。

**改法**：
1. `_EVIDENCE_UPDATE` 增加可选 `quote` 字段（path/line_start/line_end/text）；source 指向文件路径时由门强制必填。baseline 示例同步（:132-135）。
2. 传输门校验：`_evidence_demand_rejection`（adapters.py:164，调用点 :1264/:1297 **两处都要挂**）在既有 path 存在性检查之后，校验 quote 文本在所引文件中存在——先 `[line_start-5, line_end+5]` 窗口、再全文件兜底（空白归一化 + NFKC），全无才弹回并指明期望/实际差异。
3. 触发面：ZHONGSHU 波回包含 `evidence_updates`/`finding_responses` 即校验（不限 evidence_demand 波）；未知路径豁免（合法 unknown）。

**测试**：单测：quote 命中 → 通过；quote 全文件无 → 弹回且消息含定位；行号漂移但内容在场 → 通过；未知路径豁免（回归）。

**效率**：把"critic 15–19min 后的人工核对"前移为"回包秒级弹回 + 精确反馈"；本轮若有此门，wave 9 即重试，wave 12 对应 finding 消失，省一轮 critic 波（~12min）。

### Fix 9（P2）analyst 契约规则：先抄录再解读
**问题**：规则未禁止凭记忆转述代码行为，ev-001 即栽于此；出处引用也无"只许抄写真实存在的位置"约束，导致两个 worker 编出两个不存在的出处。

**改法**：contracts/zhongshu_analyst.py 的 prompt 规则增加：① 凡对代码行为的结论必须先在 `quote` 字段逐字抄录所引行，再给出解读；② 引用出处只允许抄写注入产物（context.json / prompt.txt）中真实存在的键名与位置，不确定一律标 unknown。与 Fix 8 同批落地（规则 + 机械门互相咬合，单靠规则无约束力）。

**测试**：契约校验用例更新（baseline 合规样例含 quote）；无新逻辑。

**效率**：与 Fix 8 合并计；纯提示规则不计独立收益。

### Fix 10（P2）critic findings 波内去重与跨组 id 消歧
**问题**：wave 12 group-03 出现同一 claim ×3 份（不同 critic worker 重铸 finding-244bdc2f7aee / finding-a2d44ef08210 等），`aggregate_reviews`（zhongshu_review.py:759）未折叠；另有 finding-000001/000002 编号跨组碰撞（group-01 与 group-02 各一套），下游按 finding_id 计 stuck/identity 时口径被放大。

**改法**：
1. `aggregate_reviews` 在 action 投票前按 (item_id, 归一化 claim) 折叠同 claim findings（保留最高 severity，原 id 记入 decision_basis）。
2. finding 身份键补组作用域：stuck/identity 判定（test_zhongshu_finding_identity_and_stall.py 的既有机器）以 (group_id, finding_id) 或聚合期重铸的全局唯一 id 为准，消除跨组碰撞误计。

**测试**：单测：三份同 claim findings 折叠为一；不同 claim 不误折；跨组同名 id 不互相误并；stuck 计数在去重后与真实诉求轮数一致。

**效率**：降低 stuck 误触概率与复审噪音；本轮 group-03 的重复即虚增了刹车压力。

### Fix 11（P3，可选）证据环专用刹车
**问题**：Fix 5 只刹 node-FAIL；证据环靠通用 stuck-finding 守卫（domain/zhongshu/critic.py:163）第 4 轮才刹停，且 reason 不指明"证据环"这一具体形态。

**改法**：ReviewState 增 `evidence_round_streak`（连续聚合 action==REQUEST_ANALYST_EVIDENCE 计数，其他动作清零），阈值 3 → HUMAN_GATE（reason=`ZHONGSHU_EVIDENCE_STALL`，notifications.py:390 同款标签模式）；migration 回读新字段。Fix 6 落地后此环不应再出现，本项为更早更准的兜底。

**测试**：脚本化：连续 3 轮 evidence → HUMAN_GATE 且 reason 正确；中间夹其他动作 → 清零；migration roundtrip。

**效率**：最坏情形从 4+ 轮封顶为 3 轮（每轮 12–19min）。

### 应急（ops，今日可用）
代码修复上线前若需再跑任务：重启服务加 `--timeout 1800`。代价：真挂起时白等翻倍，仅作止血。

## 三、非目标
- 不做部分折叠为独立方案（fold 阶段的部分折叠已有 `_salvage_group_revision`，本轮两组全废发生在 worker 失败路径，由 Fix 4 覆盖）。
- 不动正常跳的耗时本身（那属于已上线的并发+机械冻结方案，等首次 critic 通过后验证）。
- 不在本方案内处理 f13561 类 `AGENT_RESULT_MISSING`（33min 派发超时白等，另行处置）。

## 四、上线与验证
1. 实现顺序：**Fix 2（独立，可先行）→ Fix 5（刹车，独立）→ Fix 4（设计评审后）→ Fix 3（设计定案后）**；Fix 1 残份（回归测试 + 陈旧反馈清理）随任一批次搭车。
2. 每项跑 `python cmd/run_tests.py`（全量 1057+ 需保持绿）。
3. 服务重启加载新代码；在跑任务保持旧代码直至自然结束或人工止损。
4. 验证观测点：派发日志出现 1200/1800s 且 max_polls 同步生效；第二次同 state node-FAIL 后出现 `DOMAIN_TRANSITION ... after=HUMAN_GATE` + `NODE_FAIL_STREAK_EXHAUSTED`；重问 bundle 出现含组级错误的 `[Retry feedback]` 且成功后无陈旧反馈；证据轮 `DOMAIN_TRANSITION ... after=ZHONGSHU_CRITIC`。

## 五、效率结论
能提高，且各项主修复均不加新开销（Fix 3 少一跳，Fix 2/5/4 纯减损）。
- 本轮可量化浪费 ≈ 54min（超时 3×15 + 盲败波 23.7 − 并行重叠）+ 循环持续烧钱（预测到 ~20:35 自停，累计 ≥150min）；clean-path 本应 ~34min 到组审完成。
- **Fix 2 是本轮事故的对症药**（消除全部三次超时及整波重跑）；**Fix 5 是同类事故的兜底**（确定性失败 2 波即停，本轮可省 ~90min）；Fix 4 免去已成功组的重付；Fix 3 每次证据需求省一整跳 solver（13–24min）。
- Fix 1 缩水后不承担效率收益（通道已在工作），只提供防回归与陈旧反馈清理。
- 注意：以上修复解决可靠性损耗，不加速正常跳；正常路径提速仍取决于机械冻结首次触发的实测。

## 六、v5 复盘：task-20260929-c261a8 证据环（2026-09-29，11:43–13:17 本地）

Fix 3 Phase 1 实证：证据直通生效——events 全程 `EVIDENCE_PACKET_READY_FOR_CRITIC`，无一次回 solver；证据轮仅 3.5–6min。但评审期 12:04–13:17 共 73min 在 critic↔analyst 间打转 8 波（critic×4 ≈59min + analyst×4 ≈15min），最终由 stuck-finding 守卫刹停（`ZHONGSHU_STUCK_FINDING` → HUMAN_GATE，守卫按设计工作）。

| 时刻（本地） | 波 | 结果 |
|---|---|---|
| 12:09–12:20 | CRITIC_6（~16min） | g01/g02 要证据，g03 要修订 → 聚合=要证据 |
| 12:23–12:26 | ANALYST_7 | 2 worker 首包 CONTRACT_REJECTED 重试；worker-01 方向读对，worker-03 幻觉函数名 `_inline_result_from_payload` |
| 12:33–12:38 | CRITIC_8 | g01/g03 要修订，g02 要证据 → 聚合=要证据（修订被饿①） |
| 12:38–12:41 | ANALYST_9 | **首现方向性错读**（FR 称"超限自动写 result_path"）；引用"requirements 数组"等不一致出处 |
| 12:50–12:53 | CRITIC_10 | 同上（修订被饿②）；g02 findings 重铸新 id |
| 12:56–12:58 | ANALYST_11 | 引用"context.json requirement_contract[N]"（在 analyst 包里为真，critic 包里没有→被判不存在）；ev-001 自我修正 |
| 13:05–13:17 | CRITIC_12（~19min） | g01/g03 要修订（饿③）；g02 首包 unstructured 重试；g03 同 claim ×3；抓出 ev-001 错误 → ZHONGSHU_STUCK_FINDING → HUMAN_GATE |

归因（按对 73min 的贡献排序）：
1. **编排（主因）**：聚合优先级（zhongshu_review.py:842、agent_effects.py:1938）让 REQUEST_ANALYST_EVIDENCE 永远压过 REVISE_GROUP，修订波零派发 → Fix 6。
2. **输入缺口（装包不对称）**：capsule [Requirement document] 空（zhongshu_review_queue.py:290 填未成文的组文档）+ requirement_contract 只注入 analyst bundle（states.py:1482；critic 13/13、solver 2/2 无）→ critic 按**自己的** context.json 核对，把"我看不到"判成"不存在"，"要原文"在证据与修订两通道均不可闭合 → Fix 7。（v5.1 勘误：analyst 引用在其自己的包里为真，非幻觉。）
3. **analyst 准确度（次要）**：wave 9/11 方向性错读两处实锤（wave 7 为幻觉符号、方向读对）；critic 核对有效但离场太远（15–19min 后才抓出）→ Fix 8/9（quote 抄录 + 秒级机械门）。"引用不存在位置"一项经逐 bundle 扫描改判为装包不对称误伤（见归因 2 勘误）。
4. **去重缺陷**：同 claim ×3、finding_id 跨组碰撞，虚增 stuck 压力 → Fix 10。
5. **守卫偏晚**：stuck-finding 第 4 轮才刹住 → Fix 11（可选兜底）。

结论：CRITIC 并非过严（claims 有据、会升级诉求、还主动承认旧主张失效）；ANALYST 有一处方向性错读（wave 9/11）但不是主因，"引用不存在位置"实为 critic 按自己的包核对所致的误判；**时间是耗在编排饿死 + 装包不对称导致的不可闭合需求上**——即便 analyst 从 wave 9 起零错误，循环照样转。

实施顺序建议：Fix 7 → Fix 10 → Fix 8+9 → Fix 6（需设计评审，注意与审批棘轮/并发租约交互）→ Fix 11（可选兜底）。每批全量 `python cmd/run_tests.py` 保持绿。
