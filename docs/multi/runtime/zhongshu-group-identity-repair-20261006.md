# 最新轮卡住：分组回复身份校验与契约冲突

任务 `task-20261006-3d4062`。13:33:01 进入 `HUMAN_GATE`，原因 `NODE_FAIL_STREAK_EXHAUSTED`。这不是正常需求文档审查门，也不是模型没产出方案。

| 时间（北京时间） | 进度 | 结果 |
| --- | --- | --- |
| 13:08:16–13:09:55 | Analyst 提取需求契约 | 1分39秒，完成 |
| 13:09:55–13:15:58 | Analyst 证据调查 | 6分03秒，完成 |
| 13:15:58–13:25:36 | Solver 正式方案 | 9分38秒，完成；1组、5个成员任务 |
| 13:25:36–13:30:35 | Critic 初审 | 4分59秒；原始回复为 `APPROVE_GROUP`，却在 join 被拒 |
| 13:30:36–13:33:01 | 重试 Critic | 约2分25秒；再次 `APPROVE_GROUP`，再次被拒 |
| 13:33:01 | 连续 node 失败达到 2 次 | 转异常 `HUMAN_GATE`，没有进入正常方案人工审批或门下省 |

启动至异常门约 24分45秒。第二次审查约2分25秒是此次误拒直接造成的重复；随后人工停顿才是当前不推进的原因。

## 根因与证据

上一批效率优化在 `AgentNodeJoiner._join_group_review` 增加身份校验，调用 `review_identity_error` 强制要求模型回复包含业务 revision 和 plan hash。

当前 `cmd/orchestrator/contracts/zhongshu_critic.py` 的真实 `REVIEW_GROUP` schema 不要求这两个字段，prompt_rules 更明确规定：模型不要回显业务 revision/hash，由 Orchestrator 从 dispatch record 注入。在线 skill 同样要求组审不回显业务 hash。

该改动错误地把旧整图审查的“必须回显”规则用于新组审契约。Critic 正确遵循当前契约，传输层完成 JSON、schema、task/request 关联校验并接受回复，join 层却因字段缺失报告 `ZHONGSHU_CRITIC_REVISION_MISMATCH`。这是此次改动引入的代码兼容性回归，不是 Multica skill 未同步或业务方案被否决。

本轮两份原始结果：

- `runs/transport/prompt-bundles/task-20261006-3d4062/task-20261006-3d4062_ZHONGSHU_CRITIC_4_group-01/result.json`
- `runs/transport/prompt-bundles/task-20261006-3d4062/task-20261006-3d4062_ZHONGSHU_CRITIC_6_group-01/result.json`

两份均 `action=APPROVE_GROUP`、`findings=[]`、`remaining_blockers=[]`；schema_hash 和当前契约一致。日志 `multica/orchestrator-20261006-13.log` 的两次 FAIL 均为 `NODE_GROUP_REVIEW_RESULT_INVALID: ZHONGSHU_CRITIC_REVISION_MISMATCH`。

## 修复

当前组审的业务坐标缺失时，由已关联到请求的可信 dispatch binding 补入，然后校验。不改变原始 worker payload，不改变 schema，不要求模型增加字段。如回复实际提供了错误 revision/hash，仍拒绝；group 归属与 finding 成员归属校验继续保留。传输层当前请求关联及状态序列守卫继续生效。

原来的效率测试人为构造了带业务 ID/hash 的回复，漏掉了真实 schema 禁止模型回显的路径。新增真实 `CONTRACT.example_payload()` → schema 校验 → group join 回归，确保契约合法且没有业务 ID 的回复可接受；另测显式错误 revision 拒绝。既有错误 hash、表面改变后重新审查测试保持通过。

离线重放读取本轮两份真实回复，修复后均 `SUCCEEDED / APPROVE_CRITIC`，绑定计划哈希正确，活跃 state 文件哈希未变。证据保存于 `runs/maintenance/20261006-group-identity-repair/verification.json`。

完整安全测试与 Multica 回查结果分别保存于同目录的 `full-tests.log` 和 `multica-check/verification.json`。此次仅改代码和测试；在线 skill 已符合正确契约，无需把合法 agent 回复改成旧协议。

最终 **1316 项测试全部通过，48.628 秒**；Multica 三 agent 回读 `drift=[]`、六份阶段 skill 与本地一致，`ok=true`。`git diff --check` 通过。

## 当前生效边界

修复已落盘。运行中的 Orchestrator 需要安全重新加载代码后恢复异常 gate 的 Critic 步骤；离线重放没有推进生产状态。没有替用户回复“通过”，没有将异常门误当作正常方案审批。8766 活跃服务未停止或重绑。
