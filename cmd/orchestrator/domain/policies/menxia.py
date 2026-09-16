"""Pure Menxia scope and item finding rules."""

from __future__ import annotations

from collections.abc import Iterable

from ..errors import InvariantViolation
from ..findings import Finding


def validate_scope(
    findings: Iterable[Finding],
    *,
    group_id: str,
    item_id: str,
) -> tuple[Finding, ...]:
    group = str(group_id or "").strip()
    item = str(item_id or "").strip()
    if not group or not item:
        raise InvariantViolation("Menxia item finding scope requires group_id and item_id")
    result = tuple(findings)
    for finding in result:
        if finding.group_id and finding.group_id != group:
            raise InvariantViolation("finding group_id does not match active Menxia group")
        if finding.item_id and finding.item_id != item:
            raise InvariantViolation("finding item_id does not match active Menxia item")
    return result


def findings_for_scope(
    findings: Iterable[Finding],
    *,
    group_id: str,
    item_id: str | None = None,
    include_global: bool = False,
) -> tuple[Finding, ...]:
    group = str(group_id or "").strip()
    item = str(item_id or "").strip()
    if item and not group:
        raise InvariantViolation("item scope requires group_id")
    result = []
    for finding in findings:
        if item:
            if finding.group_id == group and finding.item_id == item:
                result.append(finding)
        elif finding.group_id == group or (
            include_global and not finding.group_id and not finding.item_id
        ):
            result.append(finding)
    return tuple(result)


__all__ = ["findings_for_scope", "validate_scope"]
