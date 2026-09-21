"""Pure Zhongshu finding and revision rules."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
import hashlib
import re

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


def _finding_identity(finding: object) -> str:
    """Content identity of one finding observation.

    Critic rounds may re-raise the same opinion under a different ``finding_id``
    (abbreviated vs full id), which used to store two entries for one opinion
    and let the twin waste a batch slot and double-count blockers.  A finding
    with a ``canonical_key`` is identified by that content hash; only findings
    without one fall back to the structural identity.
    """

    canonical = str(_finding_attr(finding, "canonical_key", "") or "").strip()
    if canonical:
        return f"content:{canonical}"
    return "id:{0}|{1}|{2}".format(
        str(_finding_attr(finding, "group_id", "") or ""),
        str(_finding_attr(finding, "item_id", "") or ""),
        str(_finding_attr(finding, "finding_id", "") or ""),
    )


def _same_finding_id(left: str, right: str) -> bool:
    """Match a batch id against a stored id, tolerating abbreviated forms."""

    if not left or not right:
        return False
    if left == right:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    return len(shorter) >= 16 and longer.startswith(shorter)


def merge_findings(
    current: Iterable[Finding],
    incoming: Iterable[Finding | Mapping[str, object]],
) -> tuple[Finding, ...]:
    """Fold incoming findings into the ledger, one entry per opinion.

    Entries are keyed by content identity (see ``_finding_identity``), so a
    re-raise under a different id form replaces the stored opinion instead of
    duplicating it.  As before, the incoming observation wins: the latest
    Critic output carries the current lifecycle status.
    """

    merged: dict[str, Finding] = {}
    for finding in current or ():
        value = (
            finding
            if isinstance(finding, Finding)
            else Finding.from_dict(dict(finding))
        )
        merged[_finding_identity(value)] = value
    for value in incoming or ():
        finding = value if isinstance(value, Finding) else Finding.from_dict(dict(value))
        merged[_finding_identity(finding)] = finding
    return tuple(merged.values())


# --- cross-language restatement gate ---------------------------------------
#
# The task-review Critic re-raises chronic complaints across rounds, and the
# live run task-20260920-bbd659 showed it alternating between Chinese and
# English while minting a fresh finding_id each time (one item collected seven
# ids for three real issues).  Text-similarity dedup cannot bridge a language
# switch (those pairs scored 0.11-0.19), so the ingest folds such restatements
# back onto the stored finding mechanically: the orchestrator stamps the
# identity, exactly like it stamps revision ids and plan hashes.  The match is
# deliberately narrow — different script AND a shared code anchor (identifier
# or file:line) on the same item — because a false merge silently replaces the
# stored claim, while prose-only rewordings inside one language stay the job of
# the similarity gate and the finding_id echo instruction.

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_CODE_ANCHOR_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9_\-.]*\.py:\d+"        # file:line references
    r"|[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]+"   # snake_case identifiers
    r"|[a-z]+[A-Z][A-Za-z0-9]+"                # camelCase identifiers
    r"|[A-Z][A-Z0-9_]{3,}"                     # shouting constants
)
_REBIND_ANCHOR_STOPWORDS = frozenset(
    {"UNKNOWN", "RESOLVED", "OPEN", "PENDING", "REOPENED", "TODO"}
)


def _claim_script(claim: str) -> str:
    cjk = len(_CJK_RE.findall(claim))
    return "cjk" if cjk * 5 >= len(claim) else "latin"


def _code_anchors(claim: str) -> frozenset[str]:
    anchors = {
        match.group(0).upper()
        for match in _CODE_ANCHOR_RE.finditer(claim)
    }
    return frozenset(anchors - _REBIND_ANCHOR_STOPWORDS)


def rebind_restatement_findings(
    current: Iterable[Finding],
    incoming: Iterable[Finding | Mapping[str, object]],
) -> tuple[tuple[Finding, ...], list[dict[str, str]]]:
    """Rewrite cross-language restatements onto the stored finding identity.

    Returns the (possibly re-keyed) incoming findings plus a report of every
    rebind for the caller to log.  Only an inbound finding that switches the
    claim language while sharing a code anchor with an active finding on the
    same item is rebound; everything else keeps the id the Critic minted.
    """

    current_findings = [
        finding if isinstance(finding, Finding) else Finding.from_dict(dict(finding))
        for finding in current or ()
    ]
    rebinds: list[dict[str, str]] = []
    rebound: list[Finding] = []
    for value in incoming or ():
        finding = (
            value if isinstance(value, Finding) else Finding.from_dict(dict(value))
        )
        claim = str(finding.claim or "")
        item_id = str(finding.item_id or "").strip()
        anchors = _code_anchors(claim)
        if item_id and anchors:
            for existing in current_findings:
                if not existing.active:
                    continue
                if str(existing.item_id or "").strip() != item_id:
                    continue
                existing_claim = str(existing.claim or "")
                if _claim_script(existing_claim) == _claim_script(claim):
                    continue
                shared = anchors & _code_anchors(existing_claim)
                if not shared:
                    continue
                rebinds.append(
                    {
                        "from": str(finding.finding_id or ""),
                        "to": str(existing.finding_id or ""),
                        "item_id": item_id,
                        "shared_anchor": sorted(shared)[0],
                    }
                )
                finding = replace(finding, finding_id=existing.finding_id)
                break
        rebound.append(finding)
    return tuple(rebound), rebinds


def age_unresolved_findings(
    previous: Iterable[Finding],
    merged: Iterable[Finding],
    observed: Iterable[Finding],
    *,
    attempted_finding_ids: Iterable[str] | None = None,
) -> tuple[Finding, ...]:
    """Count the rounds the Solver had a chance to fix a finding and failed.

    ``observed`` is this round's Critic output.  A finding that keeps coming
    back with the same identity is not converging, so its ``stuck_rounds``
    grows; the counter resets as soon as the finding is resolved or the Critic
    stops re-raising it.

    ``attempted_finding_ids`` makes the counter fair: it is the batch the
    Solver was actually dictated (or every active finding after a full
    re-plan).  Only those findings age — a finding the batch never picked
    keeps its count frozen instead of being escalated for rejections it never
    had a chance to address.  ``None`` keeps the legacy count-every-round
    behaviour for flows without a batch.
    """

    prior = {_finding_identity(finding): finding for finding in previous or ()}
    observed_keys = {_finding_identity(finding) for finding in observed or ()}
    attempted: tuple[str, ...] | None = None
    if attempted_finding_ids is not None:
        attempted = tuple(
            dict.fromkeys(
                str(value).strip() for value in attempted_finding_ids if str(value).strip()
            )
        )
    result: list[Finding] = []
    for finding in merged:
        key = _finding_identity(finding)
        if key not in observed_keys:
            result.append(finding)
            continue
        earlier = prior.get(key)
        if finding.active and earlier is not None and earlier.active:
            rounds = earlier.stuck_rounds
            if attempted is None or _finding_was_attempted(finding, attempted):
                result.append(replace(finding, stuck_rounds=rounds + 1))
            else:
                # The batch did not pick this finding: freeze its count so the
                # incoming echo (which resets ``stuck_rounds`` to zero) cannot
                # erase the history either.
                result.append(replace(finding, stuck_rounds=rounds))
        else:
            result.append(replace(finding, stuck_rounds=0))
    return tuple(result)


def _finding_was_attempted(finding: object, attempted: tuple[str, ...]) -> bool:
    finding_id = str(_finding_attr(finding, "finding_id", "") or "").strip()
    if not finding_id:
        return False
    if finding_id in attempted:
        return True
    return any(_same_finding_id(finding_id, value) for value in attempted)


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


def active_blocker_ids(findings: Iterable[object]) -> tuple[str, ...]:
    """Return the ids of the active P0/P1 findings.

    A revision may defer non-blocking follow-ups, but every blocker must be in
    the batch the Solver actually works on.
    """

    result: list[str] = []
    for finding in findings or ():
        status = str(_finding_attr(finding, "status", "") or "").strip().upper()
        severity = str(_finding_attr(finding, "severity", "") or "").strip().upper()
        if status in _ACTIVE_STATUSES and severity in _BLOCKING_SEVERITIES:
            finding_id = str(_finding_attr(finding, "finding_id", "") or "").strip()
            if finding_id:
                result.append(finding_id)
    return tuple(dict.fromkeys(result))


def blocker_fingerprint(findings: Iterable[object]) -> str:
    """Encode the active P0/P1 blocker SET for the next round's comparison.

    Counting blockers let an oscillating critic reset the no-progress detector
    on every dip (live run task-20260918-30ec74: 5->3->4->4->5->3 counted as
    progress four times), so the fingerprint is the identity of the surviving
    blockers instead: only a genuine resolution -- the set strictly shrinking
    -- counts as progress.  Identity follows ``_finding_identity`` (canonical
    content key first), hashed to keep the persisted state compact.
    """

    return _BLOCKER_FINGERPRINT_PREFIX + ",".join(
        sorted(
            _blocker_identity_hash(finding)
            for finding in _blocker_records(findings)
        )
    )


def parse_blocker_fingerprint(value: str | None) -> frozenset[str] | None:
    if not value or not value.startswith(_BLOCKER_FINGERPRINT_PREFIX):
        return None
    raw = value[len(_BLOCKER_FINGERPRINT_PREFIX):]
    if raw.isdigit():
        # Legacy count-only fingerprints cannot be compared as sets; treat
        # them as a first observation so the next round starts fair.
        return None
    return frozenset(token for token in raw.split(",") if token)


def _blocker_records(findings: Iterable[object]) -> list[object]:
    return [
        finding
        for finding in findings or ()
        if str(_finding_attr(finding, "status", "") or "").strip().upper()
        in _ACTIVE_STATUSES
        and str(_finding_attr(finding, "severity", "") or "").strip().upper()
        in _BLOCKING_SEVERITIES
    ]


def _blocker_identity_hash(finding: object) -> str:
    return hashlib.sha256(
        _finding_identity(finding).encode("utf-8")
    ).hexdigest()[:12]


def revision_made_progress(previous_fingerprint: str | None, findings: Iterable[object]) -> bool:
    """Progress means the blocker set strictly shrank.

    Every blocker active last round must be resolved with no new blocker
    replacing it: an identical set, a growing set, or a swap (one resolved,
    one minted) all mean the chronic disagreement is still alive, so the
    no-progress counter must keep aging.  A missing previous fingerprint is
    the first observed round, which is not counted as a stall.
    """

    previous = parse_blocker_fingerprint(previous_fingerprint)
    if previous is None:
        return True
    current = frozenset(
        _blocker_identity_hash(finding) for finding in _blocker_records(findings)
    )
    return current < previous


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


def active_findings_by_severity(
    findings: Iterable[object],
    severities: Iterable[str],
) -> tuple[object, ...]:
    """Return the active findings whose severity is in ``severities``."""

    wanted = {str(value).strip().upper() for value in severities}
    return tuple(
        finding
        for finding in findings or ()
        if str(_finding_attr(finding, "status", "") or "").strip().upper()
        in _ACTIVE_STATUSES
        and str(_finding_attr(finding, "severity", "") or "").strip().upper() in wanted
    )


_SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _batch_sort_key(finding: object) -> tuple[int, int, str, str]:
    severity = str(_finding_attr(finding, "severity", "") or "").strip().upper()
    try:
        stuck = int(_finding_attr(finding, "stuck_rounds", 0) or 0)
    except (TypeError, ValueError):
        stuck = 0
    return (
        _SEVERITY_RANK.get(severity, 9),
        -stuck,
        str(_finding_attr(finding, "item_id", "") or ""),
        str(_finding_attr(finding, "finding_id", "") or ""),
    )


def select_solver_batch(
    findings: Iterable[object],
    max_findings: int,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Choose the bounded batch of findings the Solver must resolve this round.

    The orchestrator owns this decision: it knows the severities, the owning
    items and the freeze policy, while the Solver only has to resolve what it is
    given.  Leaving the partition to the agent turned every revision into a
    bookkeeping puzzle (select at most six of twenty, no overlap, exact
    coverage) whose mechanical slips discarded the whole reply.

    One finding per item is picked first so a round spreads across the graph,
    then the remaining slots are filled by severity.  Within one severity,
    findings the Solver already failed to resolve (higher ``stuck_rounds``)
    come first, so a finding the Critic keeps re-raising cannot starve behind
    newer opinions.  Returns ``(selected, remaining)``: disjoint, together
    covering every active finding.
    """

    active = tuple(
        finding
        for finding in findings or ()
        if str(_finding_attr(finding, "status", "") or "").strip().upper()
        in _ACTIVE_STATUSES
        and str(_finding_attr(finding, "finding_id", "") or "").strip()
    )
    ordered = sorted(active, key=_batch_sort_key)
    cap = max(1, int(max_findings))
    selected: list[str] = []
    chosen: set[str] = set()
    seen_items: set[str] = set()
    for finding in ordered:
        finding_id = str(_finding_attr(finding, "finding_id", "") or "").strip()
        item_id = str(_finding_attr(finding, "item_id", "") or "").strip()
        if item_id and item_id in seen_items:
            continue
        selected.append(finding_id)
        chosen.add(finding_id)
        seen_items.add(item_id)
        if len(selected) >= cap:
            break
    for finding in ordered:
        if len(selected) >= cap:
            break
        finding_id = str(_finding_attr(finding, "finding_id", "") or "").strip()
        if finding_id in chosen:
            continue
        selected.append(finding_id)
        chosen.add(finding_id)
    remaining = [
        str(_finding_attr(finding, "finding_id", "") or "").strip()
        for finding in ordered
        if str(_finding_attr(finding, "finding_id", "") or "").strip() not in chosen
    ]
    return tuple(selected), tuple(remaining)


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
    "active_blocker_ids",
    "active_findings",
    "active_findings_by_severity",
    "age_unresolved_findings",
    "approved_item_ids",
    "blocker_fingerprint",
    "freeze_retry_allowed",
    "merge_findings",
    "parse_blocker_fingerprint",
    "revision_allowed",
    "revision_made_progress",
    "select_solver_batch",
    "stalled_item_ids",
    "stuck_blockers",
    "unapproved_item_ids",
]


