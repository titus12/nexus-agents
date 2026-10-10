"""Shared data, hashing and validation helpers; no Zhongshu workflow execution."""
from __future__ import annotations
from collections import Counter
import hashlib
import json
import re
from typing import Any, Mapping
from .acceptance_standards import acceptance_signal_gate
from .group_identity import GROUP_ID_SCHEMA_PATTERN
GROUP_MAX_ITEMS = 6

class ReviewQueueError(RuntimeError):
    """Raised when a queue operation is structurally invalid."""

def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()

def flatten_plan_items(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return one normalized item record per task, preserving group ownership."""
    if not isinstance(plan, Mapping):
        raise ReviewQueueError('plan must be an object')
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(raw: Mapping[str, Any], group_id: str) -> None:
        item_id = str(raw.get('item_id') or raw.get('task_key') or '').strip()
        if not item_id:
            raise ReviewQueueError('plan item is missing item_id')
        if item_id in seen:
            return
        seen.add(item_id)
        result.append({'item_id': item_id, 'group_id': str(raw.get('group_id') or group_id or '').strip(), 'dependencies': [str(dep).strip() for dep in raw.get('dependencies') or [] if str(dep).strip()], 'raw': dict(raw)})
    items = plan.get('items')
    if isinstance(items, list):
        for raw in items:
            if isinstance(raw, Mapping):
                add(raw, str(raw.get('group_id') or ''))
    groups = plan.get('groups')
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, Mapping):
                continue
            group_id = str(group.get('group_id') or '')
            for raw in group.get('items') or []:
                if isinstance(raw, Mapping):
                    add(raw, group_id)
    return result

def structural_gate(plan: Mapping[str, Any]) -> list[str]:
    """Deterministic, LLM-free checks the orchestrator owns before freeze."""
    if not isinstance(plan, Mapping):
        return ['PLAN_NOT_OBJECT']
    records = flatten_plan_items(plan)
    if not records:
        return ['PLAN_HAS_NO_ITEMS']
    issues: list[str] = []
    by_item = {record['item_id']: record for record in records}
    group_of: dict[str, str] = {}
    for group in plan.get('groups') or []:
        if not isinstance(group, Mapping):
            continue
        group_id = str(group.get('group_id') or '')
        if not re.fullmatch(GROUP_ID_SCHEMA_PATTERN, group_id):
            issues.append(f'GROUP_ID_INVALID:{group_id!r}')
        for item_id in group.get('item_ids') or []:
            item_id = str(item_id)
            if item_id in group_of:
                issues.append(f'ITEM_IN_MULTIPLE_GROUPS:{item_id}')
            group_of[item_id] = group_id
    for record in records:
        item_id = record['item_id']
        assigned = record['group_id'] or group_of.get(item_id, '')
        if not assigned:
            issues.append(f'ITEM_WITHOUT_GROUP:{item_id}')
    member_counts = Counter((str(record['group_id'] or group_of.get(record['item_id'], '')) for record in records))
    for group_id in sorted(member_counts):
        if group_id and member_counts[group_id] > GROUP_MAX_ITEMS:
            issues.append(f'GROUP_MAX_ITEMS:{group_id}:{member_counts[group_id]}')
    requirements = plan.get('requirements')
    known_requirements = {str(item.get('requirement_id')) for item in requirements or [] if isinstance(item, Mapping) and item.get('requirement_id')}
    out_of_scope = {str(item.get('requirement_id')) for item in requirements or [] if isinstance(item, Mapping) and item.get('requirement_id') and (str(item.get('scope') or '').strip().lower() == 'out')}
    covered: set[str] = set()
    for record in records:
        raw = record['raw']
        sources = [str(req).strip() for req in raw.get('source_requirement_ids') or [] if str(req).strip()]
        if not sources:
            issues.append(f"ITEM_WITHOUT_SOURCE_REQUIREMENT:{record['item_id']}")
        covered.update(sources)
    for requirement_id in sorted(known_requirements - covered - out_of_scope):
        issues.append(f'REQUIREMENT_UNCOVERED:{requirement_id}')
    for record in records:
        for dependency in record['dependencies']:
            if dependency not in by_item:
                issues.append(f"DEPENDENCY_UNKNOWN_ITEM:{record['item_id']}->{dependency}")
    for cycle in _dependency_cycles(by_item):
        issues.append('DEPENDENCY_CYCLE:' + ','.join(cycle))
    owners = {r['item_id']: r['group_id'] or group_of.get(r['item_id'], '') for r in records}
    group_edges: dict[str, set[str]] = {}
    for record in records:
        owner = owners[record['item_id']]
        if not owner:
            continue
        if not re.fullmatch(GROUP_ID_SCHEMA_PATTERN, owner):
            issue = f'GROUP_ID_INVALID:{owner!r}'
            if issue not in issues:
                issues.append(issue)
        dependencies = group_edges.setdefault(owner, set())
        for dependency in record['dependencies']:
            upstream = owners.get(dependency, '')
            if upstream and upstream != owner:
                dependencies.add(upstream)
    groups = {gid: {'dependencies': sorted(deps)} for gid, deps in sorted(group_edges.items())}
    for cycle in _dependency_cycles(groups):
        issues.append('GROUP_DEPENDENCY_CYCLE:' + ','.join(cycle) + ':split upstream prerequisite and downstream summary into separate groups')
    issues.extend(acceptance_signal_gate(plan))
    return issues

def _dependency_cycles(by_item: Mapping[str, dict[str, Any]]) -> list[list[str]]:
    """Iterative DFS back-edge detection; deep graphs cannot exhaust the stack."""
    color: dict[str, int] = {}
    cycles: list[list[str]] = []
    for root in by_item:
        if color.get(root, 0):
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        path: list[str] = []
        while stack:
            node, dep_index = stack[-1]
            if dep_index == 0:
                color[node] = 1
                path.append(node)
            dependencies = [str(dep) for dep in by_item.get(node, {}).get('dependencies', []) if str(dep) in by_item]
            if dep_index < len(dependencies):
                stack[-1] = (node, dep_index + 1)
                dependency = dependencies[dep_index]
                state = color.get(dependency, 0)
                if state == 1:
                    start = path.index(dependency)
                    cycles.append(path[start:] + [dependency])
                elif state == 0:
                    stack.append((dependency, 0))
            else:
                stack.pop()
                path.pop()
                color[node] = 2
    return cycles
