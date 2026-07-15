# 工作流与角色激活

工作流入口由 `.agents/skills/wf-*` 目录中的 Codex skills 维护。
每个 `wf-*` skill 自己声明对应的 `.claude/workflows/*.md` source of truth。

本文不维护 workflow 路由表，只保留通用路由原则与全局底线。

## 路由原则

1. 用户显式调用 `$wf-*` skill 时，按该 skill 指向的 workflow 文件执行。
2. 未显式调用 `$wf-*` skill 时，Codex 根据可用 skills、AGENTS.md、rules 和用户请求自行选择合适流程。
3. 任务模糊时先澄清；不要因为缺少 workflow 入口而猜测执行路径。
4. 新增 workflow 时，优先新增对应 `.agents/skills/wf-*` 入口，而不是在本文维护路由表。

## Go 业务变更

- 新功能、既有行为修改和跨文件业务调整统一使用 `$wf-go-feat`。
- 任务拆分默认由主线程串行执行；仅当用户明确要求并行、分工或 delegation 且写入范围不重叠时，才使用 subagent。
- 修改前必须先加载适用 Rules、知识库 routing 和真实代码证据，并生成待审核目标契约。

## 全局底线

- 声称“已修复 / 已完成 / 测试通过”前，必须有实际验证证据。
- 修 bug 时先复现并定位根因，禁止猜测性修改。
- 高风险改动按 `high-risk-api` skill 报备或确认。
- 子代理 / 并行执行只在用户明确要求 subagent、parallel 或 delegation 时启用。
