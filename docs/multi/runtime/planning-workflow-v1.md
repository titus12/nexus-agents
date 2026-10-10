> 已被 requirements-v1 实施方向替代（2026-10-07）。本文保留为历史设计与验证记录，不是当前可运行需求流程。相关未接入代码已按原内容归档至 docs/superpowers/archive/2026-10-06-planning-workflow/；canonical 实施图能力继续保留。

# 正式规划流程 planning-v1

新建 CLI 任务默认使用此流程，方案格式为 `canonical-v1`，编译策略为 `planning-v1`。已有任务恢复时使用保存的 workflow；缺少该字段的旧快照仍为 `legacy-v1`，不会自动迁移。

## 固定工序

1. Analyst 提取需求基线，每项带原始请求中的准确引用。
2. Analyst 收集必要前提证据；没有必要调查时可以返回空列表。文件引用沿用原文校验。
3. Solver 生成完整需求方案。业务组只用于展示，工作项依赖才决定执行顺序。
4. 用户确认需求方案。驳回后保留反馈、重新修订、重新确认。
5. Solver 增加绑定的输入、具体步骤、命名产物、验证方法、风险、兼容与回退。
6. Critic 审查完整当前版本；确定性检查同时验证需求覆盖、输入引用、产物与依赖、验证状态。改变已批准的需求表面必须重新确认。纯补证直接回审查；语义修复回 Solver。
7. 再次校验批准版本与证据指纹，持久化最终文件后报告 `PLAN_READY`。

需求批准绑定需求表面和批准时的方案版本；它与最终可执行性审查分别记录。执行细化产生新版本，因此不能把最初的需求批准说成对最终方案的人工批准。

## 有限修复与结果

整项任务最多 16 次 agent 派发，格式修复最多 3 次，语义修复最多 3 次，补证最多 2 次，外部故障重试最多 2 次。预算跨工序和进程恢复保留。相同阻塞缺陷连续出现 3 次、Solver 修复未改动方案时停止，避免无限往返。人工回答仍由用户提供，等待人工本身不是失败。

| 结果 | 含义 |
|---|---|
| `PLAN_READY` | 可执行方案与验证报告已经发布 |
| `NEEDS_USER_DECISION` | 人工需求确认或有明确问题、影响、责任方的用户澄清；FSM 等待在 `HUMAN_GATE` |
| `BLOCKED_BY_PREREQUISITE` | 环境、权限或输入前提不具备；本轮结束 |
| `ENGINE_ERROR` | 契约、收敛、预算或发布失败；本轮结束，原因可检查 |
| `CANCELLED` | 用户取消 |

成功产物位于 `runs/<task_id>/planning/vNNN-<hash>/`：

- `execution-plan.json`：完整不可变方案版本。
- `execution-plan.md`：执行顺序、逐项输入/步骤/产物/验收/风险/回退、决策与证据。
- `validation.json`：确定性校验、语义审查版本、证据指纹、需求审批版本、执行顺序。
- `manifest.json`：发布完成标记与各文件内容指纹。

验证工作统一标为 `planned`。生成审核方案不代表已经审核实际代码、运行测试或确认可以提交。

## 命令

```powershell
python D:\workspace\src\nexus-agents\cmd\review_orchestrator_v2.py --new "审核当前项目未提交的代码，先生成可执行的审核方案，明确检查范围、步骤、证据与风险报告产物" --project 1e41be52-4460-4fba-9360-0c60cfc18c92
```

也可以显式指定 `--workflow planning-v1`。新建时指定 `--workflow legacy-v1` 回退旧机制，默认旧方案格式；需要旧工作流的统一版本功能时同时指定 `--plan-schema canonical-v1`。单独显式指定 `--plan-schema legacy-v1` 也保留旧流程兼容入口。

恢复、取消、检查任务时不能传 `--workflow` 或 `--plan-schema` 改写原任务。`--fast-track` 及旧的并行组开关只作用于旧机制；正式流程始终保留需求确认与整体审查。

## 验证

```powershell
python cmd/run_tests.py test_planning_workflow test_canonical_plan_revision test_linear_fsm_states test_linear_fsm_transitions
python cmd/run_tests.py
```

脚本测试驱动实际应用、仓库、事件、outbox 与 transport；不调用模型、不发送飞书消息、不重启当前 Nexus 服务。实际模型质量需要另外用新任务观察，脚本通过不能证明任意需求都会一次生成合格方案。
