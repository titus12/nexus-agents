from __future__ import annotations

from pathlib import Path
import unittest


class NoLegacyProductionReferencesTests(unittest.TestCase):
    def test_production_orchestrator_has_one_fsm_surface(self):
        root = Path(__file__).parent / "orchestrator"
        files = [
            path for path in root.rglob("*.py")
            if path.name != "migration.py"
        ]
        source = "\n".join(path.read_text(encoding="utf-8") for path in files)
        root_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in files
            if path.parent == root
        )
        forbidden = (
            "StateContext",
            "StateMachine",
            "BaseState",
            "TransitionPolicy",
            "JsonStateStore",
        )
        found = [token for token in forbidden if token in source]
        found.extend(
            token for token in (
                "from .states", "from .transitions", "from .context", "from .events",
                "from .persistence", "from .recovery", "from .concurrency",
            )
            if token in root_source
        )
        self.assertEqual(found, [], f"legacy production references remain: {found}")


if __name__ == "__main__":
    unittest.main()
