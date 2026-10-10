import json
from pathlib import Path
import tempfile
import unittest
from orchestrator.app import OrchestratorApp
from orchestrator.requirements_metrics import (measure, evaluate, assess_quality, QUALITY_AXES,
                                               failure_origin)
from test_requirements_workflow_e2e import context, Scripted, pump


class MetricsTests(unittest.TestCase):
    def test_actual_three_calls_and_null_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            app=OrchestratorApp(context(),directory,multica=Scripted(),poll_interval=0,timeout_seconds=2)
            pump(app,'REQ_CONFIRM_WAIT')
            report=measure(app.root)
            self.assertEqual(sum(report['role_calls'].values()),3)
            self.assertEqual(report['menxia_role_calls'],{})
            self.assertIsNone(report['provider_usage'])
            self.assertGreaterEqual(report['first_review_ready_seconds'],report['first_complete_draft_seconds'])
            self.assertEqual(report['failure_origins'],{})
            self.assertEqual(report['mechanical_failures'],0)
            self.assertEqual(report['agent_dispatch_attempts'],3)
            self.assertEqual(report['mechanical_failure_rate'],0.0)
            case={'case_id':'singleflight'}
            self.assertEqual(assess_quality(case,app.root)['status'],'INCOMPLETE')
            review={'reviewer':'engineer','document_digest':app.ctx.requirements.draft.document_digest,
                'scores':{axis:4 for axis in QUALITY_AXES},'findings':[],
                'clause_evidence':['behavior/acceptance cover concurrent requests']}
            path=app.root/'manual-review.json'
            path.write_text(json.dumps(review),encoding='utf-8')
            self.assertEqual(assess_quality(case,app.root)['status'],'INCOMPLETE')
            review['business_confirmed']=True
            review['findings']=[{'critical':True,'reason':'missing callback behavior'}]
            path.write_text(json.dumps(review),encoding='utf-8')
            self.assertEqual(assess_quality(case,app.root)['status'],'FAIL')

    def test_missing_samples_never_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            report=evaluate(Path(__file__).parent/'fixtures'/'requirements',directory)
            self.assertEqual(report['status'],'INCOMPLETE')
            self.assertEqual(len(report['cases']),3)

    def test_failure_origin_classification(self):
        self.assertEqual(failure_origin('ROUND_TIMEOUT'),'bot_driver')
        self.assertEqual(failure_origin('LAUNCH_FAILED'),'bot_driver')
        self.assertEqual(failure_origin('AGENT_REPLY_OUTPUT_OVERFLOW'),'infrastructure')
        self.assertEqual(failure_origin('AGENT_TIMEOUT'),'infrastructure')
        self.assertEqual(failure_origin('PERSISTENCE_ERROR'),'infrastructure')
        self.assertEqual(failure_origin('AGENT_REPLY_VALIDATION_FAILED'),'product')
        self.assertEqual(failure_origin('AGENT_REPLY_CONTRACT_REJECTED'),'product')
        self.assertEqual(failure_origin('FACT_EVIDENCE_INVALID'),'product')
        self.assertEqual(failure_origin('MODEL_CALL_BUDGET_EXHAUSTED'),'product')
        self.assertEqual(failure_origin(''),'product')
        self.assertEqual(failure_origin(None),'product')
