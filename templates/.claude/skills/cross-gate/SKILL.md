---
name: cross-gate
description: "跨服务: gate-server — 涉及网关连接、协议转发、鉴权、编解码、负载均衡时使用此 skill。"
---

# Cross-Gate Skill

> 服务: btd-gate-server | 路径: D:/workspace/src/btd-gate-server
> 职责: 客户端连接管理、协议编解码、鉴权、actor 路由转发
>
> §1 职责边界 §2 仓库结构 §3 通信协议 §4 关键文件 §5 调试指引 §6 常见跨服务Bug

## §1 职责边界

| 职责 | 归属 |
|------|------|
| 客户端 TCP/WebSocket 连接 | gate |
| 协议编解码 (hermes codec) | gate |
| 鉴权 (auth token 验证) | gate |
| 负载均衡 (选择 game-server 节点) | gate |
| 消息转发 (client ↔ game-server) | gate |
| 业务逻辑处理 | game-server |
| actor 调度 | game-server |

gate 是纯转发层，**不处理业务逻辑**。

## §2 仓库结构

```
btd-gate-server/
├── cmd/            — 启动入口
├── server/         — 核心逻辑
│   ├── auth.go         — 鉴权
│   ├── balancer.go     — 负载均衡
│   ├── codec_hermes.go — 协议编解码
│   ├── dynamic_codec.go — 动态编解码
│   └── filter_actor.go  — actor 消息过滤/路由
├── pkg/
│   ├── logger/     — 日志
│   └── util/       — 工具
├── configs/        — 配置文件
└── k8s/            — 部署
```

## §3 通信协议

```
客户端 ←→ gate: hermes 二进制协议 (codec_hermes.go)
gate ←→ game-server: actor RPC (sandwich 框架内部通信)

消息流:
Client → [hermes编码] → gate → [解码+鉴权+路由] → game-server actor
Client ← [hermes编码] ← gate ← [响应] ← game-server actor
```

协议定义在 btd-config/protos/msg/ (protobuf)

## §4 关键文件（调试时看这些）

| 问题类型 | 看哪个文件 |
|----------|-----------|
| 连接断开/超时 | server/auth.go, server/balancer.go |
| 协议解析错误 | server/codec_hermes.go, server/dynamic_codec.go |
| 消息路由错误 | server/filter_actor.go |
| 鉴权失败 | server/auth.go |

## §5 调试指引

game-server 收不到客户端消息时：
1. 确认 proto 消息是否在 btd-config/protos/msg/ 定义并注册
2. 检查 gate 的 filter_actor.go 是否路由了该消息
3. 检查 codec 是否能正确解码该消息类型

客户端连接断开时：
1. 查 gate 日志看断开原因 (auth失败/超时/心跳)
2. 查 balancer.go 确认节点选择是否正确

## §6 常见跨服务 Bug

1. **新增消息未注册**: 在 game-server 加了新 Handler 但 proto 未编译/gate 不认识该消息
2. **鉴权 token 过期**: gate auth.go 的 token 验证逻辑与 game-server session 不一致
3. **actor 路由错误**: filter_actor.go 未正确识别消息应发往哪个 actor
4. **编解码版本不一致**: gate 和 game-server 用了不同版本的 proto 定义
