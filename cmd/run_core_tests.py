"""Run the durable, non-Feishu-notification regression suite.

The individual test files are historical and include experiments for Feishu
delivery, rendering, and operational diagnostics.  This entry point is the
safe default for local regression checks: it explicitly selects only the
long-lived workflow contracts and excludes tests whose purpose is sending or
verifying Feishu notifications.
"""
from __future__ import annotations
import subprocess
import sys
import os
import shutil
import uuid
from pathlib import Path
CORE_TESTS = ('test_linear_fsm_architecture', 'test_linear_fsm_direct_transport', 'test_linear_fsm_effects', 'test_linear_fsm_engine', 'test_linear_fsm_failures', 'test_linear_fsm_locking', 'test_linear_fsm_nodes', 'test_linear_fsm_persistence', 'test_linear_fsm_reducer', 'test_linear_fsm_states', 'test_linear_fsm_transitions', 'test_linear_fsm_transport', 'test_linear_fsm_dto_migration', 'test_linear_fsm_parallel_effects', 'test_node_join_unstructured', 'test_linear_fsm_concurrency', 'test_linear_fsm_terminal_run_status', 'test_linear_fsm_entrypoint', 'test_linear_fsm_no_legacy_references', 'test_linear_fsm_recovery', 'test_linear_fsm_notification_safety', 'test_linear_fsm_menxia', 'test_structured_backfill', 'test_evidence_quote_gate', 'test_json_bom_v31', 'test_analyst_partition', 'test_completed_result_grace', 'test_retry_feedback_prompt', 'test_runtime_notice_reply', 'test_requirements_cutover', 'test_requirements_workflow_e2e', 'test_requirements_recovery', 'test_requirements_review', 'test_requirements_questions', 'test_requirements_metrics', 'test_requirements_rubric', 'test_requirements_draft_first', 'test_requirements_lint',
    'test_requirements_provenance', 'test_requirements_quotes', 'test_requirements_budget',
    'test_requirements_scouts')

def _selected_module_names() -> set[str]:
    return set(CORE_TESTS)

def _reject_feishu_http_tests(cmd_root: Path) -> None:
    forbidden = ('FeishuHttpAdapter', 'send_text(', 'urllib.request', 'urlopen(', 'httpx.')
    for module_name in _selected_module_names():
        source = (cmd_root / f'{module_name}.py').read_text(encoding='utf-8')
        for marker in forbidden:
            if marker in source:
                raise RuntimeError(f'core test selection contains Feishu HTTP test code: {module_name} ({marker})')

def main() -> int:
    cmd_root = Path(__file__).parent
    _reject_feishu_http_tests(cmd_root)
    test_temp_root = cmd_root / f'.core-test-tmp-{uuid.uuid4().hex}'
    test_temp_root.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment['TMP'] = str(test_temp_root)
    environment['TEMP'] = str(test_temp_root)
    environment['TMPDIR'] = str(test_temp_root)
    environment['NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS'] = '1'
    command = [sys.executable, '-u', '-m', 'unittest', '-v', *CORE_TESTS]
    print('Running durable non-Feishu-notification regression tests', flush=True)
    try:
        result = subprocess.run(command, cwd=cmd_root, env=environment, timeout=60)
        return result.returncode
    except subprocess.TimeoutExpired:
        print('Core test suite exceeded 60 seconds; terminated safely', flush=True)
        return 124
    finally:
        shutil.rmtree(test_temp_root, ignore_errors=True)
if __name__ == '__main__':
    raise SystemExit(main())
