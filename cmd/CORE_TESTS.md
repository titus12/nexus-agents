# 核心回归测试

默认执行：

```powershell
rtk python cmd/run_core_tests.py
```

不要执行 `python -m unittest discover`：目录中仍保留历史测试，其中包含真实飞书 HTTP 交互测试。

这套测试只覆盖长期有效的关键逻辑：状态机与持久化、并发租约与收敛、中书省拆解契约、门下省执行契约、Solver 边界、回复关联、结果文件、超时恢复和一次端到端流程。

以下测试不进入默认集合：

- 飞书发送、通知去重、通知渲染、心跳展示、最终通知；
- 飞书命令解析和飞书人工闸门 HTTP 交互；
- 已被新契约测试覆盖的旧版轮询/回复回退测试；
- 一次性 canary、小时日志和历史编排器兼容性测试。

`test_full_workflow_v31.py` 使用 `FakeFeishuAdapter`，因此核心回归测试不会向真实飞书发送消息。
