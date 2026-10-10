import unittest
from dataclasses import replace
from orchestrator.domain.requirements.document import *
from requirements_test_support import make_state, complete_draft


class DocumentTests(unittest.TestCase):
    def test_authored_complete_document_and_hash(self):
        state = make_state()
        draft = complete_draft(state)
        self.assertEqual(validate_draft(draft, state.consensus), ())
        self.assertIn('并发登录', render_markdown(draft))
        self.assertEqual(text_digest(render_markdown(draft)), draft.document_digest)

    def test_text_blocks_and_many_topics_are_legal(self):
        state = make_state()
        section = DraftSection('S_scope', 'scope', '会话标识', '```text\nprojectId + uid\n```')
        draft = complete_draft(state, section)
        draft = replace(draft, sections=draft.sections + tuple(DraftSection(f'T{i}', 'topic', f'专题{i}', '具体规则') for i in range(10)))
        draft = replace(draft, document_digest=text_digest(render_markdown(draft)))
        self.assertEqual(validate_draft(draft, state.consensus), ())

    def test_skeleton_is_not_deliverable(self):
        state = make_state()
        self.assertTrue(validate_draft(new_skeleton('想改善登录'), state.consensus))
