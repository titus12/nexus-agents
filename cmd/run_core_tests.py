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


CORE_TESTS = (
    "test_linear_fsm_architecture",
    "test_linear_fsm_direct_transport",
    "test_linear_fsm_effects",
    "test_linear_fsm_engine",
    "test_linear_fsm_failures",
    "test_linear_fsm_locking",
    "test_linear_fsm_nodes",
    "test_linear_fsm_persistence",
    "test_linear_fsm_reducer",
    "test_linear_fsm_states",
    "test_linear_fsm_transitions",
    "test_linear_fsm_transport",
    "test_linear_fsm_dto_migration",
    "test_linear_fsm_parallel_effects",
    "test_linear_fsm_concurrency",
    "test_linear_fsm_terminal_run_status",
    "test_linear_fsm_entrypoint",
    "test_linear_fsm_no_legacy_references",
    "test_linear_fsm_migration",
    "test_linear_fsm_recovery",
    "test_linear_fsm_notification_safety",
    "test_linear_fsm_menxia",
)


def _selected_module_names() -> set[str]:
    return set(CORE_TESTS)


def _reject_feishu_http_tests(cmd_root: Path) -> None:
    forbidden = (
        "FeishuHttpAdapter",
        "send_text(",
        "urllib.request",
        "urlopen(",
        "httpx.",
    )
    for module_name in _selected_module_names():
        source = (cmd_root / f"{module_name}.py").read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in source:
                raise RuntimeError(
                    f"core test selection contains Feishu HTTP test code: "
                    f"{module_name} ({marker})"
                )


def main() -> int:
    cmd_root = Path(__file__).parent
    _reject_feishu_http_tests(cmd_root)
    # Keep test-created temporary files inside the repository workspace.  On
    # this host the user temp directory contains stale files from old runs and
    # can make tempfile.mkstemp retry indefinitely.
    test_temp_root = cmd_root / f".core-test-tmp-{uuid.uuid4().hex}"
    test_temp_root.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["TMP"] = str(test_temp_root)
    environment["TEMP"] = str(test_temp_root)
    environment["TMPDIR"] = str(test_temp_root)
    # Defense in depth: even an accidentally selected test that constructs an
    # OrchestratorApp without injecting a fake adapter cannot reach Feishu.
    environment["NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS"] = "1"
    command = [sys.executable, "-u", "-m", "unittest", "-v", *CORE_TESTS]
    print("Running durable non-Feishu-notification regression tests", flush=True)
    try:
        result = subprocess.run(
            command,
            cwd=cmd_root,
            env=environment,
            timeout=60,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        print("Core test suite exceeded 60 seconds; terminated safely", flush=True)
        return 124
    finally:
        shutil.rmtree(test_temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
