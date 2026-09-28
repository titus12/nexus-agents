from __future__ import annotations

import tempfile

from orchestrator.app import OrchestratorApp
from test_zhongshu_convergence_e2e import _ConvergingMultica, _context


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        adapter = _ConvergingMultica()
        app = OrchestratorApp(
            _context(),
            root=directory,
            multica=adapter,
            poll_interval=0,
            timeout_seconds=5,
        )
        ok = app.run()
        snapshot = app.repository.load("task-e2e")
        progression = snapshot.context.progression
        print("run ok:", ok)
        print("state:", progression.state, "seq:", progression.sequence)
        print("blocked:", snapshot.context.recovery.blocked_reason)
        failure = snapshot.context.recovery.last_failure
        if failure is not None:
            print("last_failure:", failure.error_code, "|", failure.message[:400])
        targets = [getattr(r, "target_state", None) for r in adapter.dispatched]
        print("dispatch targets:", targets)
        import pathlib

        journal = pathlib.Path(directory) / "tasks" / "task-e2e" / "workflow-events.jsonl"
        if not journal.exists():
            candidates = list(pathlib.Path(directory).rglob("workflow-events.jsonl"))
            print("journal candidates:", candidates)
            journal = candidates[0] if candidates else None
        if journal is not None:
            lines = journal.read_text(encoding="utf-8").splitlines()
            for line in lines[-15:]:
                print(line[:300])


main()
