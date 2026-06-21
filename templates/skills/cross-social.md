---
name: cross-social
description: "跨服务: social-server — 涉及社交功能（好友/聊天/排行榜跨服）时使用此 skill。"
---

# Cross-Social Skill

> 服务: btd-social-server | 路径: D:/workspace/src/btd-social-server
> 职责: 跨服社交功能（好友、聊天、全局排行榜等）
>
> §1 职责边界 §2 通信方式 §3 调试指引 §4 常见跨服务Bug

## §1 职责边界

| 职责 | 归属 |
|------|------|
| 玩家内数据（背包/任务/属性） | game-server |
| 公会内部逻辑 (club actor) | game-server |
| 跨服好友关系 | social-server |
| 跨服聊天 | social-server |
| 全局排行榜 | social-server 或 game-server(服内) |

## §2 通信方式

```
game-server ←→ social-server: RPC (sandwich 框架)

典型调用:
game-server handler → system.SocialActorSystemBox.Method() → social-server 处理 → 返回
```

proto 定义: btd-config/protos/msg/ 中社交相关 proto

## §3 调试指引

跨服社交功能出问题时：
1. 先确认是 game-server 侧还是 social-server 侧
2. game-server 侧: 查 `app/manager/social_manager.go`
3. social-server 侧: 查 social-server 仓库对应逻辑
4. 通信问题: 检查 proto 定义是否一致

## §4 常见跨服务 Bug

1. **RPC 超时**: social-server 负载高或网络问题，game-server 未处理超时 error
2. **数据不一致**: 玩家改名后 social-server 的缓存未同步
3. **proto 版本不一致**: 两端用不同版本的消息定义

注意: social-server 仓库当前内容较少（仅 README），可能正在开发中或使用其他方式部署。实际调试时需确认服务是否已部署。
