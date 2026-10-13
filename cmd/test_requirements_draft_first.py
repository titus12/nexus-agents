"""Draft-first: the solver authors a complete draft before any user question (T2)."""
import json
from dataclasses import asdict
import tempfile
import unittest
from unittest.mock import patch
from orchestrator.app import OrchestratorApp
from orchestrator.transport.external import ExternalMessage
from requirements_test_support import question
from test_requirements_workflow_e2e import Scripted, context, pump


class DraftFirstScript(Scripted):
    def __init__(self, blocking_assumption=False, solver_questions=(), action='DRAFT_READY',
                 analyst_questions=False, every_draft_questions=False):
        super().__init__()
        self.blocking_assumption = blocking_assumption
        self.solver_questions = tuple(solver_questions)
        self.action = action
        self.analyst_questions = analyst_questions
        self.every_draft_questions = every_draft_questions
        self.analyses = 0
        self.drafts = 0

    def dispatch(self, request):
        receipt = super().dispatch(request)
        body = self.replies[request.request_id][0].payload
        if request.target_state == 'REQ_ANALYZE':
            self.analyses += 1
            if self.analyses == 1 and self.blocking_assumption:
                body['analysis']['assumptions'] = [{'local_key': 'assumption-retry',
                    'statement': '失败后自动重试', 'blocking': True, 'disposition': 'proposed'}]
            if self.analyses == 1 and self.analyst_questions:
                body['questions'] = [asdict(question())]
        elif request.target_state == 'REQ_DRAFT':
            self.drafts += 1
            body['action'] = self.action if self.drafts == 1 else 'DRAFT_READY'
            if self.every_draft_questions:
                identity = self.solver_questions[self.drafts - 1 if self.drafts <= len(self.solver_questions) else -1]
                identities = (identity,)
            else:
                identities = self.solver_questions if self.drafts == 1 else ()
            if identities:
                rows = []
                for item in identities:
                    row = asdict(question(item))
                    if self.every_draft_questions:
                        # Distinct topic per wave: merge_questions dedupes by
                        # content (topic_key+text+options), so a verbatim re-ask
                        # would be dropped instead of opening the next round.
                        row['topic_key'] = 'retry-wave-%d' % self.drafts
                        row['question'] = row['question'] + '（第%d轮补充）' % self.drafts
                    rows.append(row)
                body['questions'] = rows
            consensus = request.context['consensus']
            for section in body['document']['sections']:
                if section['kind'] in ('behavior', 'acceptance'):
                    section['requirement_ids'] = [c['requirement_id'] for c in consensus['claims'] if c['status'] == 'active']
                    section['markdown'] += ' '.join(c['statement'] for c in consensus['claims'] if c['status'] == 'active')
                if section['kind'] == 'behavior':
                    section['decision_ids'] = consensus['active_decision_ids']
        self.replies[request.request_id][0] = ExternalMessage(request.agent_id,
                                                              json.loads(json.dumps(body)), request.request_id)
        return receipt


def submit_answers(app, option='confirm_assumption'):
    req = app.ctx.requirements
    app.requirements_runtime.submit('ANSWERS_SUBMITTED', {'actor_id': 'local-operator',
        'round_id': req.rounds[-1].round_id,
        'answers': [{'question_id': identity, 'question_revision': 1, 'option_ids': [option]}
                    for identity in req.rounds[-1].question_ids]}, 'answer-1')
    app.step()


class DraftFirstTests(unittest.TestCase):
    def test_blocking_assumption_becomes_question_only_after_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(context(), directory, multica=DraftFirstScript(blocking_assumption=True),
                                  poll_interval=0, timeout_seconds=2)
            pump(app, 'REQ_CLARIFY_WAIT')
            req = app.ctx.requirements
            self.assertEqual(req.draft.version, 2)
            self.assertEqual(req.rounds[-1].return_to, 'REQ_DRAFT')
            self.assertTrue(any(q.default_assumption_id == 'assumption-000001' for q in req.questions))
            submit_answers(app)
            pump(app, 'REQ_REVIEW')
            self.assertEqual(app.ctx.requirements.consensus.assumptions[0].disposition, 'accepted')

    def test_draft_ready_with_questions_commits_draft_then_opens_round(self):
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(context(), directory, multica=DraftFirstScript(solver_questions=('q-000001',)),
                                  poll_interval=0, timeout_seconds=2)
            pump(app, 'REQ_CLARIFY_WAIT')
            req = app.ctx.requirements
            self.assertEqual(req.draft.version, 2)
            self.assertEqual(req.rounds[-1].return_to, 'REQ_DRAFT')
            submit_answers(app, 'manual')
            pump(app, 'REQ_REVIEW')
            self.assertEqual(len(app.ctx.requirements.rounds), 1)

    def test_needs_clarification_never_commits_a_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(context(), directory,
                                  multica=DraftFirstScript(action='NEEDS_CLARIFICATION', solver_questions=('q-000001',)),
                                  poll_interval=0, timeout_seconds=2)
            pump(app, 'REQ_CLARIFY_WAIT')
            req = app.ctx.requirements
            self.assertEqual(req.draft.version, 1)
            self.assertEqual(req.rounds[-1].return_to, 'REQ_DRAFT')
            submit_answers(app, 'manual')
            pump(app, 'REQ_REVIEW')
            self.assertGreaterEqual(app.ctx.requirements.draft.version, 2)

    def test_analyst_questions_still_reach_the_user_as_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(context(), directory, multica=DraftFirstScript(analyst_questions=True),
                                  poll_interval=0, timeout_seconds=2)
            pump(app, 'REQ_CLARIFY_WAIT')
            req = app.ctx.requirements
            self.assertEqual(req.draft.version, 1)
            self.assertEqual(req.rounds[-1].return_to, 'REQ_ANALYZE')
            submit_answers(app, 'manual')
            pump(app, 'REQ_REVIEW')
            self.assertEqual(len(app.ctx.requirements.rounds), 1)

    def test_clarify_round_cap_stops_new_rounds_after_limit(self):
        script_questions = ('q-000001', 'q-000002')
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict('os.environ', {'REQUIREMENTS_CLARIFY_ROUND_CAP': '1'}):
                app = OrchestratorApp(context(), directory,
                                      multica=DraftFirstScript(solver_questions=script_questions,
                                                               every_draft_questions=True),
                                      poll_interval=0, timeout_seconds=2)
                pump(app, 'REQ_CLARIFY_WAIT')
                submit_answers(app, 'manual')
                pump(app, 'REQ_REVIEW')
            self.assertEqual(len(app.ctx.requirements.rounds), 1)
            self.assertEqual(app.ctx.requirements.draft.version, 3)

    def test_without_cap_a_fresh_draft_question_opens_another_round(self):
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(context(), directory,
                                  multica=DraftFirstScript(solver_questions=('q-000001', 'q-000002'),
                                                           every_draft_questions=True),
                                  poll_interval=0, timeout_seconds=2)
            pump(app, 'REQ_CLARIFY_WAIT')
            submit_answers(app, 'manual')
            pump(app, 'REQ_CLARIFY_WAIT')
            self.assertEqual(len(app.ctx.requirements.rounds), 2)


if __name__ == '__main__':
    unittest.main()
