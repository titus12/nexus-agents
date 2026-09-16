"""Pure Zhongshu finding and revision rules."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace

from ..context import ReviewState
from ..findings import Finding


# Critic actions that send the plan back to the Solver and therefore consume
# one Zhongshu revision round out of the bounded convergence budget.
REVISION_ACTIONS = frozenset(
    {
        "REQUEST_SOLVER_REVISION",
        "REQUEST_REGROUP",
        "TASK_CHANGES_REQUIRED",
    }
)

# Freeze-check actions that send the plan back to the Solver and therefore
# consume one attempt out of the bounded freeze-retry budget.
FREEZE_RETRY_ACTIONS = frozenset({"FREEZE_REJECTED"})

# Critic actions that assert the reviewed plan is good enough to freeze.  A
# freeze releases the plan to Menxia, so it must be justified by the whole task
# ledger, not only by the tasks re-reviewed in the current round.
APPROVAL_ACTIONS = frozenset(
    {"APPROVE_CRITIC", "APPROVE_FREEZE", "TASK_APPROVED", "FREEZE_APPROVED"}
)

_BLOCKER_FINGERPRINT_PREFIX = "blockers="
_ACTIVE_STATUSES = frozenset(
    {"OPEN", "ASSIGNED_TO_ANALYST", "ASSIGNED_TO_SOLVER", "IN_REVIEW", "REOPENED"}
)
_BLOCKING_SEVERITIES = frozenset({"P0", "P1"})


def merge_findings(
    current: Iterable[Finding],
    incoming: Iterable[Finding | Mapping[str, object]],
) -> tuple[Finding, ...]:
    merged = {finding.identity_key(): finding for finding in current}
    for value in incoming:
        finding = value if isinstance(value, Finding) else Finding.from_dict(dict(value))
        merged[finding.identity_key()] = finding
    return tuple(merged.values())


def age_unresolved_findings(
    previous: Iterable[Finding],
    merged: Iterable[Finding],
    observed: Iterable[Finding],
) -> tuple[Finding, ...]:
    """Count consecutive rounds the Critic has re-raised the same finding.

    ``observed`` is this round's Critic output.  A finding that keeps coming
    back with the same identity is not converging even while the Solver keeps
    re-emitting the plan, so its ``stuck_rounds`` grows.  The counter resets as
    soon as the finding is resolved, or as soon as the Critic stops re-raising
    it (a finding the Critic dropped is no longer "observed").
    """

    prior = {finding.identity_key(): finding for finding in previous or ()}
    observed_keys = {finding.identity_key() for finding in observed or ()}
    result: list[Finding] = []
    for finding in merged:
        key = finding.identity_key()
        if key not in observed_keys:
            result.append(finding)
            continue
        earlier = prior.get(key)
        if finding.active and earlier is not None and earlier.active:
            result.append(replace(finding, stuck_rounds=earlier.stuck_rounds + 1))
        else:
            result.append(replace(finding, stuck_rounds=0))
    return tuple(result)


def stuck_blockers(
    findings: Iterable[object],
    max_rounds: int,
) -> tuple[object, ...]:
    """Return active P0/P1 findings the Solver has failed to resolve in time.

    ``max_rounds <= 0`` disables the escalation.
    """

    if max_rounds <= 0:
        return ()
    result = []
    for finding in findings or ():
        if not _finding_attr(finding, "active", False):
            continue
        severity = str(_finding_attr(finding, "severity", "") or "").strip().upper()
        if severity not in _BLOCKING_SEVERITIES:
            continue
        try:
            stuck = int(_finding_attr(finding, "stuck_rounds", 0) or 0)
        except (TypeError, ValueError):
            stuck = 0
        if stuck >= max_rounds:
            result.append(finding)
    return tuple(result)


def active_findings(review: ReviewState | None) -> tuple[Finding, ...]:
    if review is None:
        return ()
    return tuple(finding for finding in review.findings if finding.active)


def revision_allowed(review: ReviewState | None) -> bool:
    """Return whether one more Solver revision is within the recorded budget.

    ``None`` means no review aggregate has been materialized yet, so there is
    nothing to bound and the transition is allowed.
    """

    return review is None or review.zhongshu_revision_round < review.max_zhongshu_revision_rounds


def freeze_retry_allowed(review: ReviewState | None) -> bool:
    """Return whether one more freeze rejection may return to the Solver."""

    return review is None or review.freeze_check_attempt < review.max_freeze_check_attempts


def _finding_attr(finding: object, name: str, default: object = None) -> object:
    if isinstance(finding, Mapping):
        return finding.get(name, default)
    return getattr(finding, name, default)


def active_blocker_count(findings: Iterable[object]) -> int:
    """Count active P0/P1 findings; the critic's convergence pressure metric."""

    total = 0
    for finding in findings or ():
        status = str(_finding_attr(finding, "status", "") or "").strip().upper()
        severity = str(_finding_attr(finding, "severity", "") or "").strip().upper()
        if status in _ACTIVE_STATUSES and severity in _BLOCKING_SEVERITIES:
            total += 1
    return total


def blocker_fingerprint(count: int) -> str:
    """Encode the current active-blocker count for the next round's comparison."""

    return f"{_BLOCKER_FINGERPRINT_PREFIX}{int(count)}"


def parse_blocker_fingerprint(value: str | None) -> int | None:
    if not value or not value.startswith(_BLOCKER_FINGERPRINT_PREFIX):
        return None
    try:
        return int(value[len(_BLOCKER_FINGERPRINT_PREFIX):])
    except ValueError:
        return None


def revision_made_progress(previous_fingerprint: str | None, active_blockers: int) -> bool:
    """Progress means the active P0/P1 blocker count strictly decreased.

    A missing previous fingerprint is the first observed round, which is not
    counted as a stall.
    """

    previous = parse_blocker_fingerprint(previous_fingerprint)
    if previous is None:
        return True
    return active_blockers < previous


def unapproved_item_ids(
    ledger: Iterable[object],
    expected_item_ids: Iterable[object] = (),
) -> tuple[str, ...]:
    """Return tasks that are not approved yet.

    ``task_review_ledger`` folds every round's verdict, so an empty result is
    the only sound basis for leaving Zhongshu: a round re-reviews just the tasks
    that are still unapproved or whose content changed, which means the round's
    own verdict set can be a strict subset of the graph.  Tasks the ledger never
    recorded are reported as unapproved when ``expected_item_ids`` names them,
    so a partial ledger cannot release unreviewed work either.
    """

    statuses: dict[str, str] = {}
    for record in ledger or ():
        item_id = str(_finding_attr(record, "item_id", "") or "").strip()
        if not item_id:
            continue
        status = str(_finding_attr(record, "status", "") or "").strip().upper()
        statuses[item_id] = status
    pending = [item for item, status in statuses.items() if status != "APPROVED"]
    pending.extend(
        item
        for item in (
            str(value).strip() for value in (expected_item_ids or ())
        )
        if item and item not in statuses
    )
    return tuple(dict.fromkeys(pending))


def approved_item_ids(ledger: Iterable[object]) -> tuple[str, ...]:
    """Return tasks whose latest Critic verdict is an approval."""

    approved: list[str] = []
    for record in ledger or ():
        item_id = str(_finding_attr(record, "item_id", "") or "").strip()
        if not item_id:
            continue
        status = str(_finding_attr(record, "status", "") or "").strip().upper()
        if status == "APPROVED":
            approved.append(item_id)
    return tuple(dict.fromkeys(approved))


def stalled_item_ids(
    ledger: Iterable[object],
    max_rounds: int,
) -> tuple[str, ...]:
    """Return tasks rejected for too many consecutive rounds.

    The Zhongshu revision budget bounds the whole graph, so a single task can
    spend it alone: without a per-task bound, one task the Critic keeps
    rejecting hides the convergence of every other task and ends the run in a
    generic budget block.  ``max_rounds <= 0`` disables the escalation.
    """

    if max_rounds <= 0:
        return ()
    stalled: list[str] = []
    for record in ledger or ():
        item_id = str(_finding_attr(record, "item_id", "") or "").strip()
        if not item_id:
            continue
        status = str(_finding_attr(record, "status", "") or "").strip().upper()
        if status == "APPROVED":
            continue
        try:
            rounds = int(_finding_attr(record, "changes_rounds", 0) or 0)
        except (TypeError, ValueError):
            rounds = 0
        if rounds >= max_rounds:
            stalled.append(item_id)
    return tuple(dict.fromkeys(stalled))


__all__ = [
    "APPROVAL_ACTIONS",
    "FREEZE_RETRY_ACTIONS",
    "REVISION_ACTIONS",
    "active_blocker_count",
    "active_findings",
    "age_unresolved_findings",
    "approved_item_ids",
    "blocker_fingerprint",
    "freeze_retry_allowed",
    "merge_findings",
    "parse_blocker_fingerprint",
    "revision_allowed",
    "revision_made_progress",
    "stalled_item_ids",
    "stuck_blockers",
    "unapproved_item_ids",
]
