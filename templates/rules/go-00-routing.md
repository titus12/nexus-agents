# 工作流与角色激活

用户消息以 `--xxx` 开头时，按下表加载对应 workflow 文件，剩余内容作为输入。

本文件只维护项目级 **trigger → workflow** 索引；具体流程、阶段、验证和分工以对应 workflow 文件为准。

## 路由表

| Trigger | Workflow |
|---|---|
| `--feat` | `.claude/workflows/go-feature-development.md` |
| `--mod` | `.claude/workflows/go-modify-existing.md` |
| `--bug` | `.claude/workflows/go-bugfix.md` |
| `--rev` | `.claude/workflows/go-code-review.md` |
| `--design` | `.claude/workflows/design.md` |
| `--ask` | `.claude/workflows/research.md` |
| `--commit` | `.claude/workflows/commit-gate.md` |
| `--refactor` | `.claude/workflows/go-refactor.md` |
| `--lark` | `.claude/workflows/lark-integration.md` |
| `--subagents` / `--parallel` | `.claude/workflows/subagent-driven-development.md` |

## 路由规则

1. 匹配到 trigger 后，加载路由表中的 workflow 文件并执行。
2. workflow 的具体步骤、触发说明、适用场景、验证要求以 workflow 文件为准。
3. 本文件不承载具体 workflow 内容，不从本文件展开 Project Workflow 副本。
4. 无 `--xxx` 前缀时，按正常 agent / rule / skill 路由处理；任务模糊时先澄清。
5. 新增 workflow 后，只有需要命令触发时才在本文件注册 trigger。

## 全局底线

- 声称“已修复 / 已完成 / 测试通过”前，必须有实际验证证据。
- 修 bug 时先复现并定位根因，禁止猜测性修改。
- 高风险改动按 `high-risk-api` skill 报备或确认。
- 子代理 / 并行执行只在用户明确要求 subagent、parallel 或 delegation 时启用。
