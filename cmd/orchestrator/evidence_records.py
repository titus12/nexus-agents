"""Shared provenance-preserving evidence record merge."""
from __future__ import annotations
import copy
import json
from typing import Any

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
_EVIDENCE_PROVENANCE_FIELDS = frozenset({'worker_id', 'worker_ids', 'source_workers', 'observed_variants'})

def _evidence_identity(value: dict[str, Any]) -> tuple[str, str, str]:
    """Return a stable identity for a worker-local evidence id and its scope.

    Analyst workers may independently choose the same local id (for example
    ``ev-001``).  Worker provenance is therefore metadata, while scope keeps
    two same-named facts about different requirements/items separate.
    """
    evidence_id = str(value.get('evidence_id') or '').strip()
    scope = str(value.get('finding_id') or value.get('item_id') or value.get('requirement_id') or '').strip()
    if evidence_id:
        return ('evidence', scope, evidence_id)
    payload = {key: copy.deepcopy(item) for key, item in value.items() if key not in _EVIDENCE_PROVENANCE_FIELDS}
    return ('record', scope, json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')))

def _evidence_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(item) for key, item in value.items() if key not in _EVIDENCE_PROVENANCE_FIELDS}

def _evidence_worker_ids(value: dict[str, Any]) -> set[str]:
    worker_ids: set[str] = set()
    for key in ('worker_id', 'worker_ids', 'source_workers'):
        raw = value.get(key)
        if isinstance(raw, list):
            worker_ids.update((str(item).strip() for item in raw if str(item).strip()))
        elif raw:
            worker_ids.add(str(raw).strip())
    return worker_ids

def _append_evidence_variant(canonical: dict[str, Any], value: dict[str, Any], worker_ids: set[str]) -> None:
    variant = _evidence_payload(value)
    if not variant:
        return
    canonical_payload = _evidence_payload(canonical)
    if variant == canonical_payload:
        return
    variants = canonical.setdefault('observed_variants', [])
    if not isinstance(variants, list):
        variants = []
        canonical['observed_variants'] = variants
    variant_key = json.dumps(variant, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    for existing in variants:
        if not isinstance(existing, dict):
            continue
        if json.dumps(_evidence_payload(existing), sort_keys=True, ensure_ascii=False, separators=(',', ':')) == variant_key:
            existing_ids = _evidence_worker_ids(existing)
            existing_ids.update(worker_ids)
            if existing_ids:
                existing['worker_ids'] = sorted(existing_ids)
            return
    entry = copy.deepcopy(variant)
    if worker_ids:
        entry['worker_ids'] = sorted(worker_ids)
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
                    canonical['worker_id'] = sorted(worker_ids)[0]
                    canonical['worker_ids'] = sorted(worker_ids)
                for variant in item.get('observed_variants') or []:
                    if isinstance(variant, dict):
                        _append_evidence_variant(canonical, variant, _evidence_worker_ids(variant))
                positions[key] = len(merged)
                merged.append(canonical)
                continue
            canonical = merged[position]
            if not isinstance(canonical, dict):
                continue
            existing_ids = _evidence_worker_ids(canonical)
            existing_ids.update(worker_ids)
            if existing_ids:
                canonical['worker_id'] = sorted(existing_ids)[0]
                canonical['worker_ids'] = sorted(existing_ids)
            current_payload = _evidence_payload(item)
            canonical_payload = _evidence_payload(canonical)
            if current_payload != canonical_payload:
                _append_evidence_variant(canonical, item, worker_ids)
            for variant in item.get('observed_variants') or []:
                if isinstance(variant, dict):
                    _append_evidence_variant(canonical, variant, _evidence_worker_ids(variant))
            for field, field_value in current_payload.items():
                if field not in canonical or canonical[field] in (None, '', [], {}):
                    canonical[field] = copy.deepcopy(field_value)
    return merged
