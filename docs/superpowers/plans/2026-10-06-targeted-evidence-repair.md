# 中书省补证收敛修复（用户已批准）

1. 请求补证携带 item_id 与该任务的 source_requirement_ids，返回内容在传输边界校验，拒绝缺失与错配。
2. 定向补证引用失真由原 worker 在现有有限重试预算内修复，不以降级引用完成补证。
3. 保留 Critic 的 required_change；混合问题补证完成后交 Solver 修订现有方案，纯证据问题仍直达 Critic。
4. 脚本测试覆盖范围、引用、状态恢复、方案保留和纯证据路径；同步三个 agent 及六份 Multica skill 并回读。

不改人工“通过”门槛，不增加重试预算，不重写运行中的任务或 prompt bundle，不停止 8766 服务。
