---
name: quest-system
description: "任务/成就系统 — 涉及任务(quest)的接受/触发/提交/完成流程，或新增任务类型/目标类型时使用此 skill。"
---

# Quest System Skill

> 模块: 任务/成就系统 | 入口: QuestsHandler → QuestSystem interface
> 核心文件: manager/quests_manager.go, handlers/quests.go, modules/quest/
>
> §1 架构 §2 核心接口 §3 数据结构 §4 扩展点 §5 配置 §6 踩坑 §7 测试 §8 变更检查

## §1 架构概览

```
handlers/quests.go          — Handler 层，处理客户端请求
    ↓
manager/quests_manager.go   — QuestSystem 接口实现，业务逻辑
manager/quests_manager_ext.go — 扩展方法
    ↓
modules/quest/              — 底层引擎
├── quest_handler.go        — ActionHandlerProvider（action类型处理器注册）
└── quest_counter.go        — GoalCounter（目标计数逻辑）
    ↓
entities/                   — 数据层 (xbean)
conf/taskconfig, goalconfig — 配置层
```

## §2 核心接口

```go
// QuestSystem — 任务系统主接口 (manager/quests_manager.go)
type QuestSystem interface {
    InitQuestsOnNewPlayer(ctx, entity, quests)    // 新玩家初始化
    InitQuestsOnLogin(ctx, entity, quests)        // 登录刷新
    OnActivityQuest(ctx, entity, activityId, status) // 活动任务触发
    Find(ctx, entity, questId...)                 // 查询任务
    Trigger(ctx, entity, targetType, args)        // 事件触发进度
    GoalCounter(ctx, entity, quests, goalType, delta) // 目标计数
    AcceptQuest(ctx, entity, questId)             // 接受任务
    SubmitQuest(ctx, entity, questId)             // 提交任务
    AbortQuest(ctx, entity, questId)              // 放弃任务
    SetQuestProgress(ctx, entity, questId, progress) // 设置进度
    FinishQuest(ctx, entity, quests, quest, spec)    // 完成任务
    FinishQuestGoals(ctx, entity, questId, goalId...) // 完成指定目标
}

// ActionHandlerProvider — action 类型处理器 (modules/quest/)
type HandlerFunc func(*rawdata.GoalConfig, *int32, HandlerParams) error

// CollectDataProvider — collect 类型数据采集
type CollectDataProvider interface {
    GetCollectGoalValue(entity, quest, target) (int32, error)
}
```

## §3 数据结构

```
xbean.Quest:
  - QuestId int32
  - Status  (Doing/Done/Submitted)
  - Goals   map[goalId]progress
  - Priority uint32

entities.Quests:
  - DoingQuests  map[questId]Quest
  - DoneQuests   map[questId]Quest
  - QuestPool    []int32

配置表:
  - taskconfig    — 任务定义（ID/类型/前置/奖励）
  - tasklistconfig — 任务列表/分组
  - goalconfig    — 目标定义（类型/参数/目标值）
  - tutorialsconfig — 新手引导任务
```

## §4 扩展点

### 新增任务目标类型 (Goal Type)

1. `modules/quest/quest_counter.go` — 注册新的 counter handler
2. `modules/quest/quest_handler.go` — 注册新的 action handler（如果是 action 类型）
3. 配置表 `goalconfig` 添加新类型定义

模式：
```go
// quest_counter.go 或新文件
func init() {
    RegisterCounter("new_goal_type", func(entity, quest, goal, delta) error {
        // 计数逻辑
        return nil
    })
}
```

### 新增任务触发点

1. 在对应业务 Manager 中调用 `Trigger(ctx, entity, "target_type", params)`
2. 确保在 DoTransaction 内或数据已持久化后触发

### 新增任务类型 (Category)

1. `manager/quests_manager.go` — 在 InitQuests / Accept / Submit 中处理新 category
2. 配置表 `taskconfig` 定义新 category 值
3. Handler 层如有新接口需在 `handlers/quests.go` 添加

## §5 配置依赖

| 配置表 | 用途 | 访问方式 |
|--------|------|----------|
| taskconfig | 任务定义 | `taskconfig.GetByKeyWithContext(ctx, id)` |
| tasklistconfig | 任务分组 | `tasklistconfig.GetByKeyWithContext(ctx, id)` |
| goalconfig | 目标定义 | `goalconfig.GetByKeyWithContext(ctx, id)` |
| rewardconfig | 奖励配置 | `rewardconfig.GetByKeyWithContext(ctx, id)` |
| tutorialsconfig | 新手任务 | `tutorialsconfig.GetByKeyWithContext(ctx, id)` |

## §6 踩坑记录

1. **Trigger 时机**：必须在数据修改持久化后触发，否则 GoalCounter 读到旧数据
2. **任务完成连锁**：FinishQuest 可能触发新任务解锁，注意循环检测
3. **活动任务生命周期**：OnActivityQuest 在活动开启/关闭时调用，关闭时要清理 DoingQuests
4. **QuestPool 溢出**：任务池有大小限制，超出时旧任务被挤出
5. **并发安全**：Quest 数据修改在 DoTransaction 内，但 Trigger 可能从多处调用，确保幂等

## §7 测试方法

```bash
# 任务相关测试
go test -tags actor_id_uint64 -run TestQuest ./test/manager_test/
go test -tags actor_id_uint64 -run TestQuest ./app/manager/

# 需要的环境
# - 无外部依赖（纯逻辑测试）
# - mock entity 和 config
```

## §8 变更检查清单

修改任务系统时额外检查：

- [ ] 新目标类型是否注册了 counter/handler
- [ ] Trigger 调用是否在数据持久化之后
- [ ] 配置表是否需要同步更新（btd-config 仓库）
- [ ] 任务完成是否有连锁效应（解锁新任务/触发奖励）
- [ ] 活动任务是否处理了活动结束的清理
- [ ] QuestPool 是否有容量保护
- [ ] 是否需要通知客户端（UpdateQuests 消息）
