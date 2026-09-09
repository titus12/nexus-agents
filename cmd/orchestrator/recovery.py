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
        if ctx.dispatch_status == "confirmed" and ctx.dispatch_external_message_id:
            return RecoveryResult("confirmed", "poll", "dispatch already confirmed")
        if ctx.dispatch_status == "confirmed" and not ctx.dispatch_external_message_id:
            ctx.dispatch_status = "pending"
        if ctx.dispatch_status != "pending":
            return RecoveryResult("none", "dispatch", "no pending dispatch")
        key = ctx.dispatch_idempotency_key or request.idempotency_key
        existing = adapter.find_existing_request(key, request.issue_id)
        if existing is not None:
            ctx.dispatch_status = (
                "confirmed" if existing.external_message_id else "pending"
            )
            ctx.dispatch_operation_id = existing.operation_id
            ctx.dispatch_external_message_id = existing.external_message_id
            self.store.save_state(ctx)
            return RecoveryResult(
                ctx.dispatch_status,
                "reconcile",
                (
                    "external request already exists"
                    if existing.external_message_id
                    else "external request exists but trigger correlation is pending"
                ),
            )
        receipt: DispatchReceipt = adapter.dispatch(request)
        ctx.dispatch_status = (
            "confirmed"
            if receipt.confirmed and receipt.external_message_id
            else "pending"
        )
        ctx.dispatch_operation_id = receipt.operation_id
        ctx.dispatch_external_message_id = receipt.external_message_id
        self.store.save_state(ctx)
        return RecoveryResult(ctx.dispatch_status, "redispatch", "no matching external request")

    def require_recoverable(self, ctx: StateContext) -> None:
        if not ctx.recoverable:
            raise PersistenceError("task is marked non-recoverable")
