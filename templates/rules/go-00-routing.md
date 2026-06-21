# 工作流与角色激活

用户消息以 `--xxx` 开头时激活对应工作流，剩余内容作为输入。

## 工作流（多步流程，带阶段检查点）

### `--feat` 新功能开发
全新功能 / 较大模块。加载 `dev-workflow` skill，走九步流程，每步汇报 → 用户审核 → 推进。
- 步骤 1 前：问 2-3 个澄清问题确认需求边界
- 步骤 6（实现）：加载 `coding-rules` skill
- 写测试阶段：先写失败测试 → 看它失败 → 再写实现 → 看它通过

### `--mod` 功能调整
改动已有功能。流程：
1. 摸清现状：codegraph/Grep 定位影响范围，读对应模块 skill（若有）
2. 改动：加载 `coding-rules` skill，多文件 → hephaestus，单文件 → quick
3. 触发审核：按 `high-risk-api` skill 判断是否派 reviewer
4. 验证：`go build -tags actor_id_uint64 ./cmd/server/` + 相关 test

### `--bug` Bug 排查修复
报错 / 异常 / crash。流程：
1. debugger 日志优先排查（≤15 turns）
2. 复杂 / 跨模块 → 升级 oracle 深度分析
3. 定位根因 → 加载 `coding-rules` skill → 修复（quick / hephaestus）
4. 验证：复现路径 + build
- 铁律：修复前必须定位根因，禁止猜测性修改
- 连续 3 次修复失败 → 停下来质疑方向，与用户讨论

### `--rev` 代码审核
审当前改动。3 reviewer 并行：logic / perf（含质量） / security → 汇总去重、按严重度分级。

### `--design` 方案设计
只出方案不写码。流程：
1. 问 2-3 个澄清问题（约束/优先级/时间）
2. prometheus 读对应 skill + codegraph 摸底
3. 提出 2-3 方案对比（复杂度/性能/兼容性）
4. 推荐一个 → 落盘 `docs/dev-plans/`
5. 后续可接 `--feat` 或 `--mod` 实现

### `--ask` 理解/调研
只读不改。流程：
1. 代码理解 → oracle + codegraph 追踪调用链
2. 外部文档 → librarian + context7 查询
3. 输出：结论 + 关键路径 + 参考代码位置

### `--commit` 提交检查
提交前门禁。流程：
1. gatekeeper 扫描 git diff → 生成风险清单
2. 有高风险项 → 逐项向用户确认
3. 全部确认 → commit（不 push）

### `--refactor` 重构
大范围结构调整，外部行为不变。流程：
1. codegraph_impact 分析影响面，列出涉及文件/调用者
2. prometheus 设计分步方案（确保每步可编译）
3. 加载 `coding-rules` skill，hephaestus 分阶段实现，每阶段 build 验证
4. 3 reviewer 并行审核
5. 验证：行为不变（原有 test 通过 / 接口签名不变）

### `--lark` 飞书操作
查飞书文档/发消息/操作多维表格等。触发 `feishu` skill 路由到具体 lark-* 子 skill。
- 在 `--feat` 步骤 1（阅读文档）中，若需求文档在飞书，自动触发此工作流获取内容

## 铁律（所有工作流通用）

1. **验证才能声称完成**：声称"已修复/已完成/测试通过"前，必须实际跑验证命令并贴出证据
2. **根因优先**：修 bug 时禁止猜测性修改，必须先定位根因
3. **3 次失败停下来**：连续 3 次修复尝试失败 → 停止，质疑方向，与用户讨论
4. **TDD（写测试时）**：先写失败测试 → 看它失败 → 写实现 → 看它通过

## 通用规则

- 工作流每阶段：完成 → 汇报 → 用户确认 → 进入下一步
- 高风险改动按 `high-risk-api` skill 报备/确认
- 无 `--xxx` 前缀：正常处理；任务模糊先澄清，复杂时可主动建议对应工作流
