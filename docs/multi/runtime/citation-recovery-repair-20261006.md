# 中书省引用与技术熔断修复（2026-10-06）

本次针对 `task-20261006-63df09` 在 17:39:35 进入 `HUMAN_GATE / NODE_FAIL_STREAK_EXHAUSTED` 的两个已复现机制缺陷。此前第 5 波存在回复校验失败，第 7 波 worker-02 则发生 Reasonix 运行失败；旧实现按同一状态累计，将两类故障算作两次连续失败，提前熔断。

## 修复与质量边界

1. `evidence_repair.py` 将工作区内同一文件的绝对路径、相对路径、Windows 分隔符及大小写作词法等价比较。有效复用由 quote gate 恢复事实池中的原始 source/quote；不改写事实或引文。原文、行号、不同文件、符号后缀、额外调查声明仍严格校验，不通过文件系统或符号链接推断等价。
2. 中书省节点连续失败计数沿用现有 `_failure_budget` 分类。回复失败与外部运行失败切换时，新类型的连续次数从 1 起算；独立累计预算不清零，同类连续失败仍熔断。旧快照缺失 last_failure 时保守保留旧计数。门下省机制不变。
3. 拒绝反馈明确 changed fields、期望 source 与 quote.path；复用证据不得附加新调查声明，新调查应提交独立 evidence_update。

## 真实归档回放

- 第 5 波 worker-02 attempt-2：原文 277 字符与行号相同，仅 quote.path 从工作区绝对路径变为相对路径。旧实现拒绝；修复后整个回复通过 scope 与 quote gate，引用恢复为池中权威字段。
- 第 5 波 worker-01 attempt-2：除路径别名外，source 还增加了 `(87-90) + full repo search`。新实现仍拒绝新增调查声明，并明确只标识 source 错误。
- 用归档第 7 波上下文与实际 FAIL 事件回放，结果从 HUMAN_GATE 变为 RETRY，external_retry_count=1、node_fail_streak=1；reply 预算保留。回放前后原任务快照、事件日志 SHA256 一致。
- 第 7 波 worker-01 的 inline 回复曾触发 3 个 QUOTE_MISMATCH；后续文件结果已验收并保存，因此恢复时沿用文件结果，而不是接受有误的 inline 版本。

## 测试与 Multica 对齐

- 聚焦安全 runner：69 项通过；最终完整安全 runner：1354 项通过，51.571 秒。测试没有发送外部通知。
- Multica 发布后回读：Analyst 的指令与 skill、本次三 agent 共用 REQUIRES.md 均与本地一致，三角色 drift=[]；Solver/Critic 其余内容保持一致。
- 模型、runtime、skill 绑定受保护设置保持不变；门下省三份 skill 与本地一致，附件不变。发布供下次派发读取，不修改已执行任务的 prompt。
- git diff --check 成功；仅有仓库原有 LF/CRLF 提示。

## 原任务恢复

17:55:36 按用户“修复”授权在技术熔断检查点恢复原任务；旧 PID 1778612 → 新 PID 1786168。使用正式 `--resume` 事件路径，不直接编辑 workflow-state 或事件日志，不提供人审答案。

恢复后第 9 波为 ZHONGSHU_ANALYST，只派发 `zhongshu_analyst-worker-02`，保留已验收 worker-01、worker-03。另一任务 `task-20261006-c84b33` 的 PLAN_REVIEW 快照与日志哈希未改变。Nexus 8766 健康检查 HTTP 200。当前任务仍在运行，方案尚未完成；只有收到用户对当前统一方案回复“通过”，才可进入门下省。

恢复沿用既有连续次数与预算，仅通过正式操作员恢复授予下一次执行；没有绕过累计预算或批量重置失败记录。

## 未解决的独立运行故障

Reasonix 第 7 波 worker-02 的第二次 run 为 `c3dada47-6a63-472a-81e7-015e243da69b`，17:35:08 创建 session，17:35:44 返回 `stopReason=error / agent_error.unknown`，约 37 秒。daemon 显示 timeout=0s、idle watchdog=0s；并非本流程 1500 秒超时。run-messages 记录工具调用及返回，却没有底层错误或最终结果；本地该任务 logs/output 目录为空。

因此本次已修复错误分类与路径误拒，不能宣称已经修复 Reasonix 内部崩溃。若该外部故障持续发生，同类失败与累计预算仍会触发技术暂停，避免无限消耗。

## 证据文件

维护目录：`runs/maintenance/20261006-citation-recovery/`。

- `offline-replay.json` / `replay.py`：只读归档回放。
- `full-tests.log`：完整测试结果。
- `multica-sync/verification.json`：三 agent/skill 发布回读及保护项。
- `resume-verification.json`：恢复进程、状态、人审与另一任务不变量。
- `resume-backup/`：恢复前快照与事件日志备份。
- `resume.stderr.log`：恢复后的派发日志。
- `reasonix-run-messages.json`、`daemon-diagnostic.log`：运行失败诊断，后者已去除环境敏感值。
