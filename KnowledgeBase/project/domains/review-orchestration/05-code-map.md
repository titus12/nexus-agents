# 05 代码目录地图

## 1. 入口

```text
D:\workspace\src\nexus-agents\cmd\review_orchestrator_v2.py
```

当前入口是轻量 wrapper，实际实现位于：

```text
D:\workspace\src\nexus-agents\cmd\orchestrator\
```

## 2. orchestrator 模块

| 文件 | 职责 |
|---|---|
| `app.py` | CLI、FSM 主循环、任务创建/恢复/取消、payload 消费 |
| `states.py` | 各状态的 enter/update/exit 和 Agent 派发 |
| `state_machine.py` | 当前状态驱动、事件处理、持久化协调 |
| `transitions.py` | 合法状态迁移和业务路由 |
| `models.py` | `AgentRequest`、`AgentBinding`、`Finding`、外部消息和人审模型 |
| `context.py` | `StateContext` 持久化上下文 |
| `events.py` | FSM 事件模型 |
| `validators.py` | author/task/request/role/phase/action 校验 |
| `adapters.py` | Multica CLI、Issue comment、Feishu、外部 Agent 消息轮询 |
| `persistence.py` | state.json、events.jsonl、artifacts 的原子持久化 |
| `recovery.py` | 恢复、schema、sequence 和中断处理 |
| `locks.py` | task lock 和进程互斥 |
| `policies.py` | 超时、重试、严重度和门禁策略 |
| `notifications.py` | 角色化飞书/Issue 通知、人审提示 |

## 3. 测试地图

```text
cmd/test_fsm_core_v31.py
cmd/test_full_workflow_v31.py
cmd/test_heartbeat_and_timeout_v31.py
cmd/test_json_bom_v31.py
cmd/test_multica_dispatch_assignment_v31.py
cmd/test_multica_poll_filter_v31.py
cmd/test_notifications_and_gate_v31.py
cmd/test_notification_dedupe_v31.py
cmd/test_process_interruption_v31.py
cmd/test_timeout_loop_v31.py
cmd/test_v31_audit.py
```

测试重点：

- 状态迁移和门禁；
- Agent assignment 与轮询过滤；
- BOM/JSON 解析；
- P1/P2 gate；
- heartbeat/timeout/recovery；
- 通知去重；
- 进程中断和恢复。

## 4. 设计与协议文档

```text
docs/multi/multi-agent-solution-review-protocol.md
docs/2026-08-23-review-orchestrator-fsm-refactor-design-v3.1.md
docs/superpowers/specs/2026-08-16-multi-review-runtime-core-design.md
docs/superpowers/plans/2026-08-16-multi-review-runtime-core-plan.md
```

## 5. 不要混淆的目录

```text
D:\workspace\src\nexus-agents\internal\codexrouter
```

这是 Nexus 自身的模型路由服务，不是当前 Multica OpenCode runner。

Multica 本机运行时是安装后的二进制和用户状态目录：

```text
C:\Users\Administrator\.multica\bin\multica.exe
C:\Users\Administrator\.multica\daemon.log
C:\Users\Administrator\.multica\reasonix-state\
```
