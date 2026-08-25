# 04 FSM 与状态数据契约

## 1. 主状态顺序

```text
REQUEST_INTAKE
 -> ZHONGSHU_ANALYST
 -> ZHONGSHU_SOLVER
 -> ZHONGSHU_CRITIC
 -> ZHONGSHU_FREEZE_CHECK
 -> MENXIA_ITEM_SOLVER
 -> MENXIA_ITEM_ANALYST
 -> MENXIA_ITEM_CRITIC
 -> MENXIA_GROUP_GATE
 -> 下一个 item/group
 -> DONE
```

异常和人工路径不能绕过业务门禁：

```text
TIMEOUT -> 重试当前状态 / BLOCKED
INVALID_AGENT_REPLY -> 重派当前状态 / BLOCKED
MULTICA_ERROR -> 退避重试 / BLOCKED
HUMAN_GATE -> resume_state / HUMAN_GATE_TIMEOUT
CANCELLED -> 终端状态
```

## 2. 状态生命周期

每个状态具备：

```text
enter()
update()
exit()
```

`enter` 必须先持久化 intent/checkpoint，再执行外部副作用；恢复时不能无条件重复派发。

## 3. StateContext 核心数据

必须能恢复：

- `task_id`、`issue_id`、`current_state`、`resume_state`；
- `entered_at`、`updated_at`、`sequence`；
- 当前 group/item 索引；
- `request_payload`、`last_agent_payload`；
- `active_finding_ids`、`pending_solver_finding_ids`、`resolved_finding_ids`；
- `revision_round` 和错误/超时重试次数；
- dispatch intent/receipt；
- human gate 的 `decision_id` 和选项。

## 4. 绑定字段

transport 负责绑定：

```text
task_id
request_id
role
phase
expected_agent_id
```

LLM 只需返回业务字段和 `action`。显式错误的 role/phase 必须拒绝；缺失的绑定字段可由 transport 根据当前状态补齐。

## 5. Findings 生命周期

```text
OPEN
 -> ASSIGNED
 -> RESOLVED
 -> VERIFIED
```

无法接受或暂不处理的 finding 必须显式标为 `DEFERRED`/`WONTFIX`，并记录原因和剩余风险，不能从 active 列表静默删除。
