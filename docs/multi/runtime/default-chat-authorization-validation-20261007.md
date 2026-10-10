# 默认单人群授权与旧命令兼容验收

用户确认群内只有本人和机器人，并明确要求默认不需要 `--decision-owner`。

## 最终行为

- 新任务没有显式 owner 时，持久化 `decision_owner_policy=chat-members`，启动不查询成员或要求 open_id。
- 只接受由 Gate 官方 SDK 收到并核对 app、目标群、gate token、page、message receipt 的真人回调；domain 只在 gateway bridge 的已验证群标记存在时接受空 owner 列表。
- 澄清回答、冲突回答和批准共用授权判断。批准仍进入 `REQ_APPROVAL_VERIFY`，必须核对当前文档与云端内容才能继续。
- 指定 owner 时保持只允许指定人员的行为。历史快照缺少新字段时仍采用 explicit，不擅自重写旧任务的权限配置。

## 验收

从 `D:\workspace\src\nexus-agents\multica` 执行，不带 owner：

```powershell
python -X utf8 "D:\workspace\src\nexus-agents\cmd\review_orchestrator_v2.py" --check-prerequisites --runs-root "D:\workspace\src\nexus-agents\runs"
```

实际返回 `ready=true, communication=feishu, decision_actor_ids=[], owner_policy=chat-members`，没有创建远端 issue、调用模型或发消息。

默认授权模块 6 项通过（1.777 秒），覆盖旧命令 context 创建、无额外成员查询、显式 owner 拒绝非指定人员、默认回答/审批回调、错误群/消息拒绝和快照恢复。相关回归 38 项通过（1.631 秒，新增审批回调用例之前）。HTTP 均模拟，测试入口禁用真实通知。

最终 `rtk python cmd/run_tests.py`：711 项全部通过，92.492 秒；`git -c core.safecrlf=false diff --check` 通过。

独立 gateway 已重载新代码；仅停止了经进程命令与锁 owner 核对的旧 gateway，未重启 Nexus 8766。就绪检查确认 WebSocket 和进程锁正常。

本次未执行真实 RTC 全链路，也未代替用户回答或批准。用户原来的 --new 命令无需新增 owner 参数即可进入工作流。
