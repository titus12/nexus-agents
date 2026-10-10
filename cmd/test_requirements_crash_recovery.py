"""Fault injection through the production app and durable repositories."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from orchestrator.app import OrchestratorApp
from orchestrator.runtime.requirements_effects import immutable_write
from test_requirements_workflow_e2e import context, Scripted, pump


class CrashRecoveryTests(unittest.TestCase):
    def test_result_is_recovered_after_notification_is_lost(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=Scripted()
            app=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            app.step()
            app.step()  # Result committed, no result event has been delivered yet.
            self.assertEqual(len(adapter.dispatched),1)
            restored=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            pump(restored,'REQ_CONFIRM_WAIT')
            self.assertEqual(len(adapter.dispatched),3)
            self.assertEqual(len(restored.ctx.requirements.decisions),1)
            self.assertEqual(restored.ctx.requirements.budget.calls,3)

    def test_prior_human_feedback_supersedes_durable_model_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=Scripted()
            app=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            app.step()
            app.step()
            app.requirements_runtime.submit('REQUIREMENTS_FEEDBACK',{'actor_id':'local-operator','text':'仅限制同一会话。'},'human-first')
            restored=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            restored.step()
            self.assertEqual(restored.ctx.progression.state,'REQ_DRAFT')
            self.assertEqual(restored.ctx.requirements.sources[-1].text,'仅限制同一会话。')
            restored.step()  # Old Analyst completion is stale, not applied to consensus.
            self.assertEqual(restored.ctx.progression.state,'REQ_DRAFT')
            self.assertEqual(restored.ctx.requirements.consensus.claims,())
            self.assertEqual(restored.ctx.requirements.consensus.pending_source_ids,('src-000001','src-000002'))

    def test_snapshot_replacement_failure_recovers_committed_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            app=OrchestratorApp(context(),directory,multica=Scripted(),poll_interval=0,timeout_seconds=2)
            app._ensure_start_event()
            event=app.inbox.next(context().identity.task_id)
            from orchestrator.domain.errors import PersistenceError
            with patch.object(app.repository,'_atomic_write',side_effect=PersistenceError('injected snapshot failure')):
                with self.assertRaises(Exception):
                    app.engine.dispatch(event)
            restored=OrchestratorApp(context(),directory,multica=Scripted(),poll_interval=0,timeout_seconds=2)
            self.assertEqual(restored.ctx.progression.state,'REQ_ANALYZE')
            self.assertEqual(restored.ctx.requirements.budget.calls,1)
            self.assertEqual([effect.request.effect_type for effect in
                restored.repository.pending_effects(context().identity.task_id)],
                ['requirements_progress','agent_dispatch'])
            pump(restored,'REQ_CONFIRM_WAIT')
            self.assertEqual(restored.ctx.requirements.budget.calls,3)

    def test_restart_after_handoff_keeps_same_input_and_does_not_republish(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=Scripted()
            app=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            pump(app,'REQ_CONFIRM_WAIT')
            req=app.ctx.requirements
            app.requirements_runtime.submit('REQUIREMENTS_APPROVED',{'actor_id':'local-operator',
                'gate_id':req.review_gate_id,'document_digest':req.draft.document_digest},'approve')
            pump(app,'MENXIA_REQUIREMENTS_INTAKE')
            approved_ref=app.ctx.requirements.published_ref
            body=Path(approved_ref,'requirements.md').read_bytes()
            restored=OrchestratorApp(context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            pump(restored,'DONE')
            self.assertEqual(restored.ctx.requirements.published_ref,approved_ref)
            self.assertEqual(Path(approved_ref,'requirements.md').read_bytes(),body)
            self.assertEqual(restored.ctx.review.approved_requirements_input.markdown.encode(),body)
            self.assertEqual(len(list(Path(approved_ref).parent.iterdir())),1)

    def test_interrupted_materialization_can_replay_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'requirements.md'
            with patch('orchestrator.runtime.requirements_effects.os.replace',side_effect=OSError('interrupted rename')):
                with self.assertRaises(OSError):
                    immutable_write(path,b'approved body\n')
            immutable_write(path,b'approved body\n')
            immutable_write(path,b'approved body\n')
            with self.assertRaisesRegex(ValueError,'COLLISION'):
                immutable_write(path,b'altered\n')
