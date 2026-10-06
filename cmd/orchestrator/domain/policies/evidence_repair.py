"""Mechanical scope checks for targeted Zhongshu evidence repairs."""
from collections.abc import Mapping
import copy
import json
from pathlib import Path
import posixpath
import re

_WORKSPACE_ROOT = Path(__file__).parents[4].as_posix()
_SOURCE_FILE = re.compile(r'^(.+?\.(?:py|go|md|json|txt|toml|yaml|yml|cfg|ini|rst|js|ts|tsx|jsx|cs))(:.*)?$')


def _workspace_file(path, workspace_root=_WORKSPACE_ROOT):
    """Lexical identity only: no filesystem reads or symlink equivalence."""
    value = str(path or '').strip().replace('\\', '/')
    root = posixpath.normpath(str(workspace_root).replace('\\', '/')).rstrip('/')
    if not value:
        return None
    windows = bool(re.match(r'^[A-Za-z]:/', root))
    if re.match(r'^[A-Za-z]:', value) and not re.match(r'^[A-Za-z]:/', value):
        return None  # Drive-relative paths depend on another process's cwd.
    absolute = value.startswith('/') or bool(re.match(r'^[A-Za-z]:/', value))
    candidate = posixpath.normpath(value if absolute else root + '/' + value)
    if windows:
        root, candidate = root.casefold(), candidate.casefold()
    return candidate if candidate.startswith(root + '/') else None


def _source_locator(source):
    match = _SOURCE_FILE.fullmatch(str(source or ''))
    return (match.group(1), match.group(2) or '') if match else None


def _same_source(left, right):
    if left == right:
        return True
    first, second = _source_locator(left), _source_locator(right)
    if not first or not second or first[1] != second[1]:
        return False
    canonical = _workspace_file(first[0])
    return canonical is not None and canonical == _workspace_file(second[0])


def _same_quote(left, right):
    first, second = left.get('quote'), right.get('quote')
    if first == second:
        return True
    if not isinstance(first, Mapping) or not isinstance(second, Mapping):
        return False
    if {k: v for k, v in first.items() if k != 'path'} != {k: v for k, v in second.items() if k != 'path'}:
        return False
    def path(row, quote):
        locator = _source_locator(row.get('source'))
        return quote.get('path') or (locator[0] if locator else '')
    canonical = _workspace_file(path(left, first))
    return canonical is not None and canonical == _workspace_file(path(right, second))


def normalize_evidence_reuse(payload, demand):
    """Restore equivalent citation aliases from the authoritative fact pool."""
    if not demand.get('strict_item_scope') or not isinstance(payload, dict):
        return payload
    sources = {str(row.get('evidence_id')): row for row in demand.get('available_evidence') or []
               if isinstance(row, Mapping)}
    result = None
    for index, row in enumerate(payload.get('evidence_updates') or []):
        if not isinstance(row, Mapping):
            continue
        source = sources.get(str(row.get('source_evidence_id') or ''))
        if not source or not _same_source(row.get('source'), source.get('source')) or not _same_quote(row, source):
            continue
        if row.get('source') == source.get('source') and row.get('quote') == source.get('quote'):
            continue
        if result is None:
            result = copy.deepcopy(payload)
        result['evidence_updates'][index]['source'] = copy.deepcopy(source.get('source'))
        result['evidence_updates'][index]['quote'] = copy.deepcopy(source.get('quote'))
    return result if result is not None else payload


def available_evidence(review, targets=(), limit=16, max_chars=24000, evidence_ids=()):
    """Bounded source-preserving facts, never an implicit approval or scope change."""
    packet = getattr(review, 'evidence_packet', None)
    if not isinstance(packet, Mapping) or packet.get('phase') != 'ZHONGSHU':
        return []
    canonical = {str(row.get('requirement_id')) for row in getattr(review, 'requirements', ())}
    candidates = []
    by_id = {}
    ambiguous = set()
    for row in packet.get('evidence_updates') or []:
        if not isinstance(row, Mapping):
            continue
        quote = row.get('quote')
        eid = str(row.get('evidence_id') or '')
        if (not eid or row.get('requirement_id') not in canonical
                or str(row.get('conclusion') or '').startswith('[UNVERIFIED-QUOTE]')
                or row.get('observed_variants')
                or not isinstance(quote, Mapping) or not quote.get('text')
                or len(str(quote['text'])) > 4096 or str(quote['text']).count('\n') >= 30):
            continue
        fact = {key: row[key] for key in ('evidence_id', 'item_id', 'requirement_id', 'source', 'quote',
                'conclusion', 'decision_relevance', 'quote_group_id', 'quote_part_index', 'quote_part_count') if key in row}
        if eid in by_id and (by_id[eid].get('source'), by_id[eid].get('quote')) != (fact.get('source'), fact.get('quote')):
            ambiguous.add(eid)
        by_id.setdefault(eid, fact)
    paths = [str(row.get('path') or '').replace('\\', '/').lower() for row in targets]
    demanded_ids = set(evidence_ids)
    ordered = sorted((row for eid, row in by_id.items() if eid not in ambiguous),
        key=lambda row: (row['evidence_id'] not in demanded_ids,
            not any(path and path in str(row.get('source') or '').replace('\\', '/').lower() for path in paths)))
    size = 0
    for row in ordered:
        encoded = len(json.dumps(row, ensure_ascii=False))
        if len(candidates) >= limit:
            break
        if size + encoded > max_chars:
            continue
        candidates.append(row)
        size += encoded
    return candidates


def evidence_scope_rejection(payload: Mapping, demand: Mapping) -> str:
    if not demand.get("strict_item_scope"):
        return ""
    def rows(value):
        return (row for row in value if isinstance(row, Mapping)) if isinstance(value, (list, tuple)) else ()
    findings = {str(row.get("finding_id")): row for row in rows(demand.get("active_findings"))}
    updates = {str(row.get("evidence_id")): row for row in rows(payload.get("evidence_updates"))}
    responses = {str(row.get("finding_id")): row for row in rows(payload.get("finding_responses"))}
    if len(updates) != len(list(rows(payload.get("evidence_updates")))):
        return 'evidence scope: duplicate evidence_id'
    if len(responses) != len(list(rows(payload.get("finding_responses")))):
        return 'evidence scope: duplicate finding response'
    sources = {str(row.get('evidence_id')): row for row in rows(demand.get('available_evidence'))}
    for finding_id, finding in findings.items():
        response = responses.get(finding_id)
        if response is None:
            return f"evidence scope: missing response for {finding_id}"
        ids = response.get("evidence_ids") or []
        if not ids:
            if response.get("suggested_disposition") == "NEEDS_RUNTIME_DATA" and payload.get("unknowns"):
                continue
            return f"evidence scope: {finding_id} must reference scoped evidence_ids or record a runtime UNKNOWN"
        for evidence_id in ids:
            row = updates.get(str(evidence_id))
            if row is None:
                return f"evidence scope: {finding_id} references absent evidence_id {evidence_id}"
            item_id = str(finding.get("item_id") or "")
            allowed = finding.get("source_requirement_ids") or []
            if item_id and row.get("item_id") != item_id:
                return f"evidence scope: {evidence_id} requires item_id={item_id}"
            if allowed and row.get("requirement_id") not in allowed:
                return f"evidence scope: {evidence_id} requirement_id must be one of {allowed}"
    # Additional updates must belong to one of this worker's demanded scopes.
    for evidence_id, row in updates.items():
        source_id = str(row.get('source_evidence_id') or '')
        if source_id:
            source = sources.get(source_id)
            if source is None:
                return f'evidence reuse: unavailable source_evidence_id={source_id}'
            if not _same_source(row.get('source'), source.get('source')) or not _same_quote(row, source):
                changed = []
                if not _same_source(row.get('source'), source.get('source')):
                    changed.append('source')
                left, right = row.get('quote') or {}, source.get('quote') or {}
                if not _same_quote(row, source) and isinstance(left, Mapping) and isinstance(right, Mapping):
                    changed.extend('quote.' + key for key in sorted(set(left) | set(right)) if left.get(key) != right.get(key))
                return (f'evidence reuse: {evidence_id} changed the original source or quote of {source_id}; '
                        f'changed fields={changed}; expected source={source.get("source")!r}, '
                        f'quote.path={right.get("path") if isinstance(right, Mapping) else None!r}. '
                        'Copy the available_evidence source and quote unchanged. Put new investigation sources '
                        'in a separate evidence update without source_evidence_id; do not repeat the repository audit.')
            if evidence_id == source_id and (row.get('item_id'), row.get('requirement_id')) != (source.get('item_id'), source.get('requirement_id')):
                return f'evidence reuse: {source_id} requires a distinct scoped evidence_id'
        if not any(
            (not f.get("item_id") or row.get("item_id") == f.get("item_id"))
            and (not f.get("source_requirement_ids") or row.get("requirement_id") in f["source_requirement_ids"])
            for f in findings.values()
        ):
            return f"evidence scope: unsolicited scope for {evidence_id}"
    return ""
