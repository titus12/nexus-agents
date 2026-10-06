# 中书省交接与门下省流程对齐（2026-10-06）

此次对齐覆盖编排代码、三个共享 agent 的指令、本地门下省 skill 和 Multica 上对应 skill。
采用现有组级共享文档机制，保留人工需求审批、证据审计和对抗审查，不另建一套流程。

## 实际推进流程

| 顺序/状态 | 负责方与产物 | 推进条件与回路 |
| --- | --- | --- |
| 中书省收敛 | Analyst 提取需求/补证；Solver 形成组需求；Critic 检查并冻结 | 冻结方案生成一份版本化 canonical requirements document |
| PLAN_REVIEW 人工审查 | 用户审同一份需求文档 | 精确回复“通过”才进入门下省；其他非空内容交 Solver 修订，再回人工审，跳过 Critic/Analyst |
| MENXIA_GROUP_SOLVER | Solver 为当前就绪组全部成员写一份门下省正文 | 首次 v1，六个固定正文小节完整；成功先到 Analyst；结构错误走组内格式修复 |
| MENXIA_GROUP_ANALYST | Analyst 检查同一文档的证据、需求及成员覆盖 | 只追加建议；证据充分到 Critic；正文问题或补充需求回 Solver |
| MENXIA_GROUP_CRITIC | Critic 检查反例、约束、风险和验收闭合 | 缺证据回 Analyst；实质缺陷回 Solver；当前版本 Analyst 已通过且无阻断，才批准本组 |
| MENXIA_GROUP_GATE | Critic 按单独 Gate 契约核对组结果与跨组关系 | 就绪后汇总；必要时定向组修订/继续中书省交接；所有成员满足要求后完成 |

组间依赖由编排器调度，不由 agent 自行更改；同组内部顺序为 Solver → Analyst → Critic。
批次按可执行阶段推进，已完成的其他组不会因为一个组格式修复而重新启动。
旧 MENXIA_ITEM_* 状态仅消费对应条目契约；三个新 skill 已说明按派发状态分流。

## 文档与责任边界

人工审查和门下省消费的是同一个已经批准的需求版本/hash。门下省按组写的细化/审查正文
是内部工作产物，不是第二份替代需求。保存完整需求依据，按 group_id 提取所负责范围。

门下省正文统一六节：目标与设计决策、现状与目标行为、统一约束、任务分解、验收映射、风险与兼容。
代码的 BODY_PART_TITLES 是骨架与 Solver 契约提示的共同来源，派发同时携带 body_part_titles。
各节必须有内容，ITEM 子节放在 #### 层级；不得用 ITEM 标题替代固定六节。

| 文档区域 | 允许操作 |
| --- | --- |
| 冻结基线 | 三个 worker 均不得改写 |
| 正文 | Solver 填写/修订；后续每次升一个版本并声明 touched_scope |
| 建议 | Analyst/Critic 追加自己的稳定编号；Solver 按编号吸收或不采纳，不能静默删除 |
| 吸收/处置台账 | 保留历史；吸收须有真实正文变化；作者确认反驳的闭合由编排器记录 |
| 签收 | 编排器根据有效审查结果写入当前版本签收，worker 不伪造 |

首轮指导保留 v1；为兼容旧适配器，代码也接受旧式首轮 v2。后续修订严格 +1；reviewer 保持版本。
reviewed_version/hash 对应派发原文，而不是追加建议后的文档。新契约要求填写这些字段；
兼容旧回复字段缺省时仍以派发绑定及受保护分区验证为准，显式错误值不会通过。
文档 parse/render 的签收状态现已稳定，备注中的“通过”不会被误判成批准。

Solver 不采纳 P0/P1 时保留 rejected-pending。原作者用 confirmed_rejection_ids 明确接受反驳，
或用 reaffirmed_rejection_ids 坚持原意见；两个数组只允许自己的待确认编号。
编排器应用状态变更、记录台账和签收；原作者无需改写受保护分区。不能用模糊“已处理”关闭阻断。

## 昨晚故障及修复

来源：task-20261005-498c2f / GRP-001，首次 Solver 派发序号 9。
19:34:20 中书省冻结送审，20:03:09 用户“通过”，20:03:10 门下省 Solver 开始，
20:13:17 返回 READY_FOR_CRITIC，20:13:18 缺六小节被标 BLOCKED，20:13:19 转人工。
Analyst、Critic 和依赖组 GRP-002 均未开始；因此不是门下省审查拒绝了方案。

| 问题 | 改动 |
| --- | --- |
| 本地与线上 skill 同步，但都以条目方法为主，和运行的组文档协议不一致 | 三份 skill 改为组流程主协议，保留角色方法与旧状态分流 |
| 骨架正文为空，校验却要求六小节；首轮版本提示互相冲突 | 默认提供固定六节，契约/派发复用标题定义；明确首轮与后续版本规则 |
| 正文结构错误立即 BLOCKED 转人工 | 同组 Solver 有界修复，携带错误与拒绝草稿，权威文档不变 |
| joiner 给阶段调度传入 group_id→stage，而调度函数需要 stage→count | 聚合前转为阶段计数，混合成功/待修复批次能继续正确阶段 |
| 状态重建丢失 requirement_markdown | 用 replace 保留需求依据及新增恢复字段 |
| Solver 的 READY_FOR_CRITIC 可以跳过证据审计 | 组级成功回复归一到 READY_FOR_ANALYST |
| 签收模型未接执行链，审查版本/hash 未核对 | 接入签收、显式回显校验及当前版本 Analyst 通过守卫；Solver 伪造签收仍阻断 |
| 作者无法在不改受保护建议段的情况下确认不采纳 | 增加明确编号处置字段，编排器确认并记账 |
| 组级真实问题未透传到聚合结果 | 带原因的具体问题透传，保留权威文档；Solver 空问题请求进入有界修复 |
| 同批组提示复用了首组成员数量 | 每个组独立计算成员数量 |
| 审查需求被旧 skill 强制扩张成实现规格 | 按需求范围区分审查报告与实现设计，保留可实施/可证伪的质量要求 |

## 纠错和人工门

可修复项：缺/空固定小节、顺序错误、未声明 touched_scope，以及 Solver 空人工问题请求。
组状态保存 reply_retry_count、reply_rejection、rejected_doc_markdown；默认最多三次额外修复，
与内容 revision_round 分开。初次回复加三次修复仍失败，就明确阻断。

成功后清除恢复字段；拒绝草稿仅供修订参考，不占权威版本，不重跑已完成组。
身份、冻结基线、历史台账或签收篡改不自动放行。实质修订预算与停滞守卫继续生效。
真实 HUMAN_GATE 要有具体问题及原因，可在完整初稿形成前提出；不接受草稿中的文档变更。
系统阻断/预算耗尽仍可能转人工，不能把这类人工门等同于“请再次批准中书省”。

## 本地与 Multica 对应关系

| 角色 | Agent ID | 门下省 Skill ID | 本地权威文件 |
| --- | --- | --- | --- |
| Analyst | 71dce355-1ede-4a56-9969-9be4420d5fbc | baa3eb30-bcef-4a7c-818a-4146c4e8b793 | docs/multi/menxia-analyst-skill.md |
| Solver | 4d0e9fab-769f-4b0f-a8a3-57a170c477eb | bef10843-5fcf-4064-bf8b-4f05c8533b76 | docs/multi/menxia-solver-skill.md |
| Critic/Gate | 1a9139c5-be70-4a3b-a943-a3ff8b27b880 | c1acb9bb-f692-4af4-b5bb-af6799490112 | docs/multi/menxia-critic-skill.md |

三份 agent 指令位于 docs/multi/runtime/agent-instructions，保留中书省与人工审查规则。
同步使用 cmd/sync_multica_review_agents.py；验证三角色指令、两阶段技能内容及中书省附件，
并检查模型、运行配置、技能绑定、门下省附件和共享语言 skill 未变化。
发布前后完整快照与回读 hash 记录在 runs/maintenance/20261006-menxia-alignment/multica-sync（初次）与 multica-sync-final（最终）。

## 验证与生效边界

安全回归入口为 python cmd/run_tests.py，关闭外部通知，使用 scripted adapters，不调用真实模型。
新增回归覆盖六节骨架、空正文拒绝、组内修复、预算耗尽、其他组不重跑、需求持久化、
签收读写、过期审查拒绝、反驳确认权限、真实问题透传及实际旧回复离线重放。
全套 1306 项测试通过（48.283 秒）。验证记录在 runs/maintenance/20261006-menxia-alignment，
线上三角色回读均为 drift=[]、menxia_matches_local=true，保护设置和门下省附件未改变；
详见该目录的 multica-sync-final/verification.json。

线上 skill/指令在下一次派发生效；已锁定的 prompt bundle 不因线上静态配置变化而改变。
本地机制代码需运行实例重新加载后才生效。本次没有重启 8766，也没有手改/恢复昨晚任务的
workflow-state.json；离线重放记录生产状态前后 hash 相同。旧人工暂停仍需单独受控恢复。
脚本验证证明机制能收敛，不代表已测得真实 LLM 完成时间。
