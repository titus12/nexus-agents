# UIArchitect Editor 与 Runtime 边界规范

- 仅 Editor 使用的流水线代码必须位于 Editor 程序集或 Editor 路径下。
- Runtime 中的 UIArchitect 代码不得依赖 `UnityEditor`。
- 生成脚本必须在预期的 Runtime 或 Editor 上下文中完成编译。
- 可复用 UIArchitect 插件代码的编译不得依赖宿主项目业务代码。
