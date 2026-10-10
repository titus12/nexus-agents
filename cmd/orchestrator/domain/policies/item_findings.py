"""Shared data, hashing and validation helpers; no Zhongshu workflow execution."""
from __future__ import annotations
from collections.abc import Iterable
from typing import Any

def item_findings(findings: Iterable[object], item_id: str) -> tuple[dict[str, Any], ...]:
    """Active findings owned by one item, as payload dicts."""
    payload: list[dict[str, Any]] = []
    for finding in findings or ():
        if not getattr(finding, 'active', False):
            continue
        if str(getattr(finding, 'item_id', '') or '').strip() != item_id:
            continue
        to_dict = getattr(finding, 'to_dict', None)
        payload.append(dict(to_dict()) if callable(to_dict) else dict(finding))
    return tuple(payload)
