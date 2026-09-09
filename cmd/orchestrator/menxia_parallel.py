from __future__ import annotations
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable

ACTIVE = {'RUNNING', 'WAITING_REPLY', 'RETRYING'}
TERMINAL = {'APPROVED', 'BLOCKED', 'CANCELLED'}

class CoordinatorResult(str, Enum):
    WAITING = 'WAITING'
    ITEM_UPDATED = 'ITEM_UPDATED'
    GROUP_READY = 'GROUP_READY'
    FAN_IN_READY = 'FAN_IN_READY'
    FATAL_ERROR = 'FATAL_ERROR'

@dataclass(frozen=True)
class MenxiaParallelConfig:
    enabled: bool = False
    max_concurrent_groups: int = 1
    max_concurrent_items: int = 3
    failure_policy: str = 'continue_and_block_group'
    prompt_max_bytes: int = 12 * 1024
    revision_prompt_max_bytes: int = 24 * 1024

    def validate(self) -> None:
        if self.max_concurrent_groups < 1 or self.max_concurrent_items < 1:
            raise ValueError('concurrency limits must be >= 1')
        if self.failure_policy not in {'fail_fast', 'continue_and_block_group', 'allow_partial'}:
            raise ValueError('unsupported failure_policy')

@dataclass
class MenxiaItemRuntime:
    group_id: str
    item_id: str
    group_index: int
    item_index: int
    plan_revision_id: str
    state: str = 'MENXIA_ITEM_SOLVER'
    execution_status: str = 'PENDING'
    outcome: str | None = None
    request_id: str = ''
    dispatch_idempotency_key: str = ''
    dispatch_operation_id: str = ''
    dispatch_external_message_id: str = ''
    dispatch_status: str = 'none'
    sent_after: str = ''
    attempt: int = 0
    revision_round: int = 0
    reply_retry_count: int = 0
    external_retry_count: int = 0
    timeout_retry_count: int = 0
    started_at: str = ''
    updated_at: str = ''
    last_error: dict[str, Any] = field(default_factory=dict)
    proposal: dict[str, Any] = field(default_factory=dict)
    analyst_review: dict[str, Any] = field(default_factory=dict)
    critic_review: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)
    active_decision_id: str = ''
    human_gate: dict[str, Any] = field(default_factory=dict)
    resume_state: str = ''

    @property
    def terminal(self) -> bool:
        return self.execution_status == 'TERMINAL' and self.outcome in TERMINAL

@dataclass(frozen=True)
class RuntimeUpdate:
    task_id: str
    group_id: str
    item_id: str
    plan_revision_id: str
    request_id: str
    state: str
    event: str
    payload: dict[str, Any]
    observed_at: str = ''

class MenxiaParallelCoordinator:
    def __init__(self, task_id: str, frozen_plan: dict[str, Any], config: MenxiaParallelConfig | None = None, dispatch: Callable[[MenxiaItemRuntime], None] | None = None, poll: Callable[[MenxiaItemRuntime], RuntimeUpdate | None] | None = None) -> None:
        self.task_id = task_id
        self.frozen_plan = frozen_plan
        self.config = config or MenxiaParallelConfig(enabled=True)
        self.config.validate()
        self.dispatch = dispatch or (lambda _runtime: None)
        self.poll = poll or (lambda _runtime: None)
        self.plan_revision_id = str(frozen_plan.get('plan_revision_id') or 'freeze-1')
        self.runtimes: dict[tuple[str, str], MenxiaItemRuntime] = {}
        self.group_statuses: dict[str, str] = {}
        self._initialized = False

    def initialize_from_frozen_plan(self) -> None:
        if self._initialized:
            return
        groups = self.frozen_plan.get('groups') or self.frozen_plan.get('formal_groups') or []
        if not isinstance(groups, list) or not groups:
            raise ValueError('frozen plan has no groups')
        for gi, group in enumerate(groups):
            if not isinstance(group, dict):
                raise ValueError('group must be an object')
            gid = str(group.get('group_id') or f'group-{gi + 1:06d}')
            items = group.get('items')
            if not isinstance(items, list) or not items:
                raise ValueError(f'group {gid} has no items')
            self.group_statuses[gid] = 'PENDING'
            for ii, item in enumerate(items):
                if not isinstance(item, dict):
                    raise ValueError(f'item {gid}/{ii} must be an object')
                iid = str(item.get('item_id') or f'item-{ii + 1:06d}')
                key = (gid, iid)
                if key in self.runtimes:
                    raise ValueError(f'duplicate runtime {gid}/{iid}')
                self.runtimes[key] = MenxiaItemRuntime(gid, iid, gi, ii, self.plan_revision_id)
        self._initialized = True

    def tick(self) -> CoordinatorResult:
        self.initialize_from_frozen_plan()
        self._refresh_group_statuses()
        self._start_ready_groups()
        self._start_ready_items()
        updates = []
        for runtime in self._ordered_runtimes():
            if runtime.execution_status in ACTIVE:
                update = self.poll(runtime)
                if update is not None:
                    updates.append(update)
        for update in updates:
            self.merge_update(update)
        self._refresh_group_statuses()
        self._assert_invariants()
        if all(runtime.terminal for runtime in self.runtimes.values()):
            return CoordinatorResult.FAN_IN_READY
        if updates:
            return CoordinatorResult.ITEM_UPDATED
        if self._has_ready_group_gate():
            return CoordinatorResult.GROUP_READY
        return CoordinatorResult.WAITING

    def merge_update(self, update: RuntimeUpdate) -> None:
        if update.task_id != self.task_id:
            raise ValueError('runtime update task_id mismatch')
        runtime = self.runtimes.get((update.group_id, update.item_id))
        if runtime is None:
            raise ValueError('runtime update item not found')
        if update.plan_revision_id != runtime.plan_revision_id or update.request_id != runtime.request_id or update.state != runtime.state:
            return
        payload = update.payload if isinstance(update.payload, dict) else {}
        runtime.updated_at = update.observed_at
        if update.event in {'APPROVE_ITEM', 'ITEM_APPROVED'}:
            runtime.execution_status = 'TERMINAL'
            runtime.outcome = 'APPROVED'
            runtime.proposal = dict(payload.get('proposal') or runtime.proposal)
            runtime.analyst_review = dict(payload.get('analyst_review') or runtime.analyst_review)
            runtime.critic_review = dict(payload.get('critic_review') or runtime.critic_review)
        elif update.event in {'BLOCK_ITEM', 'ITEM_BLOCKED'}:
            runtime.execution_status = 'TERMINAL'
            runtime.outcome = 'BLOCKED'
            runtime.last_error = dict(payload.get('error') or runtime.last_error)
            runtime.updated_at = update.observed_at
        elif update.event == 'ADVANCE':
            next_state = str(payload.get('next_state') or '')
            if next_state not in {'MENXIA_ITEM_SOLVER', 'MENXIA_ITEM_ANALYST', 'MENXIA_ITEM_CRITIC'}:
                raise ValueError('invalid next item state')
            runtime.state = next_state
            runtime.last_error = {}
            runtime.execution_status = 'PENDING'
            runtime.request_id = ''
            runtime.dispatch_status = 'none'
        elif update.event == 'WAITING_REPLY':
            runtime.execution_status = 'WAITING_REPLY'
        elif update.event == 'RETRY':
            runtime.execution_status = 'RETRYING'
            runtime.attempt += 1
        else:
            raise ValueError(f'unsupported runtime update event: {update.event}')

    def is_complete(self) -> bool:
        return bool(self.runtimes) and all(runtime.terminal for runtime in self.runtimes.values())

    def snapshot(self) -> dict[str, Any]:
        return {'enabled': self.config.enabled, 'max_concurrent_groups': self.config.max_concurrent_groups, 'max_concurrent_items': self.config.max_concurrent_items, 'failure_policy': self.config.failure_policy, 'plan_revision_id': self.plan_revision_id, 'group_statuses': dict(self.group_statuses), 'runtimes': [asdict(r) for r in self._ordered_runtimes()]}

    def _ordered_runtimes(self) -> list[MenxiaItemRuntime]:
        return sorted(self.runtimes.values(), key=lambda r: (r.group_index, r.item_index, r.item_id))

    def _groups(self) -> list[dict[str, Any]]:
        groups = self.frozen_plan.get('groups') or self.frozen_plan.get('formal_groups') or []
        return [g for g in groups if isinstance(g, dict)]

    def _start_ready_groups(self) -> None:
        active = {gid for gid, status in self.group_statuses.items() if status == 'RUNNING'}
        for group in sorted(self._groups(), key=lambda g: str(g.get('group_id') or '')):
            gid = str(group.get('group_id') or '')
            if len(active) >= self.config.max_concurrent_groups or self.group_statuses.get(gid) != 'PENDING':
                continue
            deps = group.get('dependencies') or group.get('depends_on') or []
            if deps and not all(self.group_statuses.get(str(dep)) == 'APPROVED' for dep in deps):
                continue
            self.group_statuses[gid] = 'RUNNING'
            active.add(gid)

    def _start_ready_items(self) -> None:
        active = [r for r in self.runtimes.values() if r.execution_status in ACTIVE]
        for runtime in self._ordered_runtimes():
            if len(active) >= self.config.max_concurrent_items:
                break
            if runtime.execution_status != 'PENDING' or self.group_statuses.get(runtime.group_id) != 'RUNNING':
                continue
            runtime.attempt += 1
            runtime.request_id = runtime.request_id or f'{self.task_id}:MENXIA:{runtime.group_id}:{runtime.item_id}:{runtime.state}:{runtime.attempt}:runtime'
            runtime.dispatch_idempotency_key = runtime.dispatch_idempotency_key or f'{self.task_id}:MENXIA:{runtime.plan_revision_id}:{runtime.group_id}:{runtime.item_id}:{runtime.state}:{runtime.revision_round}:{runtime.attempt}'
            runtime.execution_status = 'WAITING_REPLY'
            runtime.dispatch_status = 'confirmed'
            active.append(runtime)
            self.dispatch(runtime)

    def _refresh_group_statuses(self) -> None:
        for gid in list(self.group_statuses):
            items = [r for r in self.runtimes.values() if r.group_id == gid]
            if all(r.terminal and r.outcome == 'APPROVED' for r in items):
                self.group_statuses[gid] = 'APPROVED'
            elif any(r.terminal and r.outcome == 'BLOCKED' for r in items):
                self.group_statuses[gid] = 'PARTIAL' if self.config.failure_policy == 'allow_partial' else 'BLOCKED'
            elif any(r.execution_status in ACTIVE for r in items):
                self.group_statuses[gid] = 'RUNNING'
            else:
                self.group_statuses[gid] = 'PENDING'

    def _has_ready_group_gate(self) -> bool:
        return any(status in {'APPROVED', 'PARTIAL', 'BLOCKED'} for status in self.group_statuses.values())

    def _assert_invariants(self) -> None:
        active = [r for r in self.runtimes.values() if r.execution_status in ACTIVE]
        if len(active) > self.config.max_concurrent_items:
            raise AssertionError('max_concurrent_items exceeded')
        if len({r.group_id for r in active}) > self.config.max_concurrent_groups:
            raise AssertionError('max_concurrent_groups exceeded')
        request_ids = [r.request_id for r in active if r.request_id]
        if len(request_ids) != len(set(request_ids)):
            raise AssertionError('duplicate active request_id')
