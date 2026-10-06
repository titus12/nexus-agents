# Citation Recovery Implementation Plan

**Goal:** 消除同文件路径别名误拒及不同类型故障叠加触发的中书省早期熔断。

**Architecture:** evidence_repair 纯策略比较工作区文件定位符；quote gate 对有效引用恢复池中的权威字段；状态机利用 last_failure 分类计数，保持各类预算与冻结批准不变量。

**Tech Stack:** Python、unittest 安全 runner、Multica CLI、只读归档回放。按用户已批准方案在本会话内执行。

- [x] 在 cmd/test_citation_recovery.py 复现绝对路径变相对路径被拒与 reply→external 误熔断，验证字面引文/行号/文件变化仍拒绝。
- [x] evidence_repair.py 增加规范化定位符比较及权威引用恢复；adapters.py 的 quote gate 前应用；拒绝原因给出具体 changed fields 和 expected source/quote.path。
- [x] states.py 的 _next_node_fail_streak 接收 error_code，仅中书省按 _failure_budget(last_failure.error_code) 区分；调用路由与记账共用结果。context.py 修正注释，不新增持久化字段。
- [x] 验证同类失败仍熔断、不同类保留独立累计预算、DTO 重启后行为一致、Menxia 不变；更新过时的混合类型计数断言。
- [x] 执行 `rtk proxy python -X utf8 cmd/run_tests.py test_citation_recovery test_zhongshu_reliability test_targeted_evidence_repair test_node_fail_brake test_infrastructure_retry`，再执行完整安全 runner。
- [x] 回放真实拒绝回复与 NODE_COMPLETED/FAIL 事件，保存仅维护目录输出，明确仍存在的真实缺陷。
- [x] 对齐本地契约、三 agent 指令与中书省 skill，发布后回读核验门下省 skill 与受保护配置。
- [x] 检查 Reasonix 的原始失败事件；保留真实引文错误、工作进度和人审不变量，在技术熔断上恢复当前原任务并核验。
