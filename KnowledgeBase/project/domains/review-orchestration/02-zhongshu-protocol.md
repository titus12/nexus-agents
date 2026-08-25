# 02 中书省方案审议协议

## 1. 输入

中书省接收：

- 原始需求；
- 项目类型（Go/.NET/Unity 等）；
- 项目目录和项目 ID；
- 绑定的项目 Skill；
- 历史 findings、方案追踪文件和必要的知识库上下文。

## 2. Analyst 输出

Analyst 的正式输出必须是一个结构化 JSON，最少包含：

```json
{
  "action": "READY_FOR_SOLVER",
  "notification": "面向人的简要总结",
  "evidence_packet": {},
  "findings": [],
  "candidate_items": [],
  "candidate_groups": []
}
```

`candidate_items` 代表任务，`candidate_groups` 代表候选组。每个组必须说明：

- 组 ID、标题、目标；
- `related_items`；
- 依赖；
- 证据；
- 风险；
- 预期产物。

## 3. Solver 输出

Solver 负责把 Analyst 的证据和任务转成可执行方案，必须明确：

- 选择或修订的方案方向；
- 每个任务的目标和范围；
- 每个组的依赖和顺序；
- 实现边界、涉及文件/API/测试/回滚；
- 未决问题和需 Critic 验证的假设。

## 4. Critic 输出

Critic 重点检查：

- 是否遗漏需求或任务；
- 是否存在空 `groups`/空 `items`；
- 是否把推测当成事实；
- P0/P1/P2 finding 是否处理；
- 是否出现范围膨胀；
- 是否有可验证测试和回滚；
- 是否能交给门下省逐任务审议。

Critic 不通过时，合法路径是：

```text
ZHONGSHU_CRITIC
  -> ZHONGSHU_SOLVER
  -> ZHONGSHU_CRITIC
```

达到最大修订次数后进入 `BLOCKED` 或 `HUMAN_GATE`，不能默默通过。

## 5. Freeze Check

冻结前必须验证：

1. 正式 `groups` 非空；
2. 每个正式组有正式 `items`；
3. item 有目标、内容、验收或证据要求；
4. P0/P1 没有 active finding；
5. 依赖关系无环；
6. 方案版本和 findings 状态已持久化。
