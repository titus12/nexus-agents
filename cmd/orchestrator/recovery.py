from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .context import StateContext
from .models import AgentRequest, DispatchReceipt
from .persistence import JsonStateStore, PersistenceError


@dataclass(frozen=True)
class RecoveryResult:
    status: str
    action: str
    detail: str = ""


class RecoveryManager:
    def __init__(self, store: JsonStateStore) -> None:
        self.store = store

    def load(self) -> StateContext:
        return self.store.load()

    def reconcile_dispatch(self, ctx: StateContext, adapter: Any, request: AgentRequest) -> RecoveryResult:
        if ctx.dispatch_status == "confirmed":
            return RecoveryResult("confirmed", "poll", "dispatch already confirmed")
        if ctx.dispatch_status != "pending":
            return RecoveryResult("none", "dispatch", "no pending dispatch")
        key = ctx.dispatch_idempotency_key or request.idempotency_key
        existing = adapter.find_existing_request(key)
        if existing is not None:
            ctx.dispatch_status = "confirmed"
            ctx.dispatch_operation_id = existing.operation_id
            self.store.save_state(ctx)
            return RecoveryResult("confirmed", "reconcile", "external request already exists")
        receipt: DispatchReceipt = adapter.dispatch(request)
        ctx.dispatch_status = "confirmed" if receipt.confirmed else "pending"
        ctx.dispatch_operation_id = receipt.operation_id
        self.store.save_state(ctx)
        return RecoveryResult(ctx.dispatch_status, "redispatch", "no matching external request")

    def require_recoverable(self, ctx: StateContext) -> None:
        if not ctx.recoverable:
            raise PersistenceError("task is marked non-recoverable")
