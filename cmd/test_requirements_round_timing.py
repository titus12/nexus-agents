"""Round timing summary: parsing and aggregation of engine log lines."""
import tempfile
import unittest
from pathlib import Path

import requirements_round_timing as timing

TASK = 'task-20261013-abcdef'
LINES = [
    "2026-10-13T10:00:00+0800 [INFO] review_orchestrator_fsm - DOMAIN_TRANSITION task_id=TASK transition_id=TASK:1 event=START before=REQ_INTAKE:0 after=REQ_ANALYZE:1 action=START reason=START effects=2 failure=''",
    "2026-10-13T10:05:00+0800 [INFO] review_orchestrator_fsm - AGENT_RUN_SUMMARY task_id=TASK request_id=TASK:e1:1:REQ_ANALYZE:attempt-1-scout-intent role=review-analyst state=REQ_ANALYZE admit_wait_s=0.0 dispatch_to_running_s=5.0 run_s=290.0 result_s=1.0 total_s=296.0 polls=20 status_s=1.5 last_status=COMPLETED outcome=ANALYSIS_READY failure=-",
    "2026-10-13T10:06:00+0800 [INFO] review_orchestrator_fsm - DOMAIN_TRANSITION task_id=TASK transition_id=TASK:2 event=ANALYSIS_READY before=REQ_ANALYZE:1 after=REQ_DRAFT:2 action=ANALYSIS_ACCEPTED reason=ANALYSIS_ACCEPTED effects=2 failure=''",
    "2026-10-13T10:07:00+0800 [INFO] review_orchestrator_fsm - DOMAIN_TRANSITION task_id=TASK transition_id=TASK:3 event=DRAFT_READY before=REQ_DRAFT:2 after=REQ_REVIEW:3 action=DRAFT_READY reason=DRAFT_READY effects=2 failure=''",
    "2026-10-13T10:10:00+0800 [INFO] review_orchestrator_fsm - DOMAIN_TRANSITION task_id=TASK transition_id=TASK:4 event=REVIEW_READY before=REQ_REVIEW:3 after=REQ_RETRY_WAIT:4 action=RETRYABLE_FAILURE reason=RETRYABLE_FAILURE effects=2 failure='RECLASSIFICATION_BASIS_REQUIRED: closure x needs severity advisory'",
    "2026-10-13T10:10:15+0800 [INFO] review_orchestrator_fsm - DOMAIN_TRANSITION task_id=TASK transition_id=TASK:5 event=RETRY_READY before=REQ_RETRY_WAIT:4 after=REQ_REVIEW:5 action=RESUME reason=RESUME effects=2 failure=''",
    "2026-10-13T10:20:00+0800 [INFO] review_orchestrator_fsm - DOMAIN_TRANSITION task_id=TASK transition_id=TASK:6 event=REVIEW_READY before=REQ_REVIEW:5 after=REQ_HANDOFF:6 action=HANDOFF reason=HANDOFF effects=1 failure=''",
]


class RoundTimingTests(unittest.TestCase):
    def test_summary_counts_states_and_retry_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = '\n'.join(line.replace('TASK', TASK) for line in LINES) + '\n'
            Path(tmp, 'orchestrator-20261013-10.log').write_text(text, encoding='utf-8')
            rows = timing.load_events(Path(tmp), TASK)
            summary = timing.build_summary(TASK, rows)
        self.assertEqual(summary['final_state'], 'REQ_HANDOFF')
        self.assertEqual(summary['span_seconds'], 1200.0)
        self.assertEqual(summary['counts']['critic_retries'], 1)
        self.assertEqual(summary['counts']['agent_runs'], 1)
        self.assertTrue(summary['retries'][0]['failure'].startswith('RECLASSIFICATION_BASIS_REQUIRED'))
        self.assertEqual([row['state'] for row in summary['failures']], ['REQ_RETRY_WAIT'])
        states = {row['state']: row for row in summary['states']}
        self.assertEqual(states['REQ_ANALYZE']['seconds'], 360.0)
        self.assertEqual(states['REQ_REVIEW']['entries'], 2)
        self.assertEqual(states['REQ_REVIEW']['seconds'], 765.0)
        self.assertEqual(summary['agent_runs'][0]['run_s'], '290.0')

    def test_blocked_preflight_reports_failure_reason(self):
        line = ("2026-10-13T10:00:00+0800 [INFO] review_orchestrator_fsm - DOMAIN_TRANSITION "
                "task_id=TASK transition_id=TASK:7 event=PUBLISH before=REQ_CLARIFY_PUBLISH:6 "
                "after=REQ_BLOCKED:7 action=PREREQUISITE_MISSING reason=PREREQUISITE_MISSING effects=0 "
                "failure='DECISION_OWNER_CHAT_OR_READY_CARD_GATEWAY_MISSING: ready card gateway'")
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'orchestrator-20261013-10.log').write_text(line.replace('TASK', TASK) + '\n', encoding='utf-8')
            summary = timing.build_summary(TASK, timing.load_events(Path(tmp), TASK))
        self.assertEqual([row['state'] for row in summary['failures']], ['REQ_BLOCKED'])
        self.assertIn('ready card gateway', summary['failures'][0]['failure'])
        self.assertEqual(summary['retries'], [])

    def test_no_transitions_returns_error_summary(self):
        summary = timing.build_summary(TASK, [])
        self.assertIn('error', summary)


if __name__ == '__main__':
    unittest.main()
