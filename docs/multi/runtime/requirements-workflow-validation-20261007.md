# 中书省 requirements-v1 重构验证记录

日期：2026-10-07。实施起点：Git HEAD `78f2678`。关联方案：`docs/superpowers/plans/2026-10-07-zhongshu-requirements-workflow-implementation-plan.md`。

## 当前交付与发布状态

新领域、独立状态图、角色协议与 Skill、批量澄清、批准校验、不可变发布、真实门下输入、CLI 入口和恢复已接入。新任务默认使用 requirements-v1，可进行本地验证；旧中书执行路径已退役。

**当前已按用户授权切换为唯一新版。** 未完成六份真实模型样本、业务内容确认和飞书往返；用户授权单一收口改变运行版本策略；不能据此声称已证明稳定内容品质或效率收益。Gateway 环境已准备，已尝试启动真实长连接，但握手未成功；诊断后已停止本轮启动的网关。没有向真实飞书群发测试消息，也没有将样本输入发给模型 worker。

实际修改保留在当前工作区，尚未创建 Git commit。每个方案提交点的实现内容集中保存在此工作区，不能把方案中的预期提交说明当成已存在的提交。

## 实施映射

| 方案任务 | 实际实现与本地证据 | 状态 |
|---|---|---|
| 1–2 聚合、来源、决定、问题 | frozen records、严格 DTO、追加来源、决定 supersedes、依赖问题、部分回答、旧回答变更确认 | 本地验证通过 |
| 3 单一正文 | 八类核心语义、灵活标题及专题、确定性渲染、引用及验收覆盖校验 | 本地验证通过 |
| 4–5 审查、修复、预算 | 稳定缺陷账本、七轴 coverage、事实指纹、局部结论复用、纯补证、混合缺陷、有界重试 | 本地验证通过 |
| 6–7 角色与状态图 | workflow 选择的严格契约、新 Skill、独立 registry、真实 App/effects/worker runner | 本地验证通过 |
| 8 卡片与 gateway | JSON 2.0、SQLite 持久 inbox、权限绑定、早到回调、未知回执、部分刷新、审批屏障 | 离线通过；真实订阅与往返待验证 |
| 9 文档与批准 | 强语义读回、来源变化复核、当前版本批准、云端变更阻塞、manifest 与 approval 分离 | 本地通过；真实云端权限与格式待验证 |
| 10 门下输入 | 完整批准正文成为独立 ApprovedRequirementsInput，再建一组实施 DAG；保留真实任务依赖及需求变更入口 | 脚本真实入口通过 |
| 11 入口与恢复 | 持久 workflow、本地结构化输入、可核验恢复、锁、旧任务隔离 | 本地验证通过 |
| 12 验收与测量 | 58 个新增需求测试、三类独立 fixture、实际 journal 测量、人工评分工具、真实模型采样命令 | 机制通过；六份模型样本与内容评审未完成 |
| 13 文档与切换 | 操作说明、旧草稿校验归档、回退路径、实际测试记录 | 文档已写；按用户授权完成单一切换，真实质量验收待完成 |

门下适配集中在 `runtime/requirements_workflow.py`，编译器通过显式 approved input 选择新需求边界；没有复制另一个 canonical 平台。新中书省没有旧任务图、组级投票或 freeze。原门下内部审查继续复用共享模块，旧任务仅能只读查看与显式迁移。

## 单一收口前的实际测试（历史记录）

所有测试由 `rtk proxy python -X utf8 cmd/run_tests.py` 执行。测试 runner 设置 `NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS=1`，强制 NullNotificationPort 并拒绝真实飞书写操作。Windows 临时目录 ACL 导致最初的沙箱内运行失败；后续在获准的测试执行权限下运行，未把环境失败当成业务失败。

| 实际命令/范围 | 结果 |
|---|---|
| 初始 `test_linear_fsm_entrypoint test_zhongshu_convergence_e2e` | 9 项通过，11.797 秒 |
| 第一轮全量 | 1449 项通过，107.603 秒 |
| 加入恢复、协议、指标、混合缺陷后全量 | 1478 项通过，102.264 秒 |
| 加入云端恢复、卡片、发布及交接反馈后全量 | 1485 项通过，109.069 秒 |
| 最后补充严格持久字段后的完整回归 | 1486 项全部通过，117.334 秒；其中新增需求机制测试 58 项 |
| `git diff --check` | 通过；仅现有 Windows 行尾转换提示 |
| `GET http://127.0.0.1:8766/api/health` | `{"service":"nexus-agents","status":"ok"}`；未停止或重绑定服务 |

实现期间新增测试曾发现脚本 fixture 的 tuple 未转为 JSON array、缺必填字段、测试 API 调用及插入位置错误；均已修复并转绿。不能把这些中间失败隐藏为全程一次通过。

代表性机制证据：

- 明确请求在真实 OrchestratorApp 中经过 Analyst→Solver→Critic，恰好 3 次中书派发；批准前没有门下派发。
- 真正交接后创建 8 项依赖链实施图，不触发旧六项组容量限制；context DTO 重放保持同文来源与批准。
- 纯事实补证 C→A→C，正文 SHA 不变、Solver 调用仍为 1；混合事实/正文缺陷一次补证、一次定向重写、一次复核，全部 findings 保留。
- 两轮澄清保留稳定问题 ID、依赖和来源；重复答案不增加决定，旧不同答案请求确认变更。
- 格式失败只允许 2 次修复；发布临时失败只有 2 次重试，模型调用不因此重复。
- 人类输入先于已保存模型结果应用；旧结果被隔离。journal 提交后 snapshot 写失败、结果通知丢失、domain 提交未 ACK、handoff 后重启、文件 materialize 中断均验证恢复。
- 云端正文或被引用事实来源变化不能批准旧版本；批准目录不可变，未批准不能创建 approval 文件。
- 缺持久 budget/policy 字段不能静默恢复成默认预算；新旧 workflow 混用拒绝恢复。

## SDK 与通信前提

独立 Python：`.tools/requirements-python/cpython-3.12.13-windows-x86_64-none/python.exe`。独立 venv：`.venv-requirements-gateway`。SDK `lark-oapi==1.7.3` 已安装，实际导入与 card handler、response 序列化检查通过；依赖固定记录在 `requirements-gateway-lock.txt`。

`requirements_card_gateway.py --database runs/requirements-gateway.sqlite --check` 实际输出：SDK 1.7.3、DB 可写、凭据存在、process_lock=false、websocket_ready=false，返回 1。这表示环境可准备启动，不能声称已连接或订阅有效。

2026-10-07 用户明确指定不用隔离，沿用此前同步消息的特定飞书群。只读检查确认现有 `HUMAN_GATE_CHAT_ID`、`FEISHU_GATE_APP_ID`、`FEISHU_GATE_APP_SECRET` 均存在，未输出凭据。无需重新提供群 ID 或另建测试群；新流程仍需负责人 open_id 来绑定回答和批准权限。再次执行 readiness 检查仍返回 process_lock=false、websocket_ready=false，当前尚未验证真实卡片往返。

随后获准启动官方 SDK gateway：进程锁为 true，但 websocket_ready 持续为 false。对当前应用执行有界官方 SDK 连接诊断，HTTP 接入点查询成功；WebSocket 直连及使用现有环境代理的握手均在 20 秒内超时（TimeoutError），未取得有效连接。SDK 1.7.3 的默认连接参数显式关闭 websockets 15 的环境代理；代理诊断仅在临时探针进程中调整，未改生产源码、全局配置或环境。执行会话中断后锁仍为 true，因此另按本轮锁的 PID 核对网关命令，停止该进程并清理其锁；最终检查 process_lock=false、websocket_ready=false。未停止 Nexus 8766 或此前同步消息的服务。不能把 HTTP 消息发送可用当成卡片回调长连接可用。

## 内容与效率

fixture 独立保存在 `cmd/fixtures/requirements/`，singleflight 原文从用户给定 RTC 需求复制，记录来源与 SHA-256。对应实施方案只作为需求确认后细化深度的参考，没有混入需求承诺。

离线工具的缺样本检查实际返回 INCOMPLETE，六个独立样本均不存在。无 provider usage，token 记为 null；脚本 adapter 的机制验证模型 token 为零。尚无同输入、同模型、同里程碑的旧运行对照，不能报告节省比例。

第一次真实单样本命令被自动审批拒绝，进程未启动：

```text
rtk proxy python -X utf8 cmd/sample_requirements_workflow.py --case singleflight --sample 1 --timeout 900
```

自动审批理由：RTC 派生需求可能属于敏感仓库内容，尚缺具体输入与目标外部 worker 的授权。已向用户列明三类 fixture 和现有 Analyst（DeepSeek V4 Flash）、Solver（GLM 5.3 Flash）、Critic（GPT-6 Luna），请求允许六份独立内容样本。未通过换命令、变更载荷或间接执行绕过拒绝。

即使模型样本通过，仍需工程师逐条盲评与用户/业务 reviewer 确认五个质量轴，才能满足发布门槛。

## 起点文件与旧草稿

实施起点已有未跟踪的需求稿、实施方案、2026-10-06 planning-v1 文档和六个未接入生产的 planning 草稿；另有 `worker01.json`、`worker02.json`、`worker03.json`。worker 文件没有进入重构修改。

六个 planning 草稿按原字节保存为 `.txt`，来源映射及 SHA-256 见 `docs/superpowers/archive/2026-10-06-planning-workflow/manifest.json`。旧文档标为被 requirements-v1 替代；canonical model/compiler/render 保留。旧 test 草稿退出自动 discovery，机制场景以新真实 App 测试验证。

没有手改 generated OpenWiki，也没有向 Codex 全局 config.toml 或 nexus-model-catalog.json 写入。只读核验了受保护文件指纹；主服务模型路由保持原配置。工作区里最初沙箱失败遗留的无读权限临时目录会使 git status 提示 warning，未将其作为工程文件删除或掩盖。

## 剩余真实验收（不再保留旧中书执行版本）

1. 获取具体输入与目标 worker 的授权，完成三个 case 各两份真实正文；不能刷无限重试挑好结果。
2. 工程师与业务 reviewer 完成内容评审，所有轴至少 3/4，无关键缺陷或未经确认承诺。
3. 用户指定的现有飞书群完成卡片往返、云文档读回和明确版本批准。
4. 持续维护唯一新版的回归、历史只读/迁移与受保护配置边界；出现问题修复新版，不启用旧中书运行器。

当前准确状态为“唯一新版已接入、机制回归通过；真实模型内容品质与飞书交互验收待完成”。

## 2026-10-07 单一中书收口交付

本轮按用户指示直接接入新版，删除旧中书状态类、转移边、任务/组审合并器、文档批准恢复、fast-track、旧角色契约与 Skill。新建不传 workflow 即进入 REQ_INTAKE；legacy 选择被拒绝。唯一完整状态图为 RequirementsTransitionRegistry，原 TransitionRegistry 只保留门下子图与通用验证能力。

历史 `--inspect-task` 直接读原快照，不创建 adapter、不修补业务状态；旧 resume 拒绝并提示 `--migrate-task`。迁移保留历史附件和来源，导入原用户请求与明确反馈，新建 REQ_INTAKE，不派发 worker、不继承模型计划或旧批准。活动运行锁会阻止迁移。

共享数据、哈希、文档章节提取、失败分类独立保留。旧协议测试退役；混合测试保留传输、持久化、编译器与门下覆盖，脚本适配器从旧场景中抽出供新流程复用。收口中回归发现并修复了通用失败诊断、门下同轮批准项完成记录及 REMOVE_ITEM 投影处理；中间失败没有作为通过报告。

| 验证 | 实际结果 |
|---|---|
| 全量 `rtk proxy python -X utf8 cmd/run_tests.py` | 655 项通过，51.762 秒 |
| 最后新增历史快照不能被构造器重新标记保护、严格来源 ID 测试：`test_requirements_cutover test_requirements_contracts` | 14 项通过，0.102 秒 |
| 删除最后不可达旧补证分支后：cutover、门下、基础设施重试、回复重试、reducer 与事件通知 | 89 项通过，0.135 秒 |
| 新版真实 App：需求审查→当前正文批准→门下 8 项依赖链→DONE | 全量包含，脚本 adapter 无模型 token |
| 旧模块/Skill/测试归档 manifest | 141 条 SHA-256 校验通过，归档 .txt 不参与导入 |
| `git diff --check` | 通过 |

测试数量下降主要因为旧工作流专用协议、组审与冻结测试随退役代码退出 discovery，不能把 1486 与 655 的数量变化当作效率指标。历史测试原文仍在归档，共享和门下测试继续运行。最后保护测试在全量后单独核验；不将两组测试相加声称另一轮全量规模。

当前改动在工作区，未提交。未修改 Codex 全局配置、worker 配置、generated OpenWiki；未停止 Nexus 8766。角色和机制对比矩阵已写入操作说明。没有运行真实模型样本或飞书群交互；旧版与新版的实际品质/耗时/token 差异尚无对照实测。
