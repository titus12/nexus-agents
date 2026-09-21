"""Live health check for the multica -> reasonix agent round trip.

This is a diagnostic script, NOT part of the default unit-test suite (it spends
real agent tokens and talks to the live multica server).  Run it manually:

    python cmd/multica_reasonix_health.py

What it does:
  1. checks the local multica daemon,
  2. locates the target agent (default: name containing "Analyst-reasonix"),
  3. creates a throwaway issue with a trivial instruction,
  4. polls the issue's run until it reaches a terminal state,
  5. verifies the agent replied with the expected one-line JSON,
  6. reports timings and marks the issue cancelled (unless --keep).

Exit code 0 = healthy, 1 = unhealthy.

Useful flags:
    --agent-name TEXT   substring match against agent names
    --agent-id UUID     skip lookup and use this agent id directly
    --project UUID      project for the throwaway issue
    --timeout SECONDS   give up waiting after this many seconds (default 420)
    --slow-threshold S  elapsed seconds above which the verdict is WARN
    --keep              do not cancel the issue afterwards
    --json              machine-readable summary on stdout
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

DEFAULT_AGENT_NAME = "Analyst-reasonix"
DEFAULT_PROJECT_ID = "56dd9c04-da60-4905-a7ca-d243f1f0f304"
TERMINAL_RUN_STATES = {"completed", "failed", "cancelled"}
HEALTH_PROMPT = (
    "Health check ping. Do NOT read any files and do NOT run any tools. "
    'Reply with exactly one comment whose entire content is one line: {"ok": true}'
)


def run_cli(args: list[str], timeout: float = 60.0) -> tuple[int, str, str]:
    started = time.monotonic()
    try:
        result = subprocess.run(
            ["multica", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=tempfile.gettempdir(),
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"multica CLI timed out after {timeout}s: {' '.join(args)}")
    return result.returncode, result.stdout or "", result.stderr or ""


def run_cli_json(args: list[str], timeout: float = 60.0):
    code, out, err = run_cli(args, timeout=timeout)
    if code != 0:
        raise RuntimeError(f"multica {' '.join(args)} failed rc={code}: {err.strip()[:400]}")
    text = out.lstrip("\ufeff").strip()
    return json.loads(text, strict=False)


def cli_step(label: str, args: list[str], timeout: float = 60.0) -> tuple[float, int, str]:
    started = time.monotonic()
    code, out, err = run_cli(args, timeout=timeout)
    elapsed = time.monotonic() - started
    print(f"  [{label}] rc={code} elapsed={elapsed:.2f}s out_chars={len(out)}")
    if code != 0:
        print(f"    stderr: {err.strip()[:300]}")
    return elapsed, code, out


def find_agent(agent_name: str, agent_id: str) -> tuple[str, str, str]:
    if agent_id:
        return agent_id, "(given via --agent-id)", ""
    agents = run_cli_json(["agent", "list", "--output", "json"])
    matches = [
        a
        for a in agents
        if agent_name.lower() in str(a.get("name", "")).lower() and not a.get("archived")
    ]
    if not matches:
        raise RuntimeError(f"no agent matching name substring {agent_name!r}")
    if len(matches) > 1:
        names = ", ".join(f"{m.get('name')}({m.get('id')})" for m in matches)
        raise RuntimeError(f"agent name {agent_name!r} is ambiguous: {names}")
    agent = matches[0]
    return str(agent["id"]), str(agent.get("name")), str(agent.get("status") or "")


def latest_runs(issue_id: str) -> list[dict]:
    payload = run_cli_json(["issue", "runs", issue_id, "--output", "json"])
    if isinstance(payload, dict):
        payload = payload.get("runs") or payload.get("items") or []
    return [r for r in payload if isinstance(r, dict)]


def agent_comments(issue_id: str) -> list[dict]:
    payload = run_cli_json(["issue", "comment", "list", issue_id, "--output", "json"])
    if isinstance(payload, dict):
        payload = payload.get("comments") or payload.get("items") or []
    return [c for c in payload if isinstance(c, dict) and c.get("author_type") == "agent"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-name", default=DEFAULT_AGENT_NAME)
    parser.add_argument("--agent-id", default="")
    parser.add_argument("--project", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--timeout", type=float, default=420.0)
    parser.add_argument("--poll-interval", type=float, default=10.0)
    parser.add_argument("--slow-threshold", type=float, default=180.0)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    options = parser.parse_args()

    summary: dict = {"verdict": "FAIL", "checks": {}, "timings": {}}
    overall_started = time.monotonic()

    print("== 1. daemon ==")
    _, code, out = cli_step("daemon status", ["daemon", "status"])
    daemon_running = code == 0 and "running" in out
    summary["checks"]["daemon_running"] = daemon_running
    if not daemon_running:
        print("  FAIL: local multica daemon is not running (try: multica daemon start)")
        _emit(summary, options)
        return 1

    print("== 2. agent lookup ==")
    try:
        agent_id, agent_name, agent_status = find_agent(options.agent_name, options.agent_id)
    except Exception as error:
        print(f"  FAIL: {error}")
        summary["checks"]["agent_found"] = False
        _emit(summary, options)
        return 1
    print(f"  agent: {agent_name} ({agent_id}) status={agent_status or 'unknown'}")
    summary["checks"]["agent_found"] = True
    summary["agent"] = {"id": agent_id, "name": agent_name, "status": agent_status}
    if agent_status == "working":
        print("  WARN: agent is currently 'working'; the health run will queue behind it")

    print("== 3. create health issue ==")
    description_file = tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8", dir=tempfile.gettempdir()
    )
    description_file.write(HEALTH_PROMPT + "\n")
    description_file.close()
    create_started = time.monotonic()
    try:
        created = run_cli_json(
            [
                "issue", "create",
                "--title", f"multica-reasonix-health {datetime.now(timezone.utc):%Y%m%d-%H%M%S}",
                "--description-file", description_file.name,
                "--assignee-id", agent_id,
                "--project", options.project,
                "--output", "json",
            ],
            timeout=60.0,
        )
    except Exception as error:
        print(f"  FAIL: issue create failed: {error}")
        _emit(summary, options)
        return 1
    issue_id = _extract_issue_id(created)
    summary["issue_id"] = issue_id
    if not issue_id:
        print("  FAIL: issue create returned no id")
        _emit(summary, options)
        return 1
    print(f"  issue: {issue_id} (created in {time.monotonic() - create_started:.2f}s)")

    print("== 4. wait for run terminal state ==")
    run_state = ""
    run_error = ""
    poll_count = 0
    wait_started = time.monotonic()
    while time.monotonic() - wait_started < options.timeout:
        poll_count += 1
        try:
            runs = latest_runs(issue_id)
        except Exception as error:
            print(f"  poll {poll_count}: runs query failed: {error}")
            time.sleep(options.poll_interval)
            continue
        active = [r for r in runs if r.get("status") in ("queued", "running", "created", "pending") or r.get("status") is None]
        latest = runs[0] if runs else {}
        run_state = str(latest.get("status") or "unknown")
        print(f"  poll {poll_count}: runs={len(runs)} active={len(active)} latest_status={run_state}")
        if runs and run_state in TERMINAL_RUN_STATES:
            raw_error = latest.get("error")
            if isinstance(raw_error, dict):
                run_error = str(raw_error.get("message") or raw_error)
            else:
                run_error = str(raw_error or "")
            break
        time.sleep(options.poll_interval)
    elapsed_wait = time.monotonic() - wait_started
    summary["timings"]["terminal_wait_seconds"] = round(elapsed_wait, 1)
    summary["checks"]["run_terminal"] = run_state in TERMINAL_RUN_STATES
    summary["run_state"] = run_state
    if run_state not in TERMINAL_RUN_STATES:
        print(f"  FAIL: run did not reach terminal state within {options.timeout:.0f}s (last={run_state})")
        _emit(summary, options)
        return 1
    if run_state != "completed":
        print(f"  FAIL: run finished as {run_state}: {run_error[:300]}")
        _emit(summary, options)
        return 1
    print(f"  run completed in {elapsed_wait:.1f}s across {poll_count} polls")

    print("== 5. verify agent reply ==")
    reply_ok = False
    reply_preview = ""
    try:
        comments = agent_comments(issue_id)
        if comments:
            reply_preview = str(comments[-1].get("content") or "").strip()[:200]
            reply_ok = '"ok"' in reply_preview and "true" in reply_preview.lower()
    except Exception as error:
        print(f"  comment list failed: {error}")
    summary["checks"]["agent_reply"] = reply_ok
    print(f"  agent comments found, reply_ok={reply_ok}")
    if reply_preview:
        print(f"  reply preview: {reply_preview[:160]}")
    if not reply_ok:
        print("  FAIL: no valid agent reply comment")

    print("== 6. cleanup ==")
    if not options.keep:
        _, code, err = run_cli(["issue", "status", issue_id, "cancelled"], timeout=30.0)
        print(f"  issue cancelled rc={code}" if code == 0 else f"  cancel failed: {err.strip()[:200]}")

    total_elapsed = time.monotonic() - overall_started
    summary["timings"]["total_seconds"] = round(total_elapsed, 1)
    if run_state == "completed" and reply_ok:
        summary["verdict"] = "PASS" if elapsed_wait <= options.slow_threshold else "SLOW"
    if summary["verdict"] == "SLOW":
        print(f"\nVERDICT: SLOW (completed but took {elapsed_wait:.0f}s > {options.slow_threshold:.0f}s threshold)")
    elif summary["verdict"] == "PASS":
        print(f"\nVERDICT: PASS (round trip {elapsed_wait:.0f}s)")
    else:
        print("\nVERDICT: FAIL")
    _emit(summary, options)
    return 0 if summary["verdict"] in ("PASS", "SLOW") else 1


def _extract_issue_id(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("id", "issue_id"):
        candidate = str(value.get(key) or "")
        if candidate:
            return candidate
    nested = value.get("issue")
    if isinstance(nested, dict):
        return str(nested.get("id") or nested.get("issue_id") or "")
    return ""


def _emit(summary: dict, options) -> None:
    if options.json_output:
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
