# Menxia Alignment Implementation Plan

**Goal:** 对齐组级门下省代码契约、本地 skill、Multica skill，并让可修复 Solver 文档错误有界纠错。

**Architecture:** 六小节定义来自 domain/menxia_doc.py；骨架与提示复用，joiner 仅分类结构错误，组状态保存预算和修复输入。三个角色保留各自文档权限与签核边界。

**Tech Stack:** Python dataclasses / scripted adapters / Multica CLI。

## 1. 文档协议

- [x] 修改 cmd/orchestrator/domain/menxia_doc.py：增加 body_template()、is_initial_body()，render_initial 默认提供六标题；body_part_violations 拒绝空小节；首轮保护基线、建议、账本和签核。
- [x] cmd/orchestrator/contracts/menxia_group_solver.py 复用标题提示并统一首轮版本；states.py 派发带 body_part_titles、expected_doc_version 和 initial_document_fill。
- [x] cmd/test_menxia_alignment.py 验证骨架填写、空标题拒绝与首轮保护；python cmd/run_tests.py test_menxia_alignment。

## 2. 组内纠错

- [x] MenxiaGroupState 增加 reply_retry_count/reply_rejection/rejected_doc_markdown，并实现 from_dict 兼容旧状态。
- [x] joiner 将全为可修复正文错误的 Solver 回复变为 REPAIR_SOLVER_REPLY，草稿限长且不折入文档；超预算改为 MENXIA_GROUP_REPLY_RETRIES_EXHAUSTED。
- [x] group policy 映射纠错到 SOLVING，仅增加 reply 预算；使用 replace(row, ...) 保留需求原文。Solver 阶段仍有 SOLVING 且无 reviewer 可工作时聚合 RETRY。
- [x] 派发注入组自身的拒绝原因与草稿；成功后清理纠错输入。有效 Solver 无论声明 READY_FOR_CRITIC 或 FEASIBLE 都先走 Analyst。
- [x] 回归测试验证预算耗尽、旧快照 round-trip、其它组不重跑、保护段仍阻塞。

## 3. Skills 与流程文档

- [x] 重写 docs/multi/menxia-{solver,analyst,critic}-skill.md 为组级主流程；保留每角色证据/反证方法、数值纪律、单文档约束、旧模式按 state 分流。
- [x] 更新 docs/multi/runtime/agent-instructions 三角色的 Menxia 分流说明。
- [x] 写 docs/multi/runtime/menxia-flow-alignment-20261006.md，明确实际状态、各角色产品与权限、预算和人工门。

## 4. 验证与发布

- [x] python cmd/run_tests.py test_menxia_alignment test_menxia_doc test_menxia_group_join test_menxia_group_policy test_group_doc_loop test_human_document_loop。
- [x] python cmd/run_tests.py 全套通过；维护目录保存日志和旧回复离线重放结果。
- [x] python cmd/sync_multica_review_agents.py --apply --publish-for-next-dispatch；回读三个 agent 和两阶段技能，验证 drift=[]、保护设置不变。
- [x] 报告代码和线上配置生效边界；不手改或恢复生产任务状态，不重启 8766 服务。
