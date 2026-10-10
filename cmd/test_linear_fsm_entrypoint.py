"""Current sole-entry scripted app verification."""
from test_requirements_workflow_e2e import context as _context, Scripted as _ScriptedMultica
import tempfile, unittest
from orchestrator.app import OrchestratorApp
from test_requirements_workflow_e2e import pump
class LinearEntrypointTests(unittest.TestCase):
    def test_default_entry_requires_user_approval_before_menxia(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=_ScriptedMultica()
            app=OrchestratorApp(_context(),directory,multica=adapter,poll_interval=0,timeout_seconds=2)
            pump(app,'REQ_CONFIRM_WAIT')
            self.assertIsNone(app.ctx.requirements.approval)
            self.assertFalse(any(r.target_state.startswith('MENXIA_') for r in adapter.dispatched))
