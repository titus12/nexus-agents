from __future__ import annotations

import tempfile

from orchestrator.app import OrchestratorApp
from test_linear_fsm_entrypoint import _ScriptedMultica, _context


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        adapter = _ScriptedMultica()
        app = OrchestratorApp(
            _context(),
            root=directory,
            multica=adapter,
            poll_interval=0,
            timeout_seconds=5,
        )
        ok = app.run()
        snapshot = app.repository.load("task-entry")
        progression = snapshot.context.progression
        print("run ok:", ok)
        print("state:", progression.state, "seq:", progression.sequence)
        failure = snapshot.context.recovery.last_failure
        if failure is not None:
            print("last_failure:", failure.error_code, "|", failure.message[:300])
        for r in adapter.dispatched:
            print("dispatch:", r.target_state, r.request_id, str(r.context.get("group_id")), str(r.context.get("stage")))


main()
