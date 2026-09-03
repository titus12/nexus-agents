# Zhongshu Solver 职责边界与质量门设计

## 状态

设计已获用户确认，待进入实现计划。

## 目标

将 Zhongshu Solver 定义为“需求到任务图的质量负责人”，而不是实现方案设计师或简单的 JSON 整理器。

Solver 必须保证：

- 用户需求完整映射到任务；
- 每个任务边界清晰、独立、可验证；
- 任务依赖、分组和顺序合理；
- 未知、风险和冲突被显式保留；
- 任务图没有混入门下省的实现方案；
- 任务图能够稳定交给 Critic 和 Menxia 使用。

## 非目标

本次设计不让 Zhongshu Solver 负责：

- 设计代码、接口、文件修改或函数级实现；
- 产生 `implementation_proposal`；
- 重新扫描整个仓库收集背景资料；
- 设计多个架构方案并进行实现选型；
- 替代 Critic 做最终审查或批准；
- 替代 Analyst 做大范围证据搜集；
- 修改项目文件或改变 Orchestrator 状态。

## 三角色边界

### Analyst：发现候选任务

Analyst 负责从用户需求和有限证据中提取：

- 需求及优先级；
- 候选任务；
- 需求到任务的来源映射；
- 初步依赖和验收信号；
- 未知项、风险和冲突。

Analyst 不决定最终任务图，也不设计实现方案。

### Solver：任务图质量与正式定稿

Solver 负责：

1. 读取 canonical Analyst task graph；
2. 检查每个需求是否被任务覆盖；
3. 检查任务是否独立、具体、可验证且没有重复；
4. 校验任务依赖是否存在、无循环、方向正确；
5. 确定合理的任务分组、顺序和并行关系；
6. 补充或修正任务的验收信号、范围、未知项和风险；
7. 仅针对会改变任务图的冲突或未知项做定向调查；
8. 无法确认时返回证据不足或人工决策，不得猜测；
9. 输出唯一正式 Task Graph 交给 Critic。

Solver 的判断范围是“任务图是否正确、完整、可流转”，不是“如何实现这些任务”。

### Critic：独立挑战任务图

Critic 负责独立审查：

- 需求覆盖和来源证据；
- 任务边界和拆分质量；
- 依赖、分组、顺序和并行性；
- 验收信号是否可观察；
- 未知项、风险和越界内容。

Critic 不替 Solver 设计实现方案。

## Solver 允许的调查范围

Solver 可以重新读取证据，但必须满足以下条件：

- 证据直接影响任务拆分、依赖、范围或验收标准；
- 优先读取 Analyst 已引用的证据；
- 只检查当前任务图涉及的代码、文档或日志；
- 不进行全仓库扫描；
- 不因一般性的“可能影响”扩大调查范围。

如果定向调查仍不能确定，Solver 必须显式输出 `NEEDS_MORE_EVIDENCE` 或 `HUMAN_GATE`。

## Solver 输入契约

Solver 只接收与任务图质量相关的上下文：

```json
{
  "task": "原始需求摘要",
  "role": "ZHONGSHU_SOLVER",
  "phase": "ZHONGSHU",
  "task_graph": {
    "requirements": [],
    "candidate_items": [],
    "candidate_groups": [],
    "dependencies": [],
    "scope": {},
    "unknowns": [],
    "risks": [],
    "conflicts": []
  },
  "critic_findings": [],
  "repair_scope": [],
  "protected_paths": []
}
```

正常定稿不再携带完整历史计划、无关项目背景或重复的 Analyst 对话。发生 Critic 修订时，只携带相关 finding 和受影响任务。

## Solver 输出契约

成功时只输出任务图：

```json
{
  "action": "READY_FOR_CRITIC",
  "plan": {
    "requirements": [],
    "items": [
      {
        "item_id": "TASK-001",
        "title": "任务标题",
        "objective": "一个独立且可验证的任务目标",
        "source_requirement_ids": ["REQ-001"],
        "dependencies": [],
        "acceptance_signals": ["可观察的完成条件"],
        "unknowns": [],
        "risks": [],
        "parallelizable": true
      }
    ],
    "groups": [],
    "dependencies": [],
    "scope": {},
    "unknowns": [],
    "risks": []
  }
}
```

Solver 必须原样保留 Analyst 的需求 ID 和需求语义，不得新增、删除、改写用户需求。任务可以被拆分、合并或重新分组，但每一项变化必须能追溯到需求、证据或 Critic finding。

## 固定质量门

Solver 在输出前必须完成以下检查：

1. 每条 `must` 需求至少被一个任务引用；
2. 每个任务有唯一 `item_id`、明确 `objective` 和至少一个 `acceptance_signals`；
3. 每个任务至少有一个 `source_requirement_ids`，且引用已知需求；
4. 所有依赖都引用已知任务，依赖图无循环；
5. 每个任务恰好属于一个正式分组；
6. 任务分组、执行顺序和 `parallelizable` 标记与依赖一致；
7. `scope`、`protected_paths`、`unknowns` 和 `risks` 没有被静默扩大或丢失；
8. 输出中不存在实现方案、文件修改、代码修改或函数级设计字段；
9. 信息不足时不输出猜测性的 `READY_FOR_CRITIC`。

其中 1、3、4、5、8 由 Orchestrator 的确定性校验器强制执行；2、6、7、9 由 Solver 结构化输出和 Critic 独立审查共同保证。

## 失败与修订策略

- 结构缺失：Orchestrator 只要求 Solver 修复具体字段，不重新规划整份任务图；
- 需求覆盖不足：返回明确缺失的 `requirement_id`；
- 依赖不明或存在循环：返回 `NEEDS_MORE_EVIDENCE`，不得自行编造依赖；
- 业务或架构取舍：返回 `HUMAN_GATE`；
- Critic 指出局部问题：只发送受影响任务和 finding，禁止重放完整历史；
- 连续无进展：进入 `BLOCKED` 或人工决策，不继续重复长规划。

建议 Zhongshu Solver 单次运行使用独立的较短超时和单次尝试；重试只修复结构或指定问题，不重做完整调查。

## Skill 与 Prompt 的统一

`zhongshu-solver` 的运行时 Skill 必须与本设计一致，且只保留：

- 任务图完整性；
- 任务边界；
- 依赖和分组；
- 验收信号；
- 范围、未知项和风险；
- 定向证据核验；
- Critic finding 的局部修订。

Skill 中的方案选型、接口设计、数据流设计、迁移设计、回滚设计和实现步骤属于 Menxia，不得作为 Zhongshu Solver 的默认职责。

运行时必须能定位到实际 Skill 内容，而不能只传递 `ACTIVE_RUNTIME_SKILL` 名称。若外部 Agent Skill 与本契约冲突，以 Orchestrator 协议和本契约为准。

## 验收标准

实现完成后必须满足：

- Solver prompt 不再要求 `options`、`recommendation` 或实现设计字段；
- Solver 输入只包含当前任务图和相关修订上下文；
- Validator 能拒绝需求改写、未知依赖、需求覆盖缺失和实现字段；
- Critic 修订不会把完整旧计划重复发送给 Solver；
- 正常任务图可在不调用大范围调查的情况下定稿；
- 现有 Menxia 实现方案流程不被改变；
- Zhongshu 相关回归测试覆盖正常定稿、局部修订、补证据和越界输出。
