from __future__ import annotations

import importlib
import unittest


def _load(module_name: str, attribute: str):
    try:
        module = importlib.import_module(module_name)
        return getattr(module, attribute)
    except (ImportError, AttributeError) as error:
        raise AssertionError(
            f"architecture contract is not implemented: "
            f"{module_name}.{attribute}: {error}"
        ) from error


class LinearFsmArchitectureContractTests(unittest.TestCase):
    def test_system_state_preserves_resume_state(self):
        TaskIdentity = _load("orchestrator.domain.context", "TaskIdentity")
        ProgressState = _load("orchestrator.domain.context", "ProgressState")
        WorkflowContext = _load("orchestrator.domain.context", "WorkflowContext")
        HumanGateState = _load("orchestrator.domain.context", "HumanGateState")

        identity = TaskIdentity("task-1", "issue-1", "demo", "request-1")
        context = WorkflowContext(
            identity=identity,
            progression=ProgressState(
                state="HUMAN_GATE",
                sequence=3,
                entered_at="2026-09-09T00:00:00Z",
                resume_state="ZHONGSHU_SOLVER",
            ),
            human_gate=HumanGateState(
                decision_id="decision-1",
                reason_code="CRITIC_BLOCKED",
                resume_state="ZHONGSHU_SOLVER",
            ),
        )

        self.assertEqual(context.progression.state, "HUMAN_GATE")
        self.assertEqual(context.progression.resume_state, "ZHONGSHU_SOLVER")
        self.assertEqual(context.human_gate.resume_state, "ZHONGSHU_SOLVER")

    def test_node_context_is_not_main_workflow_context(self):
        TaskIdentity = _load("orchestrator.domain.context", "TaskIdentity")
        ProgressState = _load("orchestrator.domain.context", "ProgressState")
        WorkflowContext = _load("orchestrator.domain.context", "WorkflowContext")
        NodeContext = _load("orchestrator.runtime.nodes", "NodeContext")
        WorkerBinding = _load("orchestrator.runtime.nodes", "WorkerBinding")

        workflow_context = WorkflowContext(
            identity=TaskIdentity("task-1", "issue-1", "demo", "request-1"),
            progression=ProgressState(
                state="ZHONGSHU_ANALYST",
                sequence=2,
                entered_at="2026-09-09T00:00:00Z",
            ),
        )
        node_context = NodeContext(
            task_id="task-1",
            node_run_id="node-1",
            revision_id="revision-1",
            group_id=None,
            item_id=None,
        )
        binding = WorkerBinding(
            worker_id="analyst-1",
            agent_id="agent-1",
            task_id="task-1",
            request_id="request-1",
            role="ANALYST",
            phase="ZHONGSHU",
        )

        self.assertIsNot(node_context, workflow_context)
        self.assertNotIsInstance(node_context, WorkflowContext)
        self.assertFalse(hasattr(node_context, "progression"))
        self.assertEqual(workflow_context.progression.state, "ZHONGSHU_ANALYST")


if __name__ == "__main__":
    unittest.main()
