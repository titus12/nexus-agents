"""Deterministic provenance report published next to requirement drafts (T4).

Same state in, same bytes out: no timestamps, no random values, stable
iteration order.  The report never changes ``requirements.md`` or its digest.
"""
from .document import KIND_TITLES
from .model import RequirementsState
from .questions import eligible_questions

CLIP = 120


def _clip(text):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= CLIP else text[:CLIP] + '…'


def render_provenance(state: RequirementsState) -> str:
    draft = state.draft
    lines = ['# 溯源说明', '',
             f'- 正文哈希：{draft.document_digest[:12] if draft else "无"}',
             f'- 共识哈希：{state.consensus.digest[:12]}',
             f'- 草案版本：v{draft.version if draft else 0}', '']
    lines.append('## 章节与来源')
    sources = {s.source_id: s for s in state.sources}
    claims = {c.requirement_id: c for c in state.consensus.claims}
    decisions = {d.decision_id: d for d in state.decisions}
    facts = {f.fact_id: f for f in state.consensus.facts}
    for section in draft.sections if draft else ():
        lines.append('')
        lines.append(f'### {section.title}（{section.section_id}）')
        for identity in section.requirement_ids:
            claim = claims.get(identity)
            if claim:
                lines.append(f'- 需求 {identity}（{claim.origin}）：{_clip(claim.statement)}')
                for source_id in claim.source_ids:
                    source = sources.get(source_id)
                    excerpt = _clip(source.text) if source else '（来源未登记）'
                    lines.append(f'  - 来源 {source_id}：{excerpt}')
            else:
                lines.append(f'- 需求 {identity}（未登记）')
        for identity in section.decision_ids:
            decision = decisions.get(identity)
            if decision:
                lines.append(f'- 决定 {identity}：{_clip(decision.conclusion)}（答案来源 {decision.source_id}）')
            else:
                lines.append(f'- 决定 {identity}（未登记）')
        for identity in section.fact_ids:
            fact = facts.get(identity)
            if fact:
                fingerprint = fact.source_fingerprint[:12] if fact.source_fingerprint else '无指纹'
                lines.append(f'- 事实 {identity}：{_clip(fact.observation)}（source：{fact.source}；'
                             f'引文：{_clip(fact.quote)}；指纹 {fingerprint}）')
            else:
                lines.append(f'- 事实 {identity}（未登记）')
    lines.extend(['', '## 未能核实的线索'])
    unavailable = [f for f in state.consensus.facts if f.status == 'unavailable']
    if unavailable:
        for fact in unavailable:
            lines.append(f'- {fact.fact_id}：{_clip(fact.observation)}（source：{fact.source}）')
    else:
        lines.append('- 无')
    lines.extend(['', '## 假设'])
    if state.consensus.assumptions:
        for assumption in state.consensus.assumptions:
            blocking = '是' if assumption.blocking else '否'
            lines.append(f'- {assumption.assumption_id}：{_clip(assumption.statement)}'
                         f'（阻塞：{blocking}；处置：{assumption.disposition}）')
    else:
        lines.append('- 无')
    lines.extend(['', '## 未决问题'])
    pending = eligible_questions(state)
    if pending:
        for question in pending:
            lines.append(f'- {question.question_id}：{_clip(question.question)}')
    else:
        lines.append('- 无')
    return '\n'.join(lines) + '\n'
