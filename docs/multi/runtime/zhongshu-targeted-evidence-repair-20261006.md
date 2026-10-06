# 中书省定向补证收敛修复

用户批准按此前建议修复。依据任务 `task-20261006-3a0200` 的真实派发与结果归档，修复补证范围失配、引用降级后被当作完成，以及混合问题跳过 Solver 三处机制问题。

## 修改

- 定向补证需求携带任务 `item_id` 与该任务的 `source_requirement_ids`。Analyst 契约正式声明 `item_id/finding_id`。传输边界验证 finding_response 引用本次同任务同需求范围的 evidence_updates，拒绝缺失 ID、错挂需求、悬空证据 ID 和额外越界证据。fan-in 也校验范围。
- 定向补证启用 `repair_quotes_locally` 时，不合格引用及 `[UNVERIFIED-QUOTE]` 标记返回原 worker 的现有有限契约重试流程；文件回收路径同样处理拒绝。初始探索保留既有降级策略。没有增加重试预算。
- 分组 Critic 汇总保留 `required_change/missing_evidence`。有文档修改要求的补证进入 Solver 修订；纯证据请求仍直接返回 Critic。补证合并不以空任务图覆盖正式方案。
- 混合修订需求跨状态持久化；Solver 的 group_revise 派发明确提供修改要求、相关任务证据和 Analyst finding_responses。只修订受影响任务，其他组仍受既有审批与 hash 保护。
- 无证据目标的纯文档 finding 不单独占用 Analyst worker，仍留给 Solver 处理。Critic 明确用 missing_evidence 表达调查任务，required_change 仅表达补证后需要的文档修改。

## 真实归档回放

回放只读取归档，未推进或重写生产状态。

| 轮次 | 新机制判定 |
|---|---|
| Analyst 7 | `w01-evid-001` 缺少 `ITEM-003` 绑定，拒绝；归档中已有失真引用降级标记，定向补证不能据此宣称完成。 |
| Analyst 9 | `w09-evid-001` 的 requirement_id 不属于 `ITEM-003` 的 `REQ-003`，拒绝；同样拒绝未验证引用。 |
| Analyst 11 | 范围及引用检查通过；应进入 Solver，而非再次复审未改动文档。 |
| Critic 6 / GRP-002 | 真实 required_change 要求对齐 §§3、4、8 并补候选项风险验收；现在保留并明确触发 Solver 修订。 |

回放文件：`runs/maintenance/20261006-targeted-evidence-repair/offline-replay.json`。
归档引用已经经过旧版本降级，回放报告对降级标记的拒绝不等于重新验证其原始引用文本。

## 验证与同步

新增回归覆盖范围错配、悬空 ID、UNKNOWN、引用原 worker 重试、降级标记防绕过、持久化恢复、方案保留、纯证据直达，以及真实 OrchestratorApp 的混合问题“补证 → Solver → Critic”完整流转。

所有测试统一走 `python cmd/run_tests.py`，禁用外部通知，无实时模型调用。最终 **1328 项测试全部通过（49.451 秒）**，结果保存在 `runs/maintenance/20261006-targeted-evidence-repair/full-tests-final.log`。

Multica 三个 agent instructions 与中书三份 skill 已发布，门下三份 skill 回读一致。同步报告 `multica-sync/verification.json`：`ok=true`、全部 `drift=[]`、模型及运行配置保护字段未变、运行中任务未修改。

## 生效范围与限制

代码修复落盘，需由新启动的 Orchestrator 进程加载；已经加载旧模块的进程不会自动热替换。Multica 静态指令从下一次派发生效，历史 prompt bundle 不回写。

读取生产状态时，本轮已在 14:34:35 进入 `HUMAN_GATE / ZHONGSHU_STUCK_FINDING`，不是正常 `PLAN_REVIEW` 方案审批。未替用户批准或恢复该任务，未停止或重绑 8766 服务。

此修复消除已证实的无效补证和漏修订路径，但不能证明模型端缺失交付已解决。首轮 Solver 的 AGENT_RESULT_MISSING 属于另一条交付问题，仍需单独追踪；25–35 分钟目标尚未得到修复后实时运行验证。
