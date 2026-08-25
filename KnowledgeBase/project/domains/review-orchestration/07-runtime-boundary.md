# 07 Multica、Reasonix、OpenCode 与 Nexus 边界

## 1. 当前本机运行边界

```text
Multica daemon
  -> provider=opencode
  -> bundled opencode.exe run --format json
  -> configured model/provider
  -> stdout JSON event stream
  -> Multica task message reporting
  -> issue/comment or chat
```

本机 Multica 运行实例：

```text
C:\Users\Administrator\.multica\bin\multica.exe
```

OpenCode 任务实际使用的可执行文件来自 Multica/Node 安装目录，而不是 `nexus-agents\internal\codexrouter`。

## 2. Nexus 与 Multica 的关系

`nexus-agents` 的 Python orchestrator 通过 Multica issue/comment/Agent 接口派发任务并消费结果。它不直接读取 OpenCode 的模型流。

因此：

- FSM 断言看 `cmd\orchestrator`；
- Agent 是否启动看 `daemon.log`；
- OpenCode 中间消息看 daemon reported messages/Reasonix session；
- 最终 issue 回复看 Multica issue comments；
- 不能用 Nexus codexrouter 日志替代 OpenCode 证据。

## 3. 当前已知边界缺口

Multica daemon 当前能够记录 tool call/tool result 和部分 text，但不一定持久化：

- OpenCode 原始 stdout 每一行；
- provider 请求开始/结束；
- 首 token 时间；
- stderr；
- OpenCode 退出码；
- `step_finish` 的完整 reason；
- 模型请求是否一直 pending。

因此日志缺口本身是运行时可观测性问题，不应通过修改业务 FSM 猜测解决。

## 4. 配置与源码边界

```text
C:\Users\Administrator\.config\opencode
```

主要是 OpenCode 配置/依赖，不是本次 Solver 任务日志目录。

`server/pkg/agent/opencode.go` 是 Multica 上游源码路径。若要改它，必须先获取 Multica 源码并单独构建/升级 Multica；不能在 `nexus-agents` 项目中假定它存在。
