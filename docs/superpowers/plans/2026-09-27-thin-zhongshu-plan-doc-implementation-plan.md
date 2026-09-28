# 中书省瘦身与方案文档产出实施方案

> 需求来源：2026-09-27 对话定稿——中书省产出定位为"给门下省的施工图目录"，读者是人，第一标准是人能快速读懂并拍板，机器可校验只是手段；验收从测量配方改为场景断言，"怎么测"整体下沉门下省。
> 本方案设计选择已定稿（含 8 段文档骨架），实施按任务顺序执行。

**Goal：** 中书省收官（冻结检查通过）时产出一份人类可读的《方案文档》（`zhongshu-plan-review.md`，8 段骨架），走人工预审闸，批准后移交门下省；组文档验收体裁从"五要素测量配方"改为"场景断言"；critic 审查标准从"配方完整性"转向"需求可判定性"。文档体积目标：单份方案 ≤ 3 屏（当前三份组文档合计 ~11KB 密集正文）。

**Architecture（定稿决策）：**

- **方案文档（新增，中书唯一人类出口）**：新模块 `cmd/orchestrator/zhongshu_plan_doc.py`，`render_plan_review_doc(context) -> str`，固定落盘 `runs/<task_id>/zhongshu-plan-review.md`。数据源全部来自 review state（requirements、task_items/task_groups、zhongshu_groups 约束与口径、unknowns/risks、findings 处置记录）+ 请求原文——**零新状态字段**。8 段骨架（预审稿已按此验证）：
  0. 任务头（任务/来源/交付物/中书审定/待办 + 预审批注区）
  1. 背景（现状 + 问题清单，禁修订史）
  2. 目标（交付物要回答的问题，含"估算不得冒充实测"纪律）
  3. 边界（范围内/外 + 组间划分）
  4. 约束（不变量 + 非目标 + **口径定义小表**）
  5. 任务拆解（表：任务/做什么/来源需求/依赖 + 规模 大中小 + 建议顺序）
  6. 验收（**打勾断言清单**，按组分小节；审阅时逐条勾）
  7. 开放问题与待决事项（**需人工拍板** / **待实测** 两类分列）
  8. 风险与关键取舍（风险 2-4 条带缓解方向；取舍 2-3 条写清"为何这样拆、考虑过什么备选"）
  9. 交接清单（交门下省带什么：方案全文 + 证据包 + 约束条款原样带入）
- **人工预审闸（新语义，默认开）**：冻结批准（`FREEZE_APPROVED`）后不再直接进 `MENXIA_GROUP_SOLVER`，先入 `HUMAN_GATE`（复用现有状态与飞书通道），gate 消息附方案文档路径与摘要。飞书批准 → `MENXIA_GROUP_SOLVER` 交接；批注/拒绝 → 回 `ZHONGSHU_SOLVER` 修订（批注文本作为 finding 注入）。开关 `parallel.zhongshu.plan_review_gate: bool = True`；关闭时保持现有直通行为。FSM 边改为 `ZHONGSHU_FREEZE_CHECK --FREEZE_APPROVED--> HUMAN_GATE --PLAN_REVIEW_APPROVED--> MENXIA_GROUP_SOLVER`，`resume_state` 记录交接目标。
- **验收体裁改造（§8 瘦身核心）**：`acceptance_standards.py` 验收清单改为三条"场景断言"标准：①每条信号是一个可打勾断言（给定-当-则或简单谓词）；②对比性数值结论二选一——实测数字（注明来源）或"待实测"；③易混口径必须出现在 §4 口径定义表。`ACCEPTANCE_SIGNAL_UNVERIFIABLE` 闸门**只保留**②的强制（防假测量的核心事故类），放弃五要素格式强制。**五要素测量配方整体迁往门下省侧**（`menxia-solver-skill` / 实施计划要求），中书不再管"怎么测"。
- **九节组文档内容瘦身（骨架不动）**：§1 禁写修订史（升版信息只在标题行 `[vN]`）；§8 强制短句 bullet 化（单行一个断言，禁止 300 字长段）；§4 新增"口径定义"子节（术语 3-5 行小表）。九节顺序、`### <item_id>` 小节、§8↔acceptance_signals 双向闭合、版本算术、冻结区逐字节校验**全部保留**（投影/闭包/棘轮机制不破）。以 solver skill 模板更新为主，机械校验仅加"§8 单行长度上限 300 字符"一条。
- **审查标准转向**：`zhongshu_critic` 契约 prompt_rules 删"五要素配方缺一打回"条款，改为四条审查轴：**需求可判定性、场景覆盖完整、边界无歧义、口径一致**；纯测量配方类问题降级为提示、不再开 finding。`runtime/zhongshu-critic-skill.md` 同步改写审查目标段。
- **solver skill 同步**：`runtime/zhongshu-solver-skill.md` §8 说明改为"场景断言、逐字闭合"；配方要求移除并指向门下省；新增"口径定义写入 §4"。
- **规模/顺序字段来源**：任务规模（大/中/小）由 solver 在 plan item 新增可选字段 `estimate`（枚举 S/M/L，缺省 M），实施顺序由依赖拓扑 + 组划分机械推导，均不新增人工步骤。

**Tech Stack：** Python（cmd/orchestrator），unittest，`python cmd/run_tests.py`（runner 强制 NullNotificationPort）。

**不改动：** 组流水线机制全部（裁决投影、组面棘轮、组内 patch 冻结区、§8 闭包投影、组胶囊增量、partial salvage、预算与停滞记账）；analyst 证据 lenses 与 v3.3 路由；finding 的 item 坐标与处置生命周期；门下省机制；传输（multica/prompt bundle/结果文件）与飞书通知通道；FSM 状态名与既有顶层边（只改 FREEZE_APPROVED 的目标）；fast_track；九节骨架与版本链。

---

## 0. 现状与目标行为表

| 场景 | 当前行为（已核实） | 目标行为 | Task |
|---|---|---|---|
| 中书人类出口 | 三份九节组文档（g1 v3 4KB/g2 v3 5.7KB/g3 v1 1.5KB），§8 长段测量配方，阅读成本高 | 一份 8 段方案文档 ≤3 屏，验收=打勾断言 | 4 |
| 人工审阅时机 | 无；冻结批准直通门下省 | 冻结后人工预审闸（可关） | 5 |
| 验收体裁 | 五要素测量配方（验证方法/基线/单位/补采信号），每信号 300+ 字 | 场景断言短句；配方下沉门下省 | 1 |
| 审查火力 | critic 按配方完整性开 finding（c0cd83 首轮 12 条几乎全是测量口径） | 四审查轴：可判定/场景/边界/口径；配方问题不开 finding | 3 |
| 假测量防护 | ACCEPTANCE_SIGNAL_UNVERIFIABLE 闸（配方强制） | 保留"对比数值必须实测或标待实测"强制 | 1 |
| 文档卫生 | 修订史混写 §1 背景；§8 单段 300 字 | 修订史只在标题行；§8 单行 ≤300 字符 bullet | 2 |
| 口径争议 | findings 大类（28KB vs 31548 字节基线、worker-seconds vs 墙钟） | §4 口径定义表前置钉死，争议不进 findings | 2/3 |

## 1. Task 1：验收体裁改造（场景断言 + 配方下沉）

**Files：** `cmd/orchestrator/acceptance_standards.py`、`cmd/orchestrator/domain/zhongshu_doc.py`（gate 调用点）、`docs/multi/runtime/zhongshu-solver-skill.md`（§8 段）、`docs/multi/menxia-solver-skill.md`（接收五要素配方要求）。

**Steps：**

1. `ZHONGSHU_ACCEPTANCE_CHECKLIST` 重写为三条场景断言标准（可打勾 / 数值二选一 / 口径入表）；原第 1-8 条中"逐条核验、度量口径、证据来源、UNKNOWN"保留语义但压缩措辞，"验证方法五要素"条款整体删除并移入 menxia skill。
2. `acceptance_signal_gate` 保留，判定条件收窄为：对比性数值结论（`_MEASUREMENT_CLAIM_MARKERS` 命中）无实测来源且未标"待实测/UNKNOWN"才拒；删除配方五要素存在性要求。
3. `acceptance_standard_applies_to` 名单不动（analyst/solver 产出时携带）。
4. menxia-solver-skill 增"实施计划须含测量配方（五要素）"章节（承接下沉的职责）。

**Verify：** `cmd/test_acceptance_standards.py` 重写断言（三条清单、gate 只拦假对比数值）；`python cmd/run_tests.py` 全绿。

## 2. Task 2：九节文档内容瘦身

**Files：** `cmd/orchestrator/domain/zhongshu_doc.py`（新增 §8 行长上限校验）、`docs/multi/runtime/zhongshu-solver-skill.md`（模板段落重写）。

**Steps：**

1. solver skill §"组需求文档"：§1 禁修订史（升版只在标题 `[vN]`）；§4 增"口径定义"子节模板（术语表 3-5 行）；§8 改为"每行一条打勾断言，单行 ≤300 字符"。
2. `doc_violation_details` 增一条形态校验：§8 单行超过 300 字符 → `acceptance_signal_too_long:<item_id>`（带期望 diff 反馈，与既有反馈同格式）。
3. 口径定义表不做机械校验（skill 约束即可，避免模板过度硬化）。

**Verify：** `cmd/test_zhongshu_doc.py` 增行长上限用例；既有九节/闭合/版本测试不动且全绿。

## 3. Task 3：critic 审查标准转向

**Files：** `cmd/orchestrator/contracts/zhongshu_critic.py`（prompt_rules）、`docs/multi/runtime/zhongshu-critic-skill.md`。

**Steps：**

1. prompt_rules 删"五要素配方缺一打回"条款；替换为四审查轴条款（可判定性/场景覆盖/边界歧义/口径一致性），并明确"测量配方缺失不开 finding，数值结论未实测也未标待实测才开"。
2. critic skill 审查目标段同步改写；"配方评估"小节改为"数值结论二选一检查"。

**Verify：** 契约类测试断言 prompt_rules 含四轴、不含五要素条款；全量回归。

## 4. Task 4：方案文档生成器

**Files：** 新增 `cmd/orchestrator/zhongshu_plan_doc.py`、`cmd/orchestrator/domain/states.py`（冻结通过决策点挂产物与通知）、`cmd/test_zhongshu_plan_doc.py`（新增）。

**Steps：**

1. `render_plan_review_doc(context) -> str`：按 8 段骨架从 review state 取数渲染；口径定义表从 zhongshu_groups 文档 §4 解析（或从 item unknowns 简化提取，实现时取最小可用来源）；开放问题=unknowns + 未决 findings；风险/取舍=review.risks + plan 级 rationale 字段（无则该段写"（无）"，不虚构）。
2. 冻结通过决策点：写 `runs/<task_id>/zhongshu-plan-review.md`，`PLAN_ARTIFACT_WRITTEN` 同类日志（`ZHONGSHU_PLAN_DOC_WRITTEN`），飞书摘要通知附路径。
3. 文档不进 prompt bundle（纯人类产物，不给 worker 读）。

**Verify：** `test_zhongshu_plan_doc.py`：骨架九段齐全、断言清单含打勾、无修订史、体积上限（>15KB 失败）；e2e 断言冻结后文件存在。

## 5. Task 5：人工预审闸

**Files：** `cmd/orchestrator/domain/transitions.py`（边）、`cmd/orchestrator/domain/states.py`（FREEZE_APPROVED 决策 + HUMAN_GATE resume 路由）、`cmd/orchestrator/domain/policies/parallel.py`（开关）、`cmd/orchestrator/app.py`（配置注入）。

**Steps：**

1. `parallel.zhongshu.plan_review_gate: bool = True`；env 覆盖 `ZHONGSHU_PLAN_REVIEW_GATE=0/1`。
2. 开启时：`FREEZE_APPROVED` → `HUMAN_GATE`（gate 文案含方案文档路径 + 三行摘要：任务数/组数/开放问题数）；飞书批准动作 → `PLAN_REVIEW_APPROVED` → `MENXIA_GROUP_SOLVER`；拒绝/批注 → `PLAN_REVIEW_REJECTED` → `ZHONGSHU_SOLVER`（批注入 findings）。
3. 关闭时：保持 `FREEZE_APPROVED -> MENXIA_GROUP_SOLVER` 直通。

**Verify：** `test_linear_fsm_entrypoint.py` 增两分支（开/关）迁移断言；人工闸批准后 `MENXIA_GROUP_SOLVER` 绑定出现。

## 6. Task 6：测试迁移与全量回归

**Steps：**

1. 迁移被新语义影响的断言：验收清单条数/文案、critic prompt_rules、`ACCEPTANCE_SIGNAL_UNVERIFIABLE` 判定（旧五要素用例改为"待实测"二选一用例）。
2. 新增：plan doc 渲染、闸门两分支、§8 行长上限、假对比数值拦截。
3. `python cmd/run_tests.py` 全绿（当前基线 995）。

## 7. Task 7：协议/skill 一致性复查

**Steps：** 按 2026-09-27 上午的核对法复查一遍：契约 prompt_rules ↔ runtime skill ↔ acceptance_standards ↔ menxia skill（配方承接处）↔ 需求文档无残留五要素强制表述；发现冲突即改。

---

**验收（整体）：**

- [ ] 一轮真实手动测试（`multica\start_review_orchestrator.ps1 --new`）收官后产出 `zhongshu-plan-review.md`，人工读感为目标 8 段、≤3 屏；
- [ ] 闸门默认卡在方案文档预审，飞书批准后才进 `MENXIA_GROUP_SOLVER`；`ZHONGSHU_PLAN_REVIEW_GATE=0` 时直通；
- [ ] 组文档 §8 为短行断言体裁，单份组文档正文较现版减半以上；
- [ ] critic findings 不再出现纯测量配方类条目；
- [ ] `python cmd/run_tests.py` 全绿。
