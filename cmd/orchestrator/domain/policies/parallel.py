"""Typed fan-in policies used by the runtime node boundary.

The functions here are deliberately pure.  They are the only domain-facing
entry point for combining parallel Analyst/Critic replies; the runtime never
merges worker dictionaries itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ...zhongshu_parallel import (
    CriticConflictResolver,
    merge_analyst_evidence,
    merge_analyst_outputs,
)


def aggregate_zhongshu_workers(
    task_id: str,
    state: str,
    revision_id: str,
    plan_hash: str,
    worker_results: Sequence[Mapping[str, object]],
    canonical_requirements: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, Any]:
    """Return one canonical decision from a bounded Zhongshu fan-in."""

    results = [dict(item) for item in worker_results]
    if not results:
        raise ValueError("parallel node has no worker results")
    actions = {str(item.get("action") or "") for item in results}
    actions.discard("")
    if len(actions) != 1:
        raise ValueError("parallel workers returned conflicting actions")
    action = next(iter(actions))

    if state == "ZHONGSHU_ANALYST":
        if action in {"EVIDENCE_PACKET_READY", "EVIDENCE_SUPPLEMENT_READY"}:
            return merge_analyst_evidence(
                task_id,
                revision_id,
                results,
                [dict(item) for item in canonical_requirements]
                if canonical_requirements
                else None,
            )
        if action == "REQUIREMENT_CONTRACT_READY":
            return _merge_requirement_contract(revision_id, results)
        if any(item.get("task_proposals") or item.get("candidate_items") for item in results):
            return merge_analyst_outputs(task_id, revision_id, results)

    if state in {"ZHONGSHU_CRITIC", "ZHONGSHU_FREEZE_CHECK"}:
        if not plan_hash:
            raise ValueError("critic fan-in requires plan_hash")
        report = CriticConflictResolver().aggregate(
            revision_id,
            plan_hash,
            results,
            quorum=min(2, len(results)),
            expected_workers={str(item.get("worker_id") or "") for item in results},
        ).to_dict()
        # The closed FSM uses APPROVE_CRITIC as its Zhongshu critic edge;
        # the domain review protocol calls the same decision APPROVE_FREEZE.
        if report.get("action") == "APPROVE_FREEZE" and state == "ZHONGSHU_CRITIC":
            report["action"] = "APPROVE_CRITIC"
        return report

    return {"action": action, "worker_count": len(results), "worker_results": results}


def _merge_requirement_contract(
    revision_id: str,
    results: Sequence[Mapping[str, object]],
) -> dict[str, Any]:
    requirements: dict[str, dict[str, Any]] = {}
    for result in results:
        raw = result.get("requirements")
        if not isinstance(raw, list):
            raise ValueError("requirement contract worker omitted requirements")
        for item in raw:
            if not isinstance(item, Mapping):
                raise ValueError("requirement contract entry must be an object")
            requirement_id = str(item.get("requirement_id") or "").strip()
            if not requirement_id:
                raise ValueError("requirement contract entry has no requirement_id")
            candidate = dict(item)
            previous = requirements.get(requirement_id)
            if previous is not None and previous != candidate:
                raise ValueError(f"conflicting requirement contract: {requirement_id}")
            requirements[requirement_id] = candidate
    if not requirements:
        raise ValueError("requirement contract is empty")
    return {
        "action": "REQUIREMENT_CONTRACT_READY",
        "revision_id": revision_id,
        "requirements": [requirements[key] for key in sorted(requirements)],
        "worker_count": len(results),
        "worker_ids": sorted(str(item.get("worker_id") or "") for item in results),
    }


__all__ = ["aggregate_zhongshu_workers"]
