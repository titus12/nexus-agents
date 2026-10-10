"""Shared scripted Menxia document replies; no workflow entrypoint."""
from __future__ import annotations

import tempfile

import unittest

from dataclasses import replace

from orchestrator.adapters import FakeMulticaAdapter

from orchestrator.app import OrchestratorApp

from orchestrator.domain.context import MenxiaParallelLimits, ParallelState, ZhongshuParallelLimits, ProgressState, RequestState, TaskIdentity, WorkflowContext

from orchestrator.domain.menxia_doc import BODY_PART_TITLES, MenxiaGroupDoc, changed_body_sections

from orchestrator.transport.external import ExternalMessage

def _acceptance_entries(requirement_markdown: str, items: object) -> tuple[str, ...]:
    """Acceptance-map left column: the group requirement document's §8 lines.

    ``menxia_approval_blockers`` only accepts map rows whose left side the
    requirement document stated verbatim, so the scripted solver derives its
    rows from the dispatched ``requirement_markdown`` (fallback: the member
    items' acceptance signals).
    """
    from orchestrator.domain.zhongshu_doc import SECTION_ACCEPTANCE, ZhongshuDocError, ZhongshuRequirementDoc
    if requirement_markdown.strip():
        try:
            parsed = ZhongshuRequirementDoc.parse(requirement_markdown)
        except ZhongshuDocError:
            parsed = None
        if parsed is not None:
            lines = tuple((line.strip() for line in parsed.section(SECTION_ACCEPTANCE).splitlines() if line.strip()))
            if lines:
                return lines
    signals: list[str] = []
    for item in items or ():
        for signal in item.get('acceptance_signals') or ():
            text = str(signal).strip()
            if text and text not in signals:
                signals.append(text)
    return tuple(signals) or ('基线验收',)

def _six_part_body(group_id: str, entries: tuple[str, ...]) -> str:
    """A plan body in the six fixed parts (requirements §9), map rows closing
    against the given acceptance entries."""
    parts = {'目标与设计决策': f'按基线执行 {group_id}，不做额外决策。', '现状与目标行为': '当前行为与目标行为如基线所述。', '统一约束': '遵循统一约束。', '任务分解': f'- {group_id} -> 一次执行。', '验收映射': '\n'.join((f'- {entry} -> 验收用例覆盖。' for entry in entries)), '风险与兼容': '风险可控。'}
    return '\n\n'.join((f'### {title}\n{parts[title]}' for title in BODY_PART_TITLES))

class GroupWaveScripting:
    """Scripted menxia group-wave replies that evolve the shared document.

    Every reply is built by mechanically evolving the dispatched document, so
    ``verify_group_reply`` is the real gatekeeper on this path — a scripted
    reply that breaks the document rules folds as MENXIA_GROUP_DOC_BREACH.

    Subclasses set ``critic_demands_revision = False`` for the short loop:
    no reviewer suggestions, the critic approves the first document.
    """
    critic_demands_revision: bool = True

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.solver_rounds: dict[str, int] = {}
        self.analyst_rounds: dict[str, int] = {}
        self.critic_rounds: dict[str, int] = {}

    def _reply(self, request, payload: dict) -> None:
        context = request.context
        body = {'task_id': request.task_id, 'request_id': request.request_id, 'phase': request.phase, 'role': request.role, 'revision_id': str(context.get('revision_id') or ''), 'plan_hash': str(context.get('plan_hash') or '')}
        body.update(payload)
        self.queue_reply(request.request_id, ExternalMessage(request.agent_id, body, request.request_id))

    def _reply_group_wave(self, request) -> None:
        context = request.context
        group_id = str(context.get('group_id') or '')
        stage = str(context.get('stage') or '')
        doc = MenxiaGroupDoc.parse(str(context.get('doc_markdown') or ''))
        if stage == 'SOLVING':
            payload = self._solver_reply(request, group_id, doc)
        elif stage == 'ANALYZING':
            payload = self._analyst_reply(group_id, doc)
        else:
            payload = self._critic_reply(group_id, doc)
        self._reply(request, payload)

    def _solver_reply(self, request, group_id: str, doc: MenxiaGroupDoc) -> dict:
        round_index = self.solver_rounds.get(group_id, 0)
        self.solver_rounds[group_id] = round_index + 1
        if round_index == 0:
            doc = replace(doc, version=doc.version + 1, body_markdown=_six_part_body(group_id, _acceptance_entries(str(request.context.get('requirement_markdown') or ''), request.context.get('items') or ())))
            action = 'FEASIBLE'
            absorbed: list[str] = []
        else:
            absorbed = [suggestion.suggestion_id for suggestion in doc.suggestions]
            doc = doc.with_version(doc.version + 1)
            for suggestion_id in absorbed:
                doc = doc.absorb(suggestion_id, note='applied to the body')
            doc = replace(doc, body_markdown=doc.body_markdown + f'\n{group_id} 评审意见已吸收。')
            action = 'READY_FOR_ANALYST'
        previous = MenxiaGroupDoc.parse(str(request.context.get('doc_markdown') or ''))
        touched = list(changed_body_sections(previous, doc))
        return {'action': action, 'summary': f'group {group_id} document v{doc.version}', 'group_id': group_id, 'doc_version': doc.version, 'doc_markdown': doc.render(), 'touched_scope': touched, 'absorbed_ids': absorbed, 'rejected_ids': []}

    def _analyst_reply(self, group_id: str, doc: MenxiaGroupDoc) -> dict:
        pass_index = self.analyst_rounds.get(group_id, 0)
        self.analyst_rounds[group_id] = pass_index + 1
        if pass_index > 0 or not self.critic_demands_revision:
            return self._reviewer_payload('EVIDENCE_SUFFICIENT', group_id, doc, [])
        doc = doc.add_suggestion(author='analyst', severity='P1', body=f'acceptance signal for {group_id} must name an observable check')
        return self._reviewer_payload('EVIDENCE_SUFFICIENT', group_id, doc, [doc.suggestions[-1].suggestion_id])

    def _critic_reply(self, group_id: str, doc: MenxiaGroupDoc) -> dict:
        pass_index = self.critic_rounds.get(group_id, 0)
        self.critic_rounds[group_id] = pass_index + 1
        if pass_index > 0 or not self.critic_demands_revision:
            return self._reviewer_payload('APPROVE_GROUP', group_id, doc, [])
        doc = doc.add_suggestion(author='critic', severity='P1', body=f'risk register missing for {group_id}')
        return self._reviewer_payload('REQUEST_SOLVER_REVISION', group_id, doc, [doc.suggestions[-1].suggestion_id])

    @staticmethod
    def _reviewer_payload(action: str, group_id: str, doc: MenxiaGroupDoc, added_ids: list[str]) -> dict:
        return {'action': action, 'group_id': group_id, 'doc_markdown': doc.render(), 'added_suggestion_ids': added_ids}
