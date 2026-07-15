---
name: coding-rules
description: "Go 编码规范合集：质量原则/命名/错误处理/日志/并发/性能/复用。写代码的工作流（$wf-go-feat/$wf-go-bugfix/$wf-go-refactor）激活时加载。"
---

# Go 编码规范

> 触发：工作流进入写代码阶段时自动加载
>
> §0 质量原则 §1 基础 §2 日志 §3 并发 §4 性能 §5 复用

## §0 代码质量原则

**可读性优先**：
- 命名自解释，不需要注释说明"做了什么"
- 函数短而聚焦（≤40 行为佳），超长函数必须拆分
- 逻辑层次清晰：early return 减少嵌套，happy path 左对齐

**简洁不过度设计**：
- 解决当前问题，不为假设的未来需求预设抽象
- 三行相似代码优于一个不直观的泛型抽象
- 无多态需求时不引入 interface
- 不写"框架级"代码，只写"业务级"代码

**写代码前决策阶梯**（停在第一个能解决的层级）：
1. 需要存在吗？投机需求 = 不写（YAGNI）
2. 标准库能做？用标准库
3. 已有代码/依赖能解决？用它，不为几行代码引新依赖
4. 一行写完？写一行
5. 以上都不行 → 写能跑的最少代码

**性能意识**：
- 热路径（Handler/Manager 方法）注意分配和循环复杂度
- 非热路径（init/配置加载）优先可读性，不需极致优化
- 性能优化必须有量化依据（调用频率/数据规模），不做"以防万一"优化

**不能简化掉的**：信任边界的输入校验、防数据丢失的错误处理、安全措施、用户明确要求的功能。

## §1 基础规范

### Import 顺序
三组，空行分隔：标准库 → 第三方（github.com, go.uber.org） → 项目内部（gitlab-sh.diandian.info/btd/...）

### 命名

| 类型 | 风格 | 示例 |
|------|------|------|
| 包名/文件名 | snake_case | `game_mod`, `random_box_manager.go` |
| 导出类型/函数 | PascalCase | `AbstractManager`, `DoTransaction` |
| 私有变量/函数 | camelCase | `defaultAbstractMgr` |
| 常量 | SCREAMING_SNAKE | `USER_KEY` |
| Singleton | defaultXxxMgr | `defaultRankMgr` |
| 测试函数 | Test_func_Scenario | `Test_calcReward_Empty` |

### 错误处理

```go
// 业务错误
return nil, xerror.NewProtoEnum(msg.ErrorCode_XXX)
// 带堆栈
return logger.XErr(msg.ErrorCode_XXX, "format %v", args)
// 禁止: errors.New() / fmt.Errorf() 用于业务错误
```

- 不忽略 error（`_ = xxx()` 仅确认安全时）
- 错误码必须与业务含义匹配
- 新模块错误码起始序号取整到下一个 10/50 边界

### 构建标签
所有 go 命令必须带 `-tags actor_id_uint64`

### 生成文件
`gen_*_optiongen.go` — 不要手动编辑

---

## §2 日志规范

| 级别 | 函数 | 场景 |
|------|------|------|
| Debug | `logger.LogDebugf` | 调试信息、中间值 |
| Info | `logger.LogInfof` | 业务关键节点 |
| Warn | `logger.LogWarnf` | 可恢复异常 |
| Error | `logger.LogErrorf` | 不可恢复，需人工排查 |

**必须**：
- 带 LogFields：`p.LogFields()` / `logger.LogPlayerId(id)` / `logger.LogGlobal()`
- 带 Module：`logger.Player` / `logger.Currency` / `logger.Server` 等
- Error 带 ErrorCode：`logger.LogErrorf(p.LogFields(), logger.Player, msg.ErrorCode_XXX, "format", args)`
- XErr 模式：`return logger.XErr(msg.ErrorCode_XXX, "msg %v", err)`

**禁止**：
- ❌ 循环内打 Info/Error（用 Debug 或循环外汇总）
- ❌ 打印密钥/token/密码
- ❌ 热路径打 Info（用 Debug）
- ❌ `fmt.Println` / `log.Println`

---

## §3 并发规范

### Actor 循环调用（严重）
```
ActorA → 调用 ActorB.Method() → ActorB 回调 ActorA → 死锁
```
- 涉及 `XxxActorSystemBox.Method()` 时，必须检查是否有回调风险
- 发现循环调用 → 立即暂停通知用户
- 常见：PlayerActor ↔ ClubActor / IslandActor 互调

### 锁
- sync.Map：仅用于读多写少的注册表
- ❌ 锁内做 IO/网络/跨 actor 调用
- ❌ 嵌套锁
- ❌ actor 内用锁（actor 单线程，不需要锁）

### Goroutine/Channel
- 必须有退出路径（ctx.Done）
- ticker/timer 退出时 Stop()
- ❌ `go func()` 里 panic 不 recover

---

## §4 性能规范

### 循环内禁止
- ❌ 跨 actor 调用（应批量）
- ❌ DoTransaction（应合并为一个事务）
- ❌ config 查询（循环外缓存）
- ❌ `make([]T, 0)`（应预分配 cap）
- ❌ proto.Marshal（循环外序列化一次）

### 内存分配
- 已知大小 slice 预分配：`make([]T, 0, expectedLen)`
- 大 struct (>5 字段) 传指针
- 避免 `[]byte` ↔ `string` 频繁转换

### 热路径识别
- Handler 方法体 / Manager 被 Handler 调用的方法 / cron 循环体 = 热路径
- 初始化/启动代码 = 非热路径

---

## §5 代码复用

### 写代码前必须
1. Grep 搜索同名/类似函数
2. 看同包是否已有类似逻辑

### 复用优先级
项目内已有 → pkg/ 公共包 → 已引入第三方 → 新写

### 抽象判断

| 情况 | 行动 |
|------|------|
| 3+ 处完全相同 | 提取函数 |
| 2 处相似有差异 | 保持内联 |
| 1 处使用 | 不提取 |

### 放置位置
- 单 Manager 内 → 同文件私有函数
- 多 Manager 共用 → `pkg/util/` 或 `app/manager/common_*.go`
- 全项目通用 → `pkg/` 对应子包
