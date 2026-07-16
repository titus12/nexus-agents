---
name: unity-ui-resolver
description: "Unity UI Resolver 规范：修改 PSD 组件解析或 UI Resolver 时保持既有模式和可重复生成。"
---

# Unity UI Resolver 规范

在 PSD 组件 Resolver 或 UI Resolver 修改中按需读取；不得据此隐式启动或选择工作流。

## 执行要求

- 优先复用既有 Resolver 模式和元数据结构。
- 修改生成逻辑时保持输出可重复、字段名和绑定路径稳定。
- 避免无关 UI 变更；生成问题应修改生成器、模板或解析逻辑。
