# 卡片回调日志验收（2026-10-07）

本次仅补充观测，不代替用户回答、不修改任务快照或回调历史。

日志统一写入 `multica/orchestrator-YYYYMMDD-HH.log`，保留 72 小时。网关不再关闭整个 SDK logger；SDK 警告/错误转为脱敏记录，仅记录调用位置、异常类型、数字错误码，不输出原始 SDK 消息、连接 URL、凭据、卡片 token 或回答正文。

## 关键事件

| 事件 | 含义 |
| --- | --- |
| CARD_SDK_CALLBACK_RECEIVED / CARD_CALLBACK_RECEIVED | SDK 分发及网关接收 |
| CARD_CALLBACK_REJECTED / CARD_CALLBACK_ERROR_RESPONSE | 校验失败，返回错误提示 |
| CARD_CALLBACK_NOT_ACKNOWLEDGED | 存储或其他异常，不确认成功 |
| CARD_CALLBACK_COMMITTED / CARD_CALLBACK_DUPLICATE | 提交事务后记录入库或去重结果 |
| CARD_CALLBACK_RESPONSE_READY | 返回 SDK 的提示已构建，尚不表示平台已收到网络响应 |
| CARD_BRIDGE_WAIT | 等待消息回执验证，同一回调每分钟最多一次 |
| CARD_BRIDGE_REJECTED / CARD_BRIDGE_QUEUED | 事件转换拒绝原因或已进入领域事件队列 |
| CARD_INBOX_ACK | 回调状态实际改变；关联领域事件 ID |
| CARD_ANSWERS_COMMITTED | 回答已提交，显示新增回答数量及剩余问题 ID |
| REQUIREMENTS_EFFECT_STARTED / FINISHED / FAILED | 卡片发布、刷新、通知等副作用执行结果 |
| CARD_REFRESH_FAILED | 回答保存后刷新卡片失败 |
| WORKFLOW_HEALTH | 每分钟显示任务状态、序号、轮次和剩余问题 |
| GATEWAY_CONNECTION_CHANGED / HEALTH / CONNECT_FAILED / SDK_DIAGNOSTIC / HEARTBEAT_FAILED | 连接变化、每分钟队列统计及脱敏错误 |

## 检查结果与限制

- 最终版本全套测试 715 项通过（94.469 秒）；专项测试 21 项通过。测试全部通过 `cmd/run_tests.py`，阻止真实通知和模型调用。
- 已恢复同一任务 `task-20261007-d5080d` 的等待进程，新日志记录序号 6、`REQ_CLARIFY_WAIT`、4 个回答和剩余 `q-000005`。
- SQLite 仍只有首次提交的 inbox 1，状态 DONE；没有第二次提交的持久回调记录。之前网关缺乏回调/拒绝/传输日志，无法还原第二次提交是否到过 SDK。
- 网关重载后观察到连接 TimeoutError。独立只读网络探针成功获取平台长连接地址，但 `msg-frontier.feishu.cn` 的 socket DNS 解析返回 Windows 11001；系统 `Resolve-DnsName` 查询也超时。直接在允许网络的进程启动仍连接超时。此证据说明当前连接不可用，不能证明历史提交失败也由同一原因导致。
- 网关继续按 SDK 机制重试。当前尚不能验收真实卡片再次提交会继续推进；需要先恢复该域名的网络解析，再观察 `GATEWAY_CONNECTION_CHANGED ready=true` 和真实回调记录。
- 没有修改系统 DNS/hosts、Codex 配置或用户回答；没有启动新的模型测试任务。
- 通过 `netstat -ano` 确认 Nexus 8766 仍由 PID 21060 监听。

## 连接恢复复验

2026-10-07 15:47:03，原网关 PID 100412 自动重连成功，记录 `GATEWAY_CONNECTION_CHANGED ready=true`；随后的分钟心跳持续为 true，`--check` 返回成功。15:49 独立探针复测成功获得长连接地址，并建立 TCP、TLS 连接。

没有改动系统 DNS/hosts，也没有硬编码 IP 或跳过 TLS 校验。此前 DNS/连接失败为本次检查时观察到的暂时故障；网络恢复后 SDK 重连机制确实完成恢复。不能据此宣称历史第二次提交失败的根因已经确定。

已请用户重新提交当前卡片第 5 个问题，等待核对真实回调和原任务推进；在收到平台回调前，真实卡片端到端验收仍为待完成。

## 真实回调验收通过（15:53）

用户反馈重新提交后仍未推进，排查确认当时旧连接没有 SDK 回调记录。本机仅存在一条网关进程链；原卡片的发送应用和群均与当前网关一致。补充仅记录类型、消息关联 ID、分片序号的数据帧诊断后重载网关，保留原 SQLite、任务进程及全部回答。

- 15:53:24，网关 PID 97596 收到 `type=event` 的真实平台数据帧，SDK 分发 `card.action.trigger`。
- 平台事件 `ec06e75466c212090ad85014fe004505` 持久化为 inbox 2，原消息 ID `om_x100b636a1dc864a4dd449b2e6d7beab`。
- 15:53:25，`requirements-human:2` 提交成功，第 5 个问题有回答，轮次为 answered，剩余问题为空。
- 原任务 `task-20261007-d5080d` 从 `REQ_CLARIFY_WAIT:6` 转为 `REQ_ANALYZE:7`，inbox 2 为 DONE。
- 下一轮分析请求 `task-20261007-d5080d:e2:7:REQ_ANALYZE:attempt-1` 已触发；15:53:43 Multica 记录 running。
- 数据帧诊断及网关专项回归 13 项通过；此前完整回归 715 项通过。

验收范围：真实卡片提交、持久化、回答受理、原任务恢复及后续分析启动。RTC 优化任务仍在运行，这不是整项 RTC 审查完成的声明。重载恢复了实际回调接收，但旧连接为什么在显示在线时没有收到此前提交，现有证据仍不足以确定；未将短暂 DNS 故障当作已确认的全部根因。
