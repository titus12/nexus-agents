---
name: unity-bugfix-developer
description: "Unity Bug 修复实施规范：基于已确认根因做最小改动并完成验证。"
---

# Unity Bug 修复实施规范

在 Unity Bug 修复任务中按需读取；不得据此隐式启动或选择工作流。

## 执行要求

- 仅实施与已确认根因直接相关的最小修复。
- 避免无关重构，以及未明确需要的 UI、Prefab、Scene 和生成文件改动。
- 条件允许时补充针对该问题的回归测试。

## 验证要求

- 验证编译、Console、问题复现路径和修复后的行为。
- 报告剩余风险和未执行的检查。
