# Analyst 空人工决策修复（2026-10-05）

task-20261005-1349a6 的首轮 Analyst 提取了三条需求，却返回 HUMAN_GATE，questions_for_user、unknowns 都为空。原来的 JSON 契约只验证动作枚举，状态机直接接受这一动作，导致 17:08:50 开始等待没有具体问题的人工决定。这是协议语义校验缺失；日志不能证明模型内部为何选错动作。

## 实施结果

- `contracts/analyst_gate.py` 统一问题提取和语义校验：字符串或 question/text/prompt 对象必须包含非空问题，并有 summary 或 question.reason 说明原因。
- 接收端 `validate_role_result_shape` 拒绝 Analyst 的空 HUMAN_GATE；Analyst 状态机另行兜底，事件名携带动作但 payload 没有 action 也不能旁路。
- 直接错误回复和传输拒绝均走现有 reply 重试预算（默认最多自动重试三次）。错误反馈说明完成提取应返回 REQUIREMENT_CONTRACT_READY、完成证据应返回 EVIDENCE_PACKET_READY。失败 worker 的各自反馈保留。
- 不把拒绝回复写入权威 review；连续错误不会触发通用两次失败的人工门。预算耗尽进入 BLOCKED，持久化 ANALYST_HUMAN_GATE_RETRIES_EXHAUSTED 和具体拒绝原因。
- 真正的澄清请求照常进入人工门，并把具体问题和原因显示给用户。
- 首轮动态 prompt、Analyst 的 Multica instructions 和 Zhongshu skill 明确成功动作及人工门条件。

PLAN_REVIEW 仍须用户精确回复“通过”才能进入门下省；其它非空意见仍只交 Solver 修订，再送人工审查。此修复不改变该循环。

## 验证证据

新增 `cmd/test_analyst_empty_human_gate.py` 共 11 项回归，覆盖接收校验、空白占位符、真实问题、状态机旁路、拒绝状态隔离、传输连续错误、预算耗尽、各 worker 反馈保留、成功动作提示、脚本化实际应用流程。

最终完整套件通过 **1298 项**，46.618 秒；专用入口为 `cmd/run_tests.py`，不通知飞书，不调用在线模型。记录：

- `runs/maintenance/20261005-analyst-empty-human-gate/full-suite.log`
- `runs/maintenance/20261005-analyst-empty-human-gate/full-suite.json`

实际测试回复 SHA256 为 `18dc2e8e61cec5c7bd257362bdaf7434d33b3eeaf2481502d028283ef2989936`。相同原始回复在修复后被接收校验拒绝；状态机离线重放目标是 RETRY_WAIT，reply_retry_count=1，不折入 review。仅将动作改为 REQUIREMENT_CONTRACT_READY，即可通过校验并生成后续 evidence node_dispatch。没有执行任何效果、调用模型或修改生产状态。

离线重放记录：`runs/maintenance/20261005-analyst-empty-human-gate/live-artifact-replay.json`。修复前证据仍保留在 `runs/maintenance/analyst-gate-evidence/replay-evidence.json`，其旧行为刻画脚本不属于正式测试发现范围。

## Multica 同步与生效范围

已发布 Analyst instructions 和 skill，并回读核对 Analyst、Solver、Critic 的指令、Zhongshu/Menxia skills 和附件。三个 agent 均 drift=[]，模型、运行配置、技能绑定等保护设置未变。同步未创建任务或向他人发送消息。

记录：`runs/maintenance/multica-agent-sync-20261005T102844Z/verification.json`，ok=true。

本地运行中的编排器进程未重启，8766 Nexus 服务保持可用。Multica 配置供后续派发读取；已生成的 prompt bundle 保持原样。旧任务 task-20261005-1349a6 的 HUMAN_GATE:2 仍然是此前持久化的状态，配置同步不会追溯撤销它。此轮未恢复或重写该任务；编排器需要重载新代码，既有任务的恢复必须另行执行，不能把这次修复或“继续”冒充正式 PLAN_REVIEW 的“通过”。
