# 01 项目边界与角色职责

## 1. 项目边界

本地项目根目录：

```text
D:\workspace\src\nexus-agents
```

本知识域关注的是该项目内的方案审议编排器，不是 Multica daemon 本身，也不是 OpenCode 上游实现。

三层必须区分：

| 层 | 位置/实体 | 职责 |
|---|---|---|
| 业务编排 | `cmd\review_orchestrator_v2.py`、`cmd\orchestrator\` | FSM、状态持久化、Multica issue/comment、飞书通知、人审门禁 |
| Agent runtime | Multica daemon、Reasonix/OpenCode | 执行 Agent、工具调用、产生中间事件和最终回复 |
| 外部模型 | Agent 配置的 provider/model | 生成模型输出；不负责 FSM 状态推进 |

`server/pkg/agent/opencode.go` 是 Multica 上游源码中的路径。当前项目没有该源码，不能把它当作本地可直接修改文件。

## 2. 中书省职责

中书省只负责完善需求和方案输入：

1. 读取需求和项目上下文。
2. 建立事实、证据、推断、未知项和证据缺口。
3. 将需求拆分为可验证的 `items`/任务。
4. 将任务组织成有依赖、有目标、有边界的 `groups`/组。
5. 通过 Analyst、Solver、Critic 循环形成 `frozen_plan`。
6. P0/P1 未解决不得通过；缺少正式任务或组不得冻结。

## 3. 门下省职责

门下省不直接修改代码。它对冻结方案中的每个任务逐项补充实现方案：

```text
MENXIA_ITEM_SOLVER
  -> MENXIA_ITEM_ANALYST
  -> MENXIA_ITEM_CRITIC
  -> 通过后进入下一个任务
```

- Analyst：从第一性原理判断实现是否解决任务、是否可行、是否足够完整。
- Critic：从对抗性视角寻找过度修改、遗漏、回滚困难、测试不足和风险。
- Solver：根据两种审查意见修订方案文件或实现方案字段。

## 4. 绝对约束

- 门下省只能在方案中描述代码实现，不直接写业务代码。
- P0/P1 finding 未解决时不能推进到通过。
- `groups` 必须含正式 `items`；只有候选组或空组不能作为冻结方案。
- 任何人工决策必须有可追踪的 `decision_id`、问题描述、选项和恢复状态。
