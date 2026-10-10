"""Provenance report: deterministic mapping, unavailable facts, body untouched (T4)."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from orchestrator.app import OrchestratorApp
from orchestrator.domain.requirements.document import render_markdown, text_digest
from orchestrator.domain.requirements.model import AssumptionRecord, FactRecord, Question, QuestionOption
from orchestrator.domain.requirements.provenance import render_provenance
from requirements_test_support import make_state, complete_draft, question
from test_requirements_workflow_e2e import Scripted, context, pump


def fact_row(status='observed'):
    fingerprint = 'fp1234567890abcdef' if status == 'observed' else ''
    source = 'shared/session.py:12' if status == 'observed' else 'probe:code-search'
    quote = 'shared session' if status == 'observed' else ''
    return FactRecord('fact-000001', '入口为共享会话', source, quote, fingerprint, status, ('req-000001',))


def bound_state(fact=None, assumption=None, pending_question=None):
    state = make_state()
    draft = complete_draft(state)
    facts = (fact,) if fact else ()
    consensus = replace(state.consensus, facts=facts,
                        assumptions=(assumption,) if assumption else ())
    if fact:
        draft = replace(draft, sections=tuple(
            replace(s, fact_ids=('fact-000001',)) if s.kind == 'acceptance' else s for s in draft.sections))
        draft = replace(draft, document_digest=text_digest(render_markdown(draft)))
    questions = (pending_question,) if pending_question else ()
    return replace(state, draft=draft, consensus=consensus, questions=questions)


class ProvenanceRenderTests(unittest.TestCase):
    def test_deterministic_and_maps_claims_decisions_facts(self):
        state = bound_state(fact=fact_row())
        first, second = render_provenance(state), render_provenance(state)
        self.assertEqual(first, second)
        draft = state.draft
        self.assertIn('- 正文哈希：' + draft.document_digest[:12], first)
        self.assertIn('- 共识哈希：' + state.consensus.digest[:12], first)
        self.assertIn('- 草案版本：v%d' % draft.version, first)
        self.assertIn('### 验收标准（S_acceptance）', first)
        self.assertIn('- 需求 req-000001（explicit）：同一会话的重复登录只触发一次连接', first)
        self.assertIn('- 来源 src-000001：同一会话的重复登录只触发一次连接', first)
        self.assertIn('- 决定 decision-000001：同一会话的重复登录只触发一次连接（答案来源 src-000001）', first)
        self.assertIn('- 事实 fact-000001：入口为共享会话（source：shared/session.py:12；'
                      '引文：shared session；指纹 fp1234567890）', first)

    def test_long_text_is_clipped(self):
        state = make_state()
        long_claim = replace(state.consensus.claims[0], statement='长' * 300)
        state = replace(state, draft=complete_draft(state),
                        consensus=replace(state.consensus, claims=(long_claim,)))
        report = render_provenance(state)
        self.assertIn('长' * 120 + '…', report)
        self.assertNotIn('长' * 121, report)

    def test_unavailable_facts_are_listed_as_unverified(self):
        state = bound_state(fact=fact_row('unavailable'))
        report = render_provenance(state)
        self.assertIn('## 未能核实的线索', report)
        self.assertIn('- fact-000001：入口为共享会话（source：probe:code-search）', report)

    def test_assumptions_and_pending_questions_are_listed(self):
        assumption = AssumptionRecord('assumption-000001', '数据协议版本', blocking=True)
        pending = question()
        state = bound_state(assumption=assumption, pending_question=pending)
        report = render_provenance(state)
        self.assertIn('## 假设', report)
        self.assertIn('- assumption-000001：数据协议版本（阻塞：是；处置：proposed）', report)
        self.assertIn('## 未决问题', report)
        self.assertIn('- q-000001：失败后是否自动重试？', report)

    def test_empty_ledgers_render_placeholder_rows(self):
        report = render_provenance(make_state())
        self.assertIn('- 无', report)
        self.assertEqual(report, render_provenance(make_state()))


class ProvenancePublishTests(unittest.TestCase):
    def test_published_directories_carry_provenance_without_touching_the_body(self):
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(context(), directory, multica=Scripted(), poll_interval=0, timeout_seconds=2)
            pump(app, 'REQ_CONFIRM_WAIT')
            before = app.ctx.requirements.draft.document_digest
            draft_reports = list(Path(directory).glob('**/requirements/drafts/*/provenance.md'))
            self.assertEqual(len(draft_reports), 1)
            body = draft_reports[0].parent / 'requirements.md'
            self.assertEqual(text_digest(body.read_text(encoding='utf-8')), before)
            self.assertEqual(body.read_text(encoding='utf-8'), render_markdown(app.ctx.requirements.draft))
            req = app.ctx.requirements
            app.requirements_runtime.submit('REQUIREMENTS_APPROVED', {'actor_id': 'local-operator',
                'gate_id': req.review_gate_id, 'document_digest': req.draft.document_digest}, 'approval-1')
            pump(app, 'DONE')
            approved = Path(app.ctx.requirements.published_ref)
            self.assertTrue((approved / 'provenance.md').is_file())
            self.assertEqual(text_digest((approved / 'requirements.md').read_text(encoding='utf-8')), before)
            self.assertEqual(app.ctx.requirements.draft.document_digest, before)
            self.assertEqual((approved / 'provenance.md').read_text(encoding='utf-8'),
                             draft_reports[0].read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
