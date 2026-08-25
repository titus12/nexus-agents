# 中书省与门下省方案审议机制

## 1. 适用范围

本域记录 `nexus-agents` 项目中由 `review_orchestrator_v2.py` 驱动的双阶段方案审议系统：

- **中书省（ZHONGSHU）**：澄清需求、建立证据、拆分任务、组织候选组、形成冻结方案。
- **门下省（MENXIA）**：不修改业务代码，只对每个任务的具体实现方案进行逐项双视角审查。
- **Analyst**：正向审查，使用第一性原理检查正确性、可行性、覆盖度和证据充分性。
- **Solver**：根据上游输入起草或修订方案；门下省中负责补充每个任务的代码实现方案。
- **Critic**：反向/对抗性审查，寻找范围膨胀、遗漏、不可验证、风险和门禁问题。

## 2. 规范来源

1. [Multi-Agent Solution Review Protocol](../../../../docs/multi/multi-agent-solution-review-protocol.md)
2. [FSM Refactor Design v3.1](../../../../docs/2026-08-23-review-orchestrator-fsm-refactor-design-v3.1.md)
3. [FSM Refactor Design](../../../../docs/2026-08-23-review-orchestrator-fsm-refactor-design.md)
4. `D:\workspace\src\nexus-agents\cmd\orchestrator\` 当前实现

## 3. 章节导航

- [01 项目边界与角色职责](01-project-scope-and-roles.md)
- [02 中书省协议](02-zhongshu-protocol.md)
- [03 门下省协议](03-menxia-protocol.md)
- [04 FSM 与状态数据契约](04-fsm-contract.md)
- [05 代码目录地图](05-code-map.md)
- [06 日志、持久化与故障诊断](06-logging-and-diagnostics.md)
- [07 Multica/Reasonix/OpenCode 边界](07-runtime-boundary.md)
- [08 测试、恢复与运行手册](08-testing-and-recovery.md)

## 4. 维护规则

- 方案协议或状态迁移发生变化时，先更新规范文档，再更新本域。
- 代码目录变化时，更新 `05-code-map.md`。
- 新增故障必须写入 `06-logging-and-diagnostics.md` 的故障证据表。
- 本知识域记录“项目事实”和“已验证结论”，不把推测写成事实。
- `task_id`、`request_id`、`role`、`phase` 由 transport/orchestrator 绑定，不要求 LLM重复生成。
