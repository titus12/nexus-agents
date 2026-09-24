"""Restore the zhongshu-final baseline and rerun from the human gate.

Live-testing menxia does not need to replay the multi-hour zhongshu phase:
the orchestrator is stateless across restarts and rebuilds everything from
``runs/<task_id>/`` (workflow-state.json + workflow-events.jsonl +
artifacts/).  Restoring that directory is time travel; ``--resume`` then
publishes the RESUME event and the run proceeds straight into menxia.

Usage:
    python cmd/run_menxia_test.py                    # restore baseline, run as original task id
    python cmd/run_menxia_test.py --task-id task-menxia-t1
    python cmd/run_menxia_test.py --no-run           # only restore/fork the directory
    python cmd/run_menxia_test.py --force            # overwrite an existing target dir

The baseline directory is created once with ``--snapshot`` while the live
orchestrator is stopped:

    python cmd/run_menxia_test.py --snapshot runs/task-20260921-8f18de

A test run that was killed mid-wave leaves its fan-out child issues open in
multica; ``--cleanup`` closes them using the external issue ids recorded in
the run journal (the same convention as ``_finalize_fanout_children``:
SUCCEEDED workers are closed as ``done``, everything else as ``cancelled``):

    python cmd/run_menxia_test.py --cleanup task-menxia-t1
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_BASELINE = Path("baselines") / "zhongshu-final"
ENTRYPOINT = Path(__file__).resolve().parent / "review_orchestrator_v2.py"

IGNORE_PATTERNS = shutil.ignore_patterns("task.lock*", "*.tmp")


def _read_task_id(state_path: Path) -> str:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    task_id = str(state.get("task_id") or "")
    if task_id:
        return task_id
    identity = state.get("context", {}).get("identity", {})
    task_id = str(identity.get("task_id") or "")
    if not task_id:
        raise SystemExit(f"cannot find task_id in {state_path}")
    return task_id


def _rewrite_task_id(root: Path, old: str, new: str) -> None:
    """Rewrite task_id, decision/event ids, and artifact paths in one pass.

    Artifact receipts store absolute paths under ``runs/<task_id>/``; replacing
    the task-id substring fixes ``task_id`` fields, ``decision_id``/``event_id``
    strings, and artifact paths simultaneously.
    """

    for name in ("workflow-state.json", "workflow-events.jsonl"):
        path = root / name
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        rewritten = [line.replace(old, new) for line in lines]
        path.write_text("".join(rewritten), encoding="utf-8")

    artifacts = root / "artifacts"
    if artifacts.is_dir():
        for path in artifacts.iterdir():
            if old in path.name:
                path.rename(path.with_name(path.name.replace(old, new)))


def _resolve_within_runs_root(runs_root: Path, task_id: str) -> Path:
    root = (runs_root / task_id).resolve()
    if runs_root.resolve() not in root.parents:
        raise SystemExit(f"task id must resolve within {runs_root}")
    return root


def _collect_external_issues(value: object, found: dict[str, str]) -> None:
    """Recursively gather ``external_issue_id`` values with worker status.

    Journal entries mirror ``WorkerResult`` dicts (``asdict`` in the node
    effect), so a status sibling is available for fan-out workers; bare
    references without a status default to ``done``.
    """

    if isinstance(value, dict):
        issue_id = str(value.get("external_issue_id") or "")
        if issue_id:
            status = str(value.get("status") or "")
            found[issue_id] = (
                "done" if status == "SUCCEEDED" else found.get(issue_id, "cancelled")
            )
        for child in value.values():
            _collect_external_issues(child, found)
    elif isinstance(value, list):
        for child in value:
            _collect_external_issues(child, found)


def _close_leftover_issues(runs_root: Path, task_id: str) -> int:
    root = _resolve_within_runs_root(runs_root, task_id)
    if not root.exists():
        raise SystemExit(f"run dir not found: {root}")
    found: dict[str, str] = {}
    for name in ("workflow-state.json", "workflow-events.jsonl"):
        path = root / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                _collect_external_issues(json.loads(line), found)
            except json.JSONDecodeError:
                continue
    if not found:
        print(f"no external issue ids recorded in {root}")
        return 0
    closed = 0
    for issue_id, status in sorted(found.items()):
        result = subprocess.run(
            ["multica", "issue", "status", issue_id, status, "--output", "json"],
            capture_output=True,
            text=True,
        )
        marker = "closed" if result.returncode == 0 else "CLOSE FAILED"
        closed += result.returncode == 0
        print(f"{marker}: {issue_id} -> {status}")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[:200]
            if detail:
                print(f"  {detail}")
    print(f"closed {closed}/{len(found)} leftover child issues")
    return 0


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--task-id", default="", help="fork the baseline under a new task id")
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    parser.add_argument("--force", action="store_true", help="overwrite an existing target dir")
    parser.add_argument("--no-run", action="store_true", help="restore only; do not launch")
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="create the baseline from this live run dir (orchestrator must be stopped)",
    )
    parser.add_argument(
        "--cleanup",
        default="",
        metavar="TASK_ID",
        help="close leftover open child issues recorded in this task's journal",
    )
    args = parser.parse_args(argv)

    if args.cleanup:
        sys.stdout.reconfigure(encoding="utf-8")
        return _close_leftover_issues(args.runs_root, args.cleanup)

    if args.snapshot:
        baseline = args.baseline.resolve()
        if baseline.exists() and not args.force:
            raise SystemExit(f"baseline exists: {baseline} (use --force to overwrite)")
        source = args.snapshot.resolve()
        if not (source / "workflow-state.json").exists():
            raise SystemExit(f"not a run dir: {source}")
        if baseline.exists():
            shutil.rmtree(baseline)
        shutil.copytree(source, baseline, ignore=IGNORE_PATTERNS)
        print(f"baseline created: {baseline}")
        return 0

    baseline = args.baseline.resolve()
    state_path = baseline / "workflow-state.json"
    if not state_path.exists():
        raise SystemExit(
            f"baseline missing: {state_path}\n"
            f"create it first: python {Path(__file__).name} --snapshot runs/<task-id>"
        )
    original_task_id = _read_task_id(state_path)
    target_task_id = args.task_id or original_task_id
    target = _resolve_within_runs_root(args.runs_root, target_task_id)

    if target.exists():
        if not args.force:
            raise SystemExit(f"target exists: {target} (use --force to overwrite)")
        shutil.rmtree(target)
    shutil.copytree(baseline, target, ignore=IGNORE_PATTERNS)
    if target_task_id != original_task_id:
        _rewrite_task_id(target, original_task_id, target_task_id)
    print(f"restored: {baseline} -> {target} (task_id={target_task_id})")

    if args.no_run:
        return 0

    command = [sys.executable, str(ENTRYPOINT), "--resume", target_task_id]
    print("launching:", " ".join(command))
    completed = subprocess.run(command)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
