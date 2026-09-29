# 分析员证据准确性（Quote 门）与需求契约跨角色可见性修复方案

> 数据来源：`task-20260929-c261a8` 运行（2026-09-29 03:27–05:17 UTC，审查上一版优化实现的轮次）。证据文件：`runs/task-20260929-c261a8/workflow-events.jsonl`、`runs/task-20260929-c261a8/artifacts/`、`runs/transport/prompt-bundles/task-20260929-c261a8/`（逐 bundle 扫描）。
> 本文供评审用。两个问题是两类不同的病：**问题 A 责任在 analyst 准确度 + 协议缺口；问题 B 责任主要在编排装包，不修正会继续制造"核验死锁"。** 分开决策，不要合并处理。

---

## 0. 摘要

| 问题 | 定性 | 责任面 | 本轮代价 | 修复 |
|---|---|---|---|---|
| **A. ev-001 读反代码行为**（"超限时写入 result-file" vs 实际"超限时拒绝"） | analyst 转述失真 + 协议只要求"来源"不要求逐字引用 + 机械闸只查"引用出现没" | analyst 协议/skill（主）+ 传输闸 | 错误读法在 wave 9/11 存活，critic wave 12 才抓出（约 36 min，跨两个往返） | **Quote 门**（P0） |
| **B. requirement_contract 引用被判"出处不存在"** | 编排装包不对称：analyst 的包里有契约，critic/solver 的包里没有；同名 `context.json` 各角色内容不同 | 编排（`states.py` 注入范围，主）+ 引用规范 | 需求原文类 finding 连挂 3 轮不收敛 → `ZHONGSHU_STUCK_FINDING` 卡人工闸；本轮 110 min 的核心浪费 | **契约注入全部核验角色 + 统一引用锚 + 可满足性防护**（P0） |

---

## 1. 问题 A：ev-001 把"超限拒绝"读成"超限写文件"

### 1.1 问题（错误内容）

analyst 在证据 `ev-001` 中对 `_persist_inline_result` 的行为给出与代码相反的结论，声称"结果超限时写入文件而非评论"。该错误说法被后续波次引用扩散，直到 critic wave 12 逐行核对才被纠正。

### 1.2 证据：代码真值（`cmd/orchestrator/adapters.py:1286-1343`）

```python
# _persist_inline_result —— 处理 agent 通过评论通道回传的 inline 结果
#（此前还有 evidence_demand 机械闸，见 1297-1308；此处略）
if not self.orchestrator_result_write_enabled:
    return payload
encoded_size = len(json.dumps(inline_payload, ensure_ascii=False).encode("utf-8"))
if encoded_size > self.inline_result_max_bytes:            # 1314-1316
    raise AgentResultFileError(
        f"inline result exceeds limit: {encoded_size}>{self.inline_result_max_bytes}"
    )
result_path = self.prompt_bundle_builder.result_path(...)   # 1318-1321
file_result = write_agent_result_file(...)                  # 1322-1343
```

- **超限 → 直接抛错拒绝**（1314-1316），不写文件；该异常在 poll 侧被捕获并记为 `contract_rejected`（`adapters.py:1627-1641`）。
- **未超限 → 编排代写 result file**（1318-1343），用于下游统一处理。
- **agent 自行预先写文件** → poll 时通过 `_recover_result_file` 被动回捞（`adapters.py:1191-1284`、1728）。
- 阈值默认 64 KiB：`adapters.py:338-340`，`int(os.environ.get("INLINE_RESULT_MAX_BYTES", str(64 * 1024)))`。

### 1.3 证据：错误表述与正确表述并存（同轮自相矛盾）

**读反的（错误）：**
- `artifacts/5e8d8e59…_ANALYST_11_worker-01.json`，ev-001 结论：
  > "dispatch 方法在结果大小超过 `inline_result_max_bytes`（默认 64KB）时**写入文件**而非评论；poll 方法通过 `_recover_result_file` 从文件恢复。"
- `artifacts/9386af…_ANALYST_9_worker-03.json`，finding_response：
  > "Agent 端输出超过 `inline_result_max_bytes`（adapters.py:1314）时**自动写入** result_path（prompt_bundle.py:232-238）并返回短链评论"
- `artifacts/a24304db…_ANALYST_7_worker-03.json`，ev-001 结论出现**不存在的函数名** `_inline_result_from_payload`（全仓 grep 无此符号），属"误指"。

**读对的（正确）：**
- `artifacts/cdadada7…_ANALYST_7_worker-01_attempt-2.json`，ev-001 结论：
  > "……超限**直接 raise 而非 fallback** 到 file-only 投递。"
- `artifacts/6ee3974d…_ANALYST_11_worker-03.json`，ev-001 结论：
  > "超限时 `_persist_inline_result` **抛出异常**（adapters.py:1314-1317），**不自动切换**到 result-file 通道。"

即：同一份代码、同一轮运行，不同 worker 给出相反结论——这不是"文档难读"，是转述环节的随机失真。

### 1.4 证据：抓出与代价

critic wave 12 group-01 最终 finding（`artifacts/2a45409a…_ZHONGSHU_CRITIC_12_group-01.json`，与最终 ledger `finding-000003` 一致）：

> "ev-001 将 adapters.py:1314-1316 描述为超限时写入 result-file；源码位置属于 `_persist_inline_result`，超出 64 KiB 时抛出 `AgentResultFileError`，而非展示超限回退。"（P1，KEEP_OPEN；impact：错误的基线证据可能让实现者以为 Critic 超限路由已存在）

同波另一条 finding（`finding-000004`）指出口径问题："验收口径以 30000 字为上限，而证据中的 `inline_result_max_bytes` 默认值为 64 KiB"。

**时间线（UTC）**：方向性错误首现于 wave 9 回包（04:41:45），wave 11 再次出现（04:58:03），wave 12 折叠时才被抓（05:17:28）——从首现到纠正间隔约 36 min，跨两个完整往返（wave 10/11）。有 quote 门时该错误在 wave 9 回包当刻即被弹回，可至少省去一轮 critic+analyst 往返（本轮口径约 15 min）+ 对应的全量 bundle 重读。

### 1.5 原因（三层）

1. **analyst 转述失真（准确度问题）**：读长函数时把 `if` 分支的方向读反；这是 LLM 读码的真实错误率，不是偶发手滑。
2. **协议只要求"转述 + 来源"**：契约要求 `source(path:line) + conclusion`，转述就是失真发生的地方；没有任何"逐字引用"强制。
3. **机械闸只查"引用出现没"**：`_evidence_demand_rejection`（`adapters.py:164-236`）只做子串存在性检查（204-213 行：path/symbol 是否在回包文本中出现过）+ finding_responses 是否作答（218-233）。**不检查对代码行为的描述对不对**。文件确实存在、确实被引用 → 放行。

### 1.6 修复：Quote 门（P0）

**目标**：把"代码事实错误"的拦截点从 critic 波（10–50 min 后）提前到传输闸（回包当刻）。

1. **契约 / skill 变更（analyst 侧，回答"是否该优化 analyst 协议"——该）**：
   - `evidence_updates` 与 `finding_responses` 中一切"对代码行为下结论"的条目，新增**必填逐字引用**字段，例如：
     ```json
     "quote": {"path": "cmd/orchestrator/adapters.py", "line_start": 1314, "line_end": 1316,
               "text": "if encoded_size > self.inline_result_max_bytes:\n    raise AgentResultFileError(...)"}
     ```
   - 契约 prompt_rules 写明：**"不得转述代码行为；先逐字引用所依据的行，再给出解读；解读必须能被所引用行直接支持。"**
   - 同样写入 `zhongshu-analyst` skill。
2. **机械校验（编排侧，与 `_evidence_demand_rejection` 同层）**：
   - **两个调用点都要挂**：`_evidence_demand_rejection` 在 `_persist_inline_result` 有 file-result（`adapters.py:1264`）与 inline（`adapters.py:1297`）两处调用；quote 校验作为同层兄弟检查必须在两处同时生效（或并入 gate 本体），只挂 inline 会漏掉 file-result 通道。
   - **schema 层 quote 记为可选字段**（unknowns 类条目无需引用），由门按"该条目 source 指向文件路径"条件强制必填——既满足 D3 的全部 evidence_updates 口径，又不误伤合法 unknown；`finding_responses` 同规则（可选 + 出现即校验）。
   - 校验算法：读取 `quote.path` 文件，空白折叠/NFKC 归一后查找 `quote.text`——**先在 `[line_start-5, line_end+5]` 窗口找，找不到再全文件兜底，全文件也无才拒收**（`QUOTE_MISMATCH: cmd/orchestrator/adapters.py:1314 expected="raise ..." actual="write_agent_result_file(...)"`），回包立即 `contract_rejected`。窗口 + 全文件两级查找保留"幻觉文本拦截"，同时容忍行号漂移，避免 ±2 行的诚实引用被误杀。
   - 这样错误读法在 wave 9/11 回包当秒被弹回，且 reject 信息直接告诉 worker 哪一行对不上，worker 可在同一轮改正，省掉一整轮 critic 波。
   - **边界声明（语义残留风险）**：门校验的是**抄录真实性**，不是**解读正确性**——quote 逐字为真而结论仍可能读反（prompt 规则"解读必须能被所引用行直接支持"只能缓解，不能机械保证）。quote 门是准确度下限的提升与检测前移，critic 的语义复核仍是必要后盾；验收指标应为"失真存活轮数下降"，而非"错误清零"。
3. **边界与豁免（需评审确认）**：
   - quote 限长（建议 ≤30 行且 ≤4KB），防止把 quote 变成新的 bundle 膨胀源；
   - 对 workspace 不存在的路径维持现状（可标 UNKNOWN，不罚）；
   - 引用的如果是计划文档（如 `2026-09-28-…plan.md §6.3`）同样可读、同样校验；
   - 先覆盖 ZHONGSHU 三角色，MENXIA 后续评估。
4. **测试**：新增 `cmd/test_evidence_quote_gate.py`：正确引用通过 / 行号偏移拒绝 / 文本篡改拒绝 / 不存在路径豁免 / NFKC 与空白折叠归一 / 超长拒绝；回归 `test_critic_evidence_context.py`、`test_dispatch_envelope.py` 等既有闸门测试。
5. **验收**：构造"把 1314-1316 读反"的 payload，回包在传输闸即被拒；脚本化 e2e 全绿。

---

## 2. 问题 B：requirement_contract 跨角色不可见 → 核验死锁

### 2.1 问题（现象，先纠正一个前期误判）

运行中 critic 判定 analyst 的"权威原文见 `context.json requirement_contract[N]`"引用**不可核实**；前期分析曾将其归因为"analyst 引用了不存在的位置 / 编排没把字段落进 context.json"。

**事实恰恰相反**：analyst 的 bundle 里一直有 `requirement_contract`；**没有它的是 critic 和 solver**。逐 bundle 扫描结果：

| 角色 bundle | 数量 | context.json 顶层含 `requirement_contract` |
|---|---|---|
| ZHONGSHU_ANALYST 证据波（wave 2/7/9/11 + 重试） | 15/15 | **有**（5 条需求全文） |
| ZHONGSHU_ANALYST 契约生产跳（wave 1） | 1 | 无（它是生产方，合理） |
| ZHONGSHU_CRITIC（wave 6/8/10/12） | 13/13 | **无** |
| ZHONGSHU_SOLVER（wave 3/5） | 2/2 | **无** |

注入代码只在 analyst 分派存在（`cmd/orchestrator/domain/states.py:1482-1500`）：

```python
if target == "ZHONGSHU_ANALYST" and contract_payload:
    analyst_binding_context = {
        "requirement_contract": contract_payload,
        "envelope": build_envelope(
            ingredients=[{"key": "requirement_contract", "source": "context.json", ...}],
            ...
        ),
    }
```

即：**同名文件 `context.json`，每个角色各拿一份、内容不同。** analyst 看到的契约，critic 看不到。

### 2.2 证据：critic 把"我看不到"判成了"不存在"

- `artifacts/f2cda2e5…_ZHONGSHU_CRITIC_12_group-02_attempt-2.json`，finding_response：
  > "此前称 `context.json` 含 `requirement_contract`，但检查到的上下文顶层**无此字段**；仍需权威来源证据。"（KEEP_OPEN）
- 最终 ledger `finding-fb903811bd8c`（P1, item-000003）：
  > "事实：`context.json` 顶层没有 `requirement_contract`；`prompt.txt` 提供派生文档但未给出 req-000003 权威原文。无法核对 must 与非目标条款。"

这两条都"对 critic 自己成立"（它的包里确实没有），但相对 analyst 的实际输入是**误判**。

### 2.3 证据：死锁的形成

最终 ledger 中两个 P1 连挂 3 轮（`max_stuck_finding_rounds=3`），触发 `ZHONGSHU_STUCK_FINDING` 进人工闸（`no_progress_count=3/3`，ledger 共 23 条 finding，3 个组全部停在 REVIEWING）：

- `finding-000001`（P1, item-000001, stuck=3）：
  > "组 capsule 未提供 req-000001 的权威文本；item 目标涵盖 Critic 及其他未命名的超限风险角色，而验收信号只覆盖 Critic。"
  - 其 `required_action`："在**组文档**补入 req-000001 原文及权威来源；明确角色集合……"
- `finding-000002`（P1, item-000002, stuck=3）：同类（req-000002 权威文本）。

**死锁环路**：critic 要求"把 req 权威原文补进组文档" → 执行方是 solver → **solver 的 bundle 里同样没有契约**（2.1 矩阵）→ 无法权威转载/核对 → 下一轮 critic 再次 KEEP_OPEN → 3 轮后卡进人工闸。这是本轮 110 min、4 波 critic + 3 波 analyst 往返、finding 6→23 的**核心成本来源**。

### 2.4 原因

1. **装包不对称**（主责，编排）：`requirement_contract` 只注入 analyst 分派（`states.py:1482`），需要核验/转载它的 critic 与 solver 拿不到。
2. **同名文件多态 + 引用无角色限定**：worker 写"`context.json requirement_contract[N]`"（在各自包里为真），核对方按**自己的** `context.json` 判断 → 误判"不存在"。
3. **"权威原文"装料责任未定义**：orchestrator 拥有的材料，被要求由 solver 补入组文档；无人定义"谁负责装料"，solver 也无从取得材料。
4. **引用写法无规范**：同一轮出现 `requirements 数组` / `context.json requirement_contract[N]` / `cmd/orchestrator/domain/context.json`（不存在的路径）三种写法——内容都对，位置表述混乱，核对方无法机器化定位。

### 2.5 修复

**B1（P0）— 契约注入全部核验角色**：把 analyst 已有的同一份 `contract_payload` 注入 ZHONGSHU_CRITIC（group review bindings）与 ZHONGSHU_SOLVER 的 dispatch_context（含 envelope ingredients 声明）。MENXIA 组审/项目 worker 是否同样需要 → 见待审决策 D2。

**B2（P0）— 统一引用锚与写作规范**：契约条目约定统一引用名（如 `requirement_contract[req-000003]`），写入 analyst/critic/solver 的契约 prompt_rules 与 skill；禁止裸写"`context.json`"不带字段与角色限定；禁止把编排源码路径（`cmd/orchestrator/domain/context.json`）当契约出处（机械可查：该文件不存在）。

**B3（P0，设计决策）— 明确"权威原文"的装料责任**，二选一：
- ① 由编排在组 capsule/上下文中注入 req 原文（critic 的"组 capsule 未提供 req 原文"要求自动满足）；
- ② 若产品决定 capsule 不携带契约原文，则 critic 此类 finding 必须有机械拦截或归属标注（不得把 orchestrator-owned 材料设为 solver 的可满足项）。
> 本轮死锁的直接触发点即"未定义归属的要求被派给了没有材料的角色"，必须先拍板再落码。

**B4（P1）— 不可满足需求防护**：finding 需求涉及 orchestrator-owned 材料时，dispatch 前做"接收方可获得性"检查（至少打标+日志），防止再次连挂到人工闸。

**测试与验收**：
- 新增 bundle 一致性测试：按"角色 × 材料"矩阵断言 critic/solver bundle 含 `requirement_contract`（对应 2.1 表格）；
- scripted e2e 复现"analyst 引用契约 → critic 核验通过、不再 KEEP_OPEN、不触发 stuck"；
- 验收：重放 c261a8 场景，`未发现 requirement_contract 字段` 与"组 capsule 缺 req 原文"两类 finding 不再出现，运行不再卡在 `ZHONGSHU_STUCK_FINDING`。

---

## 3. 附带修正（同轮暴露，建议一并处理）

1. **阈值口径（finding-000004）**：验收文案用"30000 字（字符）"，代码默认 `64 KiB`（字节，`adapters.py:338-340`）；两者不是同一单位。修复：统一 §4 口径（建议明确"以 UTF-8 字节计的 64 KiB"并补充"超过 30000 字符、但不足 64 KiB"的边界验收样例），或在验收中同时给出两种计量。
2. **大回包传输丢弃（与两个问题无关，但同轮复现）**：`artifacts/2a91dfc4…_ZHONGSHU_CRITIC_12_group-02.json` 为 143 字节占位文本（"output was too large to post safely"），group-02 因此重问一次——与 bc58c1 wave 6 同类、同一占位文本 sha256（`22af44cd…`；**此跨 run 一致性本轮未复核**，落码前抽查）。属传输层（multica 评论大小限制）问题，另项处理。

---

## 4. 本轮时间线（供审）

| 时刻 (UTC) | 事件 | 备注/证据 |
|---|---|---|
| 03:27:18 | START → ANALYST（需求契约跳） | |
| 03:43:19 | REQUIREMENT_CONTRACT_READY | 契约 hop 16 min |
| 03:50:48 | ANALYST 证据波 → SOLVER | |
| 04:01:00 | SOLVER 打回重跑 | `SOLVER_GROUP_DOC_INVALID:doc_unparseable`（组文档格式），浪费一跳 |
| 04:04:21 | SOLVER 重跑 → CRITIC | |
| 04:20:24 | CRITIC#6 → REQUEST_ANALYST_EVIDENCE（findings=6） | |
| 04:26:37 | ANALYST#7 → 证据包 | |
| 04:38:08 | CRITIC#8 → findings=6 | |
| **04:41:45** | **ANALYST#9 → 证据包（首现方向性错误读法）** | `9386af…worker-03` |
| 04:53:25 | CRITIC#10 → findings=8 | |
| **04:58:03** | **ANALYST#11 → 证据包（错误读法复现）** | `5e8d8e59…worker-01` |
| 05:17:28 | CRITIC#12 折叠（findings=14）→ **HUMAN_GATE / ZHONGSHU_STUCK_FINDING** | `no_progress_count=3/3`；ledger 23 条；3 组全部 REVIEWING |
| 05:17:28 | 结束：110 min，未冻结 | wave 12 的 group-02 另有 1 次大回包丢弃 + 重问 |

---

## 5. 待审决策（请评审时明确）

| # | 决策点 | 建议 |
|---|---|---|
| D1 | **B3 装料责任**：组 capsule/上下文是否携带 req 权威原文 | 选①（编排注入）最省事且能直接消解死锁；选②则必须同步实现 B4 拦截 |
| D2 | B1 注入范围：critic/solver 必选；MENXIA 各角色是否纳入 | 先 critic+solver，MENXIA 跑一轮观察后再定 |
| D3 | Quote 门范围：全部 evidence_updates，还是仅"代码行为性结论"；计划文档引用是否同样逐字校验 | 全部 evidence_updates（口径统一、实现最简单）；计划文档同样校验（可读） |
| D4 | 阈值口径统一方式（§3.1） | 明确字节口径 + 补字符边界样例 |
| D5 | 本方案与 2026-09-28 计划的关系 | 独立 Task 4/5 并入既有权衡，或独立立项；**建议在 prompt 切片（Task 3）上线前合并 Quote 门**——切片减少上下文后转述失真风险只会更高 |
| D6 | 与 2026-09-28 v5 计划（Fix 6/10/11）的边界 | 本方案**不覆盖**：混合裁决饿死（v5 Fix 6）、critic findings 去重（Fix 10）、证据环刹车（Fix 11）。B 修好后本轮死锁（不可满足需求）大概率不复现，但饿死机制对"真混合裁决"轮次仍是潜在风险——两份文档配套读，Fix 6 另行排期设计评审 |

## 6. 明确不做

- 不改 FSM 拓扑与转移表；不动门下省机制；不动 prompt 切片开关语义；不重写传输层 result-file 通道本体；不调整 critic 的判定逻辑（问题 A 的修复在传输闸，不在 critic 侧）。

## 7. 实施记录（2026-09-29）

已实现并全量回归（1146 tests OK，`python cmd/run_tests.py`）：

- **A 引用门**：`contracts/zhongshu_analyst.py` 新增 `_QUOTE` schema（`evidence_updates`/`finding_responses` 各含可选 `quote`，required 仅 `text`）；`adapters.py` 新增 `_quote_rejection` 并挂两处调用点（file 路径返回 None+rejections，inline 路径 raise `AgentResultFileError`），日志 `AGENT_REPLY_QUOTE_REJECTED`。校验=NFKC+空白归一包含匹配（行漂移天然通过），QUOTE_REQUIRED 仅对磁盘存在的被引文件生效，不存在的路径豁免；上限 30 行/4096 字符。
- **B1**：critic（task_review/group_review）与 solver（revise/formalize/group_revise）的 `dispatch_context` 均注入 `requirement_contract`（getattr 模式）。
- **B3①**：新增 `_requirement_contract_markdown`，组文档为空时回填 capsule 的 [Requirement document] 槽（group_review 与 group_revise 两处）。
- **B2**：三个契约各注入引用锚规则（`requirement_contract[<requirement_id>]` 锚、critic 侧先查自身 context.json、solver 侧逐字转载）。
- **下游保真**：`zhongshu_parallel.py` 的 `_compact_solver_evidence_record` 白名单加入 `quote`，Solver 证据切片保留逐字引文。
- **测试**：新增 `cmd/test_evidence_quote_gate.py`（17 例）；`test_group_prompt_feedback.py` 补 capsule 回填与 revise 契约 3 例。
- **Multica skill**：`docs/multi/runtime/` 三份 skill（analyst 逐字引用门 / critic 需求契约锚 / solver 需求原文出处）已更新，sha 锁运行时生效，无需其他动作。
- 未做：B4（需求可满足性防护）按 D1① 由 B3① 覆盖后暂缓；v5 Fix 6/10/11 另行排期（见 §5 D6）。
