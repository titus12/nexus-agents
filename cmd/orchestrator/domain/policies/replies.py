"""Pure role reply binding and normalization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..errors import ReplyBindingError, ReplyValidationError


@dataclass(frozen=True)
class NormalizedRoleReply:
    task_id: str
    request_id: str
    phase: str
    role: str
    action: str
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


def normalize_role_reply(
    payload: Mapping[str, object],
    *,
    task_id: str,
    request_id: str,
    phase: str,
    role: str,
) -> NormalizedRoleReply:
    if not isinstance(payload, Mapping):
        raise ReplyValidationError("role reply must be an object")
    for field, expected in (
        ("task_id", task_id),
        ("request_id", request_id),
        ("phase", phase),
        ("role", role),
    ):
        supplied = payload.get(field)
        if supplied is not None and str(supplied) != expected:
            raise ReplyBindingError(f"reply {field} does not match request")
    action = str(payload.get("action") or "")
    if not action:
        raise ReplyValidationError("role reply action is required")
    normalized = dict(payload)
    normalized.update(
        task_id=task_id,
        request_id=request_id,
        phase=phase,
        role=role,
    )
    return NormalizedRoleReply(task_id, request_id, phase, role, action, normalized)


__all__ = ["NormalizedRoleReply", "normalize_role_reply"]
