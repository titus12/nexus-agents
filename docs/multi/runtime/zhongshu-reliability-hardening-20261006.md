# 中书省可靠性加固与验证记录（2026-10-06）

本次已完成代码加固、真实归档回放、1344 项安全测试、Multica 静态协议对齐，并在当前任务的人审空闲窗口重载编排器。没有代替用户批准方案，没有重写当前任务的 findings 或事件日志。源代码与协议现已生效；本次耗时收益尚未经过新的真实模型运行验证。

## 已确认的问题与修复

| 问题 | 证据及影响 | 修复及品质保护 |
|---|---|---|
| **分组 Critic 的关闭判定未写回状态，属于代码 bug** | task-20261006-c84b33 的 Critic 第 6 阶段明确 RESOLVED 两个旧 finding，但第 7 阶段仍将它们与新缺口一起派发，形成 3 个补证 worker | 只对通过派发关联校验的 Critic group_reviews 应用关闭增量。RESOLVED / 显式风险接受保留状态与说明；已关闭问题退出活动队列。归档回放只剩一个真实新缺口 |
| **拒绝草稿只有 500 字符、原因过度截断，属于重试输入缺陷** | 实际草稿 17–18k 字符，旧重试提示多数只能看到结果外壳，难以一次修完所有引用 | 内嵌草稿上限 16000 字符、原因上限 8000；完整原回复仍在 raw_reply_path。列出前 8 个详细引用错误及剩余错误标识，不增加重试次数 |
| **可机械处理的连续超长引文仍触发远端重跑，属于机制效率问题** | 第 5 阶段两份结果发生多个 QUOTE_OVERSIZE；格式修复占用一次额外模型运行 | 先完整核实原文，再拆成最多 4 个连续片段；保留全部文字、来源、作用域和片段关联，将响应 evidence_ids 展开到完整片段集合。缺失、失真、无法安全拆分的引文仍退回原 worker，不能靠截去原文过关 |
| **来源事实不能有出处地复用于另一任务，属于上下文与作用域设计缺口** | 总体优化盘点需要 prompt / concurrency 等事实，其他任务已经调查，但不能直接当作本任务已交付证据 | 派发提供最多 16 条、24000 字符的来源事实池。Analyst 以新 evidence_id、原 source_evidence_id、逐字不变的 source/quote 及本任务 item/requirement 绑定明确复用。冲突或未验证记录不进入复用池，当前引文仍重新校验；来源复用不代表其他任务的验收通过 |
| **新补证和完整引文片段可能被旧证据占满切片** | 每任务切片有 8 条上限，历史记录多时可能藏掉回答中引用的新记录 | 活动 finding 的响应 evidence_ids 优先展示；Critic 的来源池同样优先这些 ID。保留有界上下文与 quote_group_id/part_count，不扩成全仓历史大包 |
| **分组判定错误缺少各 worker 的具体反馈** | 原聚合错误统一返回，多个组容易收到混合原因 | failure.worker_rejections 按 worker 携带自己的校验原因，保留有效 peer 结果。拒绝跨组引用、归属改变、重复判定、关闭后同 ID 重提为 OPEN、静默丢弃 P0、报告新 P0/P1 OPEN 却批准等情况 |

代码集中在 `domain/policies/critic_dispositions.py`、`domain/policies/evidence_repair.py`、`domain/states.py`、`runtime/agent_effects.py`、`quote_gate.py`、`adapters.py`、`runtime/nodes.py`、证据投影和中书省注入契约。`cmd/test_zhongshu_reliability.py` 包含实际 OrchestratorApp 的脚本闭环回归，验证旧问题关闭后只派发新问题并最终收敛。

## 最新真实测试的边界

task-20261006-c84b33 由加固前的进程运行：

| 时间（北京时间） | 推进 |
|---|---|
| 15:27:28 | 提取原始需求契约 |
| 15:28:35 | 初始 Analyst 证据采集 |
| 15:33:58 | Solver 形成正式方案 |
| 15:43:35 | Critic 第一次分组审查 |
| 15:49:57 | 第一轮定向补证；两份回复因超长引用重试 |
| 15:56:37 | Critic 第二次审查 |
| 16:03:09 | 第二轮补证；两个已判 RESOLVED 的旧问题仍被错误派发 |
| 16:10:38 | 补证波次失败，进入 RETRY_WAIT |
| 16:10:39 | 自动有界恢复，只重新派发未完成部分 |
| 16:14:06 | Critic 第三次审查 |
| 16:26:21 | APPROVE_CRITIC，机械冻结通过，进入 PLAN_REVIEW 等待人审 |

中书省到送审共 **3532.454 秒（58 分 52 秒）**。此后等待用户回复不应算作中书省继续调查，也不是卡死；“通过”仍是进入门下省的唯一批准回复。

只读归档回放确认：Critic6 后活动 finding 从历史 3 个变为 1 个，下一轮补证 worker 从 3 个变为 1 个，即该轮派发数减少 **66.7%**。这不等于总耗时减少 66.7%，因为 worker 可能并行、剩余新问题仍需调查与复审。

同一轮 worker-02 的 4 个超长引用，在当前来源校验下可以保真拆分为完整记录，机械校验全部通过，无需为这些格式错误启动远端修复。worker-01 仍有一段涉及已改源码的引文不能完整核实，因此保留拒绝；没有将它自动视为完成。

**25–35 分钟是待实测目标。** 当前单次 Solver 已用 9 分 37 秒，第一次 Critic 用 6 分 22 秒，最后一次 Critic 用 12 分 15 秒。机械修复能消除无意义返工，但不能保证模型审查本身按固定分钟完成，也不能为达到目标省掉真实 P0/P1 审查。

## 恢复与中断机制审计

下列机制有既有脚本测试覆盖，并纳入本次全量测试；本次没有将有限重试改为无限重试，也没有放宽冻结与人审：

| 失效场景 | 已验证机制 / 测试 |
|---|---|
| 旧回复、串任务、分配 run 的水位变化 | request/run 关联与隔离；`test_dispatch_correlation_and_isolation.py`、`test_run_watermark_correlation.py` |
| run 已结束但结果迟到、结果永久缺失 | 有界 completed-result grace，超限失败；`test_completed_result_grace.py` |
| 远端挂起、取消失败 | 角色/派发 timeout 与关联 run 取消，取消异常不掩盖原失败；`test_dispatch_timeouts.py`、`test_remote_run_cancel_on_timeout.py` |
| 基础设施故障、格式故障、内容分歧 | 区分预算，同绑定有限重试、不同 attempt 身份；`test_infrastructure_retry.py`、`test_worker_reply_reask.py` |
| 一个 worker 失败、多个组部分完成 | salvage 已验证结果，只补剩余，批准受内容与依赖 hash 约束；`test_worker_fail_group_salvage.py`、`test_group_revise_partial_salvage.py`、`test_zhongshu_approval_ratchet.py` |
| 进程重启、提交后未 ack、重复事件 | journal/snapshot 恢复、effect 幂等；`test_linear_fsm_recovery.py`、`test_linear_fsm_persistence.py` |
| 活锁、过期锁、所有权丢失 | 原子锁回收与 fail-closed；`test_linear_fsm_locking.py` |
| 多次不收敛、无效批准 | 有界失败熔断与真实卡点记录，P0/P1 与冻结保护；`test_node_fail_brake.py`、`test_zhongshu_convergence_e2e.py`、`test_zhongshu_freeze_groups.py` |
| 人审意见、版本差异、非“通过”回复 | 同一版本文档，反馈仅 Solver 修订，再次送审；`test_human_document_loop.py`、`test_plan_review_gate.py`、`test_single_document_review.py` |

真实的外部服务不可用、运行数据缺失、需要用户决策或未解决的阻塞项，仍会进入明确失败/等待状态。可靠性要求是保留进度、可定位原因、可有界恢复，不是把这些条件隐藏为成功。

## 验证与实际生效

- 安全 runner：`rtk proxy python -X utf8 cmd/run_tests.py`，**1344 项全部通过，51.708 秒**。测试禁止真实飞书通知，无模型 token 消耗。
- `git diff --check` 通过；只出现既有 LF/CRLF 提示。
- Multica：三个 agent instructions 与三个中书省 skill 已发布并回读一致；三个门下省 skill 内容与本地一致。阶段锁、模型设置、其他绑定和附件未变化，`verification.json.ok=true`，三角色 drift 都为空。
- 2026-10-06 16:43:22：在 task-20261006-c84b33 的 PLAN_REVIEW、全部 effect 已终结的窗口重载原编排进程。旧 PID 1758240，新 PID 1777028。重新进入同一人审轮询，未传递人工答复。
- 重载前后 workflow-state.json 与 workflow-events.jsonl 的 SHA256 完全相同；仍为 HUMAN_GATE / PLAN_REVIEW / sequence 12，文档 hash 为 `47d15853da904b6922bec7f6b2c7a3c6238a022c1efc87c5ef1f8e25fcf79387`。仍关联原飞书审核消息。
- launcher PID 记录已更新为核实的新进程；Nexus `http://127.0.0.1:8766/api/health` 返回 HTTP 200 / ok。
- 没有启动新一轮真实模型测试，未代替用户批准、取消或重置既有任务。旧轮耗时不会因代码重载而被改写。

验证文件位于 `runs/maintenance/20261006-zhongshu-reliability/`：`full-tests.log`、`offline-replay.json`、`multica-sync/verification.json`、`activation-verification.json`，并保留同步前后及重载前的快照。
