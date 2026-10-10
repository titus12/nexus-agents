"""Deterministic prompt projections with no transport or file I/O."""
from __future__ import annotations
from collections.abc import Mapping
from dataclasses import dataclass
import json
from types import MappingProxyType
from ..context import WorkflowContext
from ..errors import is_reply_failure
from ...acceptance_standards import acceptance_standard_applies_to, acceptance_standard_block
from ...contracts import contract_for_state
JSON_STRING_RULES = '\n[JSON string rules] Inside every string value: escape each double quote as \\" (or quote prose with 「」 instead); write each backslash as \\\\ (a Windows path is D:\\\\workspace\\\\src); write line breaks and tabs as \\n and \\t, never as raw characters. If you can run code, build the result as a Python dict, write it with json.dump(ensure_ascii=False) to a UTF-8 file, confirm json.load reads that file back, and only then submit exactly that validated text.'

@dataclass(frozen=True)
class PromptSpec:
    state: str
    phase: str
    role: str
    content: str
    references: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, 'references', MappingProxyType(dict(self.references)))

def retry_feedback(context: WorkflowContext, state: str, worker_id: str='') -> str:
    """Restate the validator's rejection so a re-ask is not a blind re-sample.

    A reply budget only helps if the agent learns what was wrong: without this
    the model re-emits the same document, burns another full round, and the
    orchestrator reports the same rejection again.  Only the failure of the state
    being dispatched is restated, so a critic slip is never fed to the solver.

    When the wave failure carries a per-worker ledger and a ``worker_id`` is
    given, that worker's own rejection is restated and workers without one get
    nothing: a sibling's error is noise that makes the re-ask fix the wrong
    defect (task-20260929-dad75d wave 4).
    """
    failure = getattr(context, 'recovery', None)
    failure = getattr(failure, 'last_failure', None)
    if failure is None:
        return ''
    if worker_id:
        rejections = tuple(getattr(failure, 'worker_rejections', ()) or ())
        own = [str(text) for entry_worker, text in rejections if str(entry_worker) == str(worker_id) and is_reply_failure(str(text).split(':', 1)[0])]
        if own and str(getattr(failure, 'state', '') or '') != str(state):
            return ''
        if own:
            reason = ' '.join(own[0].split())
            if not reason:
                return ''
            return f'\n[Retry feedback] The orchestrator rejected your previous reply: {reason[:600]}\nCorrect exactly that defect and return one complete structured result again.\n'
        if rejections:
            return ''
    if not is_reply_failure(getattr(failure, 'error_code', '')):
        return ''
    if str(getattr(failure, 'state', '') or '') != str(state):
        return ''
    reason = ' '.join(str(getattr(failure, 'message', '') or '').split())
    if not reason:
        return ''
    return f'\n[Retry feedback] The orchestrator rejected your previous reply: {reason[:600]}\nCorrect exactly that defect and return one complete structured result again.\n'

def build_prompt(context: WorkflowContext, *, target_state: str | None=None, worker_id: str='', evidence_findings: tuple[object, ...] | None=None) -> PromptSpec:
    """Project immutable context into a bounded role prompt.

    ``target_state`` is the state the dispatched agent must act as.  Dispatching
    passes the transition target so the prompt never names the state the
    orchestrator is transitioning *from* (which previously told the Solver it
    was in ``ZHONGSHU_CRITIC`` and the Analyst it was in ``REQUEST_INTAKE``).
    """
    state = target_state or context.progression.state
    phase = 'MENXIA'
    role = 'review-critic' if state.endswith(('CRITIC', 'GATE')) else 'review-analyst' if state.endswith('ANALYST') else 'review-solver'
    findings = len(context.review.findings) if context.review else 0
    revision_task = ''
    evidence_task = ''
    critic_evidence = ''
    try:
        contract_rules = contract_for_state(state).prompt_rules
    except KeyError:
        contract_rules = ()
    rules_block = '\n[Contract rules]\n' + '\n'.join((f'- {rule}' for rule in contract_rules)) if contract_rules else ''
    acceptance_block = '\n' + acceptance_standard_block() if acceptance_standard_applies_to(state) else ''
    content = f'Task {context.identity.task_id}\nState: {state}\nRequest:\n{context.request.raw_request}\nCurrent review finding count: {findings}\nReturn exactly one complete structured result for the bound state. The result must be strictly valid JSON: no trailing commas, no comments, no markdown fences, no text outside the JSON document.{JSON_STRING_RULES}{revision_task}{evidence_task}{critic_evidence}{rules_block}{acceptance_block}{retry_feedback(context, state, worker_id)}'
    return PromptSpec(state=state, phase=phase, role=role, content=content, references={'request_payload': context.request.payload_ref or '', 'last_result': context.delivery.last_result_ref or '', 'revision_id': context.review.revision_id if context.review else ''})
