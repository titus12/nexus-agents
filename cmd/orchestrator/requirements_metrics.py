"""Measured milestones and costs from durable records, never token estimates."""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path

from .domain.requirements.model import CORE_SECTIONS

QUALITY_AXES = ('intent','scenarios','consistency','acceptance','coherence')
HUMAN_WAIT = {'REQ_CLARIFY_WAIT','REQ_CONFIRM_WAIT','REQ_BLOCKED','REQ_ENGINE_ERROR'}
# Failure-source classification (spec 2026-10-10-zhongshu-scout-draft-first-modification.md T0.2):
# product failures belong to the workflow itself, bot_driver failures to the
# acceptance harness, infrastructure failures to transport/platform.  Order:
# bot_driver, then infrastructure, then everything else is product.
BOT_DRIVER_ERROR_CODES = {'ROUND_TIMEOUT', 'LAUNCH_FAILED', 'DRIVER_SCRIPT_ERROR'}
INFRASTRUCTURE_ERROR_CODES = {'AGENT_REPLY_OUTPUT_OVERFLOW', 'AGENT_TIMEOUT', 'REMOTE_RUN_FAILED',
    'REQUEST_NOT_INDEXED', 'NODE_FAILED', 'NODE_ACTION_CONFLICT', 'NOTIFICATION_NOT_DELIVERED',
    'PERSISTENCE_ERROR', 'INVALID_EFFECT_OUTCOME'}
MECHANICAL_ERROR_CODES = {'AGENT_REPLY_VALIDATION_FAILED', 'AGENT_REPLY_CONTRACT_REJECTED', 'FACT_EVIDENCE_INVALID'}


def failure_origin(error_code):
    code = str(error_code or '')
    if code in BOT_DRIVER_ERROR_CODES:
        return 'bot_driver'
    if code in INFRASTRUCTURE_ERROR_CODES:
        return 'infrastructure'
    return 'product'


def instant(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')) if value else None


def measure(task_root):
    root = Path(task_root)
    records = [json.loads(line) for line in (root/'workflow-events.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    transitions = [r for r in records if r.get('record_type') == 'transition']
    if not transitions:
        return {'status':'INCOMPLETE','reason':'no durable transitions'}
    context = transitions[-1]['snapshot']['context']
    req = context.get('requirements') or {}
    start = instant(transitions[0].get('recorded_at'))
    milestones = {}
    epochs = {}
    human_seconds = 0.0 if start else None
    for index,row in enumerate(transitions):
        ctx = row['snapshot']['context']
        state = ctx['progression']['state']
        at = instant(row.get('recorded_at'))
        draft = (ctx.get('requirements') or {}).get('draft')
        ledger = (ctx.get('requirements') or {}).get('budget')
        if ledger:
            epochs[ledger['work_epoch']] = ledger
        if (draft and draft['version'] > 1 and draft['title'].strip()
            and all(s['markdown'].strip() for s in draft['sections'])
            and set(CORE_SECTIONS) <= {s['kind'] for s in draft['sections']}):
            milestones.setdefault('first_complete_draft_seconds',(at-start).total_seconds() if at and start else None)
        if state == 'REQ_CONFIRM_WAIT':
            milestones.setdefault('first_review_ready_seconds',(at-start).total_seconds() if at and start else None)
        if index+1 < len(transitions) and state in HUMAN_WAIT:
            end = instant(transitions[index+1].get('recorded_at'))
            if at and end and human_seconds is not None:
                human_seconds += max(0,(end-at).total_seconds())
            else:
                human_seconds = None
    intents = {}
    started,finished = {},{}
    for row in records:
        kind = row.get('record_type')
        if kind == 'transition':
            for effect in row.get('effects',[]):
                intents[effect['effect_id']] = effect
        elif kind == 'effect_intent':
            intents[row['effect_id']] = row['effect']
        elif kind == 'effect_running':
            started.setdefault(row['effect_id'],instant(row.get('recorded_at')))
        elif kind == 'effect_result':
            finished[row['effect_id']] = instant(row.get('recorded_at'))
    roles = Counter()
    menxia = Counter()
    machine_seconds = 0.0
    missing_time = False
    for identity,at in started.items():
        request = intents[identity]['request']
        payload = request['payload']
        state = payload.get('target_state') or payload.get('state')
        if request['effect_type'] == 'agent_dispatch':
            target = roles if str(state).startswith('REQ_') else menxia
            target[payload.get('role','unknown')] += 1
        elif request['effect_type'] == 'node_dispatch':
            menxia[payload.get('role') or str(state)] += len(payload.get('worker_bindings') or payload.get('bindings') or [])
        if identity in finished:
            end = finished[identity]
            if at and end:
                machine_seconds += max(0,(end-at).total_seconds())
            else:
                missing_time = True
    bundles = {}
    bundle_paths = list(root.rglob('manifest.json'))
    shared = root.parent/'transport'/'prompt-bundles'/context['identity']['task_id']
    if shared.is_dir():
        bundle_paths += list(shared.rglob('manifest.json'))
    for path in bundle_paths:
        manifest = json.loads(path.read_text(encoding='utf-8'))
        if manifest.get('protocol') == 'nexus-prompt-bundle-v1' and manifest.get('task_id')==context['identity']['task_id']:
            bundles[manifest['request_id']] = sum(f['bytes'] for f in manifest['files'])
    failures = Counter()
    mechanical_failures = 0
    for row in records:
        if row.get('record_type') == 'effect_result' and row.get('status') not in (None, 'SUCCEEDED'):
            code = (row.get('failure') or {}).get('error_code') or 'UNKNOWN'
            failures[failure_origin(code)] += 1
            if code in MECHANICAL_ERROR_CODES:
                mechanical_failures += 1
    scout = {'fanouts': 0, 'probes': 0, 'probe_failures': 0}
    scout_dir = root.parent/'scout-reports'/context['identity']['task_id']
    if scout_dir.is_dir():
        for path in sorted(scout_dir.glob('*.json')):
            try:
                report = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            merged = report.get('merged') or {}
            scout['fanouts'] += 1
            scout['probes'] += int(merged.get('facets') or 0)
            scout['probe_failures'] += int(merged.get('failed') or 0)
    dispatch_attempts = sum(1 for effect in intents.values()
                            if (effect.get('request') or {}).get('effect_type') == 'agent_dispatch')
    budget = req.get('budget') or {}
    return {'status':'MEASURED','task_id':context['identity']['task_id'],'workflow':context['request']['workflow'],
        'state':context['progression']['state'],'outcome':req.get('outcome'),**milestones,
        'machine_effect_seconds':None if missing_time else machine_seconds,
        'completed_human_wait_seconds':human_seconds,'role_calls':dict(roles),'menxia_role_calls':dict(menxia),
        'clarification_rounds':len(req.get('rounds',[])),
        'failure_origins':dict(failures),'mechanical_failures':mechanical_failures,
        'agent_dispatch_attempts':dispatch_attempts,'scout':scout,
        'mechanical_failure_rate':None if not dispatch_attempts else round(mechanical_failures/dispatch_attempts,4),
        'menxia_requirements_returns':sum(1 for r in transitions if (r.get('transition') or {}).get('action')=='REQUIREMENTS_CHANGE_REQUESTED'),
        'budget':budget,'bundle_bytes':sum(bundles.values()) if bundles else None,
        'repairs':{'semantic':sum(b['semantic_repairs'] for b in epochs.values()),
            'evidence':sum(b['evidence_repairs'] for b in epochs.values()),
            'format':sum(c['format_repairs'] for b in epochs.values() for c in b['dispatch_charges']),
            'external':sum(c['external_retries'] for b in epochs.values() for c in b['dispatch_charges']),
            'publication':sum(c['count'] for b in epochs.values() for c in b['publication_retries'])},
        'provider_usage':None,'provider_usage_reason':'No durable provider usage was supplied; bytes are not tokens.'}


def assess_quality(case, task_root):
    root = Path(task_root)
    snapshot = json.loads((root/'workflow-state.json').read_text(encoding='utf-8'))
    req = snapshot['context']['requirements']
    draft = req.get('draft') or {}
    path = root/'manual-review.json'
    if not path.is_file():
        return {'status':'INCOMPLETE','reason':'human artifact review missing'}
    review = json.loads(path.read_text(encoding='utf-8'))
    if review.get('document_digest') != draft.get('document_digest') or not review.get('reviewer'):
        return {'status':'INCOMPLETE','reason':'review is unbound or reviewer missing'}
    scores = review.get('scores',{})
    if set(scores) != set(QUALITY_AXES) or any(type(s) is not int or not 0 <= s <= 4 for s in scores.values()):
        return {'status':'INCOMPLETE','reason':'all five human scoring axes required'}
    findings = review.get('findings')
    if not isinstance(findings,list) or not review.get('clause_evidence'):
        return {'status':'INCOMPLETE','reason':'explicit findings and clause-level evidence required'}
    failed = any(s < 3 for s in scores.values()) or any(f.get('critical') for f in findings)
    if not req.get('review_stamp',{}).get('passed'):
        failed = True
    status = 'FAIL' if failed else 'PASS' if review.get('business_confirmed') is True else 'INCOMPLETE'
    return {'status':status,'scores':scores,'findings':findings,'reviewer':review['reviewer'],
            'document_digest':draft.get('document_digest'),'case':case['case_id']}


def evaluate(fixtures, results, mechanisms_only=False):
    cases = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(Path(fixtures).glob('*/case.json'))]
    report = {'status':'MECHANISMS_ONLY' if mechanisms_only else 'INCOMPLETE','cases':[]}
    seen = set()
    for case in cases:
        samples = []
        for number in (1,2):
            root = Path(results)/case['case_id']/f'run-{number}'
            if not (root/'workflow-state.json').is_file():
                children = list(root.glob('*/workflow-state.json'))
                if len(children)==1:
                    root = children[0].parent
            if not (root/'workflow-state.json').is_file():
                samples.append({'status':'INCOMPLETE','reason':'independent model sample missing'})
                continue
            metrics = measure(root)
            quality = assess_quality(case,root) if not mechanisms_only else {'status':'NOT_EVALUATED'}
            if metrics['task_id'] in seen:
                quality = {'status':'FAIL','reason':'samples must be independent tasks'}
            seen.add(metrics['task_id'])
            samples.append({'metrics':metrics,'quality':quality,'status':quality['status']})
        report['cases'].append({'case_id':case['case_id'],'samples':samples})
    if not mechanisms_only and cases:
        statuses = [s['status'] for c in report['cases'] for s in c['samples']]
        report['status'] = 'FAIL' if 'FAIL' in statuses else 'PASS' if all(s=='PASS' for s in statuses) else 'INCOMPLETE'
    return report
