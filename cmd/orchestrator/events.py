from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Event:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    @property
    def action(self) -> str:
        return str(self.payload.get("action") or "")

    @property
    def reason(self) -> str:
        return str(self.payload.get("reason") or "")
