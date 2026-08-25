# 08 测试、恢复与运行手册

## 1. 本地单元测试

在项目目录执行：

```powershell
cd D:\workspace\src\nexus-agents\cmd
python -m unittest discover -s . -p "test_*.py"
```

重点覆盖：

- FSM core；
- full workflow；
- timeout/heartbeat；
- Multica poll/filter；
- assignment；
- P1/P2 gate；
- JSON BOM；
- process interruption；
- notification dedupe。

## 2. 新一轮真实测试前检查

1. Multica daemon：

```powershell
multica daemon status
```

2. 跟踪日志：

```powershell
multica daemon logs -f
```

3. 确认当前 task 的：

```text
issue_id
task_id
agent_id
role
phase
request_id
```

4. 测试完成后同时检查：

```text
daemon.log
issue comments
cmd\runs\<task-id>\state.json
cmd\runs\<task-id>\events.jsonl
```

## 3. 恢复原则

- `--resume <task-id>`：恢复本地 FSM，不等于重新启动已经被 Multica 杀掉的 Agent。
- 若 Multica execution 已被 idle watchdog 终止，需要重新派发/重跑 Agent，再由 orchestrator 消费结果。
- 迟到回复必须按 `task_id/request_id/author_id` 校验，不能按时间盲收。
- 已有 `frozen_plan` 时，不能重新从需求 intake 开始覆盖状态。

## 4. 最小复现测试

为定位 OpenCode 卡顿，先用短任务：

```text
只读取 Analyst 交付内容，输出最小 Solver 结构化 JSON。
不要扫描整个项目，不要执行长时间工具，不要修改文件。
```

观察：

```text
OpenCode start
tool call/result
model/text event
final structured reply
```

短任务成功、长任务失败时，优先检查上下文长度、工具链和模型请求超时；短任务也失败时，优先检查 OpenCode/provider/runtime。

## 5. 通过标准

一次流程只有在以下条件同时满足时才算完成：

- FSM 到达 `DONE`；
- 所有正式 item/group 均通过；
- P0/P1 无 active finding；
- 最终方案和审查记录已持久化；
- issue/comment 有最终结构化结果；
- `daemon.log` 有 Agent completed 而不是 watchdog；
- 测试结果可回放。
