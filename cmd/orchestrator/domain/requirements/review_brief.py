"""Deterministic reader brief published next to the requirement draft (roadmap S3).

Same state in, same bytes out: no timestamps, no random values.  The brief is
a side artifact: it never changes ``requirements.md`` or its digest and never
reaches Menxia.  Internal record ids stay internal; the output speaks in
chapter titles and sentences.
"""
from .model import RequirementsState
from .provenance import _clip
from .rubric import _entries

ENTRY_CLIP = 80
BRIEF_NOTE = '> 本文件不是需求正文，不参与批准，也不会交给门下省。'
BRIEF_SUFFIX = '…（完整见 review-brief.md）'
NOTIFICATION_CLIP = 1200


def _chapter_of(draft, requirement_id):
    for section in draft.sections if draft else ():
        if requirement_id in section.requirement_ids:
            return section.title
    return '（未绑定章节）'


def _clip_entry(entry):
    text = ' '.join(str(entry or '').split())
    return text if len(text) <= ENTRY_CLIP else text[:ENTRY_CLIP] + '…'


def pending_section(state):
    """Section 一: everything the reader has to judge."""
    lines = ['## 一、先看这里：需要你判断的']
    draft = state.draft
    pending = [f'{section.title}：{_clip_entry(entry)}'
               for section in (draft.sections if draft else ())
               for entry in _entries(section.markdown) if '待定' in entry]
    if pending:
        lines.append('- 正文待定条目：')
        lines.extend(f'  - {entry}' for entry in pending)
    else:
        lines.append('- 正文待定条目：无')
    questions = {q.question_id: q for q in state.questions}
    assumptions = {a.assumption_id: a for a in state.consensus.assumptions} if state.consensus else {}
    deferred = [questions.get(answer.question_id) for answer in state.answers if answer.disposition == 'defer']
    if any(text for text in deferred):
        lines.append('- 你选择暂缓的问题：')
        lines.extend(f'  - {_clip(text.question if text else "（问题已不在当前清单）")}' for text in deferred if text)
    else:
        lines.append('- 你选择暂缓的问题：无')
    defaults = [(questions.get(answer.question_id), answer) for answer in state.answers
                if answer.disposition == 'accept_default']
    if defaults:
        lines.append('- 采用了默认假设的问题：')
        for text, answer in defaults:
            assumption = assumptions.get(text.default_assumption_id) if text else None
            body = _clip(text.question) if text else '（问题已不在当前清单）'
            if assumption:
                body += f'；默认假设：{_clip(assumption.statement)}'
            lines.append(f'  - {body}')
    else:
        lines.append('- 采用了默认假设的问题：无')
    requests = [line.strip() for line in (draft.change_summary if draft else '').splitlines()
                if line.strip().startswith('【申请修改已确认章节：')]
    if requests:
        lines.append('- Solver 申请修改你已确认的章节：')
        lines.extend(f'  - {_clip_entry(line)}' for line in requests)
    return lines


def inference_section(state):
    """Section 二: inferences and assumptions the engine made on the reader's behalf."""
    lines = ['## 二、我替你做的推断']
    draft = state.draft
    sources = {s.source_id: s for s in state.sources}
    rows = []
    for claim in (state.consensus.claims if state.consensus else ()):
        if claim.status == 'active' and claim.origin in ('derived', 'proposal'):
            basis = '；'.join(_clip(sources[sid].text) for sid in claim.source_ids if sid in sources) or '无直接原话'
            rows.append(f'- [{_chapter_of(draft, claim.requirement_id)}] {_clip(claim.statement)}')
            rows.append(f'  依据：{basis}')
    for assumption in (state.consensus.assumptions if state.consensus else ()):
        if assumption.disposition in ('proposed', 'accepted'):
            blocking = '阻塞' if assumption.blocking else '非阻塞'
            rows.append(f'- 假设：{_clip(assumption.statement)}（{blocking}）')
    if rows:
        lines.extend(rows)
    else:
        lines.append('- 无')
    return lines


def unverified_section(state):
    lines = ['## 三、没能核实的线索']
    unavailable = [f for f in (state.consensus.facts if state.consensus else ()) if f.status == 'unavailable']
    if unavailable:
        lines.extend(f'- {_clip(fact.observation)}（{fact.source}）' for fact in unavailable)
    else:
        lines.append('- 无')
    return lines


def source_composition_section(state):
    lines = ['## 四、各章来源构成', '',
             '| 章 | 来自你的原话或你确认的决定 | 推断 | 资料事实 |',
             '| --- | --- | --- | --- |']
    draft = state.draft
    if not draft:
        lines.append('（无草案）')
        return lines
    claims = {c.requirement_id: c for c in state.consensus.claims} if state.consensus else {}
    for section in draft.sections:
        human = sum(1 for rid in section.requirement_ids
                    if claims.get(rid) and claims[rid].origin in ('explicit', 'choice')) + len(section.decision_ids)
        inferred = sum(1 for rid in section.requirement_ids
                       if claims.get(rid) and claims[rid].origin in ('derived', 'proposal'))
        facts = len(section.fact_ids)
        tail = '（未绑定来源）' if human == inferred == facts == 0 else ''
        lines.append(f'| {section.title} | {human} | {inferred} | {facts}{tail} |')
    return lines


def advisory_section(state):
    lines = ['## 五、机器检查提示（建议级）']
    titles = {s.section_id: s.title for s in state.draft.sections} if state.draft else {}
    rows = [issue for issue in state.issues if issue.status == 'open' and issue.severity == 'advisory']
    if rows:
        for issue in rows:
            title = titles.get(issue.section_ids[0], '全文') if issue.section_ids else '全文'
            lines.append(f'- [{title}] {_clip(issue.claim)}')
    else:
        lines.append('- 无')
    return lines


def change_section(state):
    lines = ['## 六、本版修改说明']
    summary = (state.draft.change_summary if state.draft else '').strip()
    lines.append('- ' + (_clip(summary) if summary else '首版'))
    return lines


def brief_head(state):
    """Sections 一 and 二 joined; callers truncate to NOTIFICATION_CLIP."""
    return '\n'.join(pending_section(state) + inference_section(state))


def render_review_brief(state: RequirementsState) -> str:
    draft = state.draft
    lines = [f'# 阅读摘要｜需求草案 v{draft.version if draft else 0}', '', BRIEF_NOTE, '']
    for section in (pending_section, inference_section, unverified_section,
                    source_composition_section, advisory_section, change_section):
        lines.extend(section(state))
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'
