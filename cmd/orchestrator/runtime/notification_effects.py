"""Durable notification effect execution.

Human-gate messages are effects, not state transitions.  The production port
is enabled by default; tests and local dry runs can set
``NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS=1`` to force the no-network port.
"""

from __future__ import annotations

from ..adapters import FeishuHttpAdapter
from ..domain.decisions import EffectRequest
from ..domain.errors import FailureRecord
from ..transport.external import HumanGate
from .effects import EffectOutcome
from .ports import (
    NotificationPort,
    NotificationReceipt,
    NotificationRequest,
)


class NullNotificationPort:
    """A no-network sink used by tests and local dry runs."""

    def send(self, request: NotificationRequest) -> NotificationReceipt:
        # A disabled sink is an intentional local outcome, not a delivery
        # failure.  This lets a test exercise the human-gate transition
        # without ever contacting Feishu or hanging on a retry loop.
        return NotificationReceipt(request.notification_key, delivered=True)


class FeishuNotificationPort:
    """Adapt the existing Feishu gate adapter to the runtime port."""

    def __init__(self, adapter: FeishuHttpAdapter | None = None) -> None:
        self._adapter = adapter or FeishuHttpAdapter()

    def send(self, request: NotificationRequest) -> NotificationReceipt:
        receipt = self._adapter.send_gate(
            HumanGate(
                decision_id=request.notification_key,
                task_id=request.task_id,
                resume_state="",
                prompt=request.body,
            )
        )
        return NotificationReceipt(request.notification_key, delivered=receipt.delivered)


class NotificationEffectRunner:
    """Execute one notification intent and persist its delivery outcome."""

    def __init__(self, port: NotificationPort) -> None:
        self._port = port

    def run_once(self, request: EffectRequest) -> EffectOutcome:
        notification = NotificationRequest(
            task_id=request.task_id,
            notification_key=str(request.payload.get("notification_key") or request.idempotency_key),
            body=str(request.payload.get("body") or ""),
        )
        receipt = self._port.send(notification)
        payload = {
            "notification_key": notification.notification_key,
            "delivered": receipt.delivered,
        }
        if receipt.delivered:
            return EffectOutcome(status="SUCCEEDED", event_payload=payload)
        failure = FailureRecord(
            failure_id=f"{request.effect_id}:NOTIFICATION_NOT_DELIVERED",
            stage="notification",
            owner_component="notification_port",
            task_id=request.task_id,
            state=str(request.payload.get("state") or "HUMAN_GATE"),
            sequence=int(request.payload.get("sequence") or 0),
            node_run_id=None,
            worker_id=None,
            effect_id=request.effect_id,
            error_code="NOTIFICATION_NOT_DELIVERED",
            retryable=True,
            message="notification port did not confirm delivery",
            cause_type="NotificationReceipt",
        )
        return EffectOutcome(
            status="FAILED",
            event_name="FAIL",
            event_payload=payload,
            failure=failure,
        )


__all__ = ["FeishuNotificationPort", "NotificationEffectRunner", "NullNotificationPort"]
