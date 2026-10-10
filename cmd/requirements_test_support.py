"""Independent test fixtures. Production must never import this module."""
import os
# Scripted flows dispatch one analyst reply per effect; the scout fan-out is
# covered by test_requirements_scouts with injected configs. setdefault keeps
# an explicit operator override (e.g. a fan-out e2e test) in charge.
os.environ.setdefault('REQUIREMENTS_SCOUT', '0')
from dataclasses import asdict, replace
from orchestrator.domain.requirements.model import *
from orchestrator.domain.requirements.document import render_markdown, KIND_TITLES
from orchestrator.domain.requirements.review import AXES


def make_state():
    source = SourceRecord('src-000001', 'request', '同一会话的重复登录只触发一次连接')
    claim = RequirementClaim('req-000001', 'duplicate-login', source.text,
                             source_ids=(source.source_id,), decision_ids=('decision-000001',))
    decision = DecisionRecord('decision-000001', claim.topic_key, claim.statement, source.source_id,
                              requirement_ids=(claim.requirement_id,))
    consensus = ConsensusRevision(claims=(claim,), active_decision_ids=(decision.decision_id,))
    return RequirementsState(sources=(source,), decisions=(decision,), consensus=consensus)


def complete_draft(state, replace_section=None):
    bodies = {'background': '重复登录可能重复连接。', 'goals': '重复登录只建立一次连接。',
              'constraints': '调用者负责传入会话标识。', 'behavior': '同一会话的重复登录只触发一次连接。',
              'scope': '以会话标识区分连接。', 'acceptance': '同一会话并发登录时只观察到一次连接建立。',
              'non_goals': '不修改其他会话的行为。'}
    assert set(bodies) == set(CORE_SECTIONS), 'fixture chapters must match CORE_SECTIONS exactly'
    sections = tuple(DraftSection('S_'+kind, kind, KIND_TITLES[kind], bodies[kind],
                                  ('req-000001',) if kind in ('behavior', 'acceptance') else (),
                                  ('decision-000001',) if kind == 'behavior' else ())
                     for kind in CORE_SECTIONS)
    if replace_section:
        sections = tuple(replace_section if s.section_id == replace_section.section_id else s for s in sections)
    draft = DraftRevision(2, '重复登录需求', sections, state.consensus.digest, '0'*64)
    return replace(draft, document_digest=text_digest(render_markdown(draft)))


def review_payload(issues=(), closures=()):
    return {'issues': list(issues), 'closures': list(closures),
            'coverage': [{'axis': axis, 'section_ids': ['S_'+kind for kind in CORE_SECTIONS],
                          'conclusion': '对照原始请求及来源，没有该轴的遗漏。'} for axis in AXES]}


def question(identity='q-000001', depends=(), blocking=True):
    return Question(identity, 'retry', '失败后是否自动重试？', '请求没有指定失败行为', '决定失败后是否会新增连接',
                    options=(QuestionOption('manual', '由业务触发'), QuestionOption('automatic', '自动重试')),
                    blocking=blocking, depends_on_question_ids=depends)
