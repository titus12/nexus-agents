# 重试与协议对齐审计（2026-10-07）

## 结论

反复重试中存在真实问题，不能全部归为正常容错。最新 18:26:20 启动的 task-20261007-9d7ff3 三次返回均被拒绝，18:31:50 用完两次修复预算后进入 REQ_ENGINE_ERROR:6。本次仅检查日志、返回文件与本地协议，未修改执行代码、恢复任务或新增模型调用。

## 证据

1. 18:28:39，attempt-1 schema_error_count=0、semantic_error_count=0、evidence_error_count=10；十个事实引文全部不能在源码中连续匹配。原 result.json 第一条引用包含 `public sealed class RtcValue ... private RtcValue(...) ...`，第二条拼接了 `var fields ... var items ...`。这不是网络失败；实际发送 schema 已在 quote.description 明确禁止省略和拼接。模型没有遵守已提供的引文约束。
2. 同一 attempt-1 已包含 assume_perf_scenario 的 disposition=accepted。attempt-2 保留该字段，引文检查全部通过，但 18:30:29 报 MODEL_CANNOT_ACCEPT_ASSUMPTION，随后进入第二次重试。
3. 实际发送 context.json 的 AssumptionRecord.disposition enum 包含 proposed、accepted、deferred、rejected；consensus.py 禁止模型把非已接受假设设为 accepted。这是状态 DTO 枚举直接复用于代理写入协议造成的权限表达缺口。现有已接受假设可原样保留，不应把所有 accepted 都简单禁掉。
4. requirements_agent.py 的领域试合并要求 schema、语义及证据检查全部无错误才执行。因此 attempt-1 的错误引文遮住了已经存在的 accepted 假设错误。反馈只要求修复十处引文；attempt-2 修复后才发现假设错误。第一次打印 semantic_error_count=0 不表示完整领域合并已通过。
5. attempt-3 仍返回 disposition=accepted，只新增 assumption_id，18:31:50 再次失败。反馈只有 `analysis/questions: MODEL_CANNOT_ACCEPT_ASSUMPTION`，未定位 `analysis.assumptions[0].disposition`，也未明确要求将新增假设改回 proposed。第三次误修的结果说明现有反馈不足以稳定引导修复；没有模型内部推理记录，不能确定它为什么选择只补 ID。

## 最近样本

| 任务 | 已记录内容失败次数 | 模型调用数 | 原因 |
| --- | --- | --- | --- |
| 9d7ff3 | 3 | 3 | 十条错误引文，随后连续两次假设权限错误，最终停止 |
| 150f15 | 0 | 1 | 首次分析通过 |
| 01acbc | 1 | 2 | 七条错误引文 |
| deb673 | 2 | 3 | 七条错误引文，随后仍有一条错误引文 |

样本说明最近多轮首答引文失败重复发生，但不能声称每轮都重试。轮询 running 日志和重试后的恢复消息也不能算作额外模型重试。

## 应修复的位置

代理写入协议须说明新增假设只能 proposed，已有 accepted 只能保持原值；在人类决定保护的独立预检查中报告违规。证据错误和领域权限错误应在首次返回一起反馈，避免串行发现耗尽修复预算。源码引文继续保留精确校验，应改善取证与提交前检查，不能以放宽引文或提高重试次数掩盖缺陷。尚未核验本次 Multica 远端角色 skill 的实际安装内容，不能把这些错误归因于远端 skill 版本。

原始审计提取及返回样本：runs/maintenance/retry-alignment-evidence.json。日志：multica/orchestrator-20261007-18.log 第 416、430、530、533、535 行。实际第一次分派协议：runs/transport/prompt-bundles/task-20261007-9d7ff3/task-20261007-9d7ff3_e1_1_REQ_ANALYZE_attempt-1/context.json 第 326、397 行。
