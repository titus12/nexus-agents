"""Deterministic pre-publication rubric adapted from the single-agent skill checklist.

Scored checks are advisory measurement only; blocking enforcement stays in
``document.validate_draft`` and lint.  Same state in, same result out.
"""
from dataclasses import dataclass
import re

from .model import CORE_SECTIONS, DraftRevision

INTERNAL_ID_RE = re.compile(r'\b(req|decision|fact|claim|assumption|issue|S)[-_][A-Za-z0-9]{2,}\b')
PLACEHOLDER_TOKENS = ('TODO', '待补充', 'TBD', 'xxx', 'XXX')
TEST_TYPE_WORDS = ('单元测试', '集成测试', '故障注入', '回归测试', '性能测试', '端到端', 'E2E',
                   '契约测试', '冒烟测试', '验收测试', '人工验证', '手工验证')
NUMBER_RE = re.compile(r'\d+(?:\.\d+)?\s*(?:ms|毫秒|秒|分钟|小时|%|％|次|个|条|MB|GB|KB|QPS|TPS|倍)')
HEADING_RE = re.compile(r'^#{1,6}\s', re.MULTILINE)
BASIS_KINDS = ('goals', 'constraints', 'behavior', 'scope')


@dataclass(frozen=True)
class RubricCheck:
    slug: str
    passed: bool
    detail: str = ''


@dataclass(frozen=True)
class RubricResult:
    checks: tuple
    @property
    def score(self):
        return sum(1 for c in self.checks if c.passed)
    @property
    def passed(self):
        return all(c.passed for c in self.checks)
    def to_dict(self):
        return {'score': self.score, 'passed': self.passed,
                'checks': [{'slug': c.slug, 'passed': c.passed, 'detail': c.detail} for c in self.checks]}


def _entries(markdown):
    rows = []
    for line in (markdown or '').splitlines():
        text = line.strip()
        for prefix in ('- ', '* ', '> '):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break
        if text and text[0].isdigit() and '. ' in text[:4]:
            text = text.split('. ', 1)[1].strip()
        if text:
            rows.append(text)
    return rows


def score(draft: DraftRevision, state) -> RubricResult:
    sections = {s.kind: s for s in draft.sections} if draft else {}
    checks = []
    missing = [kind for kind in CORE_SECTIONS if kind not in sections or not sections[kind].markdown.strip()]
    checks.append(RubricCheck('required-chapters', not missing, '缺少章节: ' + ','.join(missing) if missing else ''))
    acceptance = sections.get('acceptance')
    ok = bool(acceptance and acceptance.markdown.strip()
              and any(word in acceptance.markdown for word in TEST_TYPE_WORDS)
              and len(_entries(acceptance.markdown)) >= 1)
    checks.append(RubricCheck('acceptance-structure', ok,
                              '' if ok else '验收章需写明测试类型与至少一条场景/通过条件'))
    body = '\n'.join(s.markdown or '' for s in draft.sections)
    leak = sorted(set(INTERNAL_ID_RE.findall(body)))
    checks.append(RubricCheck('no-internal-ids', not leak, '泄漏内部 ID: ' + ','.join(leak) if leak else ''))
    headings = [s.section_id for s in draft.sections if s.markdown and HEADING_RE.search(s.markdown)]
    checks.append(RubricCheck('no-heading-duplication', not headings, '章节内含标题行: ' + ','.join(headings) if headings else ''))
    placeholders = sorted({token for token in PLACEHOLDER_TOKENS if token in body})
    checks.append(RubricCheck('no-placeholders', not placeholders, '含占位符: ' + ','.join(placeholders) if placeholders else ''))
    source_text = '\n'.join(s.text for s in state.sources)
    unconfirmed = []
    for kind in ('goals', 'acceptance'):
        section = sections.get(kind)
        if not section:
            continue
        if section.decision_ids or section.fact_ids:
            continue
        for entry in _entries(section.markdown):
            if NUMBER_RE.search(entry) and '待定' not in entry and entry not in source_text:
                unconfirmed.append(kind + ':' + entry[:40])
    checks.append(RubricCheck('no-unconfirmed-thresholds', not unconfirmed,
                              '数字未经确认: ' + '; '.join(unconfirmed) if unconfirmed else ''))
    unbuilt = [s.section_id for kind in BASIS_KINDS if (s := sections.get(kind))
               and s.markdown.strip() and not (s.requirement_ids or s.decision_ids or s.fact_ids)]
    checks.append(RubricCheck('provenance-binding', not unbuilt,
                              '关键章节未绑定来源: ' + ','.join(unbuilt) if unbuilt else ''))
    return RubricResult(tuple(checks))
