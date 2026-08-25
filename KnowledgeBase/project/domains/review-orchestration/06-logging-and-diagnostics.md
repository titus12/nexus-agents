# 06 日志、持久化与故障诊断

## 1. 本地 orchestrator 日志

```text
D:\workspace\src\nexus-agents\cmd\logs\
```

保存 Python 编排器日志；运行状态和审计通常在：

```text
D:\workspace\src\nexus-agents\cmd\runs\<task-id>\
  state.json
  events.jsonl
  artifacts\
```

## 2. Multica daemon 日志

```text
C:\Users\Administrator\.multica\daemon.log
```

用于确认：

- Agent 是否被唤醒；
- provider/agent/model；
- OpenCode 子进程命令；
- tool call/tool result；
- reported task messages；
- idle watchdog；
- 最终 task status 和 `output_bytes`。

常用命令：

```powershell
multica daemon status
multica daemon logs -n 200
multica daemon logs -f
```

## 3. Reasonix session

```text
C:\Users\Administrator\.multica\reasonix-state\
```

常见文件：

```text
sessions\*.jsonl
sessions\*.events.jsonl
sessions\*.acp.json
sessions\*.recovery.json
stats\YYYY-MM-DD.jsonl
```

这里可以看到 Agent 的 user、assistant、tool call、tool result 和最终 content。

## 4. 必须记录的 FSM 事件

```text
STATE_ENTER
DISPATCH_INTENT
DISPATCH_CONFIRMED
AGENT_REPLY_RECEIVED
AGENT_REPLY_ACCEPTED
AGENT_REPLY_REJECTED
TIMEOUT
HEARTBEAT
HUMAN_GATE_CREATED
HUMAN_REPLY_RECEIVED
FINDING_OPENED
FINDING_ASSIGNED
FINDING_RESOLVED
STATE_TRANSITION
STATE_EXIT
```

## 5. Multica/OpenCode 边界诊断

对于 OpenCode 任务，至少要有：

```text
process_start
command
stdout_line/raw_event
parsed_event
stderr_line
tool_call
tool_result
last_event_at
process_exit
watchdog_state
```

如果只能看到：

```text
tool_result observed
```

之后长时间没有消息，则结论只能写成：

```text
OpenCode/模型边界之后无新事件
```

不能直接写成：

```text
JSON 解析失败
```

## 6. 已验证故障证据

### Solver idle watchdog

任务 `e7950341` 的证据：

- OpenCode 启动成功；
- 11 次工具调用均有返回；
- 最后一次工具结果后没有新消息；
- `tool_in_flight=false`；
- `output_bytes=0`；
- `status=idle_watchdog`；
- 最终状态 `blocked`。

结论：没有最终 Solver JSON，问题发生在工具结果之后的 OpenCode/模型继续推理阶段，不能归因于 orchestrator JSON 过滤。

### Analyst 正常交付

任务 `9541ab16` 的证据：

- `status=completed`；
- `output_bytes>0`；
- issue 已收到 `READY_FOR_SOLVER`；
- payload 包含 evidence/findings/candidate groups/items。

## 7. 诊断原则

- 先看 task ID，再看 agent ID，再看 request/phase。
- 先证明“是否启动”和“是否有中间事件”，再判断“是否最终回复”。
- 任何“被过滤/解析失败”结论必须有 raw payload 或 parser reject 日志。
- daemon、Reasonix、orchestrator 三层日志不能混用。
