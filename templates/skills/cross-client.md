---
name: cross-client
description: "跨端: btd-client Unity — 涉及客户端协议对接、消息收发、前后端联调时使用此 skill。"
---

# Cross-Client Skill

> 项目: btd-client (Unity) | 路径: D:/workspace/src/btd-client
> 技术栈: Unity + HybridCLR(热更) + Wwise(音频)
>
> §1 职责边界 §2 项目结构 §3 协议对接 §4 联调指引 §5 常见跨端Bug

## §1 职责边界

| 职责 | 归属 |
|------|------|
| 业务逻辑/数据验证 | game-server |
| UI 展示/动画/交互 | client |
| 协议定义 | btd-config/protos/msg/ (共用) |
| 配置表数据 | btd-config/protos/rawdata_client/ (客户端专用) |

原则: **服务端是权威**，客户端只做展示和发请求。

## §2 项目结构

```
btd-client/
├── btdgame/
│   └── Assets/         — Unity 资源和代码
├── design/             — 设计文档
├── docs/               — 技术文档
├── rules/              — 规则配置
├── .claude/            — 客户端的 Claude 配置
├── AGENTS.md           — 客户端 agent 指引
└── CLAUDE.md           — 客户端开发规范
```

## §3 协议对接

```
共享协议: btd-config/protos/msg/*.proto
  ↓ 服务端生成: gen/golang/msg/*.pb.go
  ↓ 客户端生成: (客户端有自己的 proto 编译流程)

消息流:
Client → [proto序列化] → gate → game-server Handler
Client ← [proto反序列化] ← gate ← game-server 响应/推送
```

关键约定:
- 请求消息: `XxxReq` (客户端发)
- 响应消息: `XxxResp` 或 `NormalAck` (服务端回)
- 推送消息: `XxxNotify` / `XxxPush` (服务端主动发)
- 错误码: `msg.ErrorCode_XXX` (两端共用枚举)

## §4 联调指引

### 服务端新增接口后客户端对接

1. 在 btd-config/protos/msg/ 定义 Req/Resp proto
2. 服务端实现 Handler
3. 客户端生成 proto 代码 + 实现发送/接收逻辑

### 排查"客户端收不到响应"

1. 检查 Handler 是否正确返回了 resp（不是 return nil, nil）
2. 检查 proto 字段序号是否两端一致
3. 检查 gate 是否路由了该消息
4. 检查客户端是否注册了该消息的回调

### 排查"数据不对"

1. 服务端用 logger.LogDebugf 打印实际发送的数据
2. 对比 proto 定义确认字段映射
3. 注意: int32/int64/uint32 类型不匹配会导致数据截断

## §5 常见跨端 Bug

1. **字段序号不一致**: 两端 proto 版本不同 → 字段错位
2. **枚举值不同步**: 服务端加了新枚举值但客户端未更新
3. **推送消息未处理**: 服务端发了 Notify 但客户端没注册监听
4. **精度丢失**: 服务端用 int64 但客户端语言(C#)处理精度不同
5. **时间格式**: 服务端用 Unix 秒/毫秒，客户端期望不同格式
6. **空值处理**: 服务端 proto 字段默认零值，客户端可能按"未设置"处理
