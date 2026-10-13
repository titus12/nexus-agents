"""P4 逐章确认上锁：迁移、摘要、棘轮、反馈解锁与派发。"""
import argparse
import tempfile
import unittest
from dataclasses import replace

from orchestrator.app import OrchestratorApp, _resolve_section_kinds
from orchestrator.domain.context import context_from_dto, context_to_dto
from orchestrator.domain.requirements.chapter_lock import (confirm_sections, lockable_sections,
    locked_violations, locked_payload, release_locks, section_digest)
from orchestrator.domain.requirements.document import (CANONICAL_ORDER, render_markdown,
    validate_draft)
from orchestrator.domain.requirements.model import DraftSection, RequirementsState, text_digest
from orchestrator.domain.requirements.dispatch import build_dispatch
from orchestrator.domain.requirements.review_brief import render_review_brief
from orchestrator.runtime.requirements_cutover import requirements_status
from requirements_test_support import make_state, complete_draft
from test_requirements_workflow_e2e import context, pump, Scripted


def locked_state(kinds=('behavior',)):
    state = replace(make_state(), draft=complete_draft(make_state()))
    return confirm_sections(state, kinds, actor_id='ou_100', event_id='evt-lock', occurred_at='2026-10-10T00:00:00+00:00')


def redraft(state, mutate):
    sections = tuple(mutate(section) for section in state.draft.sections)
    body = replace(state.draft, sections=sections)
    return replace(state, draft=replace(body, document_digest=text_digest(render_markdown(body))))


class MigrationTests(unittest.TestCase):
    def test_old_snapshot_without_chapter_locks_loads(self):
        state = replace(make_state(), draft=complete_draft(make_state()))
        raw = locked_state().to_dict()
        del raw['chapter_locks']
        self.assertEqual(RequirementsState.from_dict(raw), state)

    def test_roundtrip_preserves_locks(self):
        state = locked_state(('behavior', 'acceptance'))
        self.assertEqual(RequirementsState.from_dict(state.to_dict()), state)

    def test_unknown_top_level_still_rejected(self):
        raw = locked_state().to_dict()
        raw['future_field'] = 1
        with self.assertRaisesRegex(ValueError, 'unknown RequirementsState fields'):
            RequirementsState.from_dict(raw)

    def test_old_context_dto_loads_with_empty_locks(self):
        dto = context_to_dto(context())
        del dto['requirements']['chapter_locks']
        self.assertEqual(context_from_dto(dto).requirements.chapter_locks, ())


class DigestTests(unittest.TestCase):
    def test_section_digest_normalizes_line_endings_and_trailing_whitespace(self):
        base = DraftSection('S_behavior', 'behavior', '核心功能清单', '同一会话只建立一次连接。\n失败时提示。')
        noisy = DraftSection('S_behavior', 'behavior', '核心功能清单',
                             '同一会话只建立一次连接。\r\n失败时提示。  \n')
        self.assertEqual(section_digest(base), section_digest(noisy))
        changed = DraftSection('S_behavior', 'behavior', '核心功能清单', '同一会话只建立一次连接。\n失败时提示用户。')
        self.assertNotEqual(section_digest(base), section_digest(changed))

    def test_lockable_sections_requires_unique_nonempty_canonical(self):
        state = replace(make_state(), draft=complete_draft(make_state()))
        lockable = lockable_sections(state.draft)
        self.assertEqual(set(lockable), {s.kind for s in state.draft.sections})
        self.assertTrue(set(lockable) <= set(CANONICAL_ORDER))
        duplicated = replace(state.draft, sections=state.draft.sections
                             + (DraftSection('S_goals2', 'goals', '目标', '重复目标'),))
        self.assertNotIn('goals', lockable_sections(duplicated))

    def test_confirm_rejects_unknown_kind_and_pending_marker(self):
        state = replace(make_state(), draft=complete_draft(make_state()))
        with self.assertRaisesRegex(ValueError, 'SECTION_UNKNOWN'):
            confirm_sections(state, ('topic',), actor_id='u', event_id='e', occurred_at='t')
        pending = redraft(state, lambda s: replace(s, markdown='【待确认：重试几次？】')
                          if s.kind == 'behavior' else s)
        with self.assertRaisesRegex(ValueError, 'SECTION_HAS_PENDING_MARKER'):
            confirm_sections(pending, ('behavior',), actor_id='u', event_id='e', occurred_at='t')

    def test_release_locks_by_kinds_and_by_facts(self):
        state = locked_state(('behavior', 'acceptance'))
        self.assertEqual([lock.kind for lock in release_locks(state, ('behavior',)).chapter_locks],
                         ['acceptance'])
        self.assertEqual(release_locks(state).chapter_locks, ())
        touched = redraft(state, lambda s: replace(s, fact_ids=('fact-000001',), markdown=s.markdown + '\n引用。')
                          if s.kind == 'behavior' else s)
        touched = confirm_sections(touched, ('behavior', 'acceptance'), actor_id='u', event_id='e', occurred_at='t')
        self.assertEqual([lock.kind for lock in release_locks(touched, fact_ids=('fact-000001',)).chapter_locks],
                         ['acceptance'])

    def test_locked_violations_report_missing_and_changed(self):
        state = locked_state(('behavior',))
        self.assertEqual(locked_violations(state.draft, state.chapter_locks), ())
        changed = redraft(state, lambda s: replace(s, markdown=s.markdown + '\n补充一行。')
                          if s.kind == 'behavior' else s)
        self.assertEqual(locked_violations(changed.draft, state.chapter_locks), (('behavior', 'changed'),))
        missing = redraft(state, lambda s: s if s.kind != 'behavior'
                          else replace(s, markdown=''))
        self.assertEqual(locked_violations(missing.draft, state.chapter_locks), (('behavior', 'missing'),))


class ValidateGateTests(unittest.TestCase):
    def test_changed_locked_section_blocks_validation(self):
        state = locked_state(('behavior',))
        changed = redraft(state, lambda s: replace(s, markdown=s.markdown + '\n顺手润色。')
                          if s.kind == 'behavior' else s)
        issues = validate_draft(changed.draft, changed.consensus, changed.decisions, changed.chapter_locks)
        row = next(i for i in issues if i.rule == 'LOCKED_SECTION_CHANGED')
        self.assertEqual(row.severity, 'blocking')
        self.assertIn('逐字一致', row.claim)
        self.assertEqual(row.acceptance, '恢复该章的已确认原文')
        self.assertEqual(validate_draft(state.draft, state.consensus, state.decisions, state.chapter_locks), ())

    def test_missing_locked_section_blocks_validation(self):
        state = locked_state(('behavior',))
        missing = redraft(state, lambda s: s if s.kind != 'behavior' else replace(s, markdown=''))
        issues = validate_draft(missing.draft, missing.consensus, missing.decisions, missing.chapter_locks)
        row = next(i for i in issues if i.rule == 'LOCKED_SECTION_MISSING')
        self.assertEqual(row.severity, 'blocking')
        self.assertIn('找不到该章的确认版本', row.claim)


def submit_confirm(app, kinds=('behavior', 'acceptance'), digest=None, gate=None, event_id='section-confirm'):
    req = app.ctx.requirements
    app.requirements_runtime.submit('REQUIREMENTS_SECTION_CONFIRMED',
        {'document_digest': digest or req.draft.document_digest, 'gate_id': gate or req.review_gate_id,
         'actor_id': 'local-operator', 'section_kinds': list(kinds)}, event_id)
    app.step()


class RatchetTests(unittest.TestCase):
    def test_confirmation_records_locks_and_reconfirm_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(context(), directory, multica=Scripted(), poll_interval=0, timeout_seconds=2)
            pump(app, 'REQ_CONFIRM_WAIT')
            submit_confirm(app)
            locks = app.ctx.requirements.chapter_locks
            self.assertEqual([lock.kind for lock in locks], ['behavior', 'acceptance'])
            self.assertEqual(locks[0].actor_id, 'local-operator')
            self.assertTrue(locks[0].event_id.startswith('requirements-human:'))
            self.assertEqual(locks[0].document_version, app.ctx.requirements.draft.version)
            before = app.ctx.requirements.chapter_locks
            submit_confirm(app, event_id='section-confirm-2')
            self.assertEqual(app.ctx.requirements.chapter_locks, before)
            self.assertEqual(app.ctx.progression.state, 'REQ_CONFIRM_WAIT')

    def test_stale_digest_unknown_actor_and_wrong_state_are_soft_rejects(self):
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(context(), directory, multica=Scripted(), poll_interval=0, timeout_seconds=2)
            pump(app, 'REQ_CONFIRM_WAIT')
            submit_confirm(app, digest='0' * 64)
            self.assertEqual(app.ctx.requirements.reason, 'STALE_SECTION_CONFIRMATION')
            self.assertEqual(app.ctx.requirements.chapter_locks, ())
            app.requirements_runtime.submit('REQUIREMENTS_SECTION_CONFIRMED',
                {'document_digest': app.ctx.requirements.draft.document_digest,
                 'gate_id': app.ctx.requirements.review_gate_id, 'actor_id': 'ou_999',
                 'section_kinds': ['behavior']}, 'no-member')
            app.step()
            self.assertEqual(app.ctx.requirements.reason, 'UNAUTHORIZED_SECTION_CONFIRMATION')
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(context(), directory, multica=Scripted(), poll_interval=0, timeout_seconds=2)
            pump(app, 'REQ_ANALYZE')
            submit_confirm(app)
            self.assertEqual(app.ctx.requirements.reason, '当前不能确认章节：REQ_ANALYZE')

    def test_feedback_with_section_kinds_rewrites_text_and_unlocks_only_targeted(self):
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(context(), directory, multica=Scripted(), poll_interval=0, timeout_seconds=2)
            pump(app, 'REQ_CONFIRM_WAIT')
            submit_confirm(app)
            app.requirements_runtime.submit('REQUIREMENTS_FEEDBACK',
                {'actor_id': 'local-operator', 'text': '验收要加性能测试', 'section_kinds': ['acceptance']},
                'fb-1')
            app.step()
            req = app.ctx.requirements
            self.assertEqual([lock.kind for lock in req.chapter_locks], ['behavior'])
            self.assertIn('【针对章节：验收标准】验收要加性能测试', req.sources[-1].text)

    def test_feedback_without_section_kinds_unlocks_all(self):
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(context(), directory, multica=Scripted(), poll_interval=0, timeout_seconds=2)
            pump(app, 'REQ_CONFIRM_WAIT')
            submit_confirm(app)
            app.requirements_runtime.submit('REQUIREMENTS_FEEDBACK',
                {'actor_id': 'local-operator', 'text': '方向不对，重写。'}, 'fb-2')
            app.step()
            self.assertEqual(app.ctx.requirements.chapter_locks, ())

    def test_dispatch_carries_locked_sections_for_draft_and_review_not_analyze(self):
        state = locked_state(('behavior',))
        for target, present in (('REQ_DRAFT', True), ('REQ_REVIEW', True), ('REQ_ANALYZE', False)):
            _, effect = build_dispatch(context(), state, target)
            dispatch = effect.payload['dispatch_context']
            self.assertEqual('locked_sections' in dispatch, present, target)
        _, effect = build_dispatch(context(), state, 'REQ_DRAFT')
        locked = effect.payload['dispatch_context']['locked_sections']
        self.assertEqual(locked[0]['kind'], 'behavior')
        self.assertEqual(locked[0]['markdown'],
                         next(s.markdown for s in state.draft.sections if s.kind == 'behavior'))


class SupportTests(unittest.TestCase):
    def test_review_brief_lists_confirmed_section_requests(self):
        state = redraft(replace(make_state(), draft=complete_draft(make_state())),
                        lambda s: s)
        requested = replace(state, draft=replace(state.draft,
            change_summary='常规改动。\n【申请修改已确认章节：验收标准】用户要求性能压测'))
        brief = render_review_brief(requested)
        self.assertIn('Solver 申请修改你已确认的章节', brief)
        self.assertIn('【申请修改已确认章节：验收标准】', brief)
        self.assertNotIn('Solver 申请修改你已确认的章节', render_review_brief(state))

    def test_kind_aliases_accept_titles_and_reject_unknown(self):
        parser = argparse.ArgumentParser()
        self.assertEqual(_resolve_section_kinds(parser, ['验收标准', 'behavior']), ['acceptance', 'behavior'])
        with self.assertRaises(SystemExit):
            _resolve_section_kinds(parser, ['不存在的章'])

    def test_requirements_status_reads_persisted_locks(self):
        with tempfile.TemporaryDirectory() as directory:
            app = OrchestratorApp(context(), directory, multica=Scripted(), poll_interval=0, timeout_seconds=2)
            pump(app, 'REQ_CONFIRM_WAIT')
            submit_confirm(app)
            status = requirements_status(directory, app.ctx.identity.task_id)
            self.assertEqual(status['state'], 'REQ_CONFIRM_WAIT')
            self.assertEqual([lock['kind'] for lock in status['chapter_locks']], ['behavior', 'acceptance'])
            self.assertEqual(status['document_digest'], app.ctx.requirements.draft.document_digest)
            self.assertFalse(status['historical_read_only'])


if __name__ == '__main__':
    unittest.main()
