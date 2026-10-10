"""Bounded metadata-only diagnostics for the human callback chain."""
import json
import logging
import re

logger = logging.getLogger('review_orchestrator_fsm')


def frame_metadata(frame):
    allowed = {'type', 'message_id', 'trace_id', 'sum', 'seq'}
    return {h.key: str(h.value)[:128] for h in frame.headers if h.key in allowed}


def record(name, **fields):
    # Callers pass identifiers/counts only, never bodies, tokens or credentials.
    logger.info('%s %s', name, json.dumps(fields, ensure_ascii=True, sort_keys=True))


def error_code(error):
    value = str(error)
    return value if re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', value) else type(error).__name__


def answer_progress(requirements, round_id):
    if requirements is None:
        return {}
    round_ = next((r for r in requirements.rounds if r.round_id == round_id), None)
    if round_ is None:
        return {'round_found': False}
    revisions = {q.question_id: q.revision for q in requirements.questions}
    covered = {a.question_id for a in requirements.answers
               if a.question_id in round_.question_ids and a.question_revision == revisions.get(a.question_id)}
    remaining = [q for q in round_.question_ids if q not in covered]
    return {'round_status': round_.status, 'answered_count': len(covered),
            'remaining_question_ids': remaining}


class SafeSdkHandler(logging.Handler):
    """Expose transport failures without SDK exception strings or frame contents."""
    def emit(self, event):
        if event.levelno >= logging.WARNING:
            record('GATEWAY_SDK_DIAGNOSTIC', level=event.levelname,
                   function=event.funcName, line=event.lineno,
                   error_type=(event.exc_info[0].__name__ if event.exc_info else 'SDKTransport'),
                   reason='SDK_WARNING_OR_ERROR_DETAILS_REDACTED')
