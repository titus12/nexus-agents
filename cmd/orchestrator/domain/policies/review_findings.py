"""Shared data, hashing and validation helpers; no Zhongshu workflow execution."""
from __future__ import annotations
from collections.abc import Iterable, Mapping
from ..findings import Finding
EVIDENCE_RECORD_FIELDS = ('evidence_id', 'requirement_id', 'item_id', 'decision_relevance', 'conclusion', 'source', 'confidence', 'source_evidence_id', 'quote_group_id', 'quote_part_index', 'quote_part_count')
EVIDENCE_TEXT_LIMITS = {'conclusion': 400, 'source': 200}
MAX_EVIDENCE_RECORDS = 8

def project_evidence_record(value: object) -> dict[str, object]:
    """Project one evidence-packet row onto the bounded record fields."""
    record: dict[str, object] = {}
    if not isinstance(value, Mapping):
        return record
    for key in EVIDENCE_RECORD_FIELDS:
        raw = value.get(key)
        if raw in (None, '', [], {}):
            continue
        if isinstance(raw, str):
            limit = EVIDENCE_TEXT_LIMITS.get(key)
            record[key] = ' '.join(raw.split())[:limit] if limit else ' '.join(raw.split())
        else:
            record[key] = raw
    variants = value.get('observed_variants')
    if isinstance(variants, list) and variants:
        record['observed_variant_count'] = len(variants)
        record['observed_variants'] = [{key: ' '.join(str(row[key]).split())[:400] for key in ('conclusion', 'source', 'confidence') if row.get(key) is not None} for row in variants[-3:] if isinstance(row, Mapping)]
    return record

def _finding_identity(finding: object) -> str:
    """Content identity of one finding observation.

    Critic rounds may re-raise the same opinion under a different ``finding_id``
    (abbreviated vs full id), which used to store two entries for one opinion
    and let the twin waste a batch slot and double-count blockers.  A finding
    with a ``canonical_key`` is identified by that content hash; only findings
    without one fall back to the structural identity.
    """
    canonical = str(_finding_attr(finding, 'canonical_key', '') or '').strip()
    if canonical:
        return f'content:{canonical}'
    return 'id:{0}|{1}|{2}'.format(str(_finding_attr(finding, 'group_id', '') or ''), str(_finding_attr(finding, 'item_id', '') or ''), str(_finding_attr(finding, 'finding_id', '') or ''))

def merge_findings(current: Iterable[Finding], incoming: Iterable[Finding | Mapping[str, object]]) -> tuple[Finding, ...]:
    """Fold incoming findings into the ledger, one entry per opinion.

    Entries are keyed by content identity (see ``_finding_identity``), so a
    re-raise under a different id form replaces the stored opinion instead of
    duplicating it.  As before, the incoming observation wins: the latest
    Critic output carries the current lifecycle status.
    """
    merged: dict[str, Finding] = {}
    for finding in current or ():
        value = finding if isinstance(finding, Finding) else Finding.from_dict(dict(finding))
        merged[_finding_identity(value)] = value
    for value in incoming or ():
        finding = value if isinstance(value, Finding) else Finding.from_dict(dict(value))
        merged[_finding_identity(finding)] = finding
    return tuple(merged.values())

def _finding_attr(finding: object, name: str, default: object=None) -> object:
    if isinstance(finding, Mapping):
        return finding.get(name, default)
    return getattr(finding, name, default)
