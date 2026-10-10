"""Deterministic rubric checks over markdown-section drafts (T0.1)."""
import unittest
from orchestrator.domain.requirements.document import render_markdown
from orchestrator.domain.requirements.model import CORE_SECTIONS, DraftRevision, DraftSection, text_digest
from orchestrator.domain.requirements.rubric import score
from requirements_test_support import make_state


def build(bodies, bindings=None, kinds=CORE_SECTIONS):
    bindings = bindings or {}
    sections = tuple(DraftSection('S_'+kind, kind, kind, bodies.get(kind, ''),
                                  bindings.get(kind, {}).get('requirement_ids', ()),
                                  bindings.get(kind, {}).get('decision_ids', ()),
                                  bindings.get(kind, {}).get('fact_ids', ()))
                     for kind in kinds)
    return DraftRevision(1, '需求草案', sections, make_state().consensus.digest, '0'*64)


FULL_BODIES = {
    'background': '重复登录来自会话标识复用。',
    'goals': '同一会话重复登录只建立一个连接。',
    'constraints': '登录通道由平台提供，无法绕过。',
    'behavior': '同一会话重复登录时复用既有连接。',
    'scope': '重试到成功或最终错误为止，不负责界面展示。',
    'acceptance': '单元测试：同一会话第二次登录返回既有连接。',
    'non_goals': '不做断点续传。',
    'rules': '会话标识以平台返回为准。',
}
FULL_BINDINGS = {kind: {'requirement_ids': ('req-000001',)} for kind in
                 ('goals', 'constraints', 'behavior', 'scope')}


class RubricTests(unittest.TestCase):
    def test_full_score_fixture(self):
        result = score(build(FULL_BODIES, FULL_BINDINGS), make_state())
        self.assertTrue(result.passed)
        self.assertEqual(result.score, len(result.checks))
        self.assertEqual(score(build(FULL_BODIES, FULL_BINDINGS), make_state()).to_dict(), result.to_dict())

    def test_missing_required_chapter(self):
        kinds = tuple(k for k in CORE_SECTIONS if k != 'acceptance')
        result = score(build(FULL_BODIES, FULL_BINDINGS, kinds), make_state())
        self.assertFalse(result.passed)
        self.assertIn('required-chapters', [c.slug for c in result.checks if not c.passed])

    def test_acceptance_without_test_type(self):
        bodies = dict(FULL_BODIES, acceptance='同一会话第二次登录返回既有连接。')
        result = score(build(bodies, FULL_BINDINGS), make_state())
        self.assertFalse(result.passed)

    def test_internal_id_leak(self):
        bodies = dict(FULL_BODIES, behavior='见 req-000001 的行为。')
        result = score(build(bodies, FULL_BINDINGS), make_state())
        self.assertIn('no-internal-ids', [c.slug for c in result.checks if not c.passed])

    def test_heading_duplication(self):
        bodies = dict(FULL_BODIES, goals='## 目标\n同一会话重复登录只建立一个连接。')
        result = score(build(bodies, FULL_BINDINGS), make_state())
        self.assertIn('no-heading-duplication', [c.slug for c in result.checks if not c.passed])

    def test_placeholder_tokens(self):
        bodies = dict(FULL_BODIES, scope='TODO 补充边界。')
        result = score(build(bodies, FULL_BINDINGS), make_state())
        self.assertIn('no-placeholders', [c.slug for c in result.checks if not c.passed])

    def test_unconfirmed_threshold_and_pending_escape(self):
        bodies = dict(FULL_BODIES, goals='延迟低于 200ms。')
        result = score(bodies and build(bodies, FULL_BINDINGS), make_state())
        self.assertIn('no-unconfirmed-thresholds', [c.slug for c in result.checks if not c.passed])
        bodies = dict(FULL_BODIES, goals='延迟目标待定（原表述 200ms）。')
        self.assertTrue(score(build(bodies, FULL_BINDINGS), make_state()).passed)

    def test_provenance_binding_required_on_key_sections(self):
        bindings = {k: v for k, v in FULL_BINDINGS.items() if k != 'scope'}
        result = score(build(FULL_BODIES, bindings), make_state())
        self.assertIn('provenance-binding', [c.slug for c in result.checks if not c.passed])

    def test_empty_draft_fails(self):
        result = score(build({}), make_state())
        self.assertFalse(result.passed)
        self.assertLess(result.score, len(result.checks))


if __name__ == '__main__':
    unittest.main()
