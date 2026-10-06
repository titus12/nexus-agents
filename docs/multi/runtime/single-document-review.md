# 中书省完整文档送审

中书省 PLAN_REVIEW 的权威来源仍是 workflow-state 中的 `review.plan_review.markdown`。飞书文档仅提供完整阅读入口；门下省继续使用你批准的同一版本原始正文。

每一版本发布一次完整文档，审核群收到一条短通知和链接。回复对应通知：只有去除首尾空白后的“通过”批准当前版本；其他非空回复交 Solver 修订，机械校验后重新送人工审查；空回复继续等待。人工修订不增加 Critic 轮次。

发布使用企业官方 `feishu-connector` / `lark-cli` 用户身份和既有授权，不读取或管理访问令牌。Windows 默认命令为当前用户目录下的 `plugins/lark-enterprise/scripts/feishu-connector.cmd`；`FEISHU_DOCUMENT_CLI` 可以指定同协议的入口。用户创建的文档对该用户可读，代码不会自动扩大到全员或外部访问。

发布回执位于 `runs/<task-id>/requirements/feishu/vNNN.json`，保存任务、版本、源正文哈希、文档标识、URL 和通知发送回执。源正文和 CLI 导入文件也保存在该目录。通知失败或进程重启时复用文档；创建结果未知时根据唯一标题恢复，不盲目创建第二份。恢复结果缺失、多个候选、正文未完整导入或发布失败时，任务保持人工闸门并记录 `PLAN_REVIEW_DOCUMENT_DELIVERY_FAILED`。

批准前读取文档核对出版时的导出正文。若有人直接改写线上文档，记录 `PLAN_REVIEW_DOCUMENT_APPROVAL_HELD` 并拒绝批准；请把修改内容作为回复发给 Solver，修订为新版本后重新审查。

通知呈现由本地 orchestrator 完成，不需要修改 Multica 上三个 agent 的角色指令或 skill。新代码在新启动的 orchestrator 进程中生效，已有进程不会自动加载。不需要停止或重新绑定当前 Nexus 服务的 8766 端口。

验证仍通过 `python cmd/run_tests.py`，禁止真实通知；脚本适配器覆盖多轮反馈、单条通知、文档复用、失败阻止批准和同一正文交给门下省。
