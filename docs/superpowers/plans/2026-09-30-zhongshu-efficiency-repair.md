# Zhongshu Efficiency Repair Implementation Plan

> **For agentic workers:** Execute in this session with regression checks after each mechanism change.

**Goal:** Remove the observed unnecessary revision cycles and configuration conflicts while preserving independent review and safe execution dependencies.

**Architecture:** Python contracts and pure convergence policies remain authoritative. Local runtime documents are the publication source for the three Multica agents; read-back verification detects drift.

**Tech Stack:** Python unittest, PowerShell, Multica CLI.

## Tasks

- [x] Add exact original-request quotation to freshly extracted requirement contracts; inject the request into requirement-mode context and validate provenance without invalidating legacy persisted plans. Test the fabricated production-disabled statement from the incident.
- [x] Normalize P2/P3-only group revision to approval with retained follow-ups; keep P0/P1 and evidence/infrastructure failures blocking. Align stall progress with actual finding identities and provide explicit revision-review scope. Add meaningful group convergence regressions.
- [x] Preserve human plan sign-off by default for all task types, including legacy null and fast/mechanical freeze; only explicit false bypasses it. Persist the complete review packet before Menxia dispatch and send full group documents in bounded UTF-8 parts. Require explicit approval and recognize replies to every part. Preserve existing paused runs and dependency-safe partial release. Fix human reply pagination and watchdog failure attribution. Test these independent paths.
- [x] Add role-aware admission keys and role counts without conflating role with phase; test simultaneous Analyst/Critic work.
- [x] Replace the three obsolete Multica permanent instruction sets with local canonical role instructions. Revise three Zhongshu runtime skills and their shared REQUIRES.md; retire the obsolete mandatory option matrix attachment. Synchronize remote Menxia skill bodies with existing local overflow/measurement rules; preserve their attachments and all model/runtime settings.
- [x] Run `rtk proxy python cmd/run_tests.py` with the selected regression modules and then the broader suite. The runner suppresses all external notifications. Do not restart/rebind the active Nexus service.
- [x] Publish the tested skill/instruction files via Multica CLI, save before/after snapshots under ignored runs/maintenance, and compare server content hashes and protected agent settings. Report measured validation and remaining model/runtime limitations.
- [x] Correct the latest live regression: rebuild outgoing Critic dispatch from the post-fold group documents, restore mandatory default human pre-review, deliver full document parts and require explicit approval before Menxia. Add real OrchestratorApp and document/hash regressions.

## 验收记录（2026-09-30）

- 最终完整离线套件：1247 项，38.930 秒，全部通过。日志：`runs/maintenance/20260930-zhongshu-efficiency/full-suite.log`。
- 新增真实 OrchestratorApp 回归：P2-only 组无需 Solver 修订即可冻结并走到 DONE，finding 仍保留在台账；原有 P1 修订、停滞、冻结、依赖和门下省测试通过。
- Multica 三个目标 agent：Analyst-reasonix-v4、Solver-opencode-v4、Critic-codex-luna。常驻指令、中书省正文/附件、门下省正文已同步；最终独立回查 drift=[]、ok=true。
- 模型、thinking level、runtime 配置、全部 skill 绑定与发布前一致；门下省附件和公共 lang-checklist 完整保留。前后快照：`runs/maintenance/20260930-zhongshu-efficiency/published/`；最终回查：`runs/maintenance/20260930-zhongshu-efficiency/final-check/verification.json`。
- 后续漂移检查入口：`rtk proxy python cmd/sync_multica_review_agents.py --check`；需要重新发布时使用 `--apply`。脚本先完成所有预检/快照，再发布，最后重新从服务端读取内容并校验。

## 实际生效范围与耗时预算

Multica 服务端数据已更新。本地 Python 机制由后续新启动的运行进程加载；现有人工 gate 的审批状态保留，当前 Nexus 服务持续可用。

本轮验证机制正确性，没有执行消耗模型 token 的完整 live 测试。因此 30–35 分钟是根据故障轮移除失败运行和多余返工的保守估算，20–25 分钟是下一轮验收目标，均不含人工等待，不能写成已达成的实测。

Multica runtime 的 10 分钟 idle watchdog 仍存在；已修复其被无结构错误评论覆盖为 AGENT_REPLY_UNSTRUCTURED 的归因问题，并通过缩小工作范围、取消旧协议/强制方案矩阵和减少返工降低触发机会。若下一轮仍发生长时间无语义消息，需要独立验证 daemon/runtime 的 watchdog 配置或模型响应行为；不能以延长 Orchestrator 的 1500 秒 deadline 宣称已经解决远端 10 分钟空闲限制。

需求原文引用校验能拒绝伪造/缺失引用；需求解释与所引用原文是否语义一致仍由角色纪律和 Critic 审查共同保证。

## 最新轮复查与修正（2026-09-30，task-20260930-1d8133）

- 15:30:47 启动中书省，16:21:00 直接进入门下省，约 50 分 13 秒。前述 20–35 分钟目标尚未实测达成。
- 原计划确实为 1 个组 / 4 个 item：prompt 收敛、门下省并发瓶颈、其他优化维度、汇总报告；前三项无依赖，汇总依赖前三项。Solver 把四个维度归为一个整体审查报告，这是分组策略而非条目丢失。门下省 max_concurrent_groups=1，同角色 agent pool_size=1；当前组级并发没有收益，不能把 configured max_concurrent_items=3 当作本轮实际并行度。
- 人工预审缺失源于上一轮误把只读任务的 null 默认解释为免审批。已纠正为默认开启，null 也开启，fast_track / mechanical_freeze 不代替人工批准；仅显式 false 可以关闭。保留现有任务状态，不代签批准。
- 文档 effect 原先排在门下省长任务后面，日志证明直到 16:36:54 才写入，晚于门下省交接约 15 分 54 秒。现改为先写完整审阅稿与各组原文，通知完整分段送出；普通文字不批准，明确批准后才继续。分段回复跨页可识别，发送失败不记录为成功。
- Solver 第一次修订已经提交 [v2] group_docs，下一轮 Critic capsule 却仍是 [v1]。根因是父类先创建 dispatch，子类随后才将新文档写入 review update。现从完整更新后的 review 重建 dispatch；回归核对实际 worker prompt 原文与 doc_hash。
- 当前审阅材料已恢复在 `runs/task-20260930-1d8133/zhongshu-plan-review.md`（含完整附录）和 `zhongshu-requirements-group-000001.md`（组原文）。不把这次事后恢复视为当前 live run 已经经过人工批准。
- 最终完整离线套件：1254 项，43.159 秒，全部通过。日志：`runs/maintenance/20260930-plan-review-correction/full-suite.log`。原有冻结、部分释放、P1/P2、门下省、恢复与传输检查通过；仅更新旧测试中“dispatch 必为第一个 effect”的假设，新增断言文档必须在 dispatch 前。
- 16:39 Multica 独立只读回查：三 agent 的常驻指令、中书/门下 skill 正文及相关附件均无漂移。验证：`runs/maintenance/multica-agent-sync-20260930T083934Z/verification.json`。本次修正在本地 Python FSM/通知机制，远端资产无需重复发布。
- 本地机制修正由新启动的运行进程加载，当前已进入门下省的 live run 不自动回退；8766 Nexus 服务持续可用，未触发额外 live 模型运行或手工飞书消息。
