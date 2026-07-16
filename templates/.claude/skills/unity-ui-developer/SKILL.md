---
name: unity-ui-developer
description: "Unity UI 实施规范：复用现有 UGUI、TMP 或 UI 架构模式，并验证交互状态。"
---

# Unity UI 实施规范

在 Unity UI 变更中按需读取；不得据此隐式启动或选择工作流。

## 执行要求

- 复用项目现有的 UGUI、TMP 和 UI 架构模式。
- 不得手动修改生成的 View 文件。
- 保持 View、ViewModel、Presenter 或项目等价层之间的职责边界。

## 验证要求

- 验证展示、交互、加载、空态、错误态、打开关闭和输入锁释放路径。
