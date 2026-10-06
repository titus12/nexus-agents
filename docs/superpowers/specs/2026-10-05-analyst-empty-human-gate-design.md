# Analyst 空人工决策修复

用户已授权实施。task-20261005-1349a6 的 Analyst 在 REQUIREMENT_CONTRACT_ONLY 提取三条需求后返回 HUMAN_GATE，questions_for_user/unknowns 都为空，协议和状态机直接接受，造成无意义等待。

接收边界和 Analyst 状态机共用语义校验：HUMAN_GATE 必须携带明确问题（字符串或 question/text/prompt 对象），并有 summary 或问题 reason 说明为何需要用户决定。空数组、空白和无内容对象不算问题。需求提取完成返回 REQUIREMENT_CONTRACT_READY；证据收集完成返回 EVIDENCE_PACKET_READY。

错误回复不折入权威 review，消耗现有 reply 重试预算，反馈明确纠正方式。预算耗尽进入有错误诊断的 BLOCKED，不能再次伪装成人工决策。传输拒绝和旧适配器直接结果都必须覆盖。合法问题应实际展示给用户。PLAN_REVIEW 和人工修订 Solver-only 循环不改变。

同步 Multica Analyst instructions/skill，核对三个 agent 的技能、绑定和保护设置。脚本化应用测试以及完整测试套件验证，不运行耗费模型 token 的验证任务，不修改运行中的 workflow-state。
