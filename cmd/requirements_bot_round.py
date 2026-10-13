"""One round of the requirements-v1 acceptance bot.

Mechanical loop per round: clean open multica issues, launch a real run,
patrol the journal, auto-answer clarification rounds with the recommended
options, auto-approve the confirm gate, stop the process at REQ_HANDOFF
(success) or on a recorded failure, then append metrics and regenerate the
short report. Judgment work (defect analysis, product fixes, commits)
stays with the operator; this script only runs, watches, records, stops.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / 'cmd' / 'review_orchestrator_v2.py'
CLEANUP = REPO / 'multica' / 'cleanup-my-issues.ps1'
RUNS = REPO / 'runs'
LOGS = REPO / 'multica'
REPORTS = LOGS / 'bot-reports'
ROUND_LOG = REPORTS / 'rounds.jsonl'
REPORT_MD = REPORTS / 'report.md'
REQUEST_DEFAULT = REPORTS / 'request.txt'
COOKIE_DEFAULT = Path(os.environ.get('TEMP', '')) / 'opencode' / 'multica-cookie.txt'
STUCK_STATES = ('REQ_CLARIFY_WAIT', 'REQ_CONFIRM_WAIT', 'REQ_BLOCKED',
                'REQ_ENGINE_ERROR', 'REQ_RETRY_WAIT')


def log(message: str) -> None:
    print(time.strftime('%H:%M:%S'), message, flush=True)


def run_cli(arguments: list[str], timeout: int = 120) -> tuple[int, str]:
    result = subprocess.run([sys.executable, '-X', 'utf8', str(CLI), *arguments],
                            cwd=str(REPO / 'multica'), capture_output=True, timeout=timeout)
    output = ((result.stdout or b'') + (result.stderr or b'')).decode('utf-8', 'replace')
    return result.returncode, output


def cleanup_issues(cookie: str = '') -> int:
    """Cancel stale review issues so the duplicate guard does not block launch.

    The old browser-cookie HTTP cleanup (cleanup-my-issues.ps1) died with the
    web session; the multica CLI keeps its own credentials, so stale issues are
    cancelled through the CLI instead.  ``cookie`` is kept for call-site
    compatibility and ignored.
    """
    rows = []
    for status in ('todo', 'in_progress', 'in_review', 'blocked', 'backlog', 'reopened'):
        result = subprocess.run(['multica', 'issue', 'list', '--status', status, '--limit', '50',
                                 '--output', 'json'], capture_output=True, timeout=180)
        output = (result.stdout or b'').decode('utf-8', 'replace').lstrip('﻿')
        if result.returncode != 0:
            log('cleanup list failed rc=%d %s' % (result.returncode, (result.stderr or b'').decode('utf-8', 'replace')[-200:]))
            return 1
        try:
            data = json.loads(output)
        except ValueError:
            log('cleanup list unparsable: %s' % output[-200:])
            return 1
        rows.extend(data.get('issues') or data.get('items') or (data if isinstance(data, list) else []))
    terminal = ('cancelled', 'canceled', 'completed', 'done', 'closed', 'archived', 'skipped')
    stale = [row for row in rows if isinstance(row, dict)
             and str(row.get('title') or '').startswith('Review: RTC-SDK')
             and str(row.get('status') or '').strip().lower() not in terminal]
    failures = 0
    for row in stale:
        cancel = subprocess.run(['multica', 'issue', 'status', str(row['id']), 'cancelled'],
                                capture_output=True, timeout=120)
        log('cleanup %s (%s) -> %s' % (row.get('identifier') or str(row['id'])[:8],
                                       row.get('status'), 'ok' if cancel.returncode == 0 else 'fail'))
        failures += 0 if cancel.returncode == 0 else 1
    log('cleanup cancelled %d/%d stale review issues' % (len(stale) - failures, len(stale)))
    return 1 if failures else 0


def journal(task_id: str) -> list[dict]:
    path = RUNS / task_id / 'workflow-events.jsonl'
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding='utf-8').splitlines() if line.strip()]


def states(journal_records: list[dict]) -> list[str]:
    return [r['snapshot']['context']['progression']['state']
            for r in journal_records if r['record_type'] == 'transition']


def latest_snapshot(journal_records: list[dict]) -> dict:
    transitions = [r for r in journal_records if r['record_type'] == 'transition']
    return transitions[-1]['snapshot'] if transitions else {}


CARD_RECOMMENDATION_FALLBACK = '建议先采集相关基线数据，再确定可量化的目标与验收标准。'
REJECT_OBJECTION = '（验收剧本：拒绝推荐项）本条不接受推荐，请按所选约束重新评估对应方案。'


def recommendation_answer(question: dict, scenario: str = 'recommend') -> dict | None:
    options = question.get('options') or []
    blocking = bool(question.get('blocking'))
    base = {'question_id': question['question_id'],
            'question_revision': question.get('revision', 1)}
    recommendation = question.get('recommendation') or ''
    if scenario == 'reject':
        if options:
            return {**base, 'option_ids': [options[-1]['option_id']],
                    'free_text': REJECT_OBJECTION, 'disposition': 'answered'}
        return {**base, 'option_ids': [], 'free_text': REJECT_OBJECTION, 'disposition': 'answered'}
    if recommendation:
        # The proxy user's answer must read as an explicit confirmation, not
        # as another recommendation: a "建议..." answer that downstream
        # consensus promotes into an approved decision is exactly the
        # recommendation-is-not-human-decision finding the critic files.
        if recommendation.startswith('建议'):
            confirmed = '用户确认：' + recommendation[len('建议'):]
        else:
            confirmed = '用户确认：' + recommendation
        return {**base, 'option_ids': [], 'free_text': confirmed, 'disposition': 'answered'}
    if options:
        return {**base, 'option_ids': [options[0]['option_id']], 'free_text': '', 'disposition': 'answered'}
    if not blocking:
        return {**base, 'option_ids': [], 'free_text': '', 'disposition': 'accept_default'}
    return {**base, 'option_ids': [], 'free_text': CARD_RECOMMENDATION_FALLBACK, 'disposition': 'answered'}


def effective_scenario(scenario: str, reject_rounds_used: int, reject_cap: int = 2) -> str:
    """A real user opposes a couple of times, then converges.

    Endless objection in the reject scenario only burns the round budget on
    clarify ping-pong (four rounds once consumed a 90-minute deadline), so
    after ``reject_cap`` rounds the proxy user switches to confirming the
    recommended options while keeping the earlier objections on record.
    """
    if scenario == 'reject' and reject_rounds_used >= reject_cap:
        return 'recommend'
    return scenario


def submit_answers(task_id: str, snapshot: dict, scenario: str = 'recommend') -> tuple[bool, str]:
    requirements = snapshot['context']['requirements']
    answered = {(a['question_id'], a['question_revision']) for a in requirements.get('answers', [])}
    rounds = requirements.get('rounds', [])
    open_round = next((r for r in rounds if r.get('status') in ('open', 'preparing', 'partial')), None)
    if not open_round:
        return False, 'NO_OPEN_ROUND'
    revisions = {q['question_id']: q.get('revision', 1) for q in requirements.get('questions', [])}
    by_id = {q['question_id']: q for q in requirements.get('questions', [])}
    rows = []
    for question_id in open_round.get('question_ids', []):
        revision = revisions.get(question_id, 1)
        if (question_id, revision) in answered:
            continue
        answer = recommendation_answer(by_id.get(question_id, {}), scenario)
        if answer is None:
            return False, 'UNSUPPORTED_BLOCKING_QUESTION:' + question_id
        rows.append(answer)
    if not rows:
        return False, 'NOTHING_TO_ANSWER'
    payload = {'round_id': open_round['round_id'], 'answers': rows}
    path = REPORTS / ('answers-%s.json' % task_id)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    code, output = run_cli(['--runs-root', str(RUNS), '--resume', task_id, '--answer-file', str(path)])
    if code or ('durably queued' not in output and 'accepted' not in output):
        return False, 'ANSWER_SUBMIT_FAILED:' + output[-300:]
    return True, json.dumps(rows, ensure_ascii=False)


def approve_requirements(task_id: str, snapshot: dict) -> tuple[bool, str]:
    requirements = snapshot['context']['requirements']
    digest = (requirements.get('draft') or {}).get('document_digest', '')
    gate_id = requirements.get('review_gate_id', '')
    if not digest or not gate_id:
        return False, 'APPROVAL_TARGET_MISSING'
    code, output = run_cli(['--runs-root', str(RUNS), '--resume', task_id,
                            '--approve-requirements', digest])
    if code:
        return False, 'APPROVE_SUBMIT_FAILED:' + output[-300:]
    return True, digest


def record_round(entry: dict) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    with ROUND_LOG.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + '\n')
    regenerate_report()


def regenerate_report() -> None:
    rounds = [json.loads(line) for line in
              ROUND_LOG.read_text(encoding='utf-8').splitlines() if line.strip()]
    total = len(rounds)
    success = sum(1 for r in rounds if r['outcome'] == 'success')
    durations = [r['duration_sec'] for r in rounds if r['outcome'] == 'success']
    lines = ['# 需求工作流验收机器人 — 轮次报告', '',
             '生成时间：%s' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'), '',
             '| 指标 | 值 |', '|---|---|',
             '| 总轮数 | %d |' % total,
             '| 成功（到达 REQ_HANDOFF） | %d |' % success,
             '| 失败 | %d |' % (total - success),
             '| 成功率 | %s |' % ('%.0f%%' % (100.0 * success / total) if total else 'n/a'),
             '| 成功轮平均耗时 | %s |' % ('%d 分 %d 秒' % divmod(sum(durations) // max(1, len(durations)), 60) if durations else 'n/a'), '',
             '| 轮次 | 任务 | 结果 | 耗时 | 说明 |', '|---|---|---|---|---|']
    for index, entry in enumerate(rounds, 1):
        minutes, seconds = divmod(entry['duration_sec'], 60)
        lines.append('| %d | %s | %s | %d:%02d | %s |' % (
            index, entry['task_id'], entry['outcome'], minutes, seconds,
            entry.get('note', '')[:120].replace('|', '\\|')))
    lines += ['', '证据：runs/<task_id>/workflow-events.jsonl 与 multica/orchestrator-*.log（按 task_id 归因）。']
    REPORT_MD.write_text('\n'.join(lines) + '\n', encoding='utf-8')


CONNECTOR = Path.home() / 'plugins' / 'lark-enterprise' / 'scripts' / 'feishu-connector.cmd'


def broker_healthy() -> tuple[bool, str]:
    try:
        result = subprocess.run([str(CONNECTOR), 'lark-cli', '--', 'drive', '+search', '--as', 'user',
                                 '--query', 'nexus-bot-preflight', '--only-title', '--created-by-me',
                                 '--doc-types', 'docx', '--page-size', '5'],
                                capture_output=True, timeout=120)
    except (subprocess.TimeoutExpired, OSError) as error:
        return False, str(error)[:200]
    output = ((result.stdout or b'') + (result.stderr or b'')).decode('utf-8', 'replace')
    if result.returncode:
        return False, output[-200:]
    return True, ''


def ensure_broker_token(budget_seconds: int = 6 * 3600) -> None:
    deadline = time.time() + budget_seconds
    while True:
        healthy, detail = broker_healthy()
        if healthy:
            return
        if time.time() > deadline:
            log('broker token unhealthy for %ds, giving up: %s' % (budget_seconds, detail))
            raise SystemExit('BROKER_TOKEN_UNHEALTHY: manual re-auth required '
                             '(feishu-connector.cmd connect --scope "offline_access docx:document")')
        log('broker token unhealthy, waiting 120s: %s' % detail[-120:])
        time.sleep(120)


def ensure_gateway() -> None:
    import sqlite3
    database = RUNS / 'requirements-gateway.sqlite'
    app_id = os.environ.get('FEISHU_GATE_APP_ID', '')
    started = None
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            db = sqlite3.connect(str(database))
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM heartbeat WHERE app_id=?', (app_id,)).fetchone()
            db.close()
        except sqlite3.Error:
            row = None
        if row and row['ready'] and time.time() - row['updated_at'] < 60:
            return
        if started is None:
            log('gateway heartbeat stale; starting requirements_card_gateway')
            started = (REPORTS / 'gateway.log').open('ab')
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW \
                if os.name == 'nt' else 0
            subprocess.Popen([sys.executable, '-X', 'utf8',
                              str(REPO / 'cmd' / 'requirements_card_gateway.py'),
                              '--database', str(database)], stdout=started,
                             stderr=subprocess.STDOUT, cwd=str(REPO), creationflags=flags)
        time.sleep(5)
    log('gateway heartbeat still stale after 90s')


def launch(request_text: str) -> tuple[int, object]:
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    log_path = REPORTS / ('launch-%s.log' % stamp)
    handle = log_path.open('ab')
    creationflags = 0
    if os.name == 'nt':
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    process = subprocess.Popen(
        [sys.executable, '-X', 'utf8', str(CLI),
         '--runs-root', str(RUNS), '--project', '1e41be52-4460-4fba-9360-0c60cfc18c92',
         '--project-type', 'unity', '--task-type', 'review',
         '--poll-interval', '15', '--timeout', '900', '--new', request_text],
        cwd=str(REPO / 'multica'), stdout=handle, stderr=subprocess.STDOUT,
        creationflags=creationflags)
    return process.pid, handle


def stop_process(pid: int) -> None:
    if os.name == 'nt':
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                       capture_output=True, text=True)
    else:
        import signal
        os.kill(pid, signal.SIGTERM)


def launch_with_retries(request_text: str, attempts: int = 3) -> tuple[int, object] | None:
    """Launch the orchestrator, retrying around platform blips.

    A failed cleanup or a transient multica error surfaces here as a
    missing task id; log the launch-log tail, re-run cleanup (stale
    issues block creation via the duplicate guard) and try again.
    """
    for attempt in range(1, attempts + 1):
        pid, handle = launch(request_text)
        log('launched pid=%d (attempt %d/%d)' % (pid, attempt, attempts))
        task_id = wait_for_task_id(pid)
        if task_id:
            return pid, handle, task_id
        stop_process(pid)
        handle.close()
        tail = ''
        try:
            logs = sorted(REPORTS.glob('launch-*.log'), key=lambda p: p.stat().st_mtime)
            if logs:
                tail = logs[-1].read_text(encoding='utf-8', errors='replace')[-400:]
        except OSError:
            pass
        log('launch attempt %d produced no task id; log tail: %s' % (attempt, tail.replace('\n', ' | ')[-380:]))
        if attempt < attempts:
            cleanup_issues()
            time.sleep(60)
    return None


def wait_for_task_id(pid: int, timeout: int = 120) -> str | None:
    deadline = time.time() + timeout
    pattern = re.compile(r'task-\d{8}-[0-9a-f]{6}')
    while time.time() < deadline:
        for log_file in sorted(LOGS.glob('orchestrator-*.log')):
            try:
                text = log_file.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            matches = [line for line in text.splitlines() if ('WORKFLOW_HEALTH' in line and str(pid) in line)]
            if matches:
                found = pattern.search(matches[-1])
                if found:
                    return found.group(0)
        time.sleep(5)
    return None


LOG_STAMP = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))')


def last_poll_age(task_id: str) -> float | None:
    marker = 'AGENT_REMOTE_RUN_STATUS'
    needle = 'task_id=' + task_id
    newest = None
    candidates = sorted(LOGS.glob('orchestrator-*.log'))[-2:]
    for log_file in candidates:
        try:
            text = log_file.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        for line in text.splitlines():
            if marker in line and needle in line:
                stamp_match = LOG_STAMP.match(line)
                if not stamp_match:
                    continue
                try:
                    stamp = datetime.fromisoformat(stamp_match.group(1).replace('Z', '+00:00'))
                except ValueError:
                    continue
                age = time.time() - stamp.timestamp()
                newest = age if newest is None else min(newest, age)
    return newest


def patrol(task_id: str, pid: int, silence_timeout: int, wait_cap: int,
           round_deadline: float, scenario: str = 'recommend') -> tuple[str, str, list[str]]:
    answered_rounds: set[str] = set()
    approved_digests: set[str] = set()
    reject_rounds_used = 0
    visited: list[str] = []
    entered_wait_at: float | None = None
    last_heartbeat = time.time()
    while time.time() < round_deadline:
        records = journal(task_id)
        current = states(records)
        state = current[-1] if current else 'STARTUP'
        if not visited or visited[-1] != state:
            visited.append(state)
            log('state -> %s' % state)
            entered_wait_at = time.time() if state in STUCK_STATES else None
        snapshot = latest_snapshot(records)
        # REQ_HANDOFF can flash by between polls (publish -> handoff -> menxia
        # intake inside one patrol interval), so judge success from the
        # journal history, not just the latest state.
        if 'REQ_HANDOFF' in current:
            return 'success', 'REQ_HANDOFF reached', visited
        if state == 'REQ_HANDOFF':
            return 'success', 'REQ_HANDOFF reached', visited
        if state in ('REQ_CANCELLED', 'REQ_ENGINE_ERROR', 'FAILED'):
            failure = (snapshot.get('context', {}).get('requirements', {}).get('reason') or state)
            return 'failure', ('%s: %s' % (state, str(failure)[:300])), visited
        if state == 'REQ_CLARIFY_WAIT':
            requirements = snapshot['context']['requirements']
            open_round = next((r for r in requirements.get('rounds', [])
                               if r.get('status') in ('open', 'preparing', 'partial')), None)
            round_id = open_round['round_id'] if open_round else ''
            if round_id and round_id not in answered_rounds:
                answered_rounds.add(round_id)
                round_scenario = effective_scenario(scenario, reject_rounds_used)
                if round_scenario == 'reject':
                    reject_rounds_used += 1
                ok, detail = submit_answers(task_id, snapshot, round_scenario)
                log('auto-answer[%s] %s %s' % (round_scenario, round_id, detail[:160] if ok else detail))
                if not ok and detail.startswith(('UNSUPPORTED', 'ANSWER_SUBMIT_FAILED')):
                    return 'failure', 'clarify gate: ' + detail, visited
            if entered_wait_at and time.time() - entered_wait_at > wait_cap:
                return 'failure', 'clarify wait exceeded cap', visited
        if state == 'REQ_CONFIRM_WAIT':
            digest = (snapshot['context']['requirements'].get('draft') or {}).get('document_digest', '')
            if digest and digest not in approved_digests:
                approved_digests.add(digest)
                ok, detail = approve_requirements(task_id, snapshot)
                log('auto-approve %s ok=%s' % (digest[:12], ok))
                if not ok:
                    return 'failure', 'confirm gate: ' + detail, visited
            if entered_wait_at and time.time() - entered_wait_at > wait_cap:
                return 'failure', 'confirm wait exceeded cap', visited
        if state == 'REQ_BLOCKED':
            requirements = snapshot['context']['requirements']
            reason = requirements.get('reason') or ''
            return 'failure', 'REQ_BLOCKED: ' + str(reason)[:300], visited
        last = records[-1] if records else None
        if last and last.get('record_type') == 'effect_running':
            journal_age = time.time() - _recorded_at(last)
            if time.time() - last_heartbeat > 300:
                poll_age = last_poll_age(task_id)
                log('patrol state=%s journal_age=%ds poll_age=%s' % (
                    state, int(journal_age),
                    'none' if poll_age is None else '%ds' % poll_age))
                last_heartbeat = time.time()
            if journal_age > silence_timeout:
                poll_age = last_poll_age(task_id)
                if poll_age is None or poll_age > silence_timeout:
                    return 'failure', ('stall: effect_running journal_age=%ds poll_age=%s '
                                       'exceeds silence_timeout=%ds' % (
                                           int(journal_age),
                                           'none' if poll_age is None else '%ds' % poll_age,
                                           silence_timeout)), visited
        elif time.time() - last_heartbeat > 300:
            log('patrol state=%s (journal idle, last record: %s)' % (
                state, last.get('record_type') if last else 'none'))
            last_heartbeat = time.time()
        time.sleep(15)
    return 'failure', 'round timeout', visited


def _recorded_at(record: dict) -> float:
    stamp = record.get('recorded_at', '')
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return time.time()


def log_config_fingerprint() -> None:
    """Log the live platform agent/skill config vs repo canon before each round.

    Multica-side skill or prompt drift (missing sections, conflicting text)
    has caused real instability, so every round starts with a drift check:
    any non-empty drift list is logged as a warning for post-mortems.
    """
    try:
        import sync_multica_review_agents as sync
    except Exception as error:
        log('config fingerprint unavailable: %r' % (error,))
        return
    for role in ('analyst', 'solver', 'critic'):
        try:
            agent_id, skill_id, _menxia_id = sync.TARGETS[role]
            agent = sync.cli('agent', 'get', agent_id)
            skill = sync.cli('skill', 'get', skill_id, '--with-content')
            drift = sync.differences(role, agent, skill)
            sha = sync.digest(skill.get('content'))
            log('config %s status=%s skill_sha=%s drift=%s' % (
                role, agent.get('status'), sha[:12], drift or 'OK'))
        except Exception as error:
            log('config %s check failed: %r' % (role, error))


def last_dispatch_role(task_id: str) -> str:
    bundles = RUNS / 'transport' / 'prompt-bundles' / task_id
    candidates = sorted(bundles.glob('*/context.json'), key=lambda p: p.stat().st_mtime) if bundles.exists() else []
    for path in reversed(candidates):
        try:
            context = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        role = context.get('role')
        if role:
            return str(role)
    return 'unknown'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request-file', type=Path, default=REQUEST_DEFAULT)
    parser.add_argument('--cookie-file', type=Path, default=COOKIE_DEFAULT)
    parser.add_argument('--round-timeout', type=int, default=7200)
    parser.add_argument('--silence-timeout', type=int, default=600)
    parser.add_argument('--wait-cap', type=int, default=1200)
    parser.add_argument('--skip-cleanup', action='store_true')
    parser.add_argument('--scenario', choices=('recommend', 'reject'), default='recommend',
                        help='reject: answer clarify rounds against the recommendation (negative path)')
    arguments = parser.parse_args()

    request_text = arguments.request_file.read_text(encoding='utf-8-sig').strip()
    if not arguments.skip_cleanup:
        for attempt in range(1, 4):
            if cleanup_issues() == 0:
                break
            log('cleanup attempt %d failed; retrying in 30s' % attempt)
            time.sleep(30)
    ensure_gateway()
    ensure_broker_token()
    log('scenario=%s' % arguments.scenario)
    log_config_fingerprint()

    started_at = time.time()
    launched = launch_with_retries(request_text)
    if launched is None:
        record_round({'task_id': 'unknown', 'outcome': 'failure',
                      'duration_sec': int(time.time() - started_at),
                      'note': 'no task id after 3 launch attempts (see launch log tails)',
                      'started_at': started_at, 'scenario': arguments.scenario})
        return 1
    pid, handle, task_id = launched
    log('task_id=%s' % task_id)
    try:
        outcome, note, visited = patrol(task_id, pid, arguments.silence_timeout,
                                        arguments.wait_cap, started_at + arguments.round_timeout,
                                        arguments.scenario)
    except Exception as error:
        outcome, note, visited = 'failure', 'driver bug: %r' % (error,), []
    finally:
        stop_process(pid)
        handle.close()
    duration = int(time.time() - started_at)
    if outcome == 'success':
        final_state = 'REQ_HANDOFF'
    else:
        log('failure dispatch role=%s' % last_dispatch_role(task_id))
        records = journal(task_id)
        current = states(records)
        final_state = current[-1] if current else 'UNKNOWN'
        if final_state not in ('REQ_CANCELLED', 'FAILED', 'REQ_ENGINE_ERROR'):
            time.sleep(130)
            deadline = time.time() + 240
            while time.time() < deadline:
                run_cli(['--runs-root', str(RUNS), '--cancel', task_id], timeout=200)
                time.sleep(10)
                if states(journal(task_id))[-1:] == ['REQ_CANCELLED']:
                    final_state = 'REQ_CANCELLED'
                    break
    record_round({'task_id': task_id, 'outcome': outcome, 'duration_sec': duration,
                  'note': note, 'states': visited, 'started_at': started_at,
                  'finished_at': time.time(), 'final_state': final_state,
                  'scenario': arguments.scenario})
    log('round %s: %s (%s)' % (task_id, outcome, note[:160]))
    return 0 if outcome == 'success' else 1


if __name__ == '__main__':
    sys.exit(main())
