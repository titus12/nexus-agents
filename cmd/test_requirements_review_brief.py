"""S3 reader brief: deterministic, id-free, side artifact; notification head (spec 3.3)."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from orchestrator.app import OrchestratorApp
from orchestrator.domain.requirements.document import render_markdown, text_digest
from orchestrator.domain.requirements.model import AnswerRecord, AssumptionRecord, Question, QuestionOption, RequirementClaim
from orchestrator.domain.requirements.provenance import render_provenance
from orchestrator.domain.requirements.review_brief import (NOTIFICATION_CLIP, BRIEF_SUFFIX,
                                                           brief_head, render_review_brief)
from orchestrator.runtime.requirements_notifications import progress_text
from requirements_test_support import make_state, complete_draft, question
from orchestrator.domain.requirements.rubric import INTERNAL_ID_RE
from test_requirements_workflow_e2e import Scripted, context, pump


def pending_question(**overrides):
    row = question()
    return replace(row, **overrides) if overrides else row


def bound_draft(state, bindings):
    """bindings: {kind: (requirement_ids, decision_ids, markdown)}; missing kinds keep fixture values."""
    base = complete_draft(state)
    sections = []
    for section in base.sections:
        reqs, decs, body = bindings.get(section.kind, (section.requirement_ids, section.decision_ids, section.markdown))
        sections.append(replace(section, requirement_ids=reqs, decision_ids=decs, markdown=body))
    draft = replace(base, sections=tuple(sections))
    return replace(draft, document_digest=text_digest(render_markdown(draft)))


def simple_state():
    state = make_state()
    draft = bound_draft(state, {'goals': (('req-000002',), (), '- 失败后自动重试，直到成功（待定）。')})
    claim = RequirementClaim('req-000002', 'auto-retry', '失败后自动重试，直到成功或返回最终错误',
                             origin='derived', source_ids=('src-000001',))
    assumption = AssumptionRecord('assumption-000001', '默认采用自动重试', blocking=False, disposition='accepted')
    first = pending_question()
    second = pending_question(**{'question_id': 'q-000002', 'topic_key': 'assumption',
                                 'default_assumption_id': 'assumption-000001'})
    answers = (AnswerRecord('answer-000001', 'q-000001', first.revision, 'round-000001', (), '', 'defer',
                            'src-000001', 'event-1'),
               AnswerRecord('answer-000002', 'q-000002', second.revision, 'round-000001', (), '', 'accept_default',
                            'src-000001', 'event-2'))
    consensus = replace(state.consensus, claims=state.consensus.claims + (claim,),
                        assumptions=(assumption,))
    return replace(state, draft=draft, consensus=consensus, questions=(first, second), answers=answers)


class BriefRenderTests(unittest.TestCase):
    def test_deterministic_and_complete(self):
        state = simple_state()
        first, second = render_review_brief(state), render_review_brief(state)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith('# 阅读摘要｜需求草案 v2\n'))
        self.assertIn('本文件不是需求正文，不参与批准，也不会交给门下省。', first)
        for heading in ('一、先看这里', '二、我替你做的推断', '三、没能核实的线索',
                        '四、各章来源构成', '五、机器检查提示', '六、本版修改说明'):
            self.assertIn(heading, first)
        self.assertEqual(first.rsplit('## 六、', 1)[-1].strip(), '本版修改说明\n- 首版')

    def test_no_internal_ids_leak(self):
        self.assertIsNone(INTERNAL_ID_RE.search(render_review_brief(simple_state())))
        self.assertIsNone(INTERNAL_ID_RE.search(render_review_brief(make_state())))

    def test_derived_claims_and_assumptions_appear_in_inference_section(self):
        state = simple_state()
        brief = render_review_brief(state)
        head = brief.split('## 三、')[0]
        self.assertIn('## 二、我替你做的推断', head)
        self.assertIn('[目标] 失败后自动重试，直到成功或返回最终错误', head)
        self.assertIn('依据：同一会话的重复登录只触发一次连接', head)
        self.assertIn('- 假设：默认采用自动重试（非阻塞）', head)

    def test_all_explicit_state_has_empty_inference_section(self):
        state = replace(make_state(), draft=complete_draft(make_state()))
        brief = render_review_brief(state)
        inference = brief.split('## 二、我替你做的推断\n')[1].split('## 三、')[0]
        self.assertEqual(inference.strip(), '- 无')

    def test_defer_and_default_answers_land_in_pending_section(self):
        head = brief_head(simple_state())
        self.assertIn('- 你选择暂缓的问题：\n  - 失败后是否自动重试？', head)
        self.assertIn('- 采用了默认假设的问题：\n  - 失败后是否自动重试？；默认假设：默认采用自动重试', head)
        self.assertIn('- 正文待定条目：\n  - 目标：失败后自动重试，直到成功（待定）。', head)

    def test_missing_question_falls_back_without_crash(self):
        state = replace(simple_state(), questions=())
        brief = render_review_brief(state)
        self.assertIn('（问题已不在当前清单）', brief)

    def test_source_composition_counts_and_unbound_marker(self):
        table = render_review_brief(simple_state()).split('## 四、各章来源构成\n')[1].split('## 五、')[0]
        self.assertIn('| 背景 | 0 | 0 | 0（未绑定来源） |', table)
        self.assertIn('| 目标 | 0 | 1 | 0 |', table)
        self.assertIn('| 核心功能清单 | 2 | 0 | 0 |', table)
        self.assertIn('| 验收标准 | 1 | 0 | 0 |', table)
        self.assertIn('| 责任边界 | 0 | 0 | 0（未绑定来源） |', table)

    def test_unavailable_facts_and_advisories_and_change_summary(self):
        from orchestrator.domain.requirements.model import FactRecord, ReviewIssue
        state = simple_state()
        fact = FactRecord('fact-000001', '重试上限未知', 'probe:code-search', '', '', 'unavailable')
        issue = ReviewIssue('issue-000001', 'lint-vague-phrase', ('S_goals',), (), '该条使用了无法判定的表述',
                            '影响', 'advisory', 'solver', '改写')
        draft = state.draft
        consensus = replace(state.consensus, facts=(fact,))
        state = replace(state, consensus=consensus, issues=(issue,),
                        draft=replace(draft, change_summary='补充了重试上限与超时。'))
        brief = render_review_brief(state)
        self.assertIn('## 三、没能核实的线索\n- 重试上限未知（probe:code-search）', brief)
        self.assertIn('## 五、机器检查提示（建议级）\n- [目标] 该条使用了无法判定的表述', brief)
        self.assertIn('## 六、本版修改说明\n- 补充了重试上限与超时。', brief)
        self.assertEqual(render_review_brief(state), render_review_brief(state))

    def test_clipped_texts_stay_bounded(self):
        state = make_state()
        long_claim = replace(state.consensus.claims[0], statement='长' * 300, origin='derived')
        draft = bound_draft(state, {'goals': (('req-000001',), (), '')})
        state = replace(state, draft=draft, consensus=replace(state.consensus, claims=(long_claim,)))
        brief = render_review_brief(state)
        self.assertIn('长' * 120 + '…', brief)
        self.assertNotIn('长' * 121, brief)


class ProgressTextTests(unittest.TestCase):
    def confirm_wait_body(self, state, review_ref='runs/x/requirements/drafts/v002-abc/requirements.md'):
        req = replace(state, review_document_ref=review_ref)
        before = SimpleNamespace(progression=SimpleNamespace(state='REQ_REVIEW_PUBLISH'))
        event = SimpleNamespace(name='REVIEW_DOCUMENT_DELIVERED', payload_mapping={})
        after = SimpleNamespace(progression=SimpleNamespace(state='REQ_CONFIRM_WAIT'),
                                identity=SimpleNamespace(task_id='task-1'), requirements=req)
        return progress_text(before, event, after)

    def test_confirm_wait_progress_carries_brief_head_and_path(self):
        body = self.confirm_wait_body(simple_state())
        self.assertIn('需求评审已通过，请查看需求文档和确认卡片。', body)
        self.assertIn('## 一、先看这里：需要你判断的', body)
        self.assertIn('## 二、我替你做的推断', body)
        self.assertNotIn('## 三、', body)
        self.assertIn('阅读摘要：', body)
        self.assertTrue(body.rstrip().endswith('review-brief.md'))
        self.assertEqual(progress_text(
            SimpleNamespace(progression=SimpleNamespace(state='REQ_REVIEW_PUBLISH')),
            SimpleNamespace(name='REVIEW_DOCUMENT_DELIVERED', payload_mapping={}),
            SimpleNamespace(progression=SimpleNamespace(state='REQ_CONFIRM_WAIT'),
                            identity=SimpleNamespace(task_id='task-1'),
                            requirements=replace(simple_state(), review_document_ref=''))),
            progress_text(
                SimpleNamespace(progression=SimpleNamespace(state='REQ_REVIEW_PUBLISH')),
                SimpleNamespace(name='REVIEW_DOCUMENT_DELIVERED', payload_mapping={}),
                SimpleNamespace(progression=SimpleNamespace(state='REQ_CONFIRM_WAIT'),
                                identity=SimpleNamespace(task_id='task-1'),
                                requirements=replace(simple_state(), review_document_ref=''))))

    def test_long_brief_head_is_clipped_with_suffix(self):
        state = make_state()
        pending = '\n'.join(f'- 场景{n}的通过条件待定，需要用户给出明确阈值、明确观测口径与判定时间窗口'
                            for n in range(60))
        draft = bound_draft(state, {'goals': ((), (), pending)})
        body = self.confirm_wait_body(replace(state, draft=draft), review_ref='')
        head = body.split('需求评审已通过，请查看需求文档和确认卡片。\n\n', 1)[1]
        clipped, marker, rest = head.partition(BRIEF_SUFFIX)
        self.assertTrue(marker)
        self.assertLessEqual(len(clipped), NOTIFICATION_CLIP)
        self.assertIn('阅读摘要：见需求文档同目录 review-brief.md', rest)

    def test_short_brief_head_is_not_clipped(self):
        body = self.confirm_wait_body(replace(make_state(), draft=complete_draft(make_state())), review_ref='')
        self.assertNotIn(BRIEF_SUFFIX, body)
        self.assertIn('阅读摘要：见需求文档同目录 review-brief.md', body)


class BriefPublishTests(unittest.TestCase):
    def test_materialize_publishes_brief_without_touching_the_body(self):
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(context(), directory, multica=Scripted(), poll_interval=0, timeout_seconds=2)
            pump(app, 'REQ_CONFIRM_WAIT')
            before = app.ctx.requirements.draft.document_digest
            drafts = list(Path(directory).glob('**/requirements/drafts/*/review-brief.md'))
            self.assertEqual(len(drafts), 1)
            body = drafts[0].parent / 'requirements.md'
            self.assertEqual(text_digest(body.read_text(encoding='utf-8')), before)
            manifest = json.loads((drafts[0].parent / 'manifest.json').read_text(encoding='utf-8'))
            self.assertIn('review-brief.md', manifest['files'])
            self.assertEqual(drafts[0].read_text(encoding='utf-8'), render_review_brief(app.ctx.requirements))
            req = app.ctx.requirements
            app.requirements_runtime.submit('REQUIREMENTS_APPROVED', {'actor_id': 'local-operator',
                'gate_id': req.review_gate_id, 'document_digest': req.draft.document_digest}, 'approval-1')
            pump(app, 'DONE')
            approved = Path(app.ctx.requirements.published_ref)
            self.assertTrue((approved / 'review-brief.md').is_file())
            self.assertEqual(text_digest((approved / 'requirements.md').read_text(encoding='utf-8')), before)
            self.assertEqual((approved / 'review-brief.md').read_text(encoding='utf-8'),
                             drafts[0].read_text(encoding='utf-8'))
            self.assertNotEqual(render_provenance(app.ctx.requirements), '')


if __name__ == '__main__':
    unittest.main()
