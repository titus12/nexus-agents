import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orchestrator.app import build_context, main, OrchestratorApp, _agent_pool_by_state
from orchestrator.contracts import all_contracts, contract_for_state
from orchestrator.domain.context import RequestState, ProgressState, WorkflowContext, TaskIdentity, context_to_dto
from orchestrator.domain.requirements.transitions import RequirementsTransitionRegistry
from orchestrator.domain.states import StateRegistry
from orchestrator.runtime.requirements_cutover import inspect_task, migrate_historical_task
from orchestrator.runtime.repository import JsonWorkflowRepository, WorkflowSnapshot


class CutoverTests(unittest.TestCase):
    def args(self):
        return argparse.Namespace(project='',project_type='unknown',task_type='review')

    def test_default_creation_enters_requirements_with_canonical_plan(self):
        with patch.dict('os.environ',{'NEXUS_FAST_TRACK':'1','ZHONGSHU_ITEM_WORKFLOW_ENABLED':'1'}):
            context=build_context(self.args(),'issue','用户需求','task-new')
        self.assertEqual(context.request.workflow,'requirements-v1')
        self.assertEqual(context.progression.state,'REQ_INTAKE')
        self.assertEqual(context.request.plan_schema,'canonical-v1')
        self.assertEqual(context.requirements.sources[0].text,'用户需求')

    def test_retired_workflow_and_fast_track_are_rejected_before_external_action(self):
        for argv in (['--new','需求','--workflow','legacy-v1'],['--new','需求','--fast-track']):
            with self.subTest(argv=argv), patch('orchestrator.app.MulticaCliAdapter') as adapter, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:main(argv)
                self.assertEqual(error.exception.code,2)
                adapter.assert_not_called()

    def test_live_graph_and_bindings_have_no_retired_zhongshu_states(self):
        graph=RequirementsTransitionRegistry()
        self.assertFalse(any(s.startswith('ZHONGSHU_') or s=='REQUEST_INTAKE' for s in graph.allowed_states))
        self.assertFalse(any(s.startswith('ZHONGSHU_') for s in StateRegistry.default().names()))
        self.assertFalse(any(s.startswith('ZHONGSHU_') for s in _agent_pool_by_state()))
        for state in ('ZHONGSHU_SOLVER','ZHONGSHU_CRITIC','ZHONGSHU_FREEZE_CHECK'):
            with self.assertRaises(KeyError):contract_for_state(state)
        self.assertTrue(any(c.state=='REQ_DRAFT' for c in all_contracts()))

    def test_placeholder_decision_owner_is_rejected_before_transport_or_model_call(self):
        for actor in ('ou_替换为实际决策人的open_id','ou_','<open_id>'):
            with self.subTest(actor=actor), patch('orchestrator.app.MulticaCliAdapter') as adapter, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    main(['--new','需求','--decision-actor-id',actor])
                self.assertEqual(error.exception.code,2)
                adapter.assert_not_called()

    def historical(self, root):
        context=WorkflowContext(identity=TaskIdentity('task-old','issue-old','','request-old'),
            progression=ProgressState(state='ZHONGSHU_SOLVER',sequence=0,entered_at='2026-10-07T00:00:00Z'),
            request=RequestState(raw_request='同会话只连接一次',workflow='legacy-v1'))
        repo=JsonWorkflowRepository(Path(root)/'task-old')
        repo.initialize(WorkflowSnapshot('task-old',context,0))
        return repo,context

    def test_historical_inspection_is_read_only(self):
        with tempfile.TemporaryDirectory() as root:
            repo,context=self.historical(root)
            before=repo.state_path.read_bytes()
            result=inspect_task(root,'task-old')
            self.assertTrue(result['historical_read_only'])
            self.assertEqual(result['context']['progression']['state'],'ZHONGSHU_SOLVER')
            self.assertEqual(repo.state_path.read_bytes(),before)
            self.assertFalse(repo.event_path.exists())

    def test_cli_historical_inspection_and_resume_never_construct_transport(self):
        with tempfile.TemporaryDirectory() as root:
            repo, _ = self.historical(root)
            before = repo.state_path.read_bytes()
            with patch('orchestrator.app.MulticaCliAdapter') as adapter, redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--inspect-task', 'task-old', '--runs-root', root]), 0)
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    main(['--resume', 'task-old', '--runs-root', root])
                self.assertEqual(error.exception.code, 2)
                adapter.assert_not_called()
            self.assertEqual(repo.state_path.read_bytes(), before)

    def test_cli_migration_is_local_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as root:
            repo, _ = self.historical(root)
            before = repo.state_path.read_bytes()
            with patch('orchestrator.app.MulticaCliAdapter') as adapter, redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--migrate-task', 'task-old', '--runs-root', root,
                    '--communication', 'local']), 0)
                adapter.assert_not_called()
            self.assertEqual(repo.state_path.read_bytes(), before)

    def test_historical_execution_is_rejected_without_creating_transport(self):
        with tempfile.TemporaryDirectory() as root:
            repo,context=self.historical(root)
            with patch('orchestrator.app.MulticaCliAdapter') as adapter:
                with self.assertRaisesRegex(ValueError,'migrate-task'):OrchestratorApp(context,root)
                adapter.assert_not_called()

    def test_existing_historical_snapshot_cannot_be_relabelled_by_constructor(self):
        with tempfile.TemporaryDirectory() as root:
            repo, _ = self.historical(root)
            before = repo.state_path.read_bytes()
            supplied = build_context(self.args(), 'issue-old', '同会话只连接一次', 'task-old')
            with patch('orchestrator.app.MulticaCliAdapter') as adapter:
                with self.assertRaisesRegex(ValueError, 'migrate-task'):
                    OrchestratorApp(supplied, root)
                adapter.assert_not_called()
            self.assertEqual(repo.state_path.read_bytes(), before)

    def test_explicit_migration_preserves_history_and_requires_new_approval(self):
        with tempfile.TemporaryDirectory() as root:
            repo,context=self.historical(root)
            before=repo.state_path.read_bytes()
            with patch('orchestrator.app.MulticaCliAdapter') as adapter:
                receipt=migrate_historical_task(root,'task-old',communication='local')
                adapter.assert_not_called()
            new_root=Path(root)/receipt['new_task_id']
            new=JsonWorkflowRepository(new_root).load(receipt['new_task_id']).context
            self.assertEqual(new.progression.state,'REQ_INTAKE')
            self.assertEqual(new.request.workflow,'requirements-v1')
            self.assertEqual(new.requirements.sources[0].text,context.request.raw_request)
            self.assertIsNone(new.requirements.approval)
            self.assertIsNone(new.review)
            self.assertEqual(repo.state_path.read_bytes(),before)
            self.assertEqual(json.loads((new_root/'historical-task.json').read_text(encoding='utf-8'))['context'],json.loads(json.dumps(context_to_dto(context))))

    def test_migration_refuses_active_historical_owner(self):
        from orchestrator.locks import TaskLock
        with tempfile.TemporaryDirectory() as root:
            repo,context=self.historical(root)
            with TaskLock(Path(root)/'task-old'/'task.lock'):
                with self.assertRaisesRegex(ValueError,'active owner'):migrate_historical_task(root,'task-old')
            self.assertEqual(len(list(Path(root).glob('task-*'))),1)

    def test_retired_executable_modules_are_absent(self):
        base=Path(__file__).parent/'orchestrator'
        for path in ('zhongshu_parallel.py','zhongshu_review.py','zhongshu_review_queue.py',
                     'zhongshu_solver_contract.py','domain/zhongshu/solver.py','contracts/zhongshu_solver.py'):
            self.assertFalse((base/path).exists(),path)


if __name__=='__main__':unittest.main()
