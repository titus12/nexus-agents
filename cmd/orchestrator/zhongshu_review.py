"""Pure Zhongshu review rules. No transport, persistence or notifications."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .domain.findings import normalize_finding_status

CRITIC_ACTIONS = frozenset({
    "APPROVE_FREEZE", "REQUEST_SOLVER_REVISION", "REQUEST_REGROUP",
    "REQUEST_ANALYST_EVIDENCE", "HUMAN_GATE", "BLOCKED",
    "TASK_APPROVED", "TASK_CHANGES_REQUIRED",
})
TASK_CRITIC_ACTIONS = frozenset({
    "TASK_APPROVED", "TASK_CHANGES_REQUIRED",
    "REQUEST_ANALYST_EVIDENCE", "HUMAN_GATE", "BLOCKED",
})
ANALYST_ACTIONS = {
    "requirement_contract": frozenset({"REQUIREMENT_CONTRACT_READY", "HUMAN_GATE", "BLOCKED"}),
    "evidence_collection": frozenset({"EVIDENCE_PACKET_READY", "HUMAN_GATE", "BLOCKED"}),
    "evidence_supplement": frozenset({"EVIDENCE_SUPPLEMENT_READY", "HUMAN_GATE", "BLOCKED"}),
}


def review_identity_error(payload: dict[str, Any], revision: str, plan_hash: str) -> str:
    revisions = [str(payload[key]) for key in ("revision_id", "plan_revision_id") if payload.get(key)]
    hashes = [str(payload[key]) for key in ("plan_hash", "reviewed_plan_hash") if payload.get(key)]
    if not revisions or any(value != revision for value in revisions):
        return "ZHONGSHU_CRITIC_REVISION_MISMATCH"
    if not hashes or any(value != plan_hash for value in hashes):
        return "ZHONGSHU_CRITIC_PLAN_HASH_MISMATCH"
    return ""


def semantic_fingerprint(result: dict[str, Any]) -> str:
    """Fingerprint review substance while excluding transport provenance.

    Worker/request IDs prove that slots were allocated; they do not prove
    that independent review work happened.  This fingerprint is therefore
    used only for Critic independence quorum, not as a finding identity.
    """
    body = result.get("payload") if isinstance(result.get("payload"), dict) else result
    worker_id = str(body.get("worker_id") or result.get("worker_id") or "").strip()
    findings = body.get("findings") if isinstance(body.get("findings"), list) else []
    normalized_findings: list[dict[str, Any]] = []
    for raw in findings:
        if not isinstance(raw, dict):
            continue
        evidence = raw.get("evidence_ids") or raw.get("basis_evidence") or []
        evidence_values = evidence if isinstance(evidence, list) else [evidence]
        finding_id = str(raw.get("finding_id") or raw.get("id") or "")
        # New finding IDs are intentionally worker-local for provenance.  Do
        # not let that transport prefix manufacture semantic independence.
        if worker_id and finding_id.startswith(worker_id + ":"):
            finding_id = "worker-local:" + finding_id.split(":", 1)[1]
        normalized_findings.append({
            "finding_id": finding_id,
            "category": str(raw.get("category") or "general"),
            "target": str(
                raw.get("target")
                or raw.get("item_id")
                or raw.get("requirement_id")
                or raw.get("field")
                or "global"
            ),
            "claim": str(raw.get("claim") or raw.get("description") or ""),
            "decision": str(
                raw.get("decision")
                or raw.get("status")
                or raw.get("next_action")
                or ""
            ).upper(),
            "severity": str(raw.get("severity") or "P2").upper(),
            "evidence_ids": sorted(str(item) for item in evidence_values if item),
            "evidence_strength": str(raw.get("evidence_strength") or "inference").lower(),
            "confidence": raw.get("confidence", 0.0),
        })
    normalized_findings.sort(
        key=lambda item: (
            item["finding_id"], item["category"], item["target"],
            item["claim"], item["decision"],
        )
    )
    semantic = {
        "action": str(body.get("action") or ""),
        "plan_revision_id": str(
            body.get("plan_revision_id") or body.get("revision_id") or ""
        ),
        "plan_hash": str(
            body.get("plan_hash") or body.get("reviewed_plan_hash") or ""
        ),
        "findings": normalized_findings,
        "review_summary": str(body.get("review_summary") or ""),
        "requirement_coverage": body.get("requirement_coverage") or [],
        "evidence_alignment": body.get("evidence_alignment") or [],
        "missing_evidence": body.get("missing_evidence") or [],
        "required_change": body.get("required_change") or [],
        "remaining_blockers": body.get("remaining_blockers") or [],
    }
    return hashlib.sha256(
        json.dumps(
            semantic,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def records(value: Any) -> list[Any]:
    """Preserve structured evidence/unknowns instead of coercing them to text."""
    if value is None:
        return []
    return copy.deepcopy(value if isinstance(value, list) else [value])


def union_records(*values: Any) -> list[Any]:
    result: list[Any] = []
    for value in values:
        for item in records(value):
            if item not in result:
                result.append(item)
    return result


_EVIDENCE_PROVENANCE_FIELDS = frozenset({
    "worker_id", "worker_ids", "source_workers", "observed_variants",
})


def _evidence_identity(value: dict[str, Any]) -> tuple[str, str, str]:
    """Return a stable identity for a worker-local evidence id and its scope.

    Analyst workers may independently choose the same local id (for example
    ``ev-001``).  Worker provenance is therefore metadata, while scope keeps
    two same-named facts about different requirements/items separate.
    """
    evidence_id = str(value.get("evidence_id") or "").strip()
    scope = str(
        value.get("finding_id")
        or value.get("item_id")
        or value.get("requirement_id")
        or ""
    ).strip()
    if evidence_id:
        return ("evidence", scope, evidence_id)
    payload = {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key not in _EVIDENCE_PROVENANCE_FIELDS
    }
    return (
        "record",
        scope,
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
    )


def _evidence_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key not in _EVIDENCE_PROVENANCE_FIELDS
    }


def _evidence_worker_ids(value: dict[str, Any]) -> set[str]:
    worker_ids: set[str] = set()
    for key in ("worker_id", "worker_ids", "source_workers"):
        raw = value.get(key)
        if isinstance(raw, list):
            worker_ids.update(str(item).strip() for item in raw if str(item).strip())
        elif raw:
            worker_ids.add(str(raw).strip())
    return worker_ids


def _append_evidence_variant(
    canonical: dict[str, Any],
    value: dict[str, Any],
    worker_ids: set[str],
) -> None:
    variant = _evidence_payload(value)
    if not variant:
        return
    canonical_payload = _evidence_payload(canonical)
    if variant == canonical_payload:
        return
    variants = canonical.setdefault("observed_variants", [])
    if not isinstance(variants, list):
        variants = []
        canonical["observed_variants"] = variants
    variant_key = json.dumps(variant, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    for existing in variants:
        if not isinstance(existing, dict):
            continue
        if json.dumps(
            _evidence_payload(existing),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ) == variant_key:
            existing_ids = _evidence_worker_ids(existing)
            existing_ids.update(worker_ids)
            if existing_ids:
                existing["worker_ids"] = sorted(existing_ids)
            return
    entry = copy.deepcopy(variant)
    if worker_ids:
        entry["worker_ids"] = sorted(worker_ids)
    variants.append(entry)


def merge_evidence_updates(*values: Any) -> list[Any]:
    """Merge evidence by evidence identity, retaining provenance and variants.

    Parallel workers commonly report the same evidence more than once, both
    across workers and across evidence-supplement rounds.  Worker provenance
    is metadata, not identity.  The canonical record keeps all source worker
    ids; genuinely conflicting records remain in ``observed_variants`` so the
    state remains lossless while prompt projections can omit repeated copies.
    """
    merged: list[Any] = []
    positions: dict[tuple[str, str, str], int] = {}
    for value in values:
        for raw in records(value):
            if not isinstance(raw, dict):
                if raw not in merged:
                    merged.append(raw)
                continue
            item = copy.deepcopy(raw)
            key = _evidence_identity(item)
            worker_ids = _evidence_worker_ids(item)
            position = positions.get(key)
            if position is None:
                canonical = _evidence_payload(item)
                if worker_ids:
                    canonical["worker_id"] = sorted(worker_ids)[0]
                    canonical["worker_ids"] = sorted(worker_ids)
                for variant in item.get("observed_variants") or []:
                    if isinstance(variant, dict):
                        _append_evidence_variant(
                            canonical,
                            variant,
                            _evidence_worker_ids(variant),
                        )
                positions[key] = len(merged)
                merged.append(canonical)
                continue
            canonical = merged[position]
            if not isinstance(canonical, dict):
                continue
            existing_ids = _evidence_worker_ids(canonical)
            existing_ids.update(worker_ids)
            if existing_ids:
                canonical["worker_id"] = sorted(existing_ids)[0]
                canonical["worker_ids"] = sorted(existing_ids)
            current_payload = _evidence_payload(item)
            canonical_payload = _evidence_payload(canonical)
            if current_payload != canonical_payload:
                _append_evidence_variant(canonical, item, worker_ids)
            for variant in item.get("observed_variants") or []:
                if isinstance(variant, dict):
                    _append_evidence_variant(
                        canonical,
                        variant,
                        _evidence_worker_ids(variant),
                    )
            for field, field_value in current_payload.items():
                if field not in canonical or canonical[field] in (None, "", [], {}):
                    canonical[field] = copy.deepcopy(field_value)
    return merged


def _critic_worker_summary(review: dict[str, Any]) -> dict[str, Any]:
    findings = review.get("findings") or []
    finding_ids = sorted({
        str(item.get("finding_id") or item.get("id"))
        for item in findings
        if isinstance(item, dict) and (item.get("finding_id") or item.get("id"))
    })
    summary: dict[str, Any] = {
        "worker_id": review.get("worker_id"),
        "worker_lens": review.get("worker_lens"),
        "action": review.get("action"),
        "revision_id": review.get("revision_id"),
        "plan_revision_id": review.get("plan_revision_id"),
        "plan_hash": review.get("plan_hash"),
        "reviewed_plan_hash": review.get("reviewed_plan_hash"),
        "finding_ids": finding_ids,
    }
    review_summary = review.get("review_summary")
    if isinstance(review_summary, str) and review_summary:
        summary["review_summary"] = review_summary[:1200]
    return {key: value for key, value in summary.items() if value not in (None, "", [])}


def _resolved(observation: dict[str, Any]) -> bool:
    return normalize_finding_status(observation.get("status"), observation.get("decision")) == "RESOLVED"


def _resolution_basis(observation: dict[str, Any]) -> bool:
    return any(observation.get(key) for key in (
        "resolution", "response", "verification", "evidence_ids", "basis_evidence",
        "supporting_evidence",
    ))


_FINDING_SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _finding_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def finding_semantic_key(finding: dict[str, Any]) -> str:
    """Return a worker-independent key for the same underlying finding.

    The raw finding ID is deliberately excluded when substantive identity is
    available: Critic workers are allowed to namespace IDs independently.
    Scope, category, claim and required action are retained so unrelated
    findings are not merged merely because their prose is similar.
    """
    claim = _finding_text(
        finding.get("claim") or finding.get("description") or finding.get("title")
    )
    target = finding.get("target") or finding.get("item_id") or finding.get("requirement_id") or finding.get("field")
    affected_items = finding.get("affected_item_ids") or finding.get("item_ids") or []
    affected_requirements = finding.get("affected_requirement_ids") or finding.get("requirement_ids") or []
    identity = {
        "scope": _finding_text(finding.get("scope") or "item"),
        "group_id": _finding_text(finding.get("group_id")),
        "item_id": _finding_text(finding.get("item_id") or target),
        "category": _finding_text(finding.get("category") or "general"),
        "target": _finding_text(target or "global"),
        "affected_item_ids": sorted(_finding_text(item) for item in affected_items) if isinstance(affected_items, list) else [_finding_text(affected_items)],
        "affected_requirement_ids": sorted(_finding_text(item) for item in affected_requirements) if isinstance(affected_requirements, list) else [_finding_text(affected_requirements)],
        "claim": claim,
        "required_action": _finding_text(finding.get("required_action") or finding.get("required_change")),
    }
    if not claim and not identity["required_action"]:
        raw_id = _finding_text(finding.get("finding_id") or finding.get("id"))
        identity = {"raw_finding_id": raw_id}
    return hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def consolidate_finding_observations(
    observations: dict[str, list[dict[str, Any]]],
    previous: dict[str, dict[str, Any]] | None = None,
    *,
    quorum: int = 2,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Consolidate duplicate Critic observations without losing provenance."""
    ledger = {
        str(finding_id): copy.deepcopy(finding)
        for finding_id, finding in (previous or {}).items()
        if isinstance(finding, dict)
    }
    disagreements: list[dict[str, Any]] = []
    consumed_previous: set[str] = set()

    def previous_match(
        key: str,
        raw_ids: set[str],
        values: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any]] | None:
        current_scopes = {
            (
                str(item.get("group_id") or "").strip(),
                str(item.get("item_id") or "").strip(),
            )
            for item in values
        }

        def scope_compatible(finding: dict[str, Any]) -> bool:
            old_scope = (
                str(finding.get("group_id") or "").strip(),
                str(finding.get("item_id") or "").strip(),
            )
            if old_scope == ("", ""):
                # Do not let a legacy/global finding ID collide with a new
                # item-owned finding. Keep the legacy record in the ledger,
                # but create a separately scoped observation.
                return ("", "") in current_scopes
            return old_scope in current_scopes

        for finding_id in sorted(raw_ids):
            if (
                finding_id in ledger
                and finding_id not in consumed_previous
                and scope_compatible(ledger[finding_id])
            ):
                return finding_id, ledger[finding_id]
        for finding_id, finding in ledger.items():
            aliases = {
                str(item).strip()
                for item in finding.get("source_finding_ids") or []
                if str(item).strip()
            }
            if (
                finding_id not in consumed_previous
                and scope_compatible(finding)
                and aliases.intersection(raw_ids)
            ):
                return finding_id, finding
        for finding_id, finding in ledger.items():
            old_key = str(finding.get("canonical_key") or finding_semantic_key(finding))
            if (
                finding_id not in consumed_previous
                and scope_compatible(finding)
                and old_key == key
            ):
                return finding_id, finding
        return None

    used_canonical_ids: set[str] = set(ledger)
    for key in sorted(observations):
        values = [copy.deepcopy(item) for item in observations[key] if isinstance(item, dict)]
        if not values:
            continue
        raw_ids = {
            str(item.get("finding_id") or item.get("id") or "").strip()
            for item in values
            if str(item.get("finding_id") or item.get("id") or "").strip()
        }
        matched = previous_match(key, raw_ids, values)
        old_id, old = matched if matched else ("", None)
        if old_id:
            consumed_previous.add(old_id)
        canonical_id = old_id or (
            next(iter(raw_ids)) if len(raw_ids) == 1
            else "finding-" + key[:20]
        )
        if canonical_id in used_canonical_ids and canonical_id != old_id:
            canonical_id = "finding-" + key[:20]
        if canonical_id in used_canonical_ids and canonical_id != old_id:
            suffix = 2
            base_id = canonical_id
            while f"{base_id}-{suffix}" in used_canonical_ids:
                suffix += 1
            canonical_id = f"{base_id}-{suffix}"
        used_canonical_ids.add(canonical_id)
        canonical = copy.deepcopy(old or values[0])
        canonical["finding_id"] = canonical_id
        canonical["canonical_key"] = key
        canonical["source_finding_ids"] = sorted(
            set(str(item).strip() for item in canonical.get("source_finding_ids") or [] if str(item).strip())
            | raw_ids
        )
        worker_ids = {
            str(item.get("worker_id") or "").strip()
            for item in values
            if str(item.get("worker_id") or "").strip()
        }
        worker_ids.update(
            str(item).strip()
            for item in canonical.get("source_workers") or []
            if str(item).strip()
        )
        canonical["source_workers"] = sorted(worker_ids)
        canonical["worker_ids"] = sorted(worker_ids)
        canonical["quorum_support"] = len(worker_ids)
        canonical["observations"] = values
        severities = [
            str(item.get("severity") or "P2").upper()
            for item in values + ([old] if old else [])
        ]
        canonical["severity"] = min(
            severities,
            key=lambda value: _FINDING_SEVERITY_RANK.get(value, 0),
        )
        resolved_by = {
            str(item.get("worker_id"))
            for item in values
            if _resolved(item) and _resolution_basis(item)
        }
        open_values = [item for item in values if not _resolved(item)]
        closed = len(resolved_by) >= quorum and not open_values
        if old and _resolved(old) and not open_values:
            closed = True
        canonical["status"] = canonical["decision"] = "RESOLVED" if closed else "OPEN"
        if closed:
            canonical["resolution"] = "; ".join(
                str(item.get("resolution") or item.get("response") or item.get("evidence_ids") or "")
                for item in values
            )
            canonical["resolution_confirmed_by"] = sorted(resolved_by)
        else:
            canonical.pop("resolution_confirmed_by", None)
        canonical["evidence_ids"] = union_records(
            canonical.get("evidence_ids"),
            *[item.get("evidence_ids") or item.get("basis_evidence") for item in values],
        )
        if resolved_by and open_values:
            disagreements.append({
                "finding_id": canonical_id,
                "status": "OPEN",
                "observations": copy.deepcopy(values),
            })
        ledger[canonical_id] = canonical
    return ledger, disagreements


def aggregate_reviews(
    revision: str, plan_hash: str, results: list[dict[str, Any]],
    *, previous: dict[str, Any] | None = None, quorum: int = 2,
    expected_workers: set[str] | None = None,
) -> dict[str, Any]:
    """Count qualified sources; preserve findings and compact worker provenance."""
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    workers: set[str] = set()
    requests: set[str] = set()
    for result in results:
        body = result.get("payload") if isinstance(result.get("payload"), dict) else result
        worker = str(body.get("worker_id") or result.get("worker_id") or "")
        request = str(body.get("logical_request_id") or body.get("request_id") or "")
        error = review_identity_error(body, revision, plan_hash)
        if not worker or (expected_workers is not None and worker not in expected_workers):
            error = "ZHONGSHU_CRITIC_WORKER_MISMATCH"
        elif worker in workers or (request and request in requests):
            error = "ZHONGSHU_CRITIC_DUPLICATE_SOURCE"
        elif body.get("action") not in CRITIC_ACTIONS:
            error = "ZHONGSHU_CRITIC_ACTION_INVALID"
        elif body.get("action") not in {"HUMAN_GATE", "BLOCKED"} and not isinstance(body.get("findings"), list):
            error = "ZHONGSHU_CRITIC_FINDINGS_MISSING"
        if error:
            rejected.append({"worker_id": worker, "reason": error, "payload": copy.deepcopy(body)})
            continue
        workers.add(worker)
        if request:
            requests.add(request)
        accepted.append({**copy.deepcopy(body), "worker_id": worker})
    if len(accepted) < quorum:
        raise ValueError(f"ZHONGSHU_CRITIC_QUORUM_INCOMPLETE:valid={len(accepted)}:required={quorum}")

    fingerprints = {semantic_fingerprint(item) for item in accepted}
    if len(fingerprints) < quorum:
        raise ValueError(
            "ZHONGSHU_CRITIC_INDEPENDENCE_INSUFFICIENT:"
            f"valid={len(accepted)}:distinct={len(fingerprints)}:required={quorum}"
        )

    previous_ledger: dict[str, dict[str, Any]] = {}
    for item in (previous or {}).get("findings") or []:
        if isinstance(item, dict) and (item.get("finding_id") or item.get("id")):
            finding_id = str(item.get("finding_id") or item["id"])
            previous_ledger[finding_id] = copy.deepcopy(item)
    observations: dict[str, list[dict[str, Any]]] = {}
    pending: list[dict[str, Any]] = []
    for review in accepted:
        if review["action"] != "APPROVE_FREEZE":
            pending.append({
                "worker_id": review["worker_id"],
                "worker_lens": review.get("worker_lens"),
                "action": review["action"],
                "finding_ids": sorted({
                    str(item.get("finding_id") or item.get("id"))
                    for item in review.get("findings") or []
                    if isinstance(item, dict) and (item.get("finding_id") or item.get("id"))
                }),
            })
        for item in review.get("findings") or []:
            if not isinstance(item, dict) or not (item.get("finding_id") or item.get("id")):
                raise ValueError("ZHONGSHU_CRITIC_FINDING_ID_MISSING")
            observation = {
                **copy.deepcopy(item),
                "worker_id": review["worker_id"],
                "worker_lens": review.get("worker_lens"),
            }
            observations.setdefault(finding_semantic_key(observation), []).append(observation)

    ledger, disagreements = consolidate_finding_observations(
        observations,
        previous_ledger,
        quorum=quorum,
    )

    findings = [ledger[key] for key in sorted(ledger)]
    blocking = [item for item in findings if not _resolved(item) and item.get("severity") in {"P0", "P1"}]
    actions = {review["action"] for review in accepted}
    approvals = sum(review["action"] == "APPROVE_FREEZE" for review in accepted)
    if "HUMAN_GATE" in actions:
        action = "HUMAN_GATE"
    elif "BLOCKED" in actions:
        action = "BLOCKED"
    elif "REQUEST_ANALYST_EVIDENCE" in actions:
        action = "REQUEST_ANALYST_EVIDENCE"
    elif blocking or disagreements or actions.intersection({"REQUEST_SOLVER_REVISION", "REQUEST_REGROUP"}):
        action = "REQUEST_SOLVER_REVISION"
    elif approvals >= quorum:
        action = "APPROVE_FREEZE"
    else:
        action = "HUMAN_GATE"
    output = {
        "action": action, "plan_revision_id": revision, "revision_id": revision,
        "plan_hash": plan_hash, "reviewed_plan_hash": plan_hash,
        "findings": findings, "conflicts": disagreements,
        "resolved_conflicts": [], "unresolved_conflicts": [item["finding_id"] for item in disagreements],
        "worker_reviews": [_critic_worker_summary(review) for review in accepted],
        "rejected_reviews": rejected, "pending_actions": pending,
        "valid_worker_ids": sorted(workers), "quorum": quorum, "approval_count": approvals,
        "distinct_review_fingerprint_count": len(fingerprints),
        "review_semantic_fingerprints": sorted(fingerprints),
        "decision_basis": [f"valid={len(accepted)} approvals={approvals} active_blockers={len(blocking)} action={action}"],
    }
    for field in ("questions_for_analyst", "missing_evidence", "required_change", "remaining_blockers"):
        output[field] = union_records(*[review.get(field) for review in accepted])
    if action in {"HUMAN_GATE", "BLOCKED"}:
        selected = next((review for review in accepted if review["action"] == action), {})
        for field in ("human_gate", "human_required", "question", "notification", "unblock_condition"):
            if field in selected:
                output[field] = copy.deepcopy(selected[field])
        if action == "HUMAN_GATE" and not output.get("human_gate"):
            output["human_gate"] = {"question": "请明确尚未解决的中书省审查分歧。"}
    return output


def aggregate_task_review_results(
    revision: str,
    plan_hash: str,
    queue: Any,
    results: list[dict[str, Any]],
    *,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate one Critic result per task without a cross-task quorum."""
    by_job: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    known_jobs = {job.review_job_id: job for job in queue.jobs}
    for raw in results:
        body = raw.get("payload") if isinstance(raw.get("payload"), dict) else raw
        if not isinstance(body, dict):
            rejected.append({"reason": "TASK_REVIEW_RESULT_NOT_OBJECT"})
            continue
        job_id = str(body.get("review_job_id") or "")
        group_id = str(body.get("group_id") or "")
        item_id = str(body.get("item_id") or "")
        if not job_id:
            try:
                job_id = queue.job_for_item(group_id, item_id).review_job_id
            except ValueError:
                rejected.append({"group_id": group_id, "item_id": item_id, "reason": "TASK_REVIEW_JOB_UNKNOWN"})
                continue
        job = known_jobs.get(job_id)
        if job is None:
            rejected.append({"review_job_id": job_id, "reason": "TASK_REVIEW_JOB_UNKNOWN"})
            continue
        if job_id in by_job:
            rejected.append({"review_job_id": job_id, "reason": "TASK_REVIEW_DUPLICATE_RESULT"})
            continue
        if (
            str(body.get("revision_id") or "") != revision
            or str(body.get("plan_hash") or "") != plan_hash
            or str(body.get("reviewed_plan_hash") or "") != plan_hash
            or str(body.get("group_id") or "") != job.group_id
            or str(body.get("item_id") or "") != job.item_id
            or str(body.get("reviewed_task_hash") or "") != job.task_hash
            or str(body.get("reviewed_dependency_hash") or "") != job.dependency_hash
        ):
            rejected.append({"review_job_id": job_id, "reason": "TASK_REVIEW_RESULT_IDENTITY_MISMATCH"})
            continue
        if job.status != "COMPLETED":
            rejected.append({"review_job_id": job_id, "reason": "TASK_REVIEW_RESULT_FOR_NONCOMPLETED_JOB"})
            continue
        by_job[job_id] = copy.deepcopy(body)

    previous_ledger = {
        str(item.get("finding_id") or item.get("id")): copy.deepcopy(item)
        for item in (previous or {}).get("findings") or []
        if isinstance(item, dict) and (item.get("finding_id") or item.get("id"))
    }
    observations: dict[str, list[dict[str, Any]]] = {}
    task_reviews: list[dict[str, Any]] = []
    actions: set[str] = set()
    worker_ids: set[str] = set()
    for job in queue.jobs:
        body = by_job.get(job.review_job_id)
        if body is None:
            continue
        worker_id = str(body.get("worker_id") or job.worker_id or "")
        if worker_id:
            worker_ids.add(worker_id)
        action = str(body.get("action") or "")
        actions.add(action)
        task_reviews.append({
            "review_job_id": job.review_job_id,
            "group_id": job.group_id,
            "item_id": job.item_id,
            "worker_id": worker_id,
            "action": action,
            "reviewed_task_hash": body.get("reviewed_task_hash"),
            "reviewed_dependency_hash": body.get("reviewed_dependency_hash"),
            "review_checks": copy.deepcopy(body.get("review_checks") or {}),
            "finding_ids": sorted({
                str(item.get("finding_id") or item.get("id"))
                for item in body.get("findings") or []
                if isinstance(item, dict) and (item.get("finding_id") or item.get("id"))
            }),
        })
        for raw_finding in body.get("findings") or []:
            if not isinstance(raw_finding, dict):
                continue
            finding = copy.deepcopy(raw_finding)
            finding["group_id"] = job.group_id
            finding["item_id"] = job.item_id
            finding.setdefault("scope", "item")
            finding.setdefault("related_item_ids", [])
            finding["worker_id"] = worker_id
            observations.setdefault(finding_semantic_key(finding), []).append(finding)

    ledger, disagreements = consolidate_finding_observations(
        observations,
        previous_ledger,
        quorum=1,
    )
    findings = [ledger[key] for key in sorted(ledger)]
    blocking = [
        item for item in findings
        if not _resolved(item) and str(item.get("severity") or "P2").upper() in {"P0", "P1"}
    ]
    queue_counts = queue.counts()
    completed_job_ids = {
        job.review_job_id for job in queue.jobs if job.status == "COMPLETED"
    }
    complete = (
        bool(queue.jobs)
        and len(completed_job_ids) == len(queue.jobs)
        and completed_job_ids == set(by_job)
    )
    if not complete:
        # A missing/stale/unfinished task review is an execution-integrity
        # problem, not evidence that Solver should rewrite the plan.
        action = "HUMAN_GATE"
    elif "HUMAN_GATE" in actions:
        action = "HUMAN_GATE"
    elif "BLOCKED" in actions:
        action = "BLOCKED"
    elif "REQUEST_ANALYST_EVIDENCE" in actions:
        action = "REQUEST_ANALYST_EVIDENCE"
    elif blocking or disagreements or "TASK_CHANGES_REQUIRED" in actions:
        action = "REQUEST_SOLVER_REVISION"
    else:
        action = "APPROVE_FREEZE"
    task_approved = sum(
        item.get("action") == "TASK_APPROVED" for item in task_reviews
    )
    affected_reviews = [
        item for item in task_reviews
        if item.get("action") != "TASK_APPROVED" or item.get("finding_ids")
    ]
    affected_item_ids = sorted({
        str(item.get("item_id"))
        for item in affected_reviews
        if item.get("item_id")
    })
    affected_group_ids = sorted({
        str(item.get("group_id"))
        for item in affected_reviews
        if item.get("group_id")
    })
    failed_task_ids = [
        job.item_id for job in queue.jobs if job.status == "HUMAN_GATE"
    ]
    retryable_task_ids = [
        job.item_id for job in queue.jobs
        if job.status in {"PENDING", "RUNNING", "RETRYABLE"}
    ]
    blocked_task_ids = [
        item["item_id"] for item in task_reviews
        if item.get("action") in {"BLOCKED", "HUMAN_GATE"}
    ]
    output: dict[str, Any] = {
        "action": action,
        "plan_revision_id": revision,
        "revision_id": revision,
        "plan_hash": plan_hash,
        "reviewed_plan_hash": plan_hash,
        "findings": findings,
        "conflicts": disagreements,
        "resolved_conflicts": [],
        "unresolved_conflicts": [item["finding_id"] for item in disagreements],
        "task_reviews": task_reviews,
        "worker_reviews": task_reviews,
        "rejected_reviews": rejected,
        "valid_worker_ids": sorted(worker_ids),
        "quorum": 1,
        "approval_count": task_approved,
        "distinct_review_fingerprint_count": len(task_reviews),
        "task_review_mode": True,
        "task_review_complete": complete,
        "total_task_count": len(queue.jobs),
        "completed_task_count": sum(
            1 for job in queue.jobs if job.status == "COMPLETED"
        ),
        "task_queue_counts": queue_counts,
        "active_p0_p1_finding_ids": [item["finding_id"] for item in blocking],
        "affected_item_ids": affected_item_ids,
        "affected_group_ids": affected_group_ids,
        "failed_task_ids": failed_task_ids,
        "retryable_task_ids": retryable_task_ids,
        "blocked_task_ids": sorted(set(blocked_task_ids)),
        "decision_basis": [
            f"tasks={len(queue.jobs)} completed={sum(1 for job in queue.jobs if job.status == 'COMPLETED')} "
            f"approved={task_approved} active_blockers={len(blocking)} action={action}"
        ],
    }
    for field in ("questions_for_analyst", "missing_evidence", "required_change", "remaining_blockers"):
        output[field] = union_records(*[review.get(field) for review in by_job.values()])
    return output


def repair_route(resolutions: list[dict[str, Any]]) -> str:
    """Only explicit ownership can redirect unresolved work to evidence collection."""
    owners = set()
    for item in resolutions:
        owner = str(item.get("owner_role") or item.get("owner") or "").lower()
        action = str(item.get("next_action") or "").upper()
        if action == "HUMAN_GATE" or owner in {"human", "user"}:
            return "HUMAN_GATE"
        if action == "BLOCKED":
            return "BLOCKED"
        if owner in {"analyst", "review-analyst"} or action in {"REQUEST_ANALYST_EVIDENCE", "NEEDS_MORE_EVIDENCE"}:
            owners.add("analyst")
        elif owner in {"solver", "review-solver"} or action in {"REQUEST_SOLVER_REVISION", "REQUEST_REGROUP"}:
            owners.add("solver")
        else:
            owners.add("unknown")
    if "unknown" in owners:
        return "HUMAN_GATE"
    return "REQUEST_ANALYST_EVIDENCE" if "analyst" in owners else "READY_FOR_CRITIC"


def progress_signature(value: Any) -> str:
    ignored = {"task_id", "request_id", "logical_request_id", "revision_id", "plan_revision_id", "plan_id", "version", "timestamp", "created_at", "updated_at", "worker_id", "worker_ids", "source_workers", "attempt", "worker_reviews", "observations", "decision_basis"}
    def clean(item: Any, field: str = "") -> Any:
        if isinstance(item, dict):
            return {key: clean(val, key) for key, val in item.items() if key not in ignored}
        if isinstance(item, list):
            values = [clean(val) for val in item]
            return values if field in {"groups", "items"} else sorted(values, key=lambda val: json.dumps(val, sort_keys=True, ensure_ascii=False))
        return item
    return hashlib.sha256(json.dumps(clean(value), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
