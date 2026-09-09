"""Immutable finding value objects used by the review aggregate."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """A scoped review finding with immutable identity and lifecycle fields."""

    finding_id: str
    severity: str
    status: str
    group_id: str | None = None
    item_id: str | None = None


__all__ = ["Finding"]
