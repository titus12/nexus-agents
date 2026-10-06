"""Scoped, explicit dispositions from authoritative Critic group reviews."""
from collections.abc import Mapping
from dataclasses import replace

from ..findings import normalize_finding_status

CLOSING_RESPONSES = {
    'RESOLVED': 'RESOLVED', 'ACCEPT': 'DEFERRED', 'ACCEPTED_RISK': 'DEFERRED',
    'DEFERRED': 'DEFERRED', 'WONT_FIX': 'WONT_FIX', 'WONT_VERIFY': 'WONT_VERIFY',
}


def group_disposition_error(prior, findings, responses, action=''):
    known = {str(row.get('finding_id')): row for row in prior}
    current = {str(row.get('finding_id')): row for row in findings}
    if len(current) != len(findings):
        return 'GROUP_FINDING_DUPLICATE_ID'
    for fid, row in current.items():
        if fid in known and row.get('item_id') != known[fid].get('item_id'):
            return f'GROUP_FINDING_OWNER_CHANGED:{fid}'
    if action == 'APPROVE_GROUP' and any(
        str(row.get('severity') or '').upper() in {'P0', 'P1'} and normalize_finding_status(
            row.get('status'), row.get('decision')) in {'OPEN', 'REOPENED', 'IN_REVIEW', 'ASSIGNED_TO_ANALYST', 'ASSIGNED_TO_SOLVER'}
        for row in findings
    ):
        return 'GROUP_APPROVAL_WITH_OPEN_BLOCKER'
    answered = set()
    for response in responses:
        fid = str(response.get('finding_id') or '')
        if fid in answered:
            return f'GROUP_RESPONSE_DUPLICATE_ID:{fid}'
        if fid not in known and fid not in current:
            return f'GROUP_RESPONSE_UNKNOWN_FINDING:{fid}'
        answered.add(fid)
        status = CLOSING_RESPONSES.get(str(response.get('response') or '').upper())
        if status and fid in current and normalize_finding_status(
            current[fid].get('status'), current[fid].get('decision')
        ) in {'OPEN', 'REOPENED', 'IN_REVIEW', 'ASSIGNED_TO_ANALYST', 'ASSIGNED_TO_SOLVER'}:
            return f'GROUP_RESPONSE_CONTRADICTION:{fid}'
        if (status and str(known.get(fid, current.get(fid, {})).get('severity') or '').upper() == 'P0'
                and not str(response.get('note') or '').strip()):
            return f'GROUP_RESPONSE_P0_REASON_MISSING:{fid}'
    for fid, row in known.items():
        if str(row.get('severity') or '').upper() == 'P0' and normalize_finding_status(
            row.get('status'), row.get('decision')
        ) in {'OPEN', 'REOPENED', 'IN_REVIEW', 'ASSIGNED_TO_ANALYST', 'ASSIGNED_TO_SOLVER'}:
            if fid not in current and fid not in answered:
                return f'GROUP_RESPONSE_P0_MISSING:{fid}'
    return ''


def apply_group_dispositions(findings, reviews, prior_findings=()):
    """Called only for Critic events whose rows passed dispatch correlation."""
    result = list(findings)
    prior_ids = {finding.finding_id for finding in prior_findings}
    for row in reviews or ():
        if not isinstance(row, Mapping):
            continue
        members = {str(value) for value in row.get('member_ids') or ()}
        group_id = str(row.get('group_id') or '')
        responses = {str(entry.get('finding_id')): entry for entry in row.get('finding_responses') or ()
                     if isinstance(entry, Mapping)}
        for index, finding in enumerate(result):
            if not finding.active or finding.item_id not in members or finding.group_id != group_id:
                continue
            response = responses.get(finding.finding_id) or {}
            status = CLOSING_RESPONSES.get(str(response.get('response') or '').upper())
            if (not status and row.get('action') == 'APPROVE_GROUP' and finding.severity != 'P0'
                    and finding.finding_id in prior_ids):
                status = 'RESOLVED'
            if status:
                result[index] = replace(finding, status=status, stuck_rounds=0,
                    resolution=str(response.get('note') or 'group explicitly approved by Critic'))
    return tuple(result)
