---
name: high-risk-api
description: "高风险 API 三级管控（报备/确认/审核）。涉及货币、物品增减、GM 接口、删除数据、鉴权修改时加载。"
---

# 高风险 API 管控

> 模块: 全局 | 触发: 涉及货币/删数据/GM/鉴权
>
> §1 报备级 §2 确认级 §3 自动审核级

## §1 报备级（写代码时主动告知用户）

检测到以下模式时，必须在动手前通知用户：

| 模式 | 原因 |
|------|------|
| DoTransaction 内删除/清空数据 | 不可逆 |
| 修改货币/钻石/付费物品相关逻辑 | 经济系统敏感 |
| 新增/修改 GM Handler | 权限控制敏感 |
| 修改 cron/定时任务逻辑 | 影响全服所有玩家 |
| 新增跨 actor 调用 (`XxxActorSystemBox.Method`) | 可能死锁/超时 |
| 修改配置解析逻辑 (rawdata/xxxconfig) | 影响全量配置 |

通知格式：`⚠️ 报备：即将修改 [xxx]，原因：[yyy]。是否继续？`

## §2 确认级（暂停等用户确认才继续）

| 操作 | 风险级别 |
|------|----------|
| 删除/清空玩家数据 (ClearAll/Delete/Reset) | 严重 |
| 修改登录/鉴权/session 流程 | 严重 |
| 修改 wire DI 注入图 (app/wire/) | 高 |
| 全服广播/群发邮件逻辑 | 高 |
| 修改数据库 schema/migration | 严重 |
| 修改 actor 生命周期/创建/销毁 | 高 |
| 修改 gate/网络层/协议路由 | 高 |

确认格式：
```
🔴 需确认：即将 [操作]，这是不可逆/高影响操作。
影响范围：[xxx]
请确认是否继续。
```

## §3 自动审核级（完成后触发 reviewer）

| 触发条件 | 审核角色 |
|----------|----------|
| 涉及货币/物品增减 | reviewer-logic + reviewer-security |
| 涉及跨 actor 调用 | reviewer-logic (检查循环调用) |
| 涉及 GM 接口 | reviewer-security |
| 涉及数据删除 | reviewer-logic + reviewer-security |
| 涉及锁的新增/修改 | reviewer-perf + reviewer-logic |
