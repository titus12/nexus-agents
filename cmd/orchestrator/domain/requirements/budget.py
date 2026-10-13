"""Durable finite work policy. Waiting and replay never consume model calls.

Policy v2 (spec T8): format repairs are free — they only grow ``total_calls``
(never ``calls``, so the per-task 12-call budget is untouched) and stay capped
at two per dispatch.  ``TOTAL_CALLS_CAP`` is the safety net against endless
format-repair accumulation.  v1 ledgers (persisted before this change) keep
the old counting semantics.
"""
from dataclasses import replace
from .model import BudgetLedger, DispatchCharge, EffectCharge, digest

TOTAL_CALLS_CAP = 40


class BudgetExceeded(ValueError):
    pass


def _v2(ledger):
    return ledger.policy_version == 'requirements-policy-v2'


def charge_dispatch(ledger, logical_dispatch_id, kind='initial', attempt=1):
    previous = next((c for c in ledger.dispatch_charges if c.logical_dispatch_id == logical_dispatch_id), None)
    if previous and attempt <= previous.attempts:
        return ledger
    if _v2(ledger) and ledger.total_calls >= TOTAL_CALLS_CAP:
        raise BudgetExceeded('TOTAL_CALL_BUDGET_EXHAUSTED')
    if ledger.calls >= 12:
        raise BudgetExceeded('MODEL_CALL_BUDGET_EXHAUSTED')
    if kind == 'semantic' and ledger.semantic_repairs >= 5:
        raise BudgetExceeded('SEMANTIC_REPAIR_BUDGET_EXHAUSTED')
    if kind == 'evidence' and ledger.evidence_repairs >= 2:
        raise BudgetExceeded('EVIDENCE_REPAIR_BUDGET_EXHAUSTED')
    formats = previous.format_repairs if previous else 0
    externals = previous.external_retries if previous else 0
    if kind == 'format' and formats >= 2:
        raise BudgetExceeded('FORMAT_REPAIR_BUDGET_EXHAUSTED')
    if kind == 'external' and externals >= 2:
        raise BudgetExceeded('EXTERNAL_RETRY_BUDGET_EXHAUSTED')
    row = DispatchCharge(logical_dispatch_id, previous.kind if previous else kind, attempt,
                         formats + (kind == 'format'), externals + (kind == 'external'))
    charges = tuple(c for c in ledger.dispatch_charges if c.logical_dispatch_id != logical_dispatch_id) + (row,)
    free = _v2(ledger) and kind == 'format'
    return replace(ledger, calls=ledger.calls + (0 if free else 1), total_calls=ledger.total_calls+1,
                   semantic_repairs=ledger.semantic_repairs+(kind == 'semantic'),
                   evidence_repairs=ledger.evidence_repairs+(kind == 'evidence'), dispatch_charges=charges)


def charge_publication(ledger, effect_id):
    old = next((c for c in ledger.publication_retries if c.effect_id == effect_id), EffectCharge(effect_id))
    if old.count >= 4:
        raise BudgetExceeded('PUBLICATION_RETRY_BUDGET_EXHAUSTED')
    return replace(ledger, publication_retries=tuple(c for c in ledger.publication_retries if c.effect_id != effect_id)+(replace(old, count=old.count+1),))


def relevant_facts_digest(facts):
    return digest(sorted({(f.source, f.quote, f.source_fingerprint, f.status) for f in facts}))


def record_progress(ledger, document_digest, relevant_facts_digest, decisions_digest):
    progress = digest((document_digest, relevant_facts_digest, decisions_digest))
    stalled = ledger.stalled_count + 1 if progress == ledger.last_progress_digest else 0
    if stalled >= 2:
        raise BudgetExceeded('NO_EFFECTIVE_PROGRESS')
    return replace(ledger, last_progress_digest=progress, stalled_count=stalled)


def new_work_epoch(ledger, input_digest):
    if input_digest == ledger.epoch_input_digest:
        return ledger
    return BudgetLedger(work_epoch=ledger.work_epoch+1, epoch_input_digest=input_digest,
                        total_calls=ledger.total_calls)


def failure_outcome(code):
    if code in ('PREREQUISITE_MISSING', 'ACCESS_DENIED', 'SOURCE_UNAVAILABLE'):
        return 'REQ_BLOCKED'
    return 'REQ_ENGINE_ERROR'
