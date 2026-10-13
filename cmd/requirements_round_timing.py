"""Summarise one requirements round from the engine's own logs.

Acceptance evidence for ``requirements_bot_round.py``.  Every number comes from
the orchestrator logs: ``DOMAIN_TRANSITION`` gives state dwell times and the
failure text behind each retry, ``AGENT_RUN_SUMMARY`` gives per-run admission,
queue-to-running, run and result-read timing.

    python -X utf8 cmd/requirements_round_timing.py --task task-20261013-7b32f0 --table
    python -X utf8 cmd/requirements_round_timing.py --task task-20261013-7b32f0 --out timing.json
"""
from __future__ import annotations

import argparse
import ast
import glob
import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LINE = re.compile(
    r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)[+-]\d{4}\s+\[(\w+)\]\s+(\S+)\s+-\s+([A-Z][A-Z0-9_]+)\b(.*)$')
EVENTS = ('DOMAIN_TRANSITION', 'AGENT_RUN_SUMMARY')
FAILURE_STATES = ('REQ_BLOCKED', 'REQ_ENGINE_ERROR', 'REQ_RETRY_WAIT')


def load_events(logs_dir: Path, task_id: str) -> list[tuple[datetime, str, str]]:
    rows: list[tuple[datetime, str, str]] = []
    for name in sorted(glob.glob(str(logs_dir / 'orchestrator-*.log'))):
        with open(name, encoding='utf-8', errors='replace') as handle:
            for line in handle:
                if task_id not in line:
                    continue
                match = LINE.match(line.rstrip('\r\n'))
                if not match:
                    continue
                stamp, _level, _logger, event, rest = match.groups()
                if event in EVENTS and f'task_id={task_id}' in rest:
                    rows.append((datetime.fromisoformat(stamp), event, rest))
    rows.sort(key=lambda row: row[0])
    return rows


def _literal(text: str) -> str:
    try:
        return str(ast.literal_eval(text.strip()))
    except (ValueError, SyntaxError):
        return text.strip()


def build_summary(task_id: str, rows: list[tuple[datetime, str, str]]) -> dict:
    transitions: list[dict] = []
    runs: list[dict] = []
    for stamp, event, rest in rows:
        if event == 'DOMAIN_TRANSITION':
            before = re.search(r'\bbefore=(\S+):(\d+)', rest)
            after = re.search(r'\bafter=(\S+):(\d+)', rest)
            action = re.search(r'\baction=(\S*)', rest)
            failure = re.search(r'\bfailure=(.*)$', rest)
            transitions.append({
                'at': stamp,
                'from': before.group(1) if before else '',
                'to': after.group(1) if after else '',
                'action': action.group(1) if action else '',
                'failure': _literal(failure.group(1)) if failure else '',
            })
        elif event == 'AGENT_RUN_SUMMARY':
            fields = dict(re.findall(r'(\w+)=(\S*)', rest))
            fields['at'] = stamp.isoformat(timespec='seconds')
            runs.append(fields)
    if not transitions:
        return {'task_id': task_id, 'error': 'no DOMAIN_TRANSITION records', 'agent_runs': runs}
    timeline = []
    for index, item in enumerate(transitions):
        leave = transitions[index + 1]['at'] if index + 1 < len(transitions) else None
        timeline.append({
            'state': item['to'],
            'entered_at': item['at'].isoformat(timespec='seconds'),
            'seconds': None if leave is None else round((leave - item['at']).total_seconds(), 1),
        })
    per_state: dict[str, dict] = {}
    for entry in timeline:
        bucket = per_state.setdefault(entry['state'], {'state': entry['state'], 'entries': 0, 'seconds': 0.0})
        bucket['entries'] += 1
        bucket['seconds'] = round(bucket['seconds'] + (entry['seconds'] or 0.0), 1)
    retries = [{'at': item['at'].isoformat(timespec='seconds'), 'state': item['from'], 'failure': item['failure']}
               for item in transitions if item['action'] == 'RETRYABLE_FAILURE']
    failures = [{'at': item['at'].isoformat(timespec='seconds'), 'state': item['to'], 'failure': item['failure']}
                for item in transitions if item['to'] in FAILURE_STATES]
    counts = {
        'clarify_rounds': sum(1 for item in transitions if item['to'] == 'REQ_CLARIFY_WAIT'),
        'evidence_rounds': sum(1 for item in transitions if item['to'] == 'REQ_EVIDENCE'),
        'retries': len(retries),
        'critic_retries': sum(1 for row in retries if row['state'] == 'REQ_REVIEW'),
        'agent_runs': len(runs),
    }
    return {
        'task_id': task_id,
        'first_at': transitions[0]['at'].isoformat(timespec='seconds'),
        'last_at': transitions[-1]['at'].isoformat(timespec='seconds'),
        'span_seconds': round((transitions[-1]['at'] - transitions[0]['at']).total_seconds(), 1),
        'final_state': transitions[-1]['to'],
        'counts': counts,
        'states': list(per_state.values()),
        'timeline': timeline,
        'agent_runs': runs,
        'retries': retries,
        'failures': failures,
    }


def print_table(summary: dict) -> None:
    if 'error' in summary:
        print(summary['error'])
        return
    print('task %s  span %.1f min  final %s' % (summary['task_id'], summary['span_seconds'] / 60, summary['final_state']))
    print('counts ' + json.dumps(summary['counts'], ensure_ascii=False))
    print('%-26s %7s %9s' % ('state', 'entries', 'minutes'))
    for row in summary['states']:
        print('%-26s %7d %9.1f' % (row['state'], row['entries'], row['seconds'] / 60))
    if summary['agent_runs']:
        print()
        print('%-10s %-17s %-14s %7s %7s %7s %7s %6s %-9s %s' % (
            'state', 'role', 'attempt', 'admit', 'queue', 'run', 'result', 'polls', 'outcome', 'failure'))
        for run in summary['agent_runs']:
            print('%-10s %-17s %-14s %7s %7s %7s %7s %6s %-9s %s' % (
                run.get('state', '').replace('REQ_', '')[:10],
                run.get('role', '')[:17],
                run.get('request_id', '').split(':')[-1][-14:],
                run.get('admit_wait_s', ''), run.get('dispatch_to_running_s', ''),
                run.get('run_s', ''), run.get('result_s', ''), run.get('polls', ''),
                run.get('outcome', '')[:9], run.get('failure', '')))
    for row in summary['retries']:
        print('retry %s state=%s failure=%s' % (row['at'], row['state'], row['failure']))
    for row in summary['failures']:
        print('failure %s state=%s reason=%s' % (row['at'], row['state'], row['failure']))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--task', required=True)
    parser.add_argument('--logs-dir', type=Path, default=REPO / 'multica')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--table', action='store_true')
    args = parser.parse_args(argv)
    rows = load_events(args.logs_dir, args.task)
    if not rows:
        print('no engine events for %s under %s' % (args.task, args.logs_dir), file=sys.stderr)
        return 2
    summary = build_summary(args.task, rows)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.table or not args.out:
        print_table(summary)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
