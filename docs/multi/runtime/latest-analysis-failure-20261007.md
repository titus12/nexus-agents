# 最新分析失败根因（2026-10-07 16:54）

任务：`task-20261007-4bd681`；Multica issue：`c006e06e-f249-4410-8b41-a521cbb2f0ae`。

## 失败链路

| 时间 | 结果 | 核验依据 |
|---|---|---|
| 16:39:36 | 首次分析返回 `FACT_EVIDENCE_INVALID` | 12 条源码事实中 5 条引文不是源码连续原文，进入第一次修复 |
| 16:41:11 | 第二次返回触发 `LOCAL_KEY_COLLISION` | 假设和问题同时使用 `runtime-target`，merge_questions 拒绝共享别名 |
| 16:42:16 | 第三次返回触发 `invalid question revision/options` | 五个问题均为 `revision=0`，Question 要求 revision >= 1；选项 ID 未重复 |
| 16:42:17 | 故障通知成功 | 消息 ID `om_x100b6354104038a8c47f963ff32b849` |

最终状态 `REQ_ENGINE_ERROR:6`，分析尚未提交，也未进入需求问答或草案生成。

## 根因及回放

`record_schema(Question)` 将 revision 暴露为普通 integer，没有表达至少为 1 的领域约束；通用 schema 校验也没有数值边界校验。跨记录 local_key 唯一性在领域合并时检查，角色协议未完整表达这一约束。

运行时先验证引文，再在领域合并时遇到第一个错误。因此同一份结果中的其他问题被遮蔽，有限的两次格式修复逐个发现不同错误，预算耗尽后停止。这不是模型没有返回内容，也不是本轮飞书通知丢失。

离线脚本 `runs/maintenance/replay-latest-analysis.py` 使用已保存原始结果和任务初始状态，只在内存调用现有 contract/apply_analysis/merge_questions，不修改任务、不调用模型、不发通知。三份结果的 contract_errors 均为空；领域回放分别复现 LOCAL_KEY_COLLISION、invalid question revision/options、LOCAL_KEY_COLLISION。

## 稳定性结论

现有脚本测试证明特定机制场景可运行，不能证明真实模型连续产出稳定。上一轮完整测试通过 723 项，但未覆盖这里的协议与领域约束差异。本轮不能认定“稳定出方案”验收通过。

修复需要统一模型协议和运行时入口校验，一次列出可检测的全部字段及跨记录错误，反馈具体路径；在同一份已保存结果上完成修复验证，并补充真实失败结果回放回归。不能靠提高重试次数、绕过源码证据校验或把 revision=0 静默当作批准依据解决。

本次完成根因调查和离线复现，未修改生产校验逻辑，未恢复失败任务。

另一个历史任务 `task-20261007-d5080d` 的飞书文档授权过期仍是独立阻塞；16:50:13 新卡片回调已收到并入库，重复提交已答问题不会解除授权阻塞。
