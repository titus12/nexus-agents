---
name: pmconf-pattern
description: "pmconf 配置预处理模式：何时使用、标准写法、参考实现。修改配置整理逻辑(postLoad/pmconf)时加载。"
---

# pmconf 预处理模式

> 触发：pmconf/预处理/postLoad/配置整理
>
> §1 何时需要 §2 标准写法 §3 参考实现 §4 注意事项

## §1 何时需要预处理

| 触发条件 | 说明 |
|----------|------|
| 读表逻辑复杂 | 需多表 join、按字段分组、建反向索引 |
| 影响性能 | 热路径里 Range 全表 / 多次 GetByKey / 排序 |
| 每次读表后重复计算 | 每次都做同样的聚合/排序/解析 |

## §2 标准写法（四步）

1. **定义数据结构**：
   ```go
   type pmConfXxxData struct {
       pmConfValueBase
       // 字段按 map[ABTag]map[key]value 组织
   }
   ```

2. **写 postLoad 函数**：
   ```go
   func postLoadXxx(ctx context.Context) (conf_loader.ExternalData, error) {
       // 用 postLoadFuncAB + xxxconfig.RangeWithContext 遍历整理
   }
   ```

3. **注册**：在 `MustInitPmConf` 里
   ```go
   RegisterExternalLoaders(NewExternalLoaderAndCacher(postLoadXxx))
   ```

4. **写读取函数**：
   ```go
   func GetXxx(ctx context.Context, key int32) *XxxValue {
       // conf_loader.GetExternalData[*pmConfXxxData](ctx) + AB tag 兜底到 TagFoundation
   }
   ```

## §3 参考实现

- `postLoadStage` — 关卡预处理
- `postLoadForge` — 锻造预处理
- `postLoadFunctionList` — 功能开放列表

## §4 注意事项

- 预处理在配置加载/热更时执行一次（非热路径）
- 业务侧只做 O(1) map 查找
- 不要发明新的预处理模式，参考已有实现
- AB tag 兜底逻辑：找不到当前 tag → fallback 到 `TagFoundation`
