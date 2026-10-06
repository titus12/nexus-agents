# Zhongshu Reliability Implementation Plan

**Goal:** 在保留品质与人工审批不变量的前提下，消除错误重派发和可避免的引用/协议返工。

**Architecture:** 使用现有 FSM/reducer/transport 的边界；在纯策略模块处理 Critic delta disposition 和证据复用，在传输层完成保真引用拆分与完整重试上下文。

**Tech Stack:** Python、unittest 脚本适配器、现有 Multica CLI。按用户授权在当前会话执行，不混合提交已有工作区修改。

## 任务 1：关闭回写与有界审查

- [x] 新增 cmd/test_zhongshu_reliability.py，复现两个 Critic RESOLVED 响应在状态仍 OPEN；期望只派发剩下的真实 finding。
- [x] 在 domain/policies/critic_dispositions.py 实现纯函数；只处理 Critic 的 group review（task review 保留原有关闭逻辑），校验组成员、known ID、矛盾重提和 P0 显式处理，保持跨组隔离。
- [x] domain/states.py 在合并 finding 后应用明确 dispositions；runtime/agent_effects.py 校验 group response 范围并保留有效 peer。
- [x] 运行 `rtk proxy python -X utf8 cmd/run_tests.py test_zhongshu_reliability test_zhongshu_convergence_e2e test_linear_fsm_entrypoint`。

## 任务 2：保真引用与重试输入

- [x] 复现长引文导致远端重跑与原草稿 500 字符截断，覆盖无丢字拆分、ID 引用同步、失真保留拒绝、输入不可变。
- [x] quote_gate.py 添加只对已验证原文执行的有界拆分；adapters.py 调用并携带有界完整 raw_reply；runtime/nodes.py 提高拒绝原因上限且保留文件 fallback。
- [x] 契约声明引用拆分关联字段并修正初始降级/定向修复描述冲突。
- [x] 运行 `rtk proxy python -X utf8 cmd/run_tests.py test_zhongshu_reliability test_evidence_quote_gate test_targeted_evidence_repair test_worker_reply_reask`。

## 任务 3：有出处的证据复用

- [x] 定向补证附当前 Zhongshu 证据池的有界有效切片；共享事实通过 source_evidence_id 明确引用，source/quote 不得篡改。
- [x] 扩充 analyst 合同与 evidence_scope_rejection，保持目标 item/requirement 检查和跨阶段隔离；Critic 可见源证据与关联说明。
- [x] 覆盖篡改、未验证/未知 ID 和累积证据投影，确保共享不会授权任务验收通过。

## 任务 4：审计、回放与发布

- [x] 审计现有 worker retry identity、terminal missing-result grace、lease/partial salvage、snapshot恢复、freeze/human gate 测试，补齐发现的真实缺陷。
- [x] 运行安全全量测试，保存日志及当前任务的只读回放结果。
- [x] 对齐三 agent instructions、中书三 skill、门下三 skill；执行 sync_multica_review_agents.py 并回读核验。
- [x] 写清已修复项、验证证据、运行中代码生效限制及仍需实测的时间目标。
