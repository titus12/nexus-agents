# Linear FSM Object Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前 review orchestrator 渐进迁移为一套线性 FSM、节点内受控并发、统一 reply envelope 和显式 effect 生命周期，同时保持现有 CLI、Multica、Feishu、prompt bundle 和 result-file 协议兼容。

**Architecture:** 新增 domain、runtime、transport 三个内部边界。状态对象只产生 typed `StateDecision`，`WorkflowEngine` 只协调状态转换，`WorkflowRepository` 持久化 journal/snapshot/effect，`EffectManager` 管理副作用生命周期，`WorkerRunner` 和 `NodeExecutor` 管理节点内 worker。旧 `app.py`、`states.py` 和 adapters 先作为兼容外壳逐步收缩，不做一次性替换。

**Tech Stack:** Python 现有运行时、标准库 `dataclasses`/`typing`/`unittest`/`concurrent.futures`、现有 JSON 文件存储和 Multica/Feishu adapters；不新增第三方依赖。

---

## 1. Scope and working-tree rules

本计划只修改 orchestrator 内部代码和对应测试。现有外部协议保持不变：

- `cmd/review_orchestrator_v2.py` 的 CLI 参数和退出码保持不变；
- Multica issue/comment、prompt bundle、result-file、remote result bridge 行为保持不变；
- Feishu notification 和 human-gate fallback 行为保持不变；
- 不修改 `C:\Users\Administrator\.codex\config.toml` 或 `nexus-model-catalog.json`；
- 不手工修改 OpenWiki 生成页面；
- 不覆盖工作树中已有的用户修改；
- 所有验证使用与当前 Nexus 服务不同的端口；如需启动验证服务，使用 `18766`，保留活动服务的 `8766`。

目标文件结构：

```text
cmd/orchestrator/
  domain/
    context.py              # typed workflow aggregates
    events.py               # domain events and actions
    decisions.py            # StateDecision, ContextUpdate, EffectRequest
    errors.py               # FailureRecord and typed failures
    transitions.py          # transition registry and guards
    states.py               # concrete workflow state objects
    findings.py             # finding ledger and scope rules
  runtime/
    engine.py               # main event loop coordinator
    repository.py           # repository port and JSON implementation bridge
    effects.py              # EffectManager and effect lifecycle
    nodes.py                # WorkerRunner, NodeExecutor, NodeJoiner
    ports.py                # LockPort, transport/notification/artifact ports
  transport/
    replies.py              # raw transport reply and ReplyEnvelope
    normalizer.py           # one canonical reply normalization pipeline
  app.py                    # compatibility wiring and CLI integration only
  persistence.py            # compatibility implementation of repository port
  adapters.py               # external adapter implementations only
```

每个新模块只允许拥有一个主语义。若一个函数同时做 normalization、domain mutation 和 I/O，必须在迁移时拆成三个对象或函数。

## 2. Task 1: Establish characterization and architecture contract tests

**Files:**
- Create: `cmd/test_linear_fsm_architecture.py`
- Create: `cmd/test_linear_fsm_transitions.py`
- Create: `cmd/test_linear_fsm_failures.py`
- Test against: `cmd/orchestrator/transitions.py`, `cmd/orchestrator/state_machine.py`, `cmd/orchestrator/parallel_runtime.py`, `cmd/orchestrator/persistence.py`

- [ ] **Step 1: Write the failing transition contract tests**

Add tests that define the current intended linear graph without importing the new modules yet:

```python
class LinearTransitionContractTests(unittest.TestCase):
    def setUp(self):
        from orchestrator.domain.context import (
            HumanGateState,
            ProgressState,
            TaskIdentity,
            WorkflowContext,
        )
        from orchestrator.domain.transitions import TransitionRegistry

        self.context_type = WorkflowContext
        self.identity = TaskIdentity("task-1", "issue-1", "demo", "request-1")
        self.progress_type = ProgressState
        self.gate_type = HumanGateState
        self.registry = TransitionRegistry.default()

    def test_business_states_have_one_forward_successor(self):
        self.assertEqual(
            self.registry.target_for("ZHONGSHU_CRITIC", "APPROVE_CRITIC"),
            "ZHONGSHU_FREEZE_CHECK",
        )

    def test_system_state_keeps_resume_state(self):
        context = self.context_type(
            identity=self.identity,
            progression=self.progress_type(
                state="HUMAN_GATE",
                sequence=3,
                entered_at="2026-09-09T00:00:00Z",
                resume_state="ZHONGSHU_SOLVER",
            ),
            human_gate=self.gate_type(
                decision_id="decision-1",
                reason_code="CRITIC_BLOCKED",
                resume_state="ZHONGSHU_SOLVER",
            ),
        )
        self.assertEqual(context.progression.resume_state, "ZHONGSHU_SOLVER")

    def test_dynamic_target_is_rejected_when_not_registered(self):
        with self.assertRaises(Exception):
            self.registry.validate_target("ZHONGSHU_CRITIC", "not-a-registered-state")
```

- [ ] **Step 2: Add failure attribution and isolation tests**

Define the required behavior before implementation:

```python
class FailureAttributionTests(unittest.TestCase):
    def test_failure_contains_unique_owner_and_correlation(self):
        from orchestrator.domain.errors import FailureRecord

        failure = FailureRecord(
            failure_id="failure-1",
            stage="node",
            owner_component="zhongshu_analyst",
            task_id="task-1",
            state="ZHONGSHU_ANALYST",
            sequence=4,
            node_run_id="node-1",
            worker_id="analyst-2",
            effect_id=None,
            error_code="WORKER_TIMEOUT",
            retryable=True,
            message="worker timed out",
            cause_type="WorkerTimeoutError",
        )
        self.assertTrue(failure.failure_id)
        self.assertEqual(failure.owner_component, "zhongshu_analyst")
        self.assertEqual(failure.worker_id, "analyst-2")
```

- [ ] **Step 3: Run the new tests and record the expected baseline failures**

Run from `D:\workspace\src\nexus-agents\cmd`:

```powershell
rtk python -m unittest -v test_linear_fsm_architecture test_linear_fsm_transitions test_linear_fsm_failures
```

Expected: FAIL because the new contracts and objects do not exist. Existing tests must not be changed to hide their current failures.

- [ ] **Step 4: Run the existing core baseline**

Run from the repository root:

```powershell
rtk python cmd/run_core_tests.py
```

Record the existing failures separately from new failures. The baseline currently includes blocked-reason/routing failures and a pending-dispatch recovery test that does not complete within the test timeout.

## 3. Task 2: Create typed domain contracts

**Files:**
- Create: `cmd/orchestrator/domain/__init__.py`
- Create: `cmd/orchestrator/domain/context.py`
- Create: `cmd/orchestrator/domain/events.py`
- Create: `cmd/orchestrator/domain/decisions.py`
- Create: `cmd/orchestrator/domain/errors.py`
- Create: `cmd/orchestrator/domain/findings.py`
- Test: `cmd/test_linear_fsm_architecture.py`

- [ ] **Step 1: Define immutable workflow aggregates**

Implement the smallest typed aggregates. Do not copy raw transport payloads into these classes:

```python
from dataclasses import dataclass, field
from typing import Mapping

@dataclass(frozen=True)
class TaskIdentity:
    task_id: str
    issue_id: str
    project: str
    request_id: str

@dataclass(frozen=True)
class ProgressState:
    state: str
    sequence: int
    entered_at: str
    resume_state: str | None = None

@dataclass(frozen=True)
class DeliveryState:
    active_request_id: str | None = None
    dispatch_operation_id: str | None = None
    idempotency_key: str | None = None
    status: str = "IDLE"

@dataclass(frozen=True)
class RecoveryState:
    retry_count: int = 0
    max_retries: int = 3
    recoverable: bool = True
    blocked_reason: str | None = None

@dataclass(frozen=True)
class ReviewState:
    revision_id: str
    active_group_id: str | None
    active_item_id: str | None
    findings: tuple["Finding", ...] = ()

@dataclass(frozen=True)
class Finding:
    finding_id: str
    severity: str
    status: str
    group_id: str | None = None
    item_id: str | None = None

@dataclass(frozen=True)
class HumanGateState:
    decision_id: str
    reason_code: str
    resume_state: str

@dataclass(frozen=True)
class ProgressUpdate:
    state: str | None = None
    resume_state: str | None = None

@dataclass(frozen=True)
class ReviewUpdate:
    revision_id: str | None = None
    active_group_id: str | None = None
    active_item_id: str | None = None
    findings: tuple[object, ...] | None = None

@dataclass(frozen=True)
class DeliveryUpdate:
    active_request_id: str | None = None
    status: str | None = None

@dataclass(frozen=True)
class HumanGateUpdate:
    decision_id: str | None = None
    resume_state: str | None = None

@dataclass(frozen=True)
class RecoveryUpdate:
    retry_count: int | None = None
    blocked_reason: str | None = None

@dataclass(frozen=True)
class WorkflowContext:
    identity: TaskIdentity
    progression: ProgressState
    delivery: DeliveryState = field(default_factory=DeliveryState)
    recovery: RecoveryState = field(default_factory=RecoveryState)
    review: "ReviewState | None" = None
    human_gate: "HumanGateState | None" = None
```

Define `ReviewState` and `HumanGateState` in the same task with explicit ownership of their fields. Put `Finding` and its scope/lifecycle methods in `domain/findings.py`; `ReviewState` stores only immutable `Finding` values. Do not add `dict[str, Any]` fields except a separately named, artifact-reference map.

- [ ] **Step 2: Define events, actions and typed updates**

Create immutable domain values:

```python
@dataclass(frozen=True)
class DomainEvent:
    name: str
    task_id: str
    sequence: int
    payload: object
    occurred_at: str

@dataclass(frozen=True)
class TransitionRequest:
    action: str
    reason_code: str

@dataclass(frozen=True)
class ContextUpdate:
    progression: "ProgressUpdate | None" = None
    review: "ReviewUpdate | None" = None
    delivery: "DeliveryUpdate | None" = None
    human_gate: "HumanGateUpdate | None" = None
    recovery: "RecoveryUpdate | None" = None

@dataclass(frozen=True)
class EffectRequest:
    effect_id: str
    effect_type: str
    task_id: str
    idempotency_key: str
    payload_ref: str | None = None

@dataclass(frozen=True)
class StateDecision:
    transition: TransitionRequest | None
    update: ContextUpdate
    effects: tuple["EffectRequest", ...]
```

`ContextUpdate` fields must be typed update objects. No generic mapping patch is allowed.

- [ ] **Step 3: Define typed failures and correlation**

Implement `FailureRecord` and typed exception classes:

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass

@dataclass(frozen=True)
class FailureRecord:
    failure_id: str
    stage: str
    owner_component: str
    task_id: str
    state: str
    sequence: int
    node_run_id: str | None
    worker_id: str | None
    effect_id: str | None
    error_code: str
    retryable: bool
    message: str
    cause_type: str
    external_id: str | None = None

    @classmethod
    def from_effect_exception(cls, effect: EffectRecord, error: Exception) -> "FailureRecord":
        return cls(
            failure_id=uuid.uuid4().hex,
            stage="effect",
            owner_component=effect.request.effect_type,
            task_id=effect.task_id,
            state="",
            sequence=0,
            node_run_id=None,
            worker_id=None,
            effect_id=effect.effect_id,
            error_code=type(error).__name__.upper(),
            retryable=False,
            message=str(error),
            cause_type=type(error).__name__,
        )
```

Define `TransportError`, `ReplyBindingError`, `ReplyValidationError`, `WorkerTimeoutError`, `LeaseLostError`, `PersistenceError`, `InvariantViolation`, and `HumanGateDeliveryError` as typed exceptions carrying an optional `FailureRecord`.

- [ ] **Step 4: Make the contract tests pass**

Run:

```powershell
rtk python -m unittest -v test_linear_fsm_architecture
```

Expected: PASS. Run `rtk python -m compileall orchestrator/domain` from `cmd` to verify the new package has no syntax errors.

## 4. Task 3: Implement the transition registry and concrete state objects

**Files:**
- Create: `cmd/orchestrator/domain/transitions.py`
- Create: `cmd/orchestrator/domain/states.py`
- Modify: `cmd/orchestrator/transitions.py` to delegate compatibility calls to the new registry
- Modify: `cmd/orchestrator/state_machine.py` to consume `StateDecision`
- Test: `cmd/test_linear_fsm_transitions.py`

- [ ] **Step 1: Define the finite state and action set**

Define the registry error and lookup API:

```python
class TransitionError(InvariantViolation):
    pass

class TransitionRegistry:
    @classmethod
    def default(cls) -> "TransitionRegistry": ...
    def target_for(self, source: str, action: str) -> str: ...
    def validate_target(self, source: str, target: str) -> None: ...
```

Use a closed set of names and actions:

```python
BUSINESS_STATES = (
    "REQUEST_INTAKE",
    "ZHONGSHU_ANALYST",
    "ZHONGSHU_SOLVER",
    "ZHONGSHU_CRITIC",
    "ZHONGSHU_FREEZE_CHECK",
    "MENXIA_ITEM_SOLVER",
    "MENXIA_ITEM_ANALYST",
    "MENXIA_ITEM_CRITIC",
    "MENXIA_GROUP_GATE",
    "DONE",
)

SYSTEM_STATES = (
    "HUMAN_GATE",
    "RETRY_WAIT",
    "BLOCKED",
    "CANCELLED",
    "FAILED",
    "PERSISTENCE_DEGRADED",
)
```

The registry maps `(from_state, action)` to one target. No caller may provide an arbitrary target state.

- [ ] **Step 2: Write transition registry tests**

Cover:

- every business state has a registered forward path;
- `HUMAN_GATE` and `PERSISTENCE_DEGRADED` require a valid `resume_state`;
- unknown action and unknown target are rejected;
- `ZHONGSHU_CRITIC` blocked semantics are explicit and tested;
- item critic P1/P2 finding routes are scoped before guards run;
- cancellation is terminal from every nonterminal state.

Run:

```powershell
rtk python -m unittest -v test_linear_fsm_transitions
```

Expected: FAIL until the registry and guard implementations are complete.

- [ ] **Step 3: Implement state objects with no I/O**

Define the state interface:

```python
class WorkflowState(Protocol):
    name: str

    def enter(self, context: WorkflowContext) -> StateDecision: ...
    def handle(self, context: WorkflowContext, event: DomainEvent) -> StateDecision: ...
    def on_timeout(self, context: WorkflowContext) -> StateDecision: ...
    def on_resume(self, context: WorkflowContext) -> StateDecision: ...
```

Implement one class per state. `enter`, `handle`, `on_timeout`, and `on_resume` may only construct typed decisions. They must not call Multica, Feishu, filesystem, thread pools, or `save_state()`.

- [ ] **Step 4: Make `StateMachine` a compatibility adapter**

Change `state_machine.py` so it delegates state decisions to `StateRegistry` and applies only validated transition results. Keep old public methods until all callers move to `WorkflowEngine`.

Run:

```powershell
rtk python -m unittest -v test_linear_fsm_transitions test_fsm_core_v31.FsmCoreTests.test_blocked_transition_populates_top_level_reason
```

Expected: transition tests PASS; the blocked-reason test must use the event's canonical reason code rather than losing it behind a static transition reason.

## 5. Task 4: Unify direct and parallel replies

**Files:**
- Create: `cmd/orchestrator/transport/__init__.py`
- Create: `cmd/orchestrator/transport/replies.py`
- Create: `cmd/orchestrator/transport/normalizer.py`
- Modify: `cmd/orchestrator/validators.py`
- Modify: `cmd/orchestrator/parallel_runtime.py`
- Modify: `cmd/orchestrator/app.py`
- Test: `cmd/test_linear_fsm_transport.py`
- Test: `cmd/test_parallel_runtime_correlation.py`

- [ ] **Step 1: Define raw and canonical reply types**

Keep raw transport data at the adapter boundary:

```python
from dataclasses import dataclass
from typing import Callable, Generic, Mapping, Protocol, TypeVar

TPayload = TypeVar("TPayload")

@dataclass(frozen=True)
class RawTransportReply:
    author_id: str
    external_message_id: str
    request_id: str | None
    payload: Mapping[str, object]
    received_at: str
    source: str

@dataclass(frozen=True)
class ReplyEnvelope(Generic[TPayload]):
    task_id: str
    request_id: str
    author_id: str
    external_message_id: str
    phase: str
    role: str
    target_state: str
    payload: TPayload
    received_at: str
    source: str
```

```python
@dataclass(frozen=True)
class ReplyBinding:
    task_id: str
    request_id: str
    author_id: str
    phase: str
    role: str
    target_state: str

class ReplyNormalizer(Protocol):
    def normalize(
        self,
        raw: RawTransportReply,
        binding: ReplyBinding,
        role_decoder: Callable[[Mapping[str, object]], TPayload],
    ) -> ReplyEnvelope[TPayload]: ...
```

- [ ] **Step 2: Implement one normalizer**

`ReplyNormalizer.normalize(raw, binding, role_decoder)` must:

- validate actual `author_id`, not the expected author copied from the request;
- validate task/request/phase/role/state binding;
- perform legacy backfill only at this boundary;
- decode a typed role payload;
- return `ReplyEnvelope` or a typed `ReplyBindingError`/`ReplyValidationError`.

- [ ] **Step 3: Adapt direct polling**

Change the direct path in `states.py`/`app.py` to pass `RawTransportReply` to the normalizer. Remove duplicate binding normalization after the envelope exists.

- [ ] **Step 4: Adapt parallel polling without dropping author identity**

Change `ParallelCoordinatorDriver` so it passes the complete polled message into `ReplyNormalizer`. Do not create a new `ExternalMessage` with `worker.request.agent_id` after polling. The actual polled `author_id` must remain authoritative.

- [ ] **Step 5: Add equivalence tests**

```python
def test_direct_and_parallel_reject_the_same_wrong_author(self):
    normalizer = ReplyNormalizer()
    binding = ReplyBinding("task-1", "r-1", "expected-agent", "ZHONGSHU", "ANALYST", "ZHONGSHU_ANALYST")
    raw = RawTransportReply(
        author_id="wrong-agent",
        external_message_id="m-1",
        request_id="r-1",
        payload={"action": "READY_FOR_SOLVER"},
        received_at="2026-09-09T00:00:00Z",
        source="test",
    )
    with self.assertRaises(ReplyBindingError):
        normalizer.normalize(raw, binding, lambda payload: payload)
```

Run:

```powershell
rtk python -m unittest -v test_linear_fsm_transport test_parallel_runtime_correlation
```

Expected: PASS, including the regression test for the previously dropped parallel author binding.

## 6. Task 5: Introduce journal-first repository commits

**Files:**
- Create: `cmd/orchestrator/runtime/__init__.py`
- Create: `cmd/orchestrator/runtime/repository.py`
- Modify: `cmd/orchestrator/persistence.py`
- Modify: `cmd/orchestrator/context.py` with an explicit DTO conversion layer
- Test: `cmd/test_linear_fsm_persistence.py`
- Test: `cmd/test_v31_audit.py`

- [ ] **Step 1: Define repository ports**

Define the repository record types before the protocol:

```python
@dataclass(frozen=True)
class WorkflowSnapshot:
    task_id: str
    context: WorkflowContext
    state_version: int

@dataclass(frozen=True)
class EffectRecord:
    effect_id: str
    task_id: str
    request: EffectRequest
    status: str
    attempt: int

@dataclass(frozen=True)
class EffectResult:
    effect_id: str
    task_id: str
    status: str
    failure: FailureRecord | None = None

@dataclass(frozen=True)
class CommitResult:
    transition_id: str
    snapshot: WorkflowSnapshot
    effects: tuple[EffectRecord, ...]
```

```python
class WorkflowRepository(Protocol):
    def load(self, task_id: str) -> WorkflowSnapshot: ...
    def commit_transition(
        self,
        before: WorkflowSnapshot,
        after: WorkflowSnapshot,
        decision: StateDecision,
    ) -> CommitResult: ...
    def pending_effects(self, task_id: str) -> tuple[EffectRecord, ...]: ...
    def mark_effect_running(self, effect_id: str) -> None: ...
    def commit_effect_result(self, result: EffectResult) -> None: ...
```

`JsonStateStore` remains the compatibility implementation while it is migrated behind this port.

- [ ] **Step 2: Implement journal-first commit**

`commit_transition()` must:

1. validate `before.sequence + 1 == after.sequence`;
2. append a transition record containing the new snapshot summary and effect intents;
3. flush and fsync the journal;
4. atomically replace the snapshot;
5. return the persisted effect records.

If journal append fails, do not update snapshot and raise `PersistenceError` with a `FailureRecord`. If snapshot replacement fails after journal commit, recovery must replay the journal record to rebuild the snapshot.

- [ ] **Step 3: Add failure-injection tests**

Test that:

- event append failure cannot advance the FSM;
- snapshot replacement failure can recover from journal;
- duplicate commit with the same transition id is idempotent;
- pending effects survive process restart;
- malformed middle journal records fail closed;
- `PERSISTENCE_DEGRADED` preserves `resume_state`.

Run:

```powershell
rtk python -m unittest -v test_linear_fsm_persistence test_v31_audit
```

Expected: PASS without changing the external `state.json` and `events.jsonl` consumer contract until compatibility tests are updated.

## 7. Task 6: Implement effect lifecycle and typed external ports

**Files:**
- Create: `cmd/orchestrator/runtime/ports.py`
- Create: `cmd/orchestrator/runtime/effects.py`
- Modify: `cmd/orchestrator/adapters.py` to implement external ports without domain decisions
- Modify: `cmd/orchestrator/notifications.py` to expose `NotificationPort`
- Test: `cmd/test_linear_fsm_effects.py`
- Test: `cmd/test_notification_dedupe_v31.py`

- [ ] **Step 1: Define low-level ports**

Define the request and receipt records used by the ports in `runtime/ports.py`:

```python
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class AgentRequest:
    task_id: str
    request_id: str
    agent_id: str
    payload_ref: str

@dataclass(frozen=True)
class PollRequest:
    task_id: str
    request_id: str
    operation_id: str

@dataclass(frozen=True)
class DispatchReceipt:
    operation_id: str
    external_message_id: str
    confirmed: bool

@dataclass(frozen=True)
class NotificationRequest:
    task_id: str
    notification_key: str
    body: str

@dataclass(frozen=True)
class NotificationReceipt:
    notification_key: str
    delivered: bool

@dataclass(frozen=True)
class ArtifactInput:
    task_id: str
    name: str
    content: bytes

@dataclass(frozen=True)
class ArtifactReceipt:
    artifact_id: str
    digest: str
```

```python
class AgentTransportPort(Protocol):
    def dispatch(self, request: AgentRequest) -> DispatchReceipt: ...
    def poll(self, request: PollRequest) -> tuple[RawTransportReply, ...]: ...
    def lookup(self, operation_id: str) -> DispatchReceipt | None: ...

class NotificationPort(Protocol):
    def send(self, request: NotificationRequest) -> NotificationReceipt: ...

class ArtifactPort(Protocol):
    def write(self, artifact: ArtifactInput) -> ArtifactReceipt: ...
```

These methods perform one external operation and either return a receipt or raise a typed infrastructure error. They do not change FSM state.

- [ ] **Step 2: Implement `EffectManager`**

`EffectManager` must persist lifecycle transitions around a single runner call:

```python
class EffectManager:
    def __init__(self, repository, runners):
        self.repository = repository
        self.runners = runners

    def execute_pending(self, task_id: str) -> tuple[EffectResult, ...]:
        effects = self.repository.pending_effects(task_id)
        results = []
        for effect in effects:
            self.repository.mark_effect_running(effect.effect_id)
            try:
                result = self.runners[effect.effect_type].run_once(effect.request)
            except Exception as error:
                result = self.classify_failure(effect, error)
            self.repository.commit_effect_result(result)
            results.append(result)
        return tuple(results)

    def classify_failure(self, effect: EffectRecord, error: Exception) -> EffectResult:
        failure = FailureRecord.from_effect_exception(effect, error)
        return EffectResult(
            effect_id=effect.effect_id,
            task_id=effect.task_id,
            status="FAILED",
            failure=failure,
        )
```

The actual implementation must catch typed infrastructure exceptions first and wrap unknown exceptions as `InvariantViolation` with a `FailureRecord`; it must not catch and silently continue.

- [ ] **Step 3: Make notification delivery retryable**

Move notification dedupe state from “sent before call” to explicit lifecycle states. A failed call must remain retryable:

```text
PENDING -> RUNNING -> SENT
PENDING -> RUNNING -> FAILED -> RETRYABLE
```

- [ ] **Step 4: Test idempotency and ownership**

Run:

```powershell
rtk python -m unittest -v test_linear_fsm_effects test_notification_dedupe_v31 test_prompt_bundle_dispatch
```

Expected: duplicate effect IDs execute at most once when the external receipt is already confirmed; failed notifications can be retried; no effect advances the business state directly.

## 8. Task 7: Unify sequential and concurrent node execution

**Files:**
- Create: `cmd/orchestrator/runtime/nodes.py`
- Modify: `cmd/orchestrator/parallel_runtime.py` to become a compatibility implementation of `WorkerRunner`
- Modify: `cmd/orchestrator/zhongshu_parallel.py` only to delegate to the new node APIs
- Modify: `cmd/orchestrator/menxia_parallel.py` only to delegate to the new node APIs
- Test: `cmd/test_linear_fsm_nodes.py`
- Test: `cmd/test_parallel_runtime_correlation.py`
- Test: `cmd/test_zhongshu_parallel.py`

- [ ] **Step 1: Define worker and join interfaces**

Define the immutable node records in `runtime/nodes.py`:

```python
@dataclass(frozen=True)
class WorkerBinding:
    worker_id: str
    agent_id: str
    task_id: str
    request_id: str
    role: str
    phase: str

@dataclass(frozen=True)
class NodeContext:
    task_id: str
    node_run_id: str
    revision_id: str
    group_id: str | None
    item_id: str | None

@dataclass(frozen=True)
class ReviewNode:
    node_run_id: str
    phase: str
    bindings: tuple[WorkerBinding, ...]

@dataclass(frozen=True)
class WorkerResult:
    worker_id: str
    status: str
    payload_ref: str | None
    failure: FailureRecord | None = None

@dataclass(frozen=True)
class NodeResult:
    node_run_id: str
    status: str
    worker_results: tuple[WorkerResult, ...]
    aggregate: object | None = None
```

```python
class WorkerRunner(Protocol):
    def run(self, binding: WorkerBinding, context: NodeContext) -> WorkerResult: ...

class NodeJoiner(Protocol):
    def join(self, results: Sequence[WorkerResult]) -> NodeResult: ...

class NodeExecutor(Protocol):
    def execute(self, node: ReviewNode, context: NodeContext) -> NodeResult: ...
```

`WorkerRunner.run()` receives an immutable `NodeContext` and returns an immutable result. It cannot receive the mutable `WorkflowContext` or call `save_state()`.

- [ ] **Step 2: Implement sequential execution**

`SequentialNodeExecutor` runs workers in order and passes only each worker result to the joiner. It uses the same binding, retry and result validation as concurrent execution.

- [ ] **Step 3: Implement concurrent execution**

`ConcurrentNodeExecutor` uses the existing bounded executor, but each future returns `WorkerResult`. It must:

- preserve actual `author_id` through normalization;
- keep `worker_id`, `logical_request_id`, `revision_id`, group and item scope;
- write worker artifacts through `ArtifactPort`;
- never mutate shared workflow context;
- make join results deterministic by sorting on worker id and logical request id;
- distinguish worker failure from node join failure with different `FailureRecord.stage` values.

- [ ] **Step 4: Replace duplicate runner behavior**

Route Zhongshu and Menxia through `NodeExecutor`. Keep old coordinator classes only as thin adapters while references remain. Do not delete old modules until `rg` finds no production import.

- [ ] **Step 5: Test node isolation and partial failure**

Run:

```powershell
rtk python -m unittest -v test_linear_fsm_nodes test_parallel_runtime_correlation test_zhongshu_parallel
```

Expected: sequential and concurrent execution produce the same `NodeResult` for the same ordered worker results; wrong-author, timeout and partial-failure cases are attributed to the worker, not the parent FSM.

## 9. Task 8: Build `WorkflowEngine` and keep CLI compatibility

**Files:**
- Create: `cmd/orchestrator/runtime/engine.py`
- Modify: `cmd/orchestrator/app.py`
- Modify: `cmd/review_orchestrator_v2.py` only if import wiring requires it
- Test: `cmd/test_linear_fsm_engine.py`
- Test: `cmd/test_orchestrator_correlation_v32.py`

- [ ] **Step 1: Implement the engine loop**

Define the remaining coordinator ports and result type in `runtime/engine.py`:

```python
from dataclasses import dataclass
from typing import Protocol

class DomainEventInbox(Protocol):
    def next(self, task_id: str) -> DomainEvent | None: ...

class StateRegistry(Protocol):
    def get(self, state_name: str) -> WorkflowState: ...

class LockPort(Protocol):
    def acquire(self, task_id: str) -> None: ...
    def refresh(self, task_id: str) -> None: ...
    def release(self, task_id: str) -> None: ...

class ContextReducer(Protocol):
    def apply(self, snapshot: WorkflowSnapshot, decision: StateDecision) -> WorkflowSnapshot: ...

@dataclass(frozen=True)
class RunResult:
    task_id: str
    status: str
    sequence: int
```

```python
class WorkflowEngine:
    def __init__(
        self,
        repository: WorkflowRepository,
        event_inbox: DomainEventInbox,
        states: StateRegistry,
        effects: EffectManager,
        lock: LockPort,
        reducer: ContextReducer,
    ) -> None: ...

    def dispatch(self, event: DomainEvent) -> StateDecision:
        snapshot = self.repository.load(event.task_id)
        state = self.states.get(snapshot.context.progression.state)
        decision = state.handle(snapshot.context, event)
        after = self.reducer.apply(snapshot, decision)
        self.repository.commit_transition(snapshot, after, decision)
        return decision
```

The production implementation must validate sequence, transition registry, context update ownership and effect intent before commit.

- [ ] **Step 2: Convert `OrchestratorApp` to a wiring façade**

Move these responsibilities out of `app.py` into the new components:

- `_next_event` -> `DomainEventInbox` and state-specific event adapters;
- `_consume_agent_payload` -> `ReplyNormalizer` and `FindingLedger`;
- `_save_artifact_for_state` -> `ArtifactPort`/`WriteArtifactEffect`;
- `_update_findings` -> `FindingLedger`;
- `_freeze_check_event` -> freeze-check state and policy;
- `_dispatch_pending` -> `EffectManager`;
- `_run_parallel_state` -> `NodeExecutor`.

Leave `app.py` responsible for CLI argument parsing, dependency wiring and process lifecycle only.

- [ ] **Step 3: Preserve public behavior**

Run:

```powershell
rtk python -m unittest -v test_orchestrator_correlation_v32 test_lifecycle test_final_delivery
```

Expected: existing task creation, resume, cancel, inspect, final delivery and correlation behavior remains unchanged.

## 10. Task 9: Migrate business states in linear order

**Files:**
- Modify: `cmd/orchestrator/domain/states.py`
- Modify: `cmd/orchestrator/domain/findings.py`
- Modify: `cmd/orchestrator/states.py` as a compatibility façade
- Modify: `cmd/orchestrator/app.py` to remove migrated branches one group at a time
- Test: `cmd/test_linear_fsm_states.py`
- Test: existing role, solver, critic, human-gate and freeze tests

- [ ] **Step 1: Migrate `REQUEST_INTAKE` and `ZHONGSHU_ANALYST`**

Keep prompt construction behind a role-specific `PromptPolicy`. The state object only creates the request effect and handles the normalized completion event. Run the full analyst and parallel test groups.

- [ ] **Step 2: Migrate `ZHONGSHU_SOLVER` and `ZHONGSHU_CRITIC`**

Move solver revision, critic action validation and blocked-reason selection into dedicated policies. Keep `FailureRecord` stage `state` for domain decisions and `node` for worker failures.

- [ ] **Step 3: Migrate `ZHONGSHU_FREEZE_CHECK`**

Move schema/freeze validation into `FreezeCheckPolicy`. It may read `ReviewState`, but it cannot write artifacts or notify Feishu. It returns a typed decision and effect requests.

- [ ] **Step 4: Migrate Menxia item and group states**

Use `FindingLedger.for_scope(group_id, item_id)` before transition guards. Verify unresolved P1/P2 routes before group gate. Do not allow an unscoped finding to be treated as resolved merely because the policy was called directly.

- [ ] **Step 5: Migrate `HUMAN_GATE`, `BLOCKED`, `FAILED`, and `PERSISTENCE_DEGRADED`**

Every system state must validate its required metadata on resume. Missing `decision_id`, `resume_state`, or persistence intent must fail closed with a typed invariant failure instead of polling forever.

- [ ] **Step 6: Run the state matrix**

Run from `cmd`:

```powershell
rtk python -m unittest -v test_linear_fsm_states test_role_phase_contracts test_solver_contract_fix test_human_gate_protocol_v31 test_human_gate_queue_v31 test_heartbeat_and_timeout_v31
```

Expected: all migrated states pass the same event/transition matrix and no state implementation performs external I/O directly.

## 11. Task 10: Harden locking, recovery and diagnostics

**Files:**
- Create: `cmd/orchestrator/runtime/locks.py` port adapter if needed
- Modify: `cmd/orchestrator/locks.py`
- Modify: `cmd/orchestrator/recovery.py`
- Modify: `cmd/orchestrator/logging_setup.py`
- Modify: `cmd/orchestrator/app.py` inspect/replay wiring
- Test: `cmd/test_linear_fsm_locking.py`
- Test: `cmd/test_process_interruption_v31.py`
- Test: `cmd/test_runtime_diagnostics_v31.py`

- [ ] **Step 1: Make lock acquisition ownership-safe**

Replace delete-then-create reclamation with an ownership/version check. `refresh()` must fail closed when ownership is lost, and the engine must stop executing effects after `LeaseLostError`.

- [ ] **Step 2: Unify recovery**

Recovery must process, in order:

1. journal/snapshot reconciliation;
2. pending/running effect recovery;
3. pending worker result recovery;
4. state resume validation.

It must not independently re-dispatch the same request without checking the effect idempotency key and external operation lookup.

- [ ] **Step 3: Add structured diagnostics**

Every state transition, effect lifecycle event and failure log must include task id, sequence, state, stage, owner component and correlation ids. Keep the existing human-readable console output, but write structured fields to the file log.

- [ ] **Step 4: Add inspect/replay support only after journal is authoritative**

`inspect` must report current state, last transition, pending effects, failure records and recovery status. Replay must read journal records without calling external adapters. Do not advertise replay before the repository tests pass.

- [ ] **Step 5: Run interruption and diagnostics tests**

```powershell
rtk python -m unittest -v test_linear_fsm_locking test_process_interruption_v31 test_runtime_diagnostics_v31
```

Expected: lock ownership loss stops the run, restart recovers pending effects exactly once, and every failure can be located by `failure_id`.

## 12. Task 11: Remove duplicate runtime paths

**Files:**
- Modify: `cmd/orchestrator/app.py`
- Modify: `cmd/orchestrator/states.py`
- Modify: `cmd/orchestrator/parallel_runtime.py`
- Modify: `cmd/orchestrator/zhongshu_parallel.py`
- Modify: `cmd/orchestrator/menxia_parallel.py`
- Modify: `cmd/orchestrator/adapters.py`
- Test: all orchestrator tests under `cmd/test_*.py`

- [ ] **Step 1: Verify production references before removal**

Run:

```powershell
rtk rg -n "ZhongshuFanoutCoordinator|_run_parallel_state|_prepare_parallel_menxia_group|_consume_agent_payload|BaseState" cmd/orchestrator cmd/review_orchestrator_v2.py
```

For each remaining reference, either migrate it to the new port or document it as a test-only compatibility fixture. Do not remove a module while a production import remains.

- [ ] **Step 2: Remove migrated branches**

Delete only branches whose behavior is covered by the new state/node tests. Keep external adapter code until compatibility tests prove the canonical envelope covers its legacy inputs.

- [ ] **Step 3: Run static quality checks**

```powershell
rtk python -m compileall cmd/orchestrator
rtk rg -n "if .*state.*name|save_state\(|requests\.|ThreadPoolExecutor|dict\[str, Any\]" cmd/orchestrator/domain cmd/orchestrator/runtime
```

Expected: no state-name dispatch branches in domain/runtime, no direct `save_state()` in state objects, no network calls in domain objects, and no untyped context patch in the new packages.

- [ ] **Step 4: Run the complete test suite**

```powershell
rtk python cmd/run_core_tests.py
rtk python -m unittest discover -s cmd -p "test_*.py" -v
```

Expected: core, transition, transport, parallel, persistence, recovery, human-gate and compatibility tests pass. Any test requiring a live validation service must use port `18766`, never the active `8766` instance.

## 13. Task 12: Documentation and handoff

**Files:**
- Modify: `openwiki/quickstart.md` only if the source workflow changes
- Modify: relevant OpenWiki source documentation, not generated pages
- Modify: `cmd/review_orchestrator_README.md` if present and tracked
- Test: documentation command examples

- [ ] **Step 1: Document the new object boundaries**

Document the ownership table and the event/effect lifecycle. Include one sequence diagram in text form:

```text
DomainEvent -> WorkflowState -> StateDecision -> Repository.commit_transition
            -> EffectManager -> ExternalPort -> EffectResult -> DomainEvent
```

- [ ] **Step 2: Document recovery guarantees**

State which failures are retryable, which are terminal, how `PERSISTENCE_DEGRADED` resumes, and how `failure_id` is used to trace a failed run.

- [ ] **Step 3: Verify documentation commands**

Run the documented unit-test commands from the repository root or `cmd` directory exactly as written. Do not hand-edit generated OpenWiki pages.

## 14. Final acceptance checklist

- [ ] Main FSM advancement is performed only by `WorkflowEngine`.
- [ ] Every state object has one domain responsibility and performs no I/O.
- [ ] No generic context patch or arbitrary dynamic target exists in the new domain layer.
- [ ] Direct and parallel replies share the same `ReplyNormalizer` and binding validator.
- [ ] Actual reply author identity is preserved through the parallel path.
- [ ] Worker execution, node scheduling, result joining and finding lifecycle are separate objects.
- [ ] Journal is authoritative for committed transitions; snapshot is recoverable.
- [ ] Effect intent/result lifecycle is durable and idempotent.
- [ ] Event append failure stops further business progression.
- [ ] Lock ownership loss stops external execution.
- [ ] Every failure has a `failure_id`, stage and owner component.
- [ ] CLI, Multica, Feishu, prompt bundle and result-file compatibility tests pass.
- [ ] Core tests and full test discovery pass without using port `8766` for validation.
- [ ] Legacy runtime paths have no production references before deletion.
