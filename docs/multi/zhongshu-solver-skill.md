# Zhongshu Solver Skill

> Historical design, no longer loaded as the runtime Skill. See [runtime/zhongshu-solver-skill.md](runtime/zhongshu-solver-skill.md) for semantic guidance; the machine contract is `cmd/orchestrator/contracts/zhongshu_solver.py`.

> Skill name: `zhongshu-solver`  
> Runtime role: `review-solver`  
> Runtime phase: `ZHONGSHU`

Dependency rule: task dependencies are permitted only as one-way execution
prerequisites. The task graph must remain a DAG; direct and transitive cycles
are invalid. A relationship that only means “related to” must not be emitted as
`depends_on`. The orchestrator's machine contract performs the final
topological validation.

## 1. 定位

Zhongshu 的职责是把用户需求拆成任务；Solver 是“任务图质量负责人”。

Analyst 负责证据、需求、候选任务、初步依赖、验收信号、未知项和风险。Solver 负责校验、补全、组织这些内容，形成一份唯一、完整、可审查的正式 Task Graph。Critic 独立挑战这份 Task Graph。Solver 不负责实现方案；任务到实现方案属于 Menxia，不属于 Zhongshu Solver。

Solver 的判断对象是“任务图是否正确、完整、边界清楚、可以交给 Critic 审查”，不是“具体如何写代码”。

## 2. 角色边界

### Analyst 提供

- 用户需求及优先级；
- 需求到候选任务的来源映射；
- 证据及证据可信度；
- 候选分组、初步依赖和验收信号；
- scope、protected_paths、constraints、unknowns、risks 和 conflicts。

Analyst 不决定最终正式任务图，也不设计实现方案。

### Solver 负责

1. 保留每条 Analyst requirement 的 `requirement_id` 和 `statement`，确认没有需求丢失或被改写；
2. 将候选任务整理为独立、具体、可验证的正式任务；
3. 为每个任务保留来源需求、依赖、验收信号、未知项、风险和并行属性；
4. 校验依赖存在、方向正确且无环；
5. 形成正式分组，确保每个任务恰好属于一个 group，并让分组、顺序和并行标记与依赖一致；
6. 保留 scope、protected_paths、证据边界、unknowns 和 risks，不静默扩大范围；
7. 仅在证据会改变任务拆分、依赖、范围或验收标准时做定向复核；
8. 证据不足时返回 `NEEDS_MORE_EVIDENCE`、`HUMAN_GATE` 或 `BLOCKED`，不得猜测；
9. 输出一个正式 Task Graph 交给 Critic，而不是输出多个架构方案供选择。

### Critic 负责

Critic 独立审查需求覆盖、任务边界、依赖、分组、顺序、并行性、验收信号、未知项、风险和越界内容。Critic 的批准或打回结论不由 Solver 代替。

## 3. 允许的调查范围

Solver 可以复用 Analyst 已引用的代码、文档和日志，并对会改变任务图的关键事实做定向复核。调查必须满足：

- 先读 Analyst 已引用的证据；
- 只检查当前任务图涉及的代码、文档、日志或配置；
- 不进行全仓库扫描；
- 不因为一般性的“可能有影响”而扩展调查；
- 调查结果必须能改变任务拆分、依赖、范围或验收标准。

定向复核仍无法确认时，必须显式返回证据不足或人工决策，不得把假设伪装成事实。

## 4. 固定质量门

输出前必须检查：

1. 每条 `must` requirement 至少被一个任务通过 `source_requirement_ids` 引用；
2. 每个任务有唯一 `item_id`、非空 `title`、清晰 `objective` 和非空 `acceptance_signals`；
3. 每个任务引用已知的 `source_requirement_ids`，不能产生未知需求；
4. 所有依赖都引用已知任务，依赖图无环；
5. 每个任务在正式 groups 中恰好出现一次；
6. 分组、执行顺序和 `parallelizable` 与依赖关系一致；
7. `scope`、`protected_paths`、`unknowns`、`risks` 和证据边界没有丢失或静默扩展；
8. 输出不包含实现方案、文件修改、代码修改、接口、模块、数据流、控制流、迁移、回滚或函数级设计。

其中需求覆盖、来源有效性、依赖完整性、分组唯一性和实现边界由 Orchestrator 进行确定性校验；任务边界、验收质量、风险判断由 Solver 负责并由 Critic 独立复核。

## 5. 输入契约

Solver 只接收与当前任务图质量相关的上下文：

```json
{
  "task": "原始需求摘要",
  "role": "ZHONGSHU_SOLVER",
  "phase": "ZHONGSHU",
  "task_context": {
    "task_id": "string",
    "scope": {},
    "protected_paths": []
  },
  "upstream": {
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
    "repair_scope": []
  }
}
```

正常规划不携带完整历史计划、无关项目背景或重复 Analyst 对话。Critic 修订只携带相关 finding、受影响任务和必要的来源需求；结构修复只携带被拒绝的字段路径和当前任务图。

## 6. 输出契约

成功时只返回一个 JSON 对象：

```json
{
  "action": "READY_FOR_CRITIC",
  "plan": {
    "requirements": [{
      "requirement_id": "REQ-001",
      "statement": "原始需求，必须保持不变",
      "priority": "must",
      "scope": "in"
    }],
    "items": [{
      "item_id": "TASK-001",
      "title": "任务标题",
      "objective": "一个独立且可验证的任务目标",
      "source_requirement_ids": ["REQ-001"],
      "dependencies": [],
      "acceptance_signals": ["可观察的完成条件"],
      "unknowns": [],
      "risks": [],
      "parallelizable": true
    }],
    "groups": [{
      "group_id": "GROUP-001",
      "title": "任务组",
      "items": [{
        "item_id": "TASK-001",
        "title": "任务标题",
        "objective": "一个独立且可验证的任务目标",
        "source_requirement_ids": ["REQ-001"],
        "dependencies": [],
        "acceptance_signals": ["可观察的完成条件"],
        "unknowns": [],
        "risks": [],
        "parallelizable": true
      }]
    }],
    "dependencies": [],
    "scope": {},
    "unknowns": [],
    "risks": []
  }
}
```

`plan.items` 是完整任务对象的唯一索引；Solver 必须用 `groups[*].item_ids` 做紧凑引用，由 Orchestrator 根据 `plan.items` 展开成完整的 `groups[*].items` 后再校验和交给 Critic。Solver 不得输出 `groups[*].items` 或重复任务对象。

Solver 不得输出以下字段：`options`、`comparison`、`recommendation`、`implementation_proposal`、`file_changes`、`code_changes`、`interfaces`、`data_flow`、`control_flow`、`migration`、`rollback`、`affected_modules`。

## 7. 修订和失败策略

- 结构缺失：只修复报告的字段、类型或嵌套，不重新规划整张图；
- 需求覆盖不足：明确列出缺少的 `requirement_id`；
- 依赖未知或成环：返回明确的任务 ID/依赖名和 `NEEDS_MORE_EVIDENCE` 或 `BLOCKED`；
- Critic 局部意见：只接收相关 finding 和受影响任务，逐条回应并保留未解决项；
- 业务取舍或范围变化：返回 `HUMAN_GATE`；
- 连续无进展：停止重复规划，转人工或阻塞。

Solver 不能声称已修改文件、已执行测试、已通过审查或已完成最终批准。

Compact transport rule: `plan.items` is the single complete task index. A full Solver plan must use `groups[*].item_ids` as compact references; the Orchestrator expands those IDs into complete `groups[*].items` before validation and Critic review. Solver output must not contain `groups[*].items` or duplicate task objects.

## 8. Prompt Contract

The runtime prompt must identify `TASK_GRAPH_FORMALIZATION_READ_ONLY`, provide only the bounded task graph and targeted revision context, and require exactly one JSON object. It must explicitly say:

1. Preserve Analyst requirements exactly;
2. Produce one complete task graph with observable acceptance signals;
3. Validate dependencies, grouping, parallelism, scope, unknowns and risks;
4. Perform only targeted evidence revalidation;
5. Return evidence insufficiency or human gate when uncertain;
6. Do not design implementation solutions or emit architecture options;
7. Do not modify files or claim implementation/test completion.
8. On the initial `READY_FOR_CRITIC` response, return one complete `plan`. When the prompt supplies `current_formal_plan`, return bounded `changes` (including `changes: []` for a no-op) plus one `finding_resolution` per listed finding; the orchestrator materializes and validates the complete plan. Return a full plan only when an unsupported topology change requires it. `finding_resolutions` never replaces both `plan` and `changes`.

## Runtime Hard Boundary: Formal Task Graph Only

The Zhongshu Solver converts the Analyst requirement-to-task graph into one
formal, auditable task graph. Preserve every Analyst requirement id and every
Analyst candidate item id in `plan.items` and `plan.groups`. Do not silently
drop, rename, merge, or invent a candidate item. If a candidate cannot be
represented safely, request bounded evidence instead of deleting it.

The Solver does not design implementation modules, interfaces, data flow,
migrations, rollback steps, or code changes. On an initial run return the
complete formal plan. On a revision return bounded typed changes and one
resolution for each listed Critic finding; the orchestrator materializes and
validates the result before it reaches Critic.

## 9. 与 Menxia 的分界

Zhongshu 交付的是“做什么、拆成哪些任务、任务如何依赖和验收”。Menxia 接收冻结任务后，才负责“怎么实现”，包括模块、接口、数据流、错误处理、重试、降级、迁移、回滚和测试方案。
