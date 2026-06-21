---
name: testing
description: "测试编写规范：运行命令、mock 策略、gomonkey 踩坑、断言规范。写测试或补测试时加载。"
---

# 测试编写规范

> 触发：写测试/gomonkey/mock/断言
>
> §1 何时写 §2 运行命令 §3 mock 策略 §4 gomonkey 踩坑 §5 断言规范 §6 检查清单

## §1 何时必须写测试

| 场景 | 是否必须 |
|------|----------|
| 数值计算/状态机/边界逻辑（升级、扣减、返还、奖励） | 必须 |
| 公共函数被多处调用 | 必须 |
| 纯 CRUD / 透传 / 仅组装 proto | 可选 |
| typo、单行修复 | 不强制 |

## §2 运行命令（固定）

```bash
go test -tags actor_id_uint64 -gcflags=all=-l -vet=off -run '<TestName>' ./app/manager/
```

| 标志 | 原因 |
|------|------|
| `-tags actor_id_uint64` | 项目构建标签 |
| `-gcflags=all=-l` | 禁内联，gomonkey patch 才能生效 |
| `-vet=off` | 跳过遗留 vet 告警 |

## §3 mock 策略

**原则：不依赖真实 DB、xbean 工厂、配置加载。**

1. **手写 xbean 接口 mock**：用 Go map 支撑，实现全部接口方法（含 IBean + Serializer）
2. **构造真实 entity 包装 mock**：`&entities.Talents{Talents: mockXbeanTalents}`
3. **gomonkey patch 外部依赖**：
   - 配置：`pmconf.GetXxx` → 返回测试构造的配置
   - 道具：`DoDecreaseItemByMap` → 捕获参数做断言
   - entity getter：`(*CacheEntity).GetXxx` → 返回 mock
4. **patch 掉会触发工厂的写方法**：避免 `xbean.NewXxx(id)` 在测试中 panic

## §4 gomonkey 踩坑

- **必须 `-gcflags=all=-l`**：否则小函数内联导致 patch 静默失效
- **方法 patch**：`ApplyMethod(reflect.TypeOf(&T{}), "Method", func(_ *T, args...) ret {...})`，第一个参数是 receiver
- **变参方法**：replacement 也要变参
- **全局生效**：按类型 patch，影响所有实例
- **隔离**：每个测试 `p := gomonkey.NewPatches()` + `defer p.Reset()`
- **捕获参数**：用闭包记录被 patch 函数收到的入参

## §5 断言规范

- **错误码**：`logger.ExtractErrorCode(err, msg.ErrorCode(-1))` 提取后比对
- **map/slice**：`reflect.DeepEqual`，期望值显式写全
- **失败信息**：`t.Errorf("期望 X，实际 %v", got)`
- **表驱动**：多分支用子测试，命名 `Test_func_Scenario`

## §6 检查清单

1. [ ] 覆盖正常 + 边界（满级、0级、空集）+ 失败路径
2. [ ] mock 不触达真实 DB/工厂/网络
3. [ ] patch 全部 defer Reset()
4. [ ] 用 §2 完整命令跑过，每个用例 PASS
5. [ ] 不声称"测试通过"而未实际运行
