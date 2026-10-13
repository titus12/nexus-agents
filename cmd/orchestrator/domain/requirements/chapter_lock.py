"""逐章确认上锁：确认过的章在摘要之下逐字保持（完整计划 P4）。纯函数。"""
import hashlib
import json
from dataclasses import replace

from .document import CANONICAL_ORDER, PENDING_MARKER_RE
from .model import ChapterLock


def _normalized(markdown):
    # 统一 \r\n → \n，去首尾空白与行尾空白；其余字节不变。
    lines = [line.rstrip() for line in str(markdown or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    return '\n'.join(lines).strip()


def section_digest(section):
    payload = {'title': section.title.strip(), 'markdown': _normalized(section.markdown)}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def lockable_sections(draft):
    """九个规范 kind 中：在正文恰出现一次且正文非空的章，kind → section。"""
    if not draft:
        return {}
    found = {}
    for section in draft.sections:
        if section.kind in CANONICAL_ORDER and section.markdown.strip():
            found.setdefault(section.kind, []).append(section)
    return {kind: rows[0] for kind, rows in found.items() if len(rows) == 1}


def confirm_sections(state, kinds, *, actor_id, event_id, occurred_at):
    """确认后登记每章摘要；kind 无正文、重复、含待确认标记则拒绝。"""
    lockable = lockable_sections(state.draft)
    locks = list(state.chapter_locks)
    for kind in kinds:
        section = lockable.get(kind)
        if section is None:
            raise ValueError('SECTION_UNKNOWN')
        if PENDING_MARKER_RE.search(section.markdown):
            raise ValueError('SECTION_HAS_PENDING_MARKER')
        digest = section_digest(section)
        row = ChapterLock(kind, section.title.strip(), digest, state.draft.version,
                          actor_id, event_id, occurred_at)
        index = next((i for i, lock in enumerate(locks) if lock.kind == kind), None)
        if index is not None and locks[index].content_digest == digest:
            continue  # 幂等
        if index is None:
            locks.append(row)
        else:
            locks[index] = row
    return replace(state, chapter_locks=tuple(locks), reason='')


def release_locks(state, kinds=None, *, fact_ids=None):
    """kinds=None 且无 fact_ids → 全部解除；给 kinds 只解除这些 kind；
    给 fact_ids 解除其正文引用了这些事实的章。"""
    if not state.chapter_locks:
        return state
    if kinds is None and not fact_ids:
        return replace(state, chapter_locks=())
    sections_by_kind = {}
    for section in (state.draft.sections if state.draft else ()):
        sections_by_kind.setdefault(section.kind, []).append(section)
    changed_facts = set(fact_ids or ())

    def released(lock):
        if kinds is not None and lock.kind in kinds:
            return True
        for section in sections_by_kind.get(lock.kind, ()):
            if changed_facts & set(section.fact_ids):
                return True
        return False

    return replace(state, chapter_locks=tuple(lock for lock in state.chapter_locks if not released(lock)))


def locked_violations(draft, locks):
    """((kind, 'missing'|'changed'), ...)，按 CANONICAL_ORDER 排序。"""
    if not locks or not draft:
        return ()
    sections_by_kind = {}
    for section in draft.sections:
        sections_by_kind.setdefault(section.kind, []).append(section)
    violations = []
    for lock in sorted(locks, key=lambda lock: CANONICAL_ORDER.index(lock.kind)):
        rows = sections_by_kind.get(lock.kind, ())
        section = rows[0] if len(rows) == 1 else None
        if section is None or not section.markdown.strip():
            violations.append((lock.kind, 'missing'))
        elif section_digest(section) != lock.content_digest:
            violations.append((lock.kind, 'changed'))
    return tuple(violations)


def locked_payload(state):
    """派发用：已确认章的当前原文，按 CANONICAL_ORDER 排序。"""
    if not state.chapter_locks or not state.draft:
        return []
    sections_by_kind = {}
    for section in state.draft.sections:
        sections_by_kind.setdefault(section.kind, []).append(section)
    payload = []
    for lock in sorted(state.chapter_locks, key=lambda lock: CANONICAL_ORDER.index(lock.kind)):
        rows = sections_by_kind.get(lock.kind, ())
        if len(rows) == 1:
            payload.append({'kind': lock.kind, 'title': rows[0].title, 'markdown': rows[0].markdown})
    return payload
