# 中书省单一工作流收口实施计划

> 按用户明确指示，直接执行已批准需求设计的收口；本轮在当前会话内实施，不再保留可运行的旧中书省工作流。

**Goal:** 所有新建中书任务只运行 requirements-v1，移除 legacy 中书建图、组审、冻结、快捷放行及对应角色协议。

**Architecture:** 新需求领域与持久编排器成为唯一入口；门下现有实施文档流水线继续复用。历史 DTO 解码用于查看和显式迁移，不选择旧运行器，也不把旧批准转换为新版批准。

**Tech Stack:** Python、现有 JSON journal、SQLite 人工输入、官方飞书 SDK。

## Task 1：入口和恢复

- [x] `cmd/orchestrator/app.py` 的 build_context 无分支创建 REQ_INTAKE 与 RequirementsState；CLI 的 workflow 只允许 requirements-v1，删除 fast-track。
- [x] OrchestratorApp 无条件装配 RequirementsRuntime，删除旧人工文档轮询与旧运行循环；只保留门下 worker 并行。
- [x] `--inspect-task` 直接读取快照，历史任务不实例化运行器。旧任务 resume 给出明确迁移命令；`--migrate-task` 生成新的需求任务并保留完整旧快照及来源关系，不派发模型，不继承旧批准。
- [x] 新增入口测试：不传 workflow 即进入 REQ_INTAKE；legacy 选择被拒绝；历史查看不写快照；迁移保存历史且不能自动批准。

## Task 2：删除旧中书执行路径

- [x] `domain/states.py` 留门下和系统状态，删除 RequestIntake/Zhongshu* 类、旧组审/冻结函数及旧中书派发分支。
- [x] `domain/transitions.py` 仅作为门下子图与通用 transition 基类；RequirementsTransitionRegistry 是唯一完整流程图，不再挂回旧中书。
- [x] `contracts/__init__.py`、structured_output、adapters、compat_effects 只绑定新中书协议和 Skill。
- [x] AgentNodeJoiner 删除旧 Analyst/组审/任务审/组修订分支，保留门下结果合并。共享哈希、来源/验收兼容解析等提取到明确的共享模块。
- [x] 被删除的旧专用模块、Skill 和专用测试按原字节归档成 .txt，并写 SHA-256 manifest；归档不参与 Python import 或测试发现。混合测试保留共享与门下覆盖，迁移旧入口断言。

## Task 3：验证与交付

- [x] 执行 `rtk proxy python -X utf8 cmd/run_tests.py test_requirements_workflow test_requirements_workflow_e2e test_requirements_cutover`，随后执行全量 runner。
- [x] 搜索生产入口和 import，确认无旧中书状态、协议或 skill 可派发；验证旧快照只读/迁移、新入口→需求批准→门下→完成、补证/澄清/停滞/取消/崩溃恢复。
- [x] 更新 README、操作说明与验证记录，说明新版为唯一入口，以及真实模型品质评审和飞书长连接尚未验证；用户授权单一切换不等于这些验收自动通过。
- [x] 不修改受保护 Codex 配置、worker 配置或 generated OpenWiki；不停止 Nexus 8766，不发送群测试消息或重新尝试已被审批拒绝的模型样本。

核心代码预期：

```python
workflow = 'requirements-v1'
aggregate = RequirementsState.empty(SourceRecord('src-000001', 'request', raw_request))
runtime = RequirementsRuntime(repository, root, context, requirements_port)
```

历史迁移的前提：旧任务运行锁没有 live owner；原始用户输入存在。保留完整历史来源；模型产出的旧计划作为历史附件，不伪装为用户新决定。新版仍须经过完整审查与用户批准。
