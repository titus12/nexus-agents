"""Pure materialization of a Solver revision onto the orchestrator-owned plan.

The Solver never owns the canonical task graph.  On a revision it returns a
bounded set of typed ``changes`` (``replace_item_fields``, ``replace_group_items``,
``replace_group_fields``, ``replace_plan_fields``) against the plan the
orchestrator already holds.  These helpers apply those changes, validate the
finding batch coverage, and expose a single error code when a reply is invalid.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from collections.abc import Iterable, Mapping, Sequence

from ...zhongshu_review_queue import canonical_hash, structural_gate
from ..errors import SOLVER_STRUCTURE_REPLY_PREFIX

logger = logging.getLogger("review_orchestrator_fsm")


CHANGE_OPS = frozenset(
    {
        "replace_item_fields",
        "replace_group_items",
        "replace_group_fields",
        "replace_plan_fields",
    }
)

# Topology and the immutable Analyst contract may only change through typed
# operations, never through a free-form plan-level patch.
FORBIDDEN_PLAN_FIELDS = frozenset({"items", "groups", "requirements"})

# Structural-gate findings that make a plan unusable and must block a revision.
# Coverage-style findings (source requirement / uncovered requirement) stay
# advisory and are surfaced to the Critic rather than rejecting the plan.
_BLOCKING_STRUCTURAL_ISSUES = (
    "PLAN_NOT_OBJECT",
    "PLAN_HAS_NO_ITEMS",
    "ITEM_IN_MULTIPLE_GROUPS",
    "ITEM_WITHOUT_GROUP",
    "DEPENDENCY_UNKNOWN_ITEM",
    "DEPENDENCY_CYCLE",
    # A measurement claim with neither a verification recipe nor an UNKNOWN
    # marker is what five-round evidence fights are made of; reject it at
    # plan-materialization time (recipe/demote is a one-line edit for the
    # Solver, see the acceptance standard rule 8).
    "ACCEPTANCE_SIGNAL_UNVERIFIABLE",
)

# The error code the Solver state composes from blocking structural issues
# (``SOLVER_PLAN_STRUCTURE_INVALID:<issue>;<issue>``).  The literal lives in
# ``domain.errors`` so the retryability predicate and the reply-failure
# taxonomy agree on it.
_GATE_STRUCTURAL_ENTRY_PREFIX = "ACCEPTANCE_SIGNAL_UNVERIFIABLE"

# Reply-shape errors the Solver can trivially reformulate on a fresh attempt.
# These are mechanical protocol slips (e.g. sending both a plan and changes),
# not evidence that the plan is wrong, so the orchestrator re-asks instead of
# blocking the whole task for a human.
_RETRYABLE_SOLVER_REPLY_PREFIXES = (
    "SOLVER_PLAN_AND_CHANGES_AMBIGUOUS",
    "SOLVER_CURRENT_PLAN_MISSING",
    "SOLVER_CHANGE_NOT_OBJECT",
    "SOLVER_CHANGE_FIELDS_NOT_OBJECT",
    "SOLVER_CHANGE_ITEM_UNKNOWN:",
    "SOLVER_CHANGE_GROUP_UNKNOWN:",
    "SOLVER_CHANGE_OP_UNKNOWN:",
    "SOLVER_CHANGE_PLAN_FIELD_FORBIDDEN:",
    # A scoped revision whose editable ids name no item in the reviewed plan
    # (an unresolved finding owner) must not silently carry the reviewed plan
    # over the solver's fixes; re-ask with a corrected scope instead.
    "SOLVER_SCOPE_ITEM_UNKNOWN:",
    # Group-revision patch rewrote a member outside the finding-owning set.
    "SOLVER_ITEM_FROZEN:",
    # §8 projection needs one subsection per member item; a missing
    # subsection is a one-line document fix for the Solver.
    "PLAN_HAS_NO_ACCEPTANCE:",
    "SOLVER_REVISION_NO_RESPONSE:",
    # A mis-typed or abbreviated finding id is the same class of mechanical
    # slip: the batch intent is often correct, only the id strings are wrong.
    # Re-ask the Solver rather than blocking the task for a human.
    "SOLVER_FINDING_BATCH_COVERAGE_INCOMPLETE:",
    "SOLVER_FINDING_BATCH_OVERLAP:",
    # A blocker the Solver pushed into the deferred remainder is a shape slip
    # too: the graph cannot freeze while an active P0/P1 is not being worked on.
    "SOLVER_BATCH_MISMATCH:",
    # A missing or mis-typed disposition entry is the same class of mechanical
    # slip: the Solver must account for every dictated batch finding, and an
    # incomplete ``finding_resolutions`` list is fixed by re-asking, not by a human.
    "SOLVER_RESOLUTION_COVERAGE_INCOMPLETE:",
    # Rewriting a group document outside the revision scope is a boundary slip
    # the Solver fixes by dropping the document or echoing it verbatim.
    "SOLVER_GROUP_DOC_FROZEN:",
    # A missing or malformed group requirement document is a mechanical slip:
    # re-asking the Solver with the authoritative documents attached lets it
    # resubmit, whereas blocking the task for a human would not fix the form.
    "SOLVER_GROUP_DOC_MISSING:",
    "SOLVER_GROUP_DOC_INVALID:",
)


def is_retryable_solver_reply_error(error: str) -> bool:
    """True when a Solver reply error is a mechanical shape slip worth a retry."""

    text = str(error)
    # An acceptance-signal gate rejection is a one-line edit for the Solver
    # (add the verification recipe or demote the claim), so the first gate
    # rejection must bounce back on the reply budget instead of blocking the
    # task for a human.  A structural error that mixes in a non-gate entry
    # (dependency cycle, uncovered blocker) stays fatal: the feedback loop
    # would not know which half the Solver can actually fix.
    if text.startswith(SOLVER_STRUCTURE_REPLY_PREFIX + ":"):
        entries = text.split(":", 1)[1].split(";")
        return bool(entries) and all(
            entry.strip().startswith(_GATE_STRUCTURAL_ENTRY_PREFIX)
            for entry in entries
        )
    return any(text.startswith(prefix) for prefix in _RETRYABLE_SOLVER_REPLY_PREFIXES)


def solver_revision_response_error(
    active_finding_ids: Sequence[object],
    has_current_plan: bool,
    payload: Mapping[str, object],
) -> str:
    """Reject a revision round that silently ignores every active finding.

    On a revision the Solver protocol requires the reply to change the plan, to
    declare ``finding_resolutions``, or to carry a ``finding_batch`` that
    partitions the active findings.  ``changes=[]`` is only a valid no-op when
    the reply still accounts for the blockers; a reply that does none of these
    is a stall, not convergence, so the orchestrator re-asks the Solver instead
    of re-running the whole Critic round on an identical plan.
    """

    if not has_current_plan:
        return ""
    active = [str(item).strip() for item in active_finding_ids if str(item).strip()]
    if not active:
        return ""
    if isinstance(payload.get("plan"), Mapping):
        return ""
    changes = payload.get("changes")
    if isinstance(changes, list) and changes:
        return ""
    resolutions = payload.get("finding_resolutions")
    if isinstance(resolutions, list) and resolutions:
        return ""
    if isinstance(payload.get("finding_batch"), Mapping):
        return ""
    return "SOLVER_REVISION_NO_RESPONSE:active=" + ",".join(active[:6])



def structural_integrity_errors(plan: Mapping[str, object] | None) -> list[str]:
    """Return the blocking structural defects of a materialized plan."""

    if not isinstance(plan, Mapping):
        return ["PLAN_NOT_OBJECT"]
    errors = [
        issue
        for issue in structural_gate(plan)
        if issue.startswith(_BLOCKING_STRUCTURAL_ISSUES)
    ]
    item_ids = {
        str(item.get("item_id"))
        for item in (plan.get("items") or [])
        if isinstance(item, Mapping) and item.get("item_id")
    }
    for group in plan.get("groups") or []:
        if not isinstance(group, Mapping):
            continue
        group_id = str(group.get("group_id") or "")
        for item_id in group.get("item_ids") or []:
            if str(item_id) not in item_ids:
                errors.append(f"GROUP_UNKNOWN_ITEM:{group_id}->{item_id}")
    return errors


_FINDING_ID_WRAPPERS = " \t\r\n\"'`"
_FINDING_ID_PREFIX = "finding-"


def _finding_key(value: object) -> str:
    """Return the comparable form of a finding id.

    Agent replies vary the surface form of an id far more than the id itself:
    they add quotes or backticks, change case, drop the ``finding-`` prefix, or
    abbreviate the long hash.  Comparing on this normalized key tolerates all of
    those while still being an exact, non-fuzzy match.
    """

    text = str(value).strip(_FINDING_ID_WRAPPERS).casefold()
    if text.startswith(_FINDING_ID_PREFIX):
        text = text[len(_FINDING_ID_PREFIX):]
    return text


def resolve_finding_ids(
    submitted: Sequence[object],
    active_finding_ids: Sequence[object],
) -> list[str]:
    """Resolve submitted finding ids onto the canonical active ids.

    A submitted id resolves when its normalized key matches an active id exactly
    or is an *unambiguous* prefix of exactly one active id.  Agent replies
    routinely abbreviate the long hash-derived ids
    (``finding-b141e6cfb253c123706e`` becomes ``finding-b141e6cf``); treating
    that as an unknown/omitted finding would block otherwise-correct work.

    The resolution never guesses: a key that is empty, a prefix of several
    active ids, or of none, is returned verbatim so the coverage check still
    reports it.  Exact matches win over prefixes, so a suffixed id
    (``finding-<hash>-2``) is never mistaken for its unsuffixed sibling.
    """

    active = [str(item).strip() for item in active_finding_ids if str(item).strip()]
    exact: dict[str, str] = {}
    for value in active:
        exact.setdefault(_finding_key(value), value)
    active_keys = [(value, _finding_key(value)) for value in active]

    resolved: list[str] = []
    for item in submitted:
        raw = str(item).strip()
        if not raw:
            continue
        key = _finding_key(raw)
        canonical: str | None = exact.get(key) if key else None
        if canonical is None and key:
            matches = [value for value, candidate in active_keys if candidate.startswith(key)]
            if len(matches) == 1:
                canonical = matches[0]
        resolved.append(canonical if canonical is not None else raw)
    return resolved


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def normalize_solver_finding_ids(
    payload: Mapping[str, object],
    active_finding_ids: Sequence[object],
) -> dict[str, object]:
    """Return a copy of a Solver reply with every finding id canonicalized.

    The Solver speaks about findings by id in ``finding_batch`` and
    ``finding_resolutions``.  Canonicalizing once, before any policy reads the
    reply, keeps coverage validation, revision scoping, and the persisted reply
    artifacts consistent even when the agent abbreviated an id.
    """

    normalized = dict(payload)
    batch = normalized.get("finding_batch")
    if isinstance(batch, Mapping):
        canonical_batch = dict(batch)
        for key in ("selected_finding_ids", "remaining_finding_ids"):
            values = canonical_batch.get(key)
            if isinstance(values, list):
                canonical_batch[key] = _dedupe(
                    resolve_finding_ids(values, active_finding_ids)
                )
        normalized["finding_batch"] = canonical_batch
    resolutions = normalized.get("finding_resolutions")
    if isinstance(resolutions, list):
        canonical_resolutions: list[object] = []
        for entry in resolutions:
            if isinstance(entry, Mapping) and entry.get("finding_id") is not None:
                resolved = resolve_finding_ids(
                    [entry.get("finding_id")], active_finding_ids
                )
                canonical_resolutions.append(
                    {**entry, "finding_id": resolved[0] if resolved else entry.get("finding_id")}
                )
            else:
                canonical_resolutions.append(entry)
        normalized["finding_resolutions"] = canonical_resolutions
    return normalized


def solver_batch_coverage_error(
    active_finding_ids: Sequence[object],
    payload: Mapping[str, object],
    *,
    expected_batch: tuple[Sequence[object], Sequence[object]] | None = None,
) -> str:
    """Validate the bounded finding batch against the active finding set.

    Returns an empty string when the batch is absent (a full-plan revision) or
    well-formed.  Only well-typed ``finding_batch`` objects are checked so the
    schema validator keeps ownership of malformed shapes.  Submitted ids are
    resolved onto the active ids first, so an abbreviated but unambiguous id
    counts as covered rather than as both missing and unknown.

    ``expected_batch`` is the ``(selected, remaining)`` batch the orchestrator
    dictated for this round.  Because the orchestrator owns the selection (it
    knows severity, owning item and the freeze policy), the reply only has to
    echo it: re-planning the partition is a shape slip the agent is asked to
    fix.  That replaces the old "never defer a P0/P1" rule, which made the task
    unsatisfiable whenever the active blockers exceeded the per-round cap.
    """

    batch = payload.get("finding_batch")
    if not isinstance(batch, Mapping):
        return ""
    selected = batch.get("selected_finding_ids")
    remaining = batch.get("remaining_finding_ids")
    if not isinstance(selected, list) or not isinstance(remaining, list):
        return ""
    selected_ids = set(_dedupe(resolve_finding_ids(selected, active_finding_ids)))
    remaining_ids = set(_dedupe(resolve_finding_ids(remaining, active_finding_ids)))
    active = {str(item).strip() for item in active_finding_ids if str(item).strip()}
    overlap = sorted(selected_ids & remaining_ids)
    if overlap:
        return f"SOLVER_FINDING_BATCH_OVERLAP:{overlap}"
    missing = sorted(active - (selected_ids | remaining_ids))
    unknown = sorted((selected_ids | remaining_ids) - active)
    if missing or unknown:
        return (
            "SOLVER_FINDING_BATCH_COVERAGE_INCOMPLETE:"
            f"missing={missing},unknown={unknown}"
        )
    if expected_batch is not None:
        expected_selected, expected_remaining = expected_batch
        want_selected = set(
            _dedupe(resolve_finding_ids(expected_selected, active_finding_ids))
        )
        want_remaining = set(
            _dedupe(resolve_finding_ids(expected_remaining, active_finding_ids))
        )
        if selected_ids != want_selected or remaining_ids != want_remaining:
            return (
                "SOLVER_BATCH_MISMATCH:"
                f"expected_selected={sorted(want_selected)},"
                f"expected_remaining={len(want_remaining)},"
                f"actual_selected={sorted(selected_ids)}"
            )
    return ""


_RESOLUTION_RESPONSES = frozenset({"absorbed", "rejected"})


def solver_resolution_coverage_error(
    selected_finding_ids: Sequence[object],
    payload: Mapping[str, object],
) -> str:
    """Require a disposition for every dictated batch finding.

    The orchestrator picks the batch, so the reply must say what happened to
    each selected finding: ``absorbed`` (the plan change answers it) or
    ``rejected`` (the Solver pushes back and the Critic settles it later).
    Ids are resolved with :func:`resolve_finding_ids` first, so an
    abbreviated but unambiguous id counts as covered.  An entry with an
    unknown ``response`` value covers nothing — it is reported missing so
    the re-ask can fix the wording instead of silently dropping the finding.
    """

    selected = [
        str(item).strip() for item in selected_finding_ids if str(item).strip()
    ]
    if not selected:
        return ""
    resolutions = payload.get("finding_resolutions")
    covered: set[str] = set()
    if isinstance(resolutions, list):
        for entry in resolutions:
            if not isinstance(entry, Mapping):
                continue
            response = str(entry.get("response") or "").strip().casefold()
            if response not in _RESOLUTION_RESPONSES:
                continue
            resolved = resolve_finding_ids([entry.get("finding_id")], selected)
            covered.update(value for value in resolved if value)
    missing = sorted(set(selected) - covered)
    if missing:
        return f"SOLVER_RESOLUTION_COVERAGE_INCOMPLETE:missing={missing}"
    return ""


def carry_forward_group_docs(
    submitted_docs: Iterable[object],
    current_rows: Iterable[object],
    editable_group_ids: Iterable[str],
    *,
    required_group_ids: Iterable[str] = (),
    review: object = None,
    plan: object = None,
) -> tuple[dict[str, dict[str, object]], str]:
    """Enforce the group-document freeze zone and fold editable submissions.

    ``submitted_docs`` is the reply's ``group_docs`` array (``group_id`` +
    ``markdown``); ``current_rows`` are the authoritative
    :class:`ZhongshuGroupState` rows.  A document for a group outside
    ``editable_group_ids`` must be byte-identical to the authoritative
    markdown (or omitted); any variant is reported as
    ``SOLVER_GROUP_DOC_FROZEN:<sorted group_ids>`` and nothing folds.

    Every group in ``required_group_ids`` (the FORMALIZE groups, or the
    REVISE batch scope) must submit a document; a gap is reported as
    ``SOLVER_GROUP_DOC_MISSING:all`` when nothing required arrived, else
    the sorted missing group ids.  Each editable submission must pass the
    mechanical document checks (nine sections, wording, acceptance
    closure, version arithmetic against the authoritative row) and is
    reported as ``SOLVER_GROUP_DOC_INVALID:<details>`` otherwise.

    An accepted editable submission folds with the title version, the
    hash recomputed here, and the plan-projection source hash, so the
    chain never trusts a worker's claim.  Groups whose submission is only
    an identical echo of the authoritative markdown — and groups with no
    submission — stay absent from the fold; the caller keeps the
    authoritative row.
    """

    from ..zhongshu_doc import (
        ZhongshuRequirementDoc,
        doc_violation_details,
        group_member_item_ids,
    )
    from ..zhongshu_doc_repair import repair_group_doc

    doc_review = _review_for_doc_verify(review, plan)
    rows = {
        str(getattr(row, "group_id", "") or ""): row
        for row in current_rows or ()
        if str(getattr(row, "group_id", "") or "")
    }
    editable = {str(gid).strip() for gid in (editable_group_ids or ()) if str(gid).strip()}
    submitted: dict[str, str] = {}
    for entry in submitted_docs or ():
        if not isinstance(entry, Mapping):
            continue
        group_id = str(entry.get("group_id") or "").strip()
        markdown = entry.get("markdown")
        if not group_id or not isinstance(markdown, str) or not markdown.strip():
            continue
        submitted[group_id] = markdown
    required = list(dict.fromkeys(
        str(gid).strip() for gid in (required_group_ids or ()) if str(gid).strip()
    ))
    missing = [group_id for group_id in required if group_id not in submitted]
    if missing:
        # ``all`` means the reply carried no documents whatsoever; a partial
        # submission names exactly which required groups stayed silent.
        code = "all" if not submitted else ",".join(sorted(missing))
        return {}, f"SOLVER_GROUP_DOC_MISSING:{code}"
    folded: dict[str, dict[str, object]] = {}
    frozen: list[str] = []
    for group_id, markdown in sorted(submitted.items()):
        if group_id not in editable:
            row = rows.get(group_id)
            authoritative = str(getattr(row, "doc_markdown", "") or "") if row else ""
            if markdown != authoritative:
                frozen.append(group_id)
            continue
        row = rows.get(group_id)
        row_version = int(getattr(row, "doc_version", 0) or 0)
        previous_version = row_version if row_version > 0 else None
        repair = repair_group_doc(
            markdown,
            group_id=group_id,
            version=(previous_version or 0) + 1,
            member_item_ids=group_member_item_ids(doc_review, group_id),
        )
        if repair.repairs:
            logger.info(
                "SOLVER_GROUP_DOC_REPAIRED group_id=%s repairs=%s",
                group_id,
                ",".join(repair.repairs),
            )
            markdown = repair.markdown
        violations = doc_violation_details(
            markdown,
            previous_version=previous_version,
            review=doc_review,
            group_id=group_id,
        )
        if violations:
            # Feedback carries the expected/actual diff so a re-ask fixes
            # the exact rows named instead of guessing at the closure rule.
            return {}, "SOLVER_GROUP_DOC_INVALID:" + ";".join(
                violation.render(with_expected=True) for violation in violations[:10]
            )
        document = ZhongshuRequirementDoc.parse(markdown)
        folded[group_id] = {
            "markdown": markdown,
            "doc_version": document.version,
            "doc_hash": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
            "doc_source_hash": _plan_source_hash(plan),
        }
    if frozen:
        return {}, f"SOLVER_GROUP_DOC_FROZEN:{sorted(set(frozen))}"
    return folded, ""


def _plan_source_hash(plan: object) -> str:
    """SHA-256 of the canonical plan projection that produced the document."""

    payload = (
        json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if isinstance(plan, Mapping)
        else ""
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class _PlanItemView:
    """Item-shaped read view over one submitted plan item."""

    def __init__(
        self, item: Mapping[str, object], fallback_group_id: str = ""
    ) -> None:
        self.item_id = str(item.get("item_id") or "")
        # Membership may live on the item itself or only in the plan's
        # ``groups[].item_ids``; both are contract-legal shapes, so the
        # projection accepts either (task-20260928-bc58c1 SOLVER:3 was
        # falsely orphaned when only the group list carried membership).
        self.group_id = (
            str(item.get("group_id") or "").strip() or fallback_group_id
        )
        self.acceptance_signals = tuple(
            str(signal)
            for signal in (item.get("acceptance_signals") or ())
            if str(signal)
        )


class _PlanReviewView:
    """Review-shaped projection of a submitted plan's items.

    The first formalization folds before the plan exists in the review, so
    the document closure (§8 vs acceptance_signals) must read the signals
    from the very plan the reply submits — not from an empty review.
    """

    def __init__(self, plan: Mapping[str, object]) -> None:
        group_of: dict[str, str] = {}
        for group in plan.get("groups") or ():
            if not isinstance(group, Mapping):
                continue
            group_id = str(group.get("group_id") or "").strip()
            if not group_id:
                continue
            for item_id in group.get("item_ids") or ():
                key = str(item_id).strip()
                if key:
                    group_of.setdefault(key, group_id)
        self.task_items = tuple(
            _PlanItemView(
                item, group_of.get(str(item.get("item_id") or "").strip(), "")
            )
            for item in (plan.get("items") or ())
            if isinstance(item, Mapping)
        )


def _review_for_doc_verify(review: object, plan: object) -> object:
    """Review projection for document verification.

    A submitted document must close with the plan being submitted: the
    materialized plan (the reply's formalization or the patched plan after an
    item revision) is the authority on acceptance signals.  Only when no plan
    projection exists does the live review provide the items.
    """

    if isinstance(plan, Mapping) and (plan.get("items") or ()):
        return _PlanReviewView(plan)
    return review


def apply_solver_changes(
    current_plan: Mapping[str, object],
    changes: Sequence[object],
    *,
    editable_item_ids: Iterable[object] | None = None,
) -> tuple[dict[str, object] | None, str]:
    """Return ``(materialized_plan, error)`` for typed plan changes.

    When ``editable_item_ids`` is supplied (scoped revision), item edits
    outside that set are rejected instead of rewriting reviewed tasks the
    batch never asked the Solver to touch.
    """

    plan = copy.deepcopy(dict(current_plan))
    editable = (
        {str(value).strip() for value in editable_item_ids if str(value).strip()}
        if editable_item_ids is not None
        else None
    )
    items = plan.get("items")
    if not isinstance(items, list):
        items = []
        plan["items"] = items
    groups = plan.get("groups")
    if not isinstance(groups, list):
        groups = []
        plan["groups"] = groups

    item_index = {
        str(item.get("item_id")): item
        for item in items
        if isinstance(item, dict) and item.get("item_id")
    }
    group_index = {
        str(group.get("group_id")): group
        for group in groups
        if isinstance(group, dict) and group.get("group_id")
    }

    for change in changes:
        if not isinstance(change, Mapping):
            return None, "SOLVER_CHANGE_NOT_OBJECT"
        op = str(change.get("op") or "")
        if op == "replace_item_fields":
            item_id = str(change.get("item_id") or "")
            item = item_index.get(item_id)
            if item is None:
                return None, f"SOLVER_CHANGE_ITEM_UNKNOWN:{item_id}"
            if editable is not None and item_id not in editable:
                return None, f"SOLVER_SCOPE_ITEM_UNKNOWN:{item_id}"
            fields = change.get("fields")
            if not isinstance(fields, Mapping):
                return None, "SOLVER_CHANGE_FIELDS_NOT_OBJECT"
            item.update(dict(fields))
        elif op == "replace_group_items":
            group_id = str(change.get("group_id") or "")
            group = group_index.get(group_id)
            if group is None:
                return None, f"SOLVER_CHANGE_GROUP_UNKNOWN:{group_id}"
            item_ids = change.get("item_ids")
            if not isinstance(item_ids, list):
                return None, "SOLVER_CHANGE_FIELDS_NOT_OBJECT"
            group["item_ids"] = [str(item_id) for item_id in item_ids]
        elif op == "replace_group_fields":
            group_id = str(change.get("group_id") or "")
            group = group_index.get(group_id)
            if group is None:
                return None, f"SOLVER_CHANGE_GROUP_UNKNOWN:{group_id}"
            fields = change.get("fields")
            if not isinstance(fields, Mapping):
                return None, "SOLVER_CHANGE_FIELDS_NOT_OBJECT"
            group.update(dict(fields))
        elif op == "replace_plan_fields":
            fields = change.get("fields")
            if not isinstance(fields, Mapping):
                return None, "SOLVER_CHANGE_FIELDS_NOT_OBJECT"
            forbidden = sorted(FORBIDDEN_PLAN_FIELDS.intersection(fields))
            if forbidden:
                return None, f"SOLVER_CHANGE_PLAN_FIELD_FORBIDDEN:{forbidden}"
            plan.update(dict(fields))
        else:
            return None, f"SOLVER_CHANGE_OP_UNKNOWN:{op}"
    return plan, ""


def _item_dependencies(item: Mapping[str, object]) -> tuple[str, ...]:
    raw = item.get("dependencies")
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return ()
    return tuple(sorted(str(value).strip() for value in raw if str(value).strip()))


def merge_group_revision_items(
    current_plan: Mapping[str, object],
    *,
    group_id: str,
    patched_items: Sequence[object],
    editable_item_ids: Iterable[object],
) -> tuple[dict[str, object] | None, str]:
    """Merge one group-revision patch onto the reviewed plan (item layer).

    The patch may rewrite only the finding-owning members of ``group_id``
    (the group-internal freeze zone, requirements §5.3): every other member
    and every foreign item must either stay unmentioned or come back as a
    byte-identical echo.  A reword of an unowned member invalidates the
    sibling review surface for nothing (the exact churn
    ``carry_forward_revision_items`` was built to stop), so it is rejected
    as ``SOLVER_ITEM_FROZEN:<item_ids>``.
    """

    items = current_plan.get("items")
    if not isinstance(items, list):
        return None, "SOLVER_PLAN_STRUCTURE_INVALID:PLAN_HAS_NO_ITEMS"
    editable = {str(value).strip() for value in editable_item_ids if str(value).strip()}
    current_index: dict[str, tuple[int, Mapping[str, object]]] = {}
    for index, item in enumerate(items):
        if isinstance(item, Mapping) and item.get("item_id"):
            current_index[str(item.get("item_id"))] = (index, item)
    merged = copy.deepcopy(dict(current_plan))
    merged_items = merged["items"]
    frozen: list[str] = []
    seen: set[str] = set()
    for raw in patched_items or ():
        if not isinstance(raw, Mapping):
            return None, "SOLVER_CHANGE_NOT_OBJECT"
        item_id = str(raw.get("item_id") or "").strip()
        if not item_id:
            return None, "ITEM_PATCH_IDENTITY:missing item_id"
        if item_id in seen:
            return None, f"SOLVER_GROUP_PATCH_DUPLICATE:{item_id}"
        seen.add(item_id)
        located = current_index.get(item_id)
        if located is None:
            frozen.append(item_id)
            continue
        index, current = located
        owner = str(current.get("group_id") or "")
        supplied_owner = str(raw.get("group_id") or "")
        if not owner and group_id and supplied_owner == group_id:
            # The plan keeps membership in ``plan.groups``; its items carry no
            # group_id.  A worker echoing the group it was dispatched for is
            # not drift (task-20260930-395679 lost a 10-minute wave to it), so
            # drop the label instead of rejecting the patch.
            raw = {key: value for key, value in raw.items() if key != "group_id"}
            supplied_owner = ""
        if supplied_owner != owner:
            # Feedback carries expected/actual: a bare ``ITEM_PATCH_IDENTITY``
            # left the re-ask guessing which identity field drifted, and live
            # run task-20260927-616863 burned all four group-revision waves on
            # the same mislabeled group_id (SOLVER:11/13/15/17).
            return None, (
                f"ITEM_PATCH_IDENTITY:{item_id} "
                f"group_id expected={owner or group_id or 'none'} "
                f"actual={supplied_owner or 'none'}"
            )
        in_scope = not group_id or not owner or owner == group_id
        if (
            (not in_scope or item_id not in editable)
            and canonical_hash(_plan_item_view(raw))
            != canonical_hash(_plan_item_view(current))
        ):
            # A full-plan reply may echo frozen members and foreign items
            # byte-identically (the compact transport rule): only real
            # rewrites outside the finding-owning set are refused.
            frozen.append(item_id)
            continue
        merged_items[index] = copy.deepcopy(dict(raw))
    if frozen:
        return None, "SOLVER_ITEM_FROZEN:" + ",".join(sorted(set(frozen)))
    return merged, ""


def _plan_item_view(item: Mapping[str, object]) -> dict[str, object]:
    """Content identity of one plan item for echo comparisons."""

    return {
        key: item.get(key)
        for key in (
            "item_id",
            "group_id",
            "title",
            "objective",
            "dependencies",
            "source_requirement_ids",
            "acceptance_signals",
            "unknowns",
            "risks",
        )
    }


def carry_forward_revision_items(
    new_plan: Mapping[str, object],
    current_plan: Mapping[str, object],
    editable_item_ids: Iterable[object],
) -> dict[str, object]:
    """Carry reviewed items that the revision was not asked to touch.

    A revision is scoped to the findings it selected.  When the Solver
    re-emits the whole graph, even untouched items come back with reworded
    prose, which changes their review hash and forces the Critic to re-review
    work that never changed (and can surface brand-new findings against it).
    Non-editable items are therefore carried verbatim from the reviewed plan so
    their review hash stays stable and a prior approval is preserved.
    """

    merged = copy.deepcopy(dict(new_plan))
    current_items = current_plan.get("items")
    new_items = merged.get("items")
    if not isinstance(current_items, list) or not isinstance(new_items, list):
        return merged

    editable = {str(value).strip() for value in editable_item_ids if str(value).strip()}
    current_index: dict[str, Mapping[str, object]] = {
        str(item.get("item_id")): item
        for item in current_items
        if isinstance(item, Mapping) and item.get("item_id")
    }

    result: list[object] = []
    for item in new_items:
        if not isinstance(item, Mapping):
            result.append(item)
            continue
        item_id = str(item.get("item_id") or "")
        current = current_index.get(item_id)
        if (
            current is None
            or item_id in editable
            or _item_dependencies(current) != _item_dependencies(item)
        ):
            result.append(item)
        else:
            result.append(copy.deepcopy(dict(current)))

    # A scoped revision must not silently drop an untargeted task.
    seen = {
        str(item.get("item_id"))
        for item in result
        if isinstance(item, Mapping) and item.get("item_id")
    }
    for item in current_items:
        if not isinstance(item, Mapping):
            continue
        item_id = str(item.get("item_id") or "")
        if item_id and item_id not in seen and item_id not in editable:
            result.append(copy.deepcopy(dict(item)))

    merged["items"] = result
    return merged


def _editable_scope_error(
    editable_item_ids: Iterable[object],
    current_plan: Mapping[str, object],
) -> str:
    """Reject a scoped revision whose editable ids name no reviewed item.

    A finding owner that failed to resolve to a real plan item used to land in
    the editable set as a free-text fragment (``item-000004.acceptance_signals
    and task_review_ledger``); ``carry_forward_revision_items`` then treated
    the real item as non-editable and silently replaced the solver's fix with
    the reviewed plan (live incident task-20260926-35833d).  Surface the
    mismatch as a retryable reply error instead.
    """

    raw_items = current_plan.get("items") or current_plan.get("candidate_items") or []
    known = {
        str(item.get("item_id") or "").strip()
        for item in raw_items
        if isinstance(item, Mapping) and str(item.get("item_id") or "").strip()
    }
    if not known:
        # No item authority to validate against; carry-forward semantics own
        # this shape (every submitted item is treated as new).
        return ""
    editable = {str(value).strip() for value in editable_item_ids if str(value).strip()}
    unknown = sorted(editable - known)
    if unknown:
        return "SOLVER_SCOPE_ITEM_UNKNOWN:" + ",".join(unknown[:8])
    return ""


def materialize_solver_reply(
    payload: Mapping[str, object],
    current_plan: Mapping[str, object] | None,
    *,
    editable_item_ids: Iterable[object] | None = None,
) -> tuple[dict[str, object] | None, str]:
    """Resolve a Solver reply into the canonical full plan.

    The complete ``plan`` is authoritative whenever it is present: it is the
    graph the agent actually revised, and in practice the ``changes`` array is a
    human-readable changelog (``target``/``fields``/``description``) rather than
    typed operations.  The typed ``changes`` path is used only when no plan is
    supplied (a bounded patch against the orchestrator-owned plan).

    When ``editable_item_ids`` is supplied the reply is treated as a scoped
    revision: only those items may change, and every other item is carried
    forward from ``current_plan`` (see ``carry_forward_revision_items``).
    """

    plan = payload.get("plan")
    changes = payload.get("changes")
    has_plan = isinstance(plan, Mapping)
    has_changes = isinstance(changes, list) and bool(changes)
    if has_plan:
        if editable_item_ids is not None and isinstance(current_plan, Mapping):
            error = _editable_scope_error(editable_item_ids, current_plan)
            if error:
                return None, error
            return carry_forward_revision_items(plan, current_plan, editable_item_ids), ""
        return dict(plan), ""
    if has_changes:
        if not isinstance(current_plan, Mapping):
            return None, "SOLVER_CURRENT_PLAN_MISSING"
        return apply_solver_changes(
            current_plan, changes, editable_item_ids=editable_item_ids
        )
    if isinstance(current_plan, Mapping):
        return copy.deepcopy(dict(current_plan)), ""
    # No plan and no changes on the initial run: preserve the legacy
    # passthrough rather than blocking a reply the schema already vets.
    return None, ""


__all__ = [
    "CHANGE_OPS",
    "FORBIDDEN_PLAN_FIELDS",
    "apply_solver_changes",
    "carry_forward_revision_items",
    "is_retryable_solver_reply_error",
    "materialize_solver_reply",
    "merge_group_revision_items",
    "normalize_solver_finding_ids",
    "resolve_finding_ids",
    "solver_batch_coverage_error",
    "solver_resolution_coverage_error",
    "carry_forward_group_docs",
    "solver_revision_response_error",
    "structural_integrity_errors",
]
