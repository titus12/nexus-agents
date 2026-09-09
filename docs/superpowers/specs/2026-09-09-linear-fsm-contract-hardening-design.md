# Linear FSM Contract Hardening Design

**Date:** 2026-09-09
**Status:** Approved for implementation

## Goal

修复新 FSM 基础层中已确认的契约问题，同时保持旧的 `TaskLock`、旧运行时入口和现有测试兼容。

## Scope

本次只处理四类问题：

1. 持久化 DTO 的可选聚合字段必须 fail-closed，不能静默丢弃损坏值。
2. effect/node 失败必须携带真实的 FSM 状态和序列信息。
3. 为旧 `TaskLock` 增加 task-bound runtime adapter，使其满足新的 `LockPort`。
4. transition 已提交但 lock release 失败时，必须暴露“已提交”的可恢复事实。

`app.py` 的生产切换、旧 `StateMachine` 删除、effect 并发调度策略和完整 human-gate 生命周期不在本次范围内。

## Design

- `context_from_dto()` 对 `review` 和 `human_gate` 接受 `null` 或 object，其他类型直接抛出 `ValueError`。
- `EffectRecord` 保存产生它的 source `state` 和 `sequence`；`NodeContext` 保存同样的执行位置。旧构造方式通过默认值保持兼容，新测试使用真实上下文验证归因。
- 新增 `TaskLockAdapter`，绑定一个 task id，并将 task-aware `LockPort` 调用转发到无 task 参数的旧 `TaskLock`。错误 task id 直接拒绝。
- 新增 `PostCommitLeaseReleaseError`，包含 `task_id`、`transition_id`、`state`、`sequence` 和 `committed=True`，避免调用方把已提交 transition 当成未提交处理。
- `OPEN_HUMAN_GATE` 只有在事件携带 `decision_id` 时才写入 `HumanGateState`；不凭空生成 decision id。`HumanGateUpdate` 增加可选 `reason_code`。

## Verification

- 新增 DTO fail-closed、failure correlation、lock adapter 和 post-commit release tests。
- 保持现有 63 个线性 FSM 基础测试通过。
- 运行相关旧 transport/parallel 测试，确认不改变兼容层行为。
