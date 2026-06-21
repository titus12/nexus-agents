# 项目模型与约束

> 阅读总则：优先 Grep/LSP 定位目标 → 再 Read(offset, limit≤200)，禁止全文通读。

## 项目背景

- 全新项目，无存量线上玩家数据
- 改写数据结构时需用户确认是否涉及数据迁移/向后兼容

## Actor 模型

- 每个玩家是独立 actor，状态修改只能在 actor 自己的 goroutine 内
- 跨 actor 通信必须通过 RPC：`system.XxxActorSystemBox.Method(ctx, senderId, targetId, req)`
- 跨 actor 调用是异步的，可能超时/失败，必须处理 error
- 禁止直接访问其他 actor 的内存状态

## 数据层 (xbean + DoTransaction)

- 所有玩家数据修改必须在 DoTransaction 内：
  ```go
  entities.DoTransaction(ctx, p, func(entity *entities.CacheEntity) error {
      entity.GetXxx().SetYyy(val)
      return nil
  }, entities.WithUseSession(true))
  ```
- 禁止在 DoTransaction 外直接修改 entity 字段
- xbean 的 Set 方法自动标记 dirty → 事务提交时持久化
- DoTransaction 内 return error 会回滚

## 配置层

- 必须用 WithContext 变体支持 A/B 测试：`xxxconfig.GetByKeyWithContext(ctx, key)`
- 禁止用不带 Context 的配置接口（会绕过 A/B）
- rawdata 是 Excel 导出的只读数据，不要修改
- 配置预处理详见 skill `pmconf-pattern`

## Manager 模式

```go
var (
    defaultXxxMgr XxxManager
    xxxOnce       sync.Once
)
func init() { xxxOnce.Do(func() { defaultXxxMgr.init() }) }
```

- 公开 API 通过包级函数代理，不暴露 Manager struct
- 不要引入新的 Manager 初始化模式

## 构建

- 所有 go 命令必须带 `-tags actor_id_uint64`
- 本地构建使用 go.work（依赖 ../btd-config 存在）
- Docker 构建用 GOWORK=off（走 go.mod 版本）

## 按需加载 Skills

检测到以下场景时，先 Read 对应 skill 再动手：

| 场景关键字 | Skill 文件 |
|-----------|------------|
| 新功能开发/九步流程/开发计划 | `dev-workflow` |
| 写测试/gomonkey/mock | `testing` |
| 货币/物品增减/GM/删数据/鉴权 | `high-risk-api` |
| pmconf/预处理/postLoad | `pmconf-pattern` |
| 创建 skill/skill 规范 | `skill-standard` |
| 审核反馈/新风险模式纳入 | `review-feedback` |
| quest/任务/成就/目标 | `quest-system` |
| gate/网关/连接/鉴权/转发 | `cross-gate` |
| social/好友/聊天/跨服 | `cross-social` |
| proto/配置表/rawdata/xbean/dbstate | `cross-config` |
| 客户端/unity/前端/联调 | `cross-client` |
