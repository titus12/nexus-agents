# Unity 工作流路由规范

Unity 工作只能通过用户显式调用的工作流入口执行：`$wf-unity-bugfix`、`$wf-unity-logic-mod`、`$wf-unity-ui-feature` 和 `$wf-unity-ui-quick`。

- 故障使用 Bug 排查工作流。
- 非 UI 行为变更使用逻辑修改工作流。
- 涉及 UGUI、TMP、UIArchitect、Prefab、Scene、Resolver 或表现层的功能开发，使用完整 UI 功能工作流。
- 仅在变更范围被明确限定的狭窄 UI 修改中使用 UI 快速工作流。
