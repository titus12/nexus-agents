"""Lossless task provenance and evidence supplementation for Zhongshu."""
from __future__ import annotations

import copy
from typing import Any

from .zhongshu_review import merge_evidence_updates, records, union_records


def candidate_sources(item: dict[str, Any], candidates: dict[str, Any]) -> list[str]:
    supplied = item.get("source_candidate_ids")
    if supplied is not None:
        return supplied if isinstance(supplied, list) and all(isinstance(value, str) for value in supplied) else []
    item_id = str(item.get("item_id") or "")
    return [item_id] if item_id in candidates else []


def preserve_task_context(plan: dict[str, Any], analyst: dict[str, Any]) -> None:
    """Materialize inherited context, not model-invented defaults or resolutions."""
    for field in ("constraints", "protected_paths", "confirmed_facts", "unknowns", "risks", "evidence_updates", "unknown_requirements", "unknown_requirement_ids", "unknown_resolutions", "evidence_packet"):
        if field == "evidence_updates":
            plan[field] = merge_evidence_updates(analyst.get(field), plan.get(field))
        else:
            plan[field] = union_records(analyst.get(field), plan.get(field))
    if isinstance(analyst.get("worker_evidence"), dict):
        plan["worker_evidence"] = copy.deepcopy(analyst["worker_evidence"])
    source_scope = analyst.get("scope")
    if isinstance(source_scope, dict):
        scope = plan.setdefault("scope", {})
        if isinstance(scope, dict):
            for key, value in source_scope.items():
                if isinstance(value, list):
                    scope[key] = union_records(value, scope.get(key))
                elif key not in scope:
                    scope[key] = copy.deepcopy(value)
    candidates = {str(item["item_id"]): item for item in analyst.get("candidate_items", []) if isinstance(item, dict) and item.get("item_id")}
    for item in plan.get("items") or []:
        if not isinstance(item, dict):
            continue
        for source_id in candidate_sources(item, candidates):
            source = candidates.get(source_id)
            if not source:
                continue
            for field in ("unknowns", "risks", "basis_evidence", "evidence_updates"):
                original = source.get("risk_signals") if field == "risks" else source.get(field)
                if field == "evidence_updates":
                    item[field] = merge_evidence_updates(original, item.get(field))
                else:
                    item[field] = union_records(original, item.get(field))


def task_provenance_error(plan: dict[str, Any], analyst: dict[str, Any]) -> str:
    candidates = {str(item["item_id"]): item for item in analyst.get("candidate_items", []) if isinstance(item, dict) and item.get("item_id")}
    mapped: dict[str, list[dict[str, Any]]] = {key: [] for key in candidates}
    for item in plan.get("items") or []:
        if not isinstance(item, dict):
            continue
        sources = candidate_sources(item, candidates)
        if candidates and not sources:
            return f"SOLVER_CANDIDATE_MAPPING_MISSING:{item.get('item_id', '')}"
        if item.get("source_candidate_ids") is not None and not sources:
            return "SOLVER_CANDIDATE_MAPPING_INVALID"
        for source in sources:
            if source not in candidates:
                return f"SOLVER_CANDIDATE_MAPPING_UNKNOWN:{source}"
            mapped[source].append(item)
    for source_id, targets in mapped.items():
        if not targets:
            return f"SOLVER_CANDIDATE_ITEMS_INCOMPLETE:{source_id}"
        expected = set(candidates[source_id].get("source_requirement_ids") or [])
        covered = {value for item in targets for value in item.get("source_requirement_ids", [])}
        if not expected.issubset(covered):
            return f"SOLVER_CANDIDATE_REQUIREMENTS_LOST:{source_id}"
    return ""


def blocking_unknowns(plan: dict[str, Any]) -> list[Any]:
    resolved = {
        str(item.get("unknown_id") or "")
        for item in plan.get("unknown_resolutions") or []
        if isinstance(item, dict) and str(item.get("status") or "").upper() == "RESOLVED"
        and item.get("evidence_ids") and item.get("response")
    }
    found = []
    for source in [plan] + [item for item in plan.get("items", []) if isinstance(item, dict)]:
        for item in records(source.get("unknowns")) + records(source.get("unknown_requirements")):
            if isinstance(item, dict) and item.get("blocking") is True:
                unknown_id = str(item.get("unknown_id") or "")
                if unknown_id and unknown_id in resolved:
                    continue
                if str(item.get("status") or "").upper() != "RESOLVED" or not item.get("evidence_ids"):
                    found.append(copy.deepcopy(item))
    return found


def supplement_plan(analyst: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    plan = copy.deepcopy(analyst)
    plan["evidence_updates"] = merge_evidence_updates(plan.get("evidence_updates"))
    items = {str(item["item_id"]): item for item in plan.get("candidate_items", []) if isinstance(item, dict) and item.get("item_id")}
    for item in items.values():
        item["evidence_updates"] = merge_evidence_updates(item.get("evidence_updates"))
    for result in results:
        for field in (
            "confirmed_facts",
            "unknowns",
            "risks",
            "constraints",
            "unknown_resolutions",
            "evidence_requests",
        ):
            plan[field] = union_records(plan.get(field), result.get(field))
        for update in result.get("evidence_updates") or []:
            if not isinstance(update, dict):
                raise ValueError("ZHONGSHU_EVIDENCE_UPDATE_INVALID")
            if not update.get("finding_id") and not update.get("item_id") and not update.get("requirement_id"):
                raise ValueError("ZHONGSHU_EVIDENCE_UPDATE_TARGET_MISSING")
            enriched = {**copy.deepcopy(update), "worker_id": result["worker_id"]}
            plan["evidence_updates"] = merge_evidence_updates(plan.get("evidence_updates"), [enriched])
            target = items.get(str(update.get("item_id") or ""))
            if target is not None:
                target["evidence_updates"] = merge_evidence_updates(target.get("evidence_updates"), [enriched])
    return plan
