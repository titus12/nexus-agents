"""Draft-first: the solver authors a complete draft before any user question (T2)."""
import json
from dataclasses import asdict
import tempfile
import unittest
from orchestrator.app import OrchestratorApp
from orchestrator.transport.external import ExternalMessage
from requirements_test_support import question
from test_requirements_workflow_e2e import Scripted, context, pump


class DraftFirstScript(Scripted):
    def __init__(self, blocking_assumption=False, solver_questions=(), action='DRAFT_READY',
                 analyst_questions=False):
        super().__init__()
        self.blocking_assumption = blocking_assumption
        self.solver_questions = tuple(solver_questions)
        self.action = action
        self.analyst_questions = analyst_questions
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
            if self.drafts == 1 and self.solver_questions:
                body['questions'] = [asdict(question(identity)) for identity in self.solver_questions]
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


if __name__ == '__main__':
    unittest.main()
