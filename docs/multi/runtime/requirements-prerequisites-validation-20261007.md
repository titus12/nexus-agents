# 工作流前置条件修复与验收

## 实际故障

最新任务 `task-20261007-b6bcc7` 已完成一次 Analyst 分析，保存了 10 条事实和 4 个澄清问题，在 `REQ_CLARIFY_PUBLISH` 阻塞。记录的原因是 `decision_actor_ids (--decision-actor-id), ready card gateway` 缺失。任务没有审批人，卡片网关没有进程锁或有效连接。此前的两条真实文本通知验收不代表交互审批链路就绪。

## 已完成修复

- 新任务在创建 Multica 适配器、远端 issue 和模型调用之前检查审批人、群、机器人凭据和 gateway 心跳。
- `--check-prerequisites` 仅检查就绪条件，不创建远端任务、不运行模型。
- `--repair-decision-owner` 可补配空审批人，仅适用于因缺少审批人而阻塞、尚未发卡、无回答/批准且没有已注册 gate 的飞书任务。通过标准持久事件和 reducer 更新，不直接修改快照，不重置模型调用预算或重新分析。
- 已启动独立网关进程（PID 86500），复查 `process_lock=true, websocket_ready=true, credentials_present=true, sdk_version=1.7.3`。没有停止或重启 Nexus 8766。

## 验收

`rtk python cmd/run_tests.py`：697 项通过，90.888 秒。补充的卡片回调回归单独运行 8 项通过，0.689 秒。`git -c core.safecrlf=false diff --check` 通过。

新增用例验证：缺少审批人/密钥/网关在远端调用前拒绝；本地检查模式；补配过程保留 consensus 和预算；重启恢复审批人；通过真实 publication effect + 模拟 HTTP 发布澄清卡；越权回调拒绝；配置审批人的回调推进状态；拒绝覆盖已有审批人、已发布轮次或已有 gate。

自动化测试入口屏蔽真实飞书发送。本次线上验收确认了网关真实连接，没有冒充用户提交回答或批准。

## 业务任务仍待输入

用户需要指定 Gate 机器人对应的真实审批人 open_id。当前任务未擅自绑定人员，仍停留在 `REQ_BLOCKED`，分析结果已保留。

获取并确认真实审批人后，用 PowerShell 变量保存该 open_id，先检查再恢复（`$ownerOpenId` 必须由用户指定）：

```powershell
rtk proxy python -X utf8 cmd/review_orchestrator_v2.py --check-prerequisites --communication feishu --decision-owner $ownerOpenId --runs-root D:\workspace\src\nexus-agents\runs
rtk proxy python -X utf8 cmd/review_orchestrator_v2.py --resume task-20261007-b6bcc7 --repair-decision-owner $ownerOpenId --runs-root D:\workspace\src\nexus-agents\runs --poll-interval 15 --timeout 900
```

正常情况下恢复到 `REQ_CLARIFY_WAIT` 等待用户回答，不意味着 RTC 实施任务已经完成。现有审批人仍不可由本入口更换。
