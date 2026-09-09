from __future__ import annotations

from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
import copy
import hashlib
import json
import logging
import inspect

from .concurrency import ConcurrencyAdmission
from .models import normalize_finding_status
from .zhongshu_review import (
    merge_evidence_updates,
    records,
    semantic_fingerprint,
    union_records,
)
from .notifications import build_zhongshu_parallel_notification


logger = logging.getLogger("review_orchestrator_fsm")


# These limits are protocol boundaries, not prompt suggestions.  The prompt
# keeps an Analyst focused; the validator prevents a verbose or malformed
# worker from expanding the Solver search space again.
ANALYST_MAX_EVIDENCE_UPDATES_PER_WORKER = 6
ANALYST_MAX_EVIDENCE_UPDATES_MERGED = 12
ANALYST_MAX_EVIDENCE_REQUESTS_PER_WORKER = 2
ANALYST_MAX_EVIDENCE_REQUESTS = 6
ANALYST_DECISION_RELEVANCE = frozenset(
    {"boundary", "coverage", "dependency", "acceptance", "risk"}
)


def canonical_plan_json(plan: Any) -> str:
    """Serialize a plan deterministically for cross-stage identity checks."""
    value = plan if isinstance(plan, dict) else {}
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_plan_hash(plan: Any) -> str:
    """Return the canonical SHA-256 identity shared by prompt and fan-in."""
    return hashlib.sha256(canonical_plan_json(plan).encode("utf-8")).hexdigest()


def canonicalize_task_graph(plan: dict[str, Any]) -> tuple[dict[str, Any], list[list[str]]]:
    """Normalize harmless exact duplicates and report semantic duplicates.

    This helper never merges two task items. A semantic collision is returned
    to Solver for an explicit decision because silently changing the graph
    after Critic approval would invalidate the review hash.
    """
    canonical = copy.deepcopy(plan)
    items = canonical.get("items") if isinstance(canonical.get("items"), list) else []
    normalized_items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or not item.get("item_id"):
            normalized_items.append(item)
            continue
        item_id = str(item["item_id"])
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        for field in ("source_requirement_ids", "dependencies", "acceptance_signals", "unknowns", "risks"):
            if isinstance(item.get(field), list):
                item[field] = union_records(item[field])
        normalized_items.append(item)
    canonical["items"] = sorted(
        normalized_items,
        key=lambda item: str(item.get("item_id") if isinstance(item, dict) else ""),
    )
    item_by_id = {
        str(item.get("item_id")): item
        for item in canonical["items"]
        if isinstance(item, dict) and item.get("item_id")
    }
    groups = canonical.get("groups") if isinstance(canonical.get("groups"), list) else []
    normalized_groups: list[Any] = []
    for group in groups:
        if not isinstance(group, dict):
            normalized_groups.append(group)
            continue
        group_items = group.get("items") if isinstance(group.get("items"), list) else []
        unique_items: list[dict[str, Any]] = []
        group_seen: set[str] = set()
        for raw_item in group_items:
            if not isinstance(raw_item, dict) or not raw_item.get("item_id"):
                unique_items.append(raw_item)
                continue
            item_id = str(raw_item["item_id"])
            if item_id in group_seen:
                continue
            group_seen.add(item_id)
            unique_items.append(copy.deepcopy(item_by_id.get(item_id, raw_item)))
        group["items"] = sorted(
            unique_items,
            key=lambda item: str(item.get("item_id") if isinstance(item, dict) else ""),
        )
        normalized_groups.append(group)
    canonical["groups"] = normalized_groups

    collisions: dict[str, list[str]] = {}
    for item in canonical["items"]:
        if not isinstance(item, dict):
            continue
        objective = _normalized_task_key(item.get("objective"))
        requirements = ",".join(sorted(_string_list(item.get("source_requirement_ids"))))
        if objective:
            collisions.setdefault(f"{requirements}|{objective}", []).append(str(item.get("item_id")))
    semantic_duplicates = [sorted(ids) for ids in collisions.values() if len(ids) > 1]
    return canonical, sorted(semantic_duplicates)


_EVIDENCE_RANK = {
    "direct_code": 6,
    "runtime_log": 6,
    "measured": 6,
    "protocol": 5,
    "reproducible_test": 5,
    "design_doc": 3,
    "inference": 2,
    "opinion": 1,
}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


_TASK_REQUIREMENT_KINDS = {
    "task",
    "action",
    "deliverable",
    "outcome",
}
_CONSTRAINT_REQUIREMENT_KINDS = {
    "constraint",
    "guardrail",
    "invariant",
    "non_goal",
    "non-goal",
    "prohibition",
}
_CONSTRAINT_MARKERS = (
    "do not ",
    "don't ",
    "must not ",
    "should not ",
    "avoid ",
    "禁止",
    "不得",
    "不要",
    "不应",
    "不能",
    "不可",
    "无需",
    "不需要",
)


def requirement_kind(requirement: Any) -> str:
    """Classify a contract entry as executable work or a preserved constraint.

    ``kind`` is the authoritative field for new contracts.  The fallback is
    intentionally conservative and keeps older contracts (which did not have
    ``kind``) compatible with the current task-coverage check.
    """
    if not isinstance(requirement, dict):
        return "task"
    raw_kind = str(
        requirement.get("kind")
        or requirement.get("requirement_kind")
        or requirement.get("requirement_type")
        or ""
    ).strip().casefold()
    if raw_kind in _CONSTRAINT_REQUIREMENT_KINDS:
        return "constraint"
    if raw_kind in _TASK_REQUIREMENT_KINDS:
        return "task"
    if str(requirement.get("scope") or "").strip().casefold() == "out":
        return "constraint"
    statement = str(requirement.get("statement") or "").strip().casefold()
    if any(marker in statement for marker in _CONSTRAINT_MARKERS):
        return "constraint"
    return "task"


def requirement_requires_task(requirement: Any) -> bool:
    """Return whether a contract entry must be mapped to an executable task."""
    return requirement_kind(requirement) == "task"


class AnalystEvidenceGap(ValueError):
    """Raised when an executable requirement is explicitly unresolved.

    An explicit unknown is a valid planning outcome, but it is not a task
    graph.  The caller must route it to the human gate instead of silently
    dropping the requirement or repeatedly retrying the same merge.
    """

    def __init__(self, missing_requirement_ids: list[str], context: dict[str, Any] | None = None) -> None:
        self.missing_requirement_ids = tuple(sorted(set(missing_requirement_ids)))
        self.context = copy.deepcopy(context or {})
        super().__init__(
            "requirement coverage unresolved: "
            + ",".join(self.missing_requirement_ids)
        )


def _normalized_task_key(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _analyst_worker_body(result: dict[str, Any]) -> dict[str, Any]:
    nested = result.get("payload")
    return nested if isinstance(nested, dict) else result


def _analyst_task_proposals(
    result: dict[str, Any],
    worker_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    body = _analyst_worker_body(result)
    plan = body.get("plan") if isinstance(body.get("plan"), dict) else {}
    raw_proposals = body.get("task_proposals")
    if not isinstance(raw_proposals, list):
        raw_proposals = plan.get("candidate_items", [])
    legacy_requirement_ids = [
        str(item.get("requirement_id"))
        for item in plan.get("requirements", [])
        if isinstance(item, dict) and item.get("requirement_id")
    ]
    proposals: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_proposals):
        if not isinstance(raw, dict):
            raise ValueError(f"task proposal {worker_id}:{index} is not an object")
        item_id = str(raw.get("item_id") or raw.get("task_id") or "").strip()
        task_key = str(raw.get("task_key") or item_id).strip()
        objective = str(raw.get("objective") or "").strip()
        if not task_key and objective:
            task_key = _normalized_task_key(objective)
        if not task_key or not objective:
            raise ValueError(f"task proposal {worker_id}:{index} missing task_key/objective")
        raw_parallelizable = raw.get("parallelizable", True)
        if not isinstance(raw_parallelizable, bool):
            raise ValueError(
                f"task proposal {worker_id}:{index} has malformed parallelizable"
            )
        raw_dependencies = raw.get("dependencies")
        if raw_dependencies is not None and (
            not isinstance(raw_dependencies, list)
            or any(not isinstance(item, str) or not item.strip() for item in raw_dependencies)
        ):
            raise ValueError(f"task proposal {worker_id}:{index} has malformed dependencies")
        source_requirements = _string_list(
            raw.get("source_requirement_ids")
            or raw.get("requirement_ids")
            or raw.get("source_requirements")
        )
        if not source_requirements and not body.get("task_proposals") and len(raw_proposals) == 1:
            # Bridge the old READY_FOR_SOLVER analyst contract.  New workers
            # must emit explicit provenance; a single legacy task can safely
            # cover the complete legacy requirement set.
            source_requirements = legacy_requirement_ids
        proposals.append({
            "task_key": task_key,
            "item_id": item_id or task_key,
            "title": str(raw.get("title") or objective).strip(),
            "problem_addressed": str(
                raw.get("problem_addressed") or raw.get("problem") or objective
            ).strip(),
            "objective": objective,
            "source_requirement_ids": source_requirements,
            "dependencies": _string_list(raw_dependencies),
            "acceptance_signals": _string_list(
                raw.get("acceptance_signals") or raw.get("acceptance_criteria")
            ),
            "evidence_ids": _string_list(
                raw.get("evidence_ids") or raw.get("basis_evidence")
            ),
            "unknowns": records(raw.get("unknowns")),
            "risks": union_records(raw.get("risks"), raw.get("risk_signals")),
            "parallelizable": raw_parallelizable,
            "group_key": str(
                raw.get("group_key")
                or raw.get("candidate_group_id")
                or raw.get("group_id")
                or "group-001"
            ).strip(),
            "source_workers": [worker_id],
        })
    raw_requirements = body.get("requirements")
    if not isinstance(raw_requirements, list):
        raw_requirements = plan.get("requirements", [])
    requirements = [item for item in raw_requirements if isinstance(item, dict)]
    return proposals, requirements


def _unknown_requirement_ids(value: Any) -> list[str]:
    """Extract explicit unresolved requirement ids without stringifying objects."""
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        if isinstance(item, dict):
            item = (
                item.get("requirement_id")
                or item.get("source_requirement_id")
                or item.get("id")
            )
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def validate_zhongshu_requirement_contract(payload: dict[str, Any]) -> str:
    """Validate the single authoritative requirement contract for Zhongshu."""
    if payload.get("action") != "REQUIREMENT_CONTRACT_READY":
        return "ZHONGSHU_REQUIREMENT_CONTRACT_ACTION_INVALID"
    requirements = payload.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        return "ZHONGSHU_REQUIREMENT_CONTRACT_MISSING"
    seen: set[str] = set()
    required_fields = {
        "requirement_id",
        "statement",
        "source",
        "priority",
        "scope",
        "acceptance_signal",
    }
    for index, requirement in enumerate(requirements):
        if not isinstance(requirement, dict):
            return f"ZHONGSHU_REQUIREMENT_CONTRACT_INVALID:{index}"
        missing = sorted(required_fields - set(requirement))
        if missing:
            return f"ZHONGSHU_REQUIREMENT_CONTRACT_FIELDS_MISSING:{index}:{','.join(missing)}"
        requirement_id = str(requirement.get("requirement_id") or "").strip()
        if not requirement_id or requirement_id in seen:
            return f"ZHONGSHU_REQUIREMENT_CONTRACT_ID_INVALID:{index}"
        if not str(requirement.get("statement") or "").strip():
            return f"ZHONGSHU_REQUIREMENT_CONTRACT_STATEMENT_MISSING:{index}"
        if not str(requirement.get("acceptance_signal") or "").strip():
            return f"ZHONGSHU_REQUIREMENT_CONTRACT_ACCEPTANCE_MISSING:{index}"
        if str(requirement.get("priority") or "").lower() not in {"must", "should", "could"}:
            return f"ZHONGSHU_REQUIREMENT_CONTRACT_PRIORITY_INVALID:{index}"
        if str(requirement.get("scope") or "").lower() not in {"in", "out", "conditional"}:
            return f"ZHONGSHU_REQUIREMENT_CONTRACT_SCOPE_INVALID:{index}"
        raw_kind = requirement.get("kind")
        if raw_kind is not None and str(raw_kind).strip().casefold() not in (
            _TASK_REQUIREMENT_KINDS | _CONSTRAINT_REQUIREMENT_KINDS
        ):
            return f"ZHONGSHU_REQUIREMENT_CONTRACT_KIND_INVALID:{index}"
        seen.add(requirement_id)
    return ""


ZHONGSHU_REQUIREMENT_CONTRACT_FIELDS = (
    "requirement_id",
    "statement",
    "source",
    "priority",
    "scope",
    "kind",
    "acceptance_signal",
)


def bind_zhongshu_requirement_contract(
    payload: dict[str, Any],
    canonical_requirements: list[dict[str, Any]] | None,
    *,
    worker_id: str = "",
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Bind immutable requirement data without changing Analyst evidence.

    Requirement IDs are the integrity boundary: they must match exactly and
    cannot be repaired. Once IDs are valid, the canonical requirement objects
    are authoritative, so model paraphrases cannot lose acceptance criteria.
    """
    bound = copy.deepcopy(payload)
    if not canonical_requirements:
        return bound, ()
    requirements = bound.get("requirements")
    if not isinstance(requirements, list):
        return bound, ()

    expected_ids = [
        str(item.get("requirement_id") or "").strip()
        for item in canonical_requirements
        if isinstance(item, dict)
    ]
    actual_ids = [
        str(item.get("requirement_id") or "").strip()
        for item in requirements
        if isinstance(item, dict)
    ]
    if (
        len(expected_ids) != len(canonical_requirements)
        or len(expected_ids) != len(set(expected_ids))
        or len(actual_ids) != len(requirements)
        or len(actual_ids) != len(set(actual_ids))
        or set(actual_ids) != set(expected_ids)
    ):
        raise ValueError(
            "ZHONGSHU_EVIDENCE_PACKET_REQUIREMENT_CONTRACT_ID_MISMATCH:"
            + (worker_id or "unknown-worker")
        )

    expected_by_id = {
        str(item["requirement_id"]).strip(): item
        for item in canonical_requirements
    }
    actual_by_id = {
        str(item["requirement_id"]).strip(): item
        for item in requirements
    }
    changed: dict[str, list[str]] = {}
    for requirement_id, reference in expected_by_id.items():
        candidate = actual_by_id[requirement_id]
        fields = [
            field
            for field in ZHONGSHU_REQUIREMENT_CONTRACT_FIELDS
            if candidate.get(field) != reference.get(field)
        ]
        if fields:
            changed[requirement_id] = fields

    if changed:
        bound["requirements"] = copy.deepcopy(canonical_requirements)
        logger.warning(
            "ZHONGSHU_ANALYST_REQUIREMENT_CONTRACT_REBOUND worker_id=%s "
            "requirement_ids=%s changed_fields=%s",
            worker_id or "unknown-worker",
            ",".join(sorted(changed)),
            json.dumps(changed, ensure_ascii=False, sort_keys=True),
        )
    else:
        bound["requirements"] = [
            copy.deepcopy(expected_by_id[requirement_id])
            for requirement_id in expected_ids
        ]
    return bound, tuple(
        f"{requirement_id}:{','.join(fields)}"
        for requirement_id, fields in sorted(changed.items())
    )


def validate_zhongshu_evidence_packet(
    payload: dict[str, Any],
    canonical_requirements: list[dict[str, Any]] | None = None,
    *,
    max_evidence_updates: int | None = None,
    max_evidence_requests: int | None = None,
    require_decision_relevance: bool = False,
) -> str:
    """Validate an Analyst result that contains evidence, never tasks.

    The explicit empty task fields are intentional: the Analyst role keeps one
    stable result envelope, while task ownership belongs exclusively to Solver.
    """
    action = payload.get("action")
    if action not in {"EVIDENCE_PACKET_READY", "EVIDENCE_SUPPLEMENT_READY"}:
        return "ZHONGSHU_EVIDENCE_PACKET_ACTION_INVALID"
    if payload.get("phase") not in {None, "ZHONGSHU"}:
        return "ZHONGSHU_EVIDENCE_PACKET_PHASE_INVALID"
    requirements = payload.get("requirements")
    if action == "EVIDENCE_PACKET_READY":
        if not isinstance(requirements, list):
            return "ZHONGSHU_EVIDENCE_PACKET_REQUIREMENTS_MISSING"
        for field in ("task_proposals", "candidate_items", "candidate_groups"):
            value = payload.get(field)
            if not isinstance(value, list) or value:
                return f"ZHONGSHU_EVIDENCE_PACKET_TASK_FIELD_NOT_EMPTY:{field}"
        seen: set[str] = set()
        for index, requirement in enumerate(requirements):
            if not isinstance(requirement, dict):
                return f"ZHONGSHU_EVIDENCE_PACKET_REQUIREMENT_INVALID:{index}"
            requirement_id = str(requirement.get("requirement_id") or "").strip()
            if not requirement_id or requirement_id in seen:
                return f"ZHONGSHU_EVIDENCE_PACKET_REQUIREMENT_ID_INVALID:{index}"
            seen.add(requirement_id)
    if action == "EVIDENCE_PACKET_READY" and canonical_requirements is not None:
        expected = {
            str(item.get("requirement_id")): item
            for item in canonical_requirements
            if isinstance(item, dict) and item.get("requirement_id")
        }
        actual = {
            str(item.get("requirement_id")): item
            for item in requirements
            if isinstance(item, dict) and item.get("requirement_id")
        }
        if set(actual) != set(expected):
            return "ZHONGSHU_EVIDENCE_PACKET_REQUIREMENT_CONTRACT_VIOLATION"
        for requirement_id, reference in expected.items():
            candidate = actual[requirement_id]
            for field in ZHONGSHU_REQUIREMENT_CONTRACT_FIELDS[1:]:
                if field in reference and candidate.get(field) != reference.get(field):
                    return (
                        "ZHONGSHU_EVIDENCE_PACKET_REQUIREMENT_CONTRACT_VIOLATION:"
                        + requirement_id
                    )
    known_requirement_ids = {
        str(item.get("requirement_id"))
        for item in (canonical_requirements or [])
        if isinstance(item, dict) and item.get("requirement_id")
    }
    updates = payload.get("evidence_updates")
    if updates is not None and not isinstance(updates, list):
        return "ZHONGSHU_EVIDENCE_PACKET_UPDATES_INVALID"
    if max_evidence_updates is not None and len(updates or []) > max_evidence_updates:
        return (
            "ZHONGSHU_EVIDENCE_PACKET_UPDATES_TOO_MANY:"
            f"{len(updates or [])}>{max_evidence_updates}"
        )
    for index, update in enumerate(updates or []):
        if not isinstance(update, dict):
            return f"ZHONGSHU_EVIDENCE_PACKET_UPDATE_INVALID:{index}"
        if not str(update.get("evidence_id") or "").strip():
            return f"ZHONGSHU_EVIDENCE_PACKET_UPDATE_ID_MISSING:{index}"
        if not (
            str(update.get("source") or "").strip()
            or str(update.get("source_type") or "").strip()
        ):
            return f"ZHONGSHU_EVIDENCE_PACKET_UPDATE_SOURCE_MISSING:{index}"
        if not str(update.get("conclusion") or update.get("statement") or "").strip():
            return f"ZHONGSHU_EVIDENCE_PACKET_UPDATE_CONCLUSION_MISSING:{index}"
        update_requirement_id = str(update.get("requirement_id") or "").strip()
        if canonical_requirements is not None and update_requirement_id and update_requirement_id not in known_requirement_ids:
            return f"ZHONGSHU_EVIDENCE_PACKET_UPDATE_REQUIREMENT_UNKNOWN:{index}"
        if require_decision_relevance:
            relevance = str(update.get("decision_relevance") or "").strip().casefold()
            if relevance not in ANALYST_DECISION_RELEVANCE:
                return f"ZHONGSHU_EVIDENCE_PACKET_UPDATE_RELEVANCE_INVALID:{index}"

    requests = payload.get("evidence_requests")
    if requests is not None and not isinstance(requests, list):
        return "ZHONGSHU_EVIDENCE_PACKET_REQUESTS_INVALID"
    max_requests = (
        ANALYST_MAX_EVIDENCE_REQUESTS
        if max_evidence_requests is None
        else max_evidence_requests
    )
    if len(requests or []) > max_requests:
        return (
            "ZHONGSHU_EVIDENCE_PACKET_REQUESTS_TOO_MANY:"
            f"{len(requests or [])}>{max_requests}"
        )
    for index, request in enumerate(requests or []):
        if not isinstance(request, dict):
            return f"ZHONGSHU_EVIDENCE_REQUEST_INVALID:{index}"
        if not (
            str(request.get("item_id") or "").strip()
            or str(request.get("requirement_id") or "").strip()
        ):
            return f"ZHONGSHU_EVIDENCE_REQUEST_SCOPE_MISSING:{index}"
        request_requirement_id = str(request.get("requirement_id") or "").strip()
        if canonical_requirements is not None and request_requirement_id and request_requirement_id not in known_requirement_ids:
            return f"ZHONGSHU_EVIDENCE_REQUEST_REQUIREMENT_UNKNOWN:{index}"
        if not str(request.get("question") or "").strip():
            return f"ZHONGSHU_EVIDENCE_REQUEST_QUESTION_MISSING:{index}"
        if not str(request.get("reason") or "").strip():
            return f"ZHONGSHU_EVIDENCE_REQUEST_REASON_MISSING:{index}"
    return ""


def merge_analyst_evidence(
    task_id: str,
    revision_id: str,
    worker_results: list[dict[str, Any]],
    canonical_requirements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge Analyst observations into an evidence packet only.

    This function deliberately has no task/group merge path. Any task graph is
    created later by the single Solver, which prevents parallel Analysts from
    multiplying candidate tasks before Critic review.
    """
    if not worker_results:
        raise ValueError("no Analyst results available for evidence merge")
    requirement_by_id = {
        str(item.get("requirement_id")): copy.deepcopy(item)
        for item in (canonical_requirements or [])
        if isinstance(item, dict) and item.get("requirement_id")
    }
    confirmed_facts: list[Any] = []
    constraints: list[Any] = []
    conflicts: list[Any] = []
    unknowns: list[Any] = []
    unknown_requirement_ids: list[str] = []
    unknown_requirements: list[Any] = []
    risks: list[Any] = []
    protected_paths: list[Any] = []
    scope: dict[str, Any] = {}
    project_context: dict[str, Any] = {}
    worker_evidence: dict[str, Any] = {}
    updates: list[dict[str, Any]] = []
    questions_for_solver: list[Any] = []
    evidence_requests: list[Any] = []
    questions_for_user: list[Any] = []
    assumptions: list[Any] = []

    for result in sorted(worker_results, key=lambda value: str(value.get("worker_id", ""))):
        if not isinstance(result, dict):
            raise ValueError("Analyst evidence result must be an object")
        result_revision = str(result.get("revision_id") or "")
        if result_revision and result_revision != revision_id:
            raise ValueError("ZHONGSHU_EVIDENCE_PACKET_REVISION_MISMATCH")
        worker_id = str(result.get("worker_id") or "unknown-worker")
        body, _binding_notes = bind_zhongshu_requirement_contract(
            _analyst_worker_body(result),
            canonical_requirements,
            worker_id=worker_id,
        )
        reason = validate_zhongshu_evidence_packet(
            body,
            canonical_requirements,
            max_evidence_updates=ANALYST_MAX_EVIDENCE_UPDATES_PER_WORKER,
            max_evidence_requests=ANALYST_MAX_EVIDENCE_REQUESTS_PER_WORKER,
            require_decision_relevance=True,
        )
        if reason:
            raise ValueError(reason)
        raw_requirements = body.get("requirements") or []
        if not raw_requirements and canonical_requirements:
            raw_requirements = canonical_requirements
        for requirement in raw_requirements:
            requirement_id = str(requirement.get("requirement_id") or "").strip()
            if not requirement_id:
                continue
            existing = requirement_by_id.get(requirement_id)
            if existing is None:
                requirement_by_id[requirement_id] = copy.deepcopy(requirement)
            else:
                for field in ZHONGSHU_REQUIREMENT_CONTRACT_FIELDS[1:]:
                    if field in existing and field in requirement and existing[field] != requirement[field]:
                        raise ValueError(
                            f"worker requirement contract violation: {requirement_id}:{field}"
                        )
        local_updates = [
            copy.deepcopy(item)
            for item in body.get("evidence_updates") or []
            if isinstance(item, dict)
        ]
        for update in local_updates:
            update.setdefault("worker_id", worker_id)
        updates.extend(local_updates)
        facts = [copy.deepcopy(item) for item in body.get("confirmed_facts") or []]
        facts = [dict(item, worker_id=worker_id) if isinstance(item, dict) else item for item in facts]
        confirmed_facts = union_records(confirmed_facts, facts)
        constraints = union_records(constraints, body.get("constraints"))
        conflicts = union_records(conflicts, body.get("conflicts"))
        unknowns = union_records(unknowns, body.get("unknowns"))
        unknown_requirement_ids = _string_list(
            unknown_requirement_ids + _unknown_requirement_ids(body.get("unknown_requirement_ids"))
        )
        unknown_requirements = union_records(
            unknown_requirements,
            body.get("unknown_requirements"),
        )
        risks = union_records(risks, body.get("risks"))
        assumptions = union_records(assumptions, body.get("assumptions"))
        protected_paths = union_records(protected_paths, body.get("protected_paths"))
        questions_for_solver = union_records(questions_for_solver, body.get("questions_for_solver"))
        evidence_requests = union_records(evidence_requests, body.get("evidence_requests"))
        questions_for_user = union_records(questions_for_user, body.get("questions_for_user"))
        candidate_scope = body.get("scope")
        if isinstance(candidate_scope, dict):
            for key, value in candidate_scope.items():
                if isinstance(value, list):
                    scope[key] = union_records(scope.get(key), value)
                elif key not in scope:
                    scope[key] = copy.deepcopy(value)
        if isinstance(body.get("project_context"), dict) and not project_context:
            project_context = copy.deepcopy(body["project_context"])
        worker_evidence[worker_id] = {
            "evidence_updates": local_updates,
            "confirmed_facts": copy.deepcopy(facts),
            "unknowns": copy.deepcopy(body.get("unknowns") or []),
            "risks": copy.deepcopy(body.get("risks") or []),
            "evidence_requests": copy.deepcopy(body.get("evidence_requests") or []),
            "source": copy.deepcopy(body.get("evidence_sources") or body.get("evidence_packet") or []),
        }

    if not requirement_by_id:
        raise ValueError("Analyst results contain no requirements")
    packet = {
        "plan_id": f"evidence-{task_id}-{revision_id}",
        "plan_revision_id": revision_id,
        "version": 1,
        "phase": "ZHONGSHU",
        "evidence_packet_version": 1,
        "problem_interpretation": "Collect and preserve evidence before task decomposition.",
        "objective": "Provide Solver with a traceable evidence packet; do not create tasks here.",
        "success_definition": "Requirements, facts, constraints, risks, unknowns, and sources are preserved without task proposals.",
        "requirements": [requirement_by_id[key] for key in sorted(requirement_by_id)],
        "goals": [
            str(requirement_by_id[key].get("statement") or "")
            for key in sorted(requirement_by_id)
            if requirement_requires_task(requirement_by_id[key])
        ],
        "non_goals": union_records(constraints),
        "project_context": project_context,
        "confirmed_facts": confirmed_facts,
        "evidence_updates": merge_evidence_updates(updates),
        "evidence_requests": evidence_requests[:ANALYST_MAX_EVIDENCE_REQUESTS],
        "worker_evidence": worker_evidence,
        "protected_paths": protected_paths,
        "conflicts": conflicts,
        "candidate_directions": [],
        "selected_direction": {},
        "alternatives": [],
        "comparison": [],
        "recommendation": {},
        "task_proposals": [],
        "candidate_items": [],
        "candidate_groups": [],
        "dependencies": [],
        "constraints": union_records(constraints),
        "scope": scope or {"in_scope": [], "out_of_scope": [], "protected_paths": []},
        "assumptions": assumptions,
        "unknowns": unknowns,
        "unknown_requirement_ids": unknown_requirement_ids,
        "unknown_requirements": unknown_requirements,
        "risks": risks,
        "questions_for_solver": questions_for_solver,
        "candidate_verification_questions": [],
        "questions_for_user": questions_for_user,
    }
    return {
        "action": "READY_FOR_SOLVER",
        "notification": "Analyst evidence packet is ready for Solver task decomposition.",
        "plan": packet,
        "evidence_packet": copy.deepcopy(packet),
        "analyst_evidence": copy.deepcopy(packet),
        "task_id": task_id,
        "request_id": f"{task_id}:ZHONGSHU_ANALYST:{revision_id}:evidence-fanin",
        "phase": "ZHONGSHU",
        "role": "review-analyst",
        "revision_id": revision_id,
        "worker_ids": sorted({str(item.get("worker_id") or "") for item in worker_results if item.get("worker_id")}),
    }


def _compact_solver_evidence_record(value: Any) -> Any:
    """Keep only fields that can change Solver's task-graph decisions."""
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    fields = (
        "evidence_id", "requirement_id", "item_id", "finding_id",
        "decision_relevance", "source", "source_type", "conclusion",
        "statement", "confidence", "unknowns", "worker_id", "worker_ids",
    )
    compact = {
        key: copy.deepcopy(value[key])
        for key in fields
        if key in value and value[key] not in (None, "", [], {})
    }
    variants = value.get("observed_variants")
    if isinstance(variants, list) and variants:
        compact["observed_variants"] = [
            _compact_solver_evidence_record(item)
            for item in variants
            if isinstance(item, dict)
        ]
    return compact


def _compact_solver_list(values: Any) -> list[Any]:
    return [
        _compact_solver_evidence_record(item)
        for item in records(values)
        if item not in (None, "", [], {})
    ]


def build_solver_evidence_context(packet: dict[str, Any]) -> dict[str, Any]:
    """Build a decision-focused Analyst context for the single Solver.

    The full Analyst packet remains lossless in StateContext for audit and
    recovery. Solver receives a bounded projection: canonical evidence,
    requirement boundaries, and unresolved decision blockers. Open-ended
    questions_for_solver stay in the durable packet because they expand the
    Solver's search space into implementation design.
    """
    if not isinstance(packet, dict):
        return {}

    evidence_updates = merge_evidence_updates(packet.get("evidence_updates"))
    def evidence_key(value: dict[str, Any]) -> tuple[str, str]:
        scope = str(
            value.get("finding_id")
            or value.get("item_id")
            or value.get("requirement_id")
            or ""
        ).strip()
        return scope, str(value.get("evidence_id") or "").strip()

    evidence_keys = {
        evidence_key(item)
        for item in evidence_updates
        if isinstance(item, dict) and item.get("evidence_id")
    }
    facts_by_evidence_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    confirmed_facts: list[Any] = []
    confirmed_fact_ids: list[str] = []
    for fact in records(packet.get("confirmed_facts")):
        if not isinstance(fact, dict):
            confirmed_facts.append(copy.deepcopy(fact))
            continue
        evidence_id = str(fact.get("evidence_id") or "").strip()
        fact_key = evidence_key(fact)
        if evidence_id and fact_key in evidence_keys:
            confirmed_fact_ids.append(evidence_id)
            bucket = facts_by_evidence_key.setdefault(fact_key, [])
            if not any(semantic_fingerprint(item) == semantic_fingerprint(fact) for item in bucket):
                bucket.append(fact)
            continue
        confirmed_facts.append(_compact_solver_evidence_record(fact))

    compact_updates: list[Any] = []
    for item in evidence_updates:
        has_conflicting_variants = bool(
            isinstance(item, dict) and item.get("observed_variants")
        )
        if (
            isinstance(item, dict)
            and str(item.get("decision_relevance") or item.get("relevance") or "").upper()
            in {"LOW", "CONTEXT_ONLY"}
            and not has_conflicting_variants
        ):
            continue
        compact = _compact_solver_evidence_record(item)
        if isinstance(item, dict):
            evidence_id = str(item.get("evidence_id") or "").strip()
            facts = facts_by_evidence_key.get(evidence_key(item)) or []
            fact = facts[0] if facts else None
            if fact:
                if fact.get("statement") and fact.get("statement") != item.get("conclusion"):
                    compact["fact_statement"] = copy.deepcopy(fact["statement"])
                for source_key, target_key in (
                    ("source_type", "fact_source_type"),
                    ("confidence", "fact_confidence"),
                    ("relevance", "fact_relevance"),
                ):
                    if fact.get(source_key) not in (None, "", [], {}):
                        compact[target_key] = copy.deepcopy(fact[source_key])
                if len(facts) > 1:
                    compact["fact_variants"] = [
                        _compact_solver_evidence_record(value)
                        for value in facts
                    ]
        compact_updates.append(compact)

    scope = packet.get("scope")
    scope_boundary = {}
    if isinstance(scope, dict):
        for key in ("in_scope", "out_of_scope", "protected_paths"):
            if key in scope:
                scope_boundary[key] = copy.deepcopy(scope[key])

    return {
        "requirements": [
            {
                key: copy.deepcopy(item[key])
                for key in (
                    "requirement_id", "statement", "source", "priority",
                    "scope", "kind", "acceptance_signal",
                )
                if isinstance(item, dict) and key in item
            }
            for item in packet.get("requirements") or []
            if isinstance(item, dict)
        ],
        "candidate_items": copy.deepcopy(packet.get("candidate_items") or []),
        "candidate_groups": copy.deepcopy(packet.get("candidate_groups") or []),
        "dependencies": copy.deepcopy(packet.get("dependencies") or []),
        "scope": scope_boundary,
        "protected_paths": copy.deepcopy(scope_boundary.get("protected_paths") or []),
        "constraints": _compact_solver_list(packet.get("constraints")),
        "evidence_updates": compact_updates,
        "confirmed_facts": confirmed_facts,
        "confirmed_fact_ids": sorted(set(confirmed_fact_ids)),
        "unknowns": _compact_solver_list(packet.get("unknowns")),
        "risks": _compact_solver_list(packet.get("risks")),
        "conflicts": _compact_solver_list(packet.get("conflicts")),
        "unknown_requirements": _compact_solver_list(packet.get("unknown_requirements")),
        "unknown_requirement_ids": copy.deepcopy(packet.get("unknown_requirement_ids") or []),
        "unknown_resolutions": _compact_solver_list(packet.get("unknown_resolutions")),
        "evidence_requests": _compact_solver_list(
            packet.get("evidence_requests")
        )[:ANALYST_MAX_EVIDENCE_REQUESTS],
        "context_policy": {
            "evidence_selection": "decision_relevant_only",
            "suppressed_questions_for_solver": len(records(packet.get("questions_for_solver"))),
            "deduplicated_confirmed_facts": len(confirmed_fact_ids),
            "raw_worker_evidence_in_prompt": False,
            "evidence_requests_are_scoped": True,
        },
    }


def merge_analyst_outputs(
    task_id: str,
    revision_id: str,
    worker_results: list[dict[str, Any]],
    canonical_requirements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge bounded Analyst task proposals into one canonical task graph."""
    if not worker_results:
        raise ValueError("no Analyst results available for task graph merge")

    requirement_by_id: dict[str, dict[str, Any]] = {
        str(value.get("requirement_id")): copy.deepcopy(value)
        for value in (canonical_requirements or [])
        if isinstance(value, dict) and value.get("requirement_id")
    }
    task_by_key: dict[str, dict[str, Any]] = {}
    task_aliases: dict[tuple[str, str], str] = {}
    project_context: dict[str, Any] = {}
    confirmed_facts: list[dict[str, Any]] = []
    constraints: list[Any] = []
    unknowns: list[Any] = []
    risks: list[Any] = []
    worker_evidence: dict[str, Any] = {}
    protected_paths: list[Any] = []
    conflicts: list[Any] = []
    scope: dict[str, Any] = {}
    explicit_unknown_requirement_ids: set[str] = set()
    explicit_unknown_requirements: list[Any] = []
    for result in sorted(worker_results, key=lambda value: str(value.get("worker_id", ""))):
        if not isinstance(result, dict):
            raise ValueError("Analyst result must be an object")
        result_revision = str(result.get("revision_id") or "")
        if result_revision and result_revision != revision_id:
            continue
        worker_id = str(result.get("worker_id") or "unknown-worker")
        body = _analyst_worker_body(result)
        plan = body.get("plan") if isinstance(body.get("plan"), dict) else {}
        proposals, requirements = _analyst_task_proposals(result, worker_id)
        worker_evidence[worker_id] = {key: union_records(body.get(key), plan.get(key)) for key in ("evidence_packet", "confirmed_facts", "evidence", "evidence_sources")}
        protected_paths = union_records(protected_paths, body.get("protected_paths"), plan.get("protected_paths"))
        if isinstance(plan.get("project_context"), dict) and not project_context:
            project_context = dict(plan["project_context"])
        candidate_scope = body.get("scope") or plan.get("scope")
        if isinstance(candidate_scope, dict):
            for scope_key, scope_value in candidate_scope.items():
                if isinstance(scope_value, list):
                    scope[scope_key] = union_records(scope.get(scope_key), scope_value)
                elif scope_key not in scope:
                    scope[scope_key] = copy.deepcopy(scope_value)
        for value in requirements:
            requirement_id = str(value.get("requirement_id") or "").strip()
            statement = str(value.get("statement") or "").strip()
            if not requirement_id or not statement:
                continue
            existing = requirement_by_id.get(requirement_id)
            if canonical_requirements is not None:
                contract_fields = (
                    "statement",
                    "source",
                    "priority",
                    "scope",
                    "kind",
                    "acceptance_signal",
                )
                mismatches = [
                    field
                    for field in contract_fields
                    if field in (existing or {}) or field in value
                    if (existing or {}).get(field) != value.get(field)
                ]
                if existing is None or mismatches:
                    detail = ",".join(mismatches) or "missing_requirement"
                    raise ValueError(
                        f"worker requirement contract violation: {requirement_id}:{detail}"
                    )
            else:
                if existing is not None and _normalized_task_key(existing.get("statement")) != _normalized_task_key(statement):
                    raise ValueError(f"conflicting requirement definition: {requirement_id}")
                if existing is None:
                    requirement_by_id[requirement_id] = dict(value)
        constraints.extend(union_records(body.get("constraints"), plan.get("constraints")))
        unknowns.extend(union_records(body.get("unknowns"), plan.get("unknowns")))
        raw_unknown_requirements = union_records(body.get("unknown_requirements"), plan.get("unknown_requirements"), body.get("unknown_requirement_ids"), plan.get("unknown_requirement_ids"))
        explicit_unknown_requirement_ids.update(
            _unknown_requirement_ids(raw_unknown_requirements)
        )
        if isinstance(raw_unknown_requirements, list):
            for unknown in raw_unknown_requirements:
                if isinstance(unknown, dict) and unknown not in explicit_unknown_requirements:
                    explicit_unknown_requirements.append(copy.deepcopy(unknown))
        risks.extend(union_records(body.get("risks"), plan.get("risks"), plan.get("risk_signals")))
        for conflict in body.get("conflicts") or plan.get("conflicts") or []:
            if conflict not in conflicts:
                conflicts.append(conflict)
        for fact in union_records(body.get("confirmed_facts"), plan.get("confirmed_facts")):
            if isinstance(fact, dict) and fact not in confirmed_facts:
                confirmed_facts.append({**copy.deepcopy(fact), "worker_id": worker_id})
        for proposal in proposals:
            key = _normalized_task_key(proposal["task_key"])
            existing = task_by_key.get(key)
            if existing is None:
                task_aliases[(worker_id, proposal["item_id"])] = proposal["item_id"]
                task_aliases[(worker_id, proposal["task_key"])] = proposal["item_id"]
                task_by_key[key] = proposal
                continue
            if _normalized_task_key(existing["objective"]) != _normalized_task_key(proposal["objective"]):
                variant_key = f"{key}::{worker_id}"
                original_item_id = proposal["item_id"]
                proposal["item_id"] = f"{proposal['item_id']}::{worker_id}"
                task_aliases[(worker_id, original_item_id)] = proposal["item_id"]
                task_aliases[(worker_id, proposal["task_key"])] = proposal["item_id"]
                conflicts.append({"kind": "candidate_definition", "task_key": key, "candidate_ids": [existing["item_id"], proposal["item_id"]], "owner_role": "review-solver"})
                task_by_key[variant_key] = proposal
                continue
            task_aliases[(worker_id, proposal["item_id"])] = existing["item_id"]
            task_aliases[(worker_id, proposal["task_key"])] = existing["item_id"]
            for field_name in (
                "source_requirement_ids",
                "dependencies",
                "acceptance_signals",
                "evidence_ids",
                "unknowns",
                "risks",
                "source_workers",
            ):
                merged = existing[field_name] + [
                    item for item in proposal[field_name] if item not in existing[field_name]
                ]
                existing[field_name] = merged
            existing["parallelizable"] = existing["parallelizable"] and proposal["parallelizable"]

    if not requirement_by_id:
        raise ValueError("Analyst results contain no requirements")
    must_ids = {
        requirement_id
        for requirement_id, requirement in requirement_by_id.items()
        if str(requirement.get("priority") or "").lower() == "must"
        and requirement_requires_task(requirement)
    }
    covered_ids = {
        requirement_id
        for task in task_by_key.values()
        for requirement_id in task["source_requirement_ids"]
    }
    unknown_requirement_refs = sorted(covered_ids - set(requirement_by_id))
    if unknown_requirement_refs:
        raise ValueError(
            "task references unknown requirements: " + ",".join(unknown_requirement_refs)
        )
    constraint_statements = [
        str(requirement.get("statement") or "").strip()
        for requirement in requirement_by_id.values()
        if not requirement_requires_task(requirement)
        and str(requirement.get("statement") or "").strip()
    ]
    constraints.extend(constraint_statements)
    missing = sorted(must_ids - covered_ids)
    if missing:
        unresolved = sorted(set(missing) & explicit_unknown_requirement_ids)
        if unresolved:
            raise AnalystEvidenceGap(missing, {"requirements": list(requirement_by_id.values()), "task_proposals": list(task_by_key.values()), "worker_results": worker_results})
        raise ValueError("requirement coverage missing: " + ",".join(missing))
    if not task_by_key:
        raise AnalystEvidenceGap(list(requirement_by_id), {"requirements": list(requirement_by_id.values()), "worker_results": worker_results})

    for task in task_by_key.values():
        task["dependencies"] = sorted({task_aliases.get((worker, dependency), dependency) for worker in task["source_workers"] for dependency in task["dependencies"]})
    tasks = []
    task_ids = {str(task["item_id"]) for task in task_by_key.values()}
    unknown_dependencies = sorted({
        dependency
        for task in task_by_key.values()
        for dependency in task["dependencies"]
        if dependency not in task_ids
    })
    if unknown_dependencies:
        raise ValueError(
            "task references unknown dependencies: " + ",".join(unknown_dependencies)
        )
    groups: dict[str, list[dict[str, Any]]] = {}
    for task in sorted(task_by_key.values(), key=lambda value: str(value["item_id"])):
        item = {
            "item_id": task["item_id"],
            "title": task["title"],
            "problem_addressed": task["problem_addressed"],
            "objective": task["objective"],
            "basis_evidence": sorted(task["evidence_ids"]),
            "why_needed": task["problem_addressed"],
            "dependencies": sorted(task["dependencies"]),
            "acceptance_signals": task["acceptance_signals"] or [
                f"Task {task['item_id']} has an observable completion result"
            ],
            "risk_signals": copy.deepcopy(task["risks"]),
            "unknowns": copy.deepcopy(task["unknowns"]),
            "source_requirement_ids": sorted(task["source_requirement_ids"]),
            "source_workers": sorted(task["source_workers"]),
            "parallelizable": task["parallelizable"],
        }
        tasks.append(item)
        groups.setdefault(task["group_key"] or "group-001", []).append(item)

    candidate_groups = [
        {
            "candidate_group_id": group_id,
            "title": group_id,
            "objective": "Tasks sharing a planning boundary",
            "reason": "Tasks were assigned to the same discovery group",
            "related_items": [item["item_id"] for item in items],
            "basis_evidence": sorted({evidence for item in items for evidence in item["basis_evidence"]}),
            "dependencies": [],
            "suggested_order": index,
        }
        for index, (group_id, items) in enumerate(sorted(groups.items()), start=1)
    ]
    unique_constraints = union_records(constraints)
    unique_unknowns = union_records(unknowns)
    unique_risks = union_records(risks)
    plan = {
        "plan_id": f"plan-{task_id}-{revision_id}",
        "plan_revision_id": revision_id,
        "version": 1,
        "task_graph_version": 1,
        "phase": "ZHONGSHU",
        "problem_interpretation": "Decompose the request into independently actionable tasks.",
        "objective": "Produce one complete requirement-to-task graph for Menxia.",
        "success_definition": "Every executable must requirement is covered by an observable task with explicit dependencies and acceptance signals; constraints remain preserved separately.",
        "requirements": [requirement_by_id[key] for key in sorted(requirement_by_id)],
        "goals": [requirement_by_id[key]["statement"] for key in sorted(requirement_by_id)],
        "non_goals": unique_constraints,
        "project_context": project_context,
        "confirmed_facts": confirmed_facts,
        "worker_evidence": worker_evidence,
        "protected_paths": protected_paths,
        "conflicts": conflicts,
        "candidate_directions": [],
        "selected_direction": {},
        "alternatives": [],
        "comparison": [],
        "recommendation": {},
        "candidate_items": tasks,
        "candidate_groups": candidate_groups,
        "dependencies": [
            {"from": item["item_id"], "to": dependency}
            for item in tasks
            for dependency in item["dependencies"]
        ],
        "constraints": unique_constraints,
        "scope": scope or {"in_scope": [], "out_of_scope": [], "protected_paths": []},
        "assumptions": [],
        "unknowns": unique_unknowns,
        "unknown_requirement_ids": sorted(explicit_unknown_requirement_ids),
        "unknown_requirements": explicit_unknown_requirements,
        "risks": unique_risks,
        "questions_for_solver": [],
        "candidate_verification_questions": [
            f"Can task {item['item_id']} be verified by its acceptance signals?"
            for item in tasks
        ],
        "questions_for_user": [],
    }
    return {
        "action": "READY_FOR_SOLVER",
        "notification": "Analyst task graph is ready for Solver.",
        "plan": plan,
        "task_id": task_id,
        "request_id": f"{task_id}:ZHONGSHU_ANALYST:{revision_id}:fanin",
        "phase": "ZHONGSHU",
        "role": "review-analyst",
        "revision_id": revision_id,
        "worker_ids": sorted({str(item.get("worker_id") or "") for item in worker_results if item.get("worker_id")}),
    }


@dataclass(frozen=True)
class CriticFinding:
    finding_id: str
    category: str
    target: str
    claim: str
    decision: str
    severity: str = "P2"
    evidence_ids: tuple[str, ...] = ()
    evidence_strength: str = "inference"
    confidence: float = 0.0
    worker_id: str = ""
    worker_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any], worker_id: str = "") -> "CriticFinding":
        evidence = value.get("evidence_ids") or value.get("basis_evidence") or []
        if not isinstance(evidence, list):
            evidence = [str(evidence)]
        source_worker_id = worker_id or str(value.get("worker_id") or "")
        raw_worker_ids = value.get("worker_ids") or []
        if isinstance(raw_worker_ids, str):
            raw_worker_ids = [raw_worker_ids]
        worker_ids = tuple(str(item) for item in raw_worker_ids if item)
        if source_worker_id and source_worker_id not in worker_ids:
            worker_ids = (source_worker_id,) + worker_ids
        return cls(
            finding_id=str(value.get("finding_id") or ""),
            category=str(value.get("category") or "general"),
            target=str(
                value.get("target")
                or value.get("item_id")
                or value.get("requirement_id")
                or value.get("field")
                or "global"
            ),
            claim=str(value.get("claim") or value.get("description") or ""),
            decision=str(
                value.get("decision")
                or value.get("status")
                or value.get("next_action")
                or ""
            ).upper(),
            severity=str(value.get("severity") or "P2").upper(),
            evidence_ids=tuple(str(item) for item in evidence if item),
            evidence_strength=str(
                value.get("evidence_strength") or "inference"
            ).lower(),
            confidence=float(value.get("confidence") or 0.0),
            worker_id=source_worker_id,
            worker_ids=worker_ids,
        )

    @property
    def conflict_key(self) -> tuple[str, str]:
        return self.category.lower(), self.target

    @property
    def evidence_rank(self) -> tuple[int, float, int]:
        return (
            _EVIDENCE_RANK.get(self.evidence_strength, 0),
            self.confidence,
            len(self.evidence_ids),
        )


def critic_semantic_fingerprint(result: dict[str, Any]) -> str:
    """Return the shared semantic review fingerprint used by quorum logic."""
    return semantic_fingerprint(result)


@dataclass(frozen=True)
class CriticConflict:
    conflict_id: str
    key: tuple[str, str]
    findings: tuple[CriticFinding, ...]
    resolution: str = ""
    status: str = "OPEN"


@dataclass(frozen=True)
class CriticAggregate:
    plan_revision_id: str
    plan_hash: str
    findings: tuple[CriticFinding, ...]
    conflicts: tuple[CriticConflict, ...]
    resolved_conflicts: tuple[CriticConflict, ...]
    unresolved_conflicts: tuple[CriticConflict, ...]
    action: str
    decision_basis: tuple[str, ...] = field(default_factory=tuple)
    report: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        if self.report is not None:
            return copy.deepcopy(self.report)
        return {
            "plan_revision_id": self.plan_revision_id,
            "plan_hash": self.plan_hash,
            "reviewed_plan_hash": self.plan_hash,
            "findings": [
                {
                    "finding_id": finding.finding_id,
                    "category": finding.category,
                    "target": finding.target,
                    "claim": finding.claim,
                    "decision": finding.decision,
                    "status": normalize_finding_status(decision=finding.decision),
                    "severity": finding.severity,
                    "evidence_ids": list(finding.evidence_ids),
                    "evidence_strength": finding.evidence_strength,
                    "confidence": finding.confidence,
                    "worker_id": finding.worker_id,
                    "worker_ids": list(finding.worker_ids),
                }
                for finding in self.findings
            ],
            "conflicts": [
                {
                    "conflict_id": conflict.conflict_id,
                    "category": conflict.key[0],
                    "target": conflict.key[1],
                    "status": conflict.status,
                    "resolution": conflict.resolution,
                    "finding_ids": [item.finding_id for item in conflict.findings],
                }
                for conflict in self.conflicts
            ],
            "resolved_conflicts": [item.conflict_id for item in self.resolved_conflicts],
            "unresolved_conflicts": [item.conflict_id for item in self.unresolved_conflicts],
            "action": self.action,
            "decision_basis": list(self.decision_basis),
        }


@dataclass(frozen=True)
class ZhongshuWorkerSpec:
    worker_id: str
    phase: str
    prompt: str


@dataclass(frozen=True)
class ZhongshuFanoutResult:
    revision_id: str
    completed: tuple[dict[str, Any], ...]
    failed: tuple[dict[str, Any], ...]
    rejected: tuple[dict[str, Any], ...]


class ZhongshuFanoutCoordinator:
    """Run bounded Analyst/Critic workers and release every lease exactly once."""

    def __init__(
        self,
        admission: ConcurrencyAdmission,
        max_workers: int = 3,
        max_attempts: int = 3,
        notify: Any | None = None,
        notification_context: Any | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.admission = admission
        self.max_workers = max_workers
        self.max_attempts = max_attempts
        self.notify = notify
        self.notification_context = notification_context

    def _notify_status(
        self,
        event_name: str,
        task_id: str,
        revision_id: str,
        phase: str,
        workers: list[dict[str, Any]],
        **summary: Any,
    ) -> None:
        if not self.notify:
            return
        payload = {
            "phase": phase,
            "revision_id": revision_id,
            "workers": workers,
            **summary,
        }
        text = build_zhongshu_parallel_notification(
            self.notification_context or {},
            event_name,
            payload,
        )
        self.notify(text, "gate")

    @staticmethod
    def _call_worker(execute: Any, spec: ZhongshuWorkerSpec, attempt: int) -> Any:
        try:
            parameters = inspect.signature(execute).parameters
            accepts_attempt = len(parameters) >= 2
        except (TypeError, ValueError):
            accepts_attempt = True
        return execute(spec, attempt) if accepts_attempt else execute(spec)

    def run(
        self,
        task_id: str,
        revision_id: str,
        specs: list[ZhongshuWorkerSpec],
        execute: Any,
    ) -> ZhongshuFanoutResult:
        if not specs:
            raise ValueError("at least one worker spec is required")
        if len(specs) > self.max_workers:
            raise ValueError("worker specs exceed configured max_workers")
        leases: dict[str, Any] = {}
        rejected: list[dict[str, Any]] = []
        for spec in specs:
            lease = self.admission.try_acquire(
                task_id,
                spec.phase,
                spec.worker_id,
                revision_id,
            )
            if lease is None:
                rejected.append(
                    {
                        "worker_id": spec.worker_id,
                        "reason": "CONCURRENCY_LIMIT_REACHED",
                    }
                )
                continue
            leases[spec.worker_id] = lease

        completed: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        logger.info(
            "ZHONGSHU_FANOUT_STARTED task_id=%s revision_id=%s requested=%s "
            "admitted=%s rejected=%s",
            task_id,
            revision_id,
            len(specs),
            len(leases),
            len(rejected),
        )
        self._notify_status(
            "ZHONGSHU_FANOUT_STARTED",
            task_id,
            revision_id,
            specs[0].phase,
            [
                {
                    "worker_id": spec.worker_id,
                    "status": "RUNNING" if spec.worker_id in leases else "REJECTED",
                    "attempt": 1 if spec.worker_id in leases else None,
                    "max_attempts": self.max_attempts,
                    "error": (
                        "CONCURRENCY_LIMIT_REACHED"
                        if spec.worker_id not in leases
                        else ""
                    ),
                }
                for spec in specs
            ],
            rejected=rejected,
        )
        if leases:
            with ThreadPoolExecutor(max_workers=len(leases)) as pool:
                futures = {
                    pool.submit(self._run_with_retries, task_id, revision_id, spec, execute, leases[spec.worker_id]): spec
                    for spec in specs
                    if spec.worker_id in leases
                }
                for future in as_completed(futures):
                    spec = futures[future]
                    try:
                        value = future.result()
                        if value["status"] == "completed":
                            completed.append(value["result"])
                        else:
                            failed.append(value["failure"])
                    finally:
                        self.admission.release(leases[spec.worker_id].lease_id)
        output = ZhongshuFanoutResult(
            revision_id=revision_id,
            completed=tuple(sorted(completed, key=lambda item: str(item.get("worker_id", "")))),
            failed=tuple(sorted(failed, key=lambda item: str(item.get("worker_id", "")))),
            rejected=tuple(sorted(rejected, key=lambda item: str(item.get("worker_id", "")))),
        )
        logger.info(
            "ZHONGSHU_FANOUT_COMPLETED task_id=%s revision_id=%s completed=%s "
            "failed=%s rejected=%s",
            task_id,
            revision_id,
            len(output.completed),
            len(output.failed),
            len(output.rejected),
        )
        self._notify_status(
            "ZHONGSHU_FANIN_COMPLETED",
            task_id,
            revision_id,
            specs[0].phase,
            [
                {
                    "worker_id": item["worker_id"],
                    "status": "COMPLETED",
                    "attempt": item.get("attempt"),
                    "max_attempts": self.max_attempts,
                }
                for item in output.completed
            ]
            + [
                {
                    "worker_id": item["worker_id"],
                    "status": "FAILED",
                    "attempt": item.get("attempts"),
                    "max_attempts": self.max_attempts,
                    "error": item.get("error"),
                }
                for item in output.failed
            ]
            + [
                {
                    "worker_id": item["worker_id"],
                    "status": "REJECTED",
                    "error": item.get("reason"),
                }
                for item in output.rejected
            ],
            completed=output.completed,
            failed=output.failed,
            rejected=output.rejected,
        )
        return output

    def _run_with_retries(
        self,
        task_id: str,
        revision_id: str,
        spec: ZhongshuWorkerSpec,
        execute: Any,
        lease: Any,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            logger.info(
                "ZHONGSHU_WORKER_ATTEMPT task_id=%s phase=%s worker_id=%s "
                "revision_id=%s attempt=%s max_attempts=%s lease_id=%s",
                task_id,
                spec.phase,
                spec.worker_id,
                revision_id,
                attempt,
                self.max_attempts,
                lease.lease_id,
            )
            try:
                value = self._call_worker(execute, spec, attempt)
                if not isinstance(value, dict):
                    raise TypeError("worker result must be a dict")
                result = dict(value)
                reported_worker_id = str(result.get("worker_id") or "")
                if reported_worker_id and reported_worker_id != spec.worker_id:
                    raise ValueError("worker result worker_id mismatch")
                result.setdefault("worker_id", spec.worker_id)
                result.setdefault("phase", spec.phase)
                result.setdefault("revision_id", revision_id)
                result["attempt"] = attempt
                return {"status": "completed", "result": result}
            except Exception as error:
                last_error = error
                logger.warning(
                    "ZHONGSHU_WORKER_FAILED task_id=%s phase=%s worker_id=%s "
                    "revision_id=%s attempt=%s max_attempts=%s error_type=%s error=%s",
                    task_id,
                    spec.phase,
                    spec.worker_id,
                    revision_id,
                    attempt,
                    self.max_attempts,
                    type(error).__name__,
                    str(error)[:300],
                )
                if attempt < self.max_attempts:
                    logger.info(
                        "ZHONGSHU_WORKER_RETRY_SCHEDULED task_id=%s phase=%s "
                        "worker_id=%s revision_id=%s next_attempt=%s",
                        task_id,
                        spec.phase,
                        spec.worker_id,
                        revision_id,
                        attempt + 1,
                    )
        return {
            "status": "failed",
            "failure": {
                "worker_id": spec.worker_id,
                "phase": spec.phase,
                "revision_id": revision_id,
                "attempts": self.max_attempts,
                "error_type": type(last_error).__name__ if last_error else "UnknownError",
                "error": str(last_error)[:500] if last_error else "worker failed",
            },
        }


class CriticConflictResolver:
    """Deterministic first-pass resolver for three parallel Critic outputs."""


    def aggregate(
        self,
        plan_revision_id: str,
        plan_hash: str,
        worker_results: list[dict[str, Any]],
        *,
        previous: dict[str, Any] | None = None,
        quorum: int = 2,
        expected_workers: set[str] | None = None,
    ) -> CriticAggregate:
        from .zhongshu_review import aggregate_reviews
        report = aggregate_reviews(
            plan_revision_id, plan_hash, worker_results,
            previous=previous, quorum=quorum, expected_workers=expected_workers,
        )
        conflicts = tuple(CriticConflict(
            conflict_id=f"dispute:{item['finding_id']}", key=("finding", item["finding_id"]),
            findings=tuple(CriticFinding.from_dict({**value, "finding_id": item["finding_id"]}) for value in item["observations"]),
        ) for item in report["conflicts"])
        return CriticAggregate(
            plan_revision_id=plan_revision_id, plan_hash=plan_hash,
            findings=tuple(CriticFinding.from_dict(item) for item in report["findings"]),
            conflicts=conflicts, resolved_conflicts=(), unresolved_conflicts=conflicts,
            action=report["action"], decision_basis=tuple(report["decision_basis"]),
            report=report,
        )
