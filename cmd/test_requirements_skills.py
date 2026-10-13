"""P3 skill contracts: one verbatim chapter-contract block, rules and literal anchors."""
import unittest
from pathlib import Path

from orchestrator.domain.requirements.document import KIND_TITLES

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / 'docs' / 'multi' / 'runtime'
START, END = '<!-- chapter-contract:start -->', '<!-- chapter-contract:end -->'


def skill_text(name):
    return (SKILLS / name).read_text(encoding='utf-8')


def chapter_contract(name):
    text = skill_text(name)
    start, end = text.index(START), text.index(END)
    return text[start + len(START):end]


class ChapterContractTests(unittest.TestCase):
    def test_block_is_verbatim_identical_in_both_skills(self):
        self.assertEqual(chapter_contract('requirements-solver-skill.md'),
                         chapter_contract('requirements-critic-skill.md'))

    def test_block_appears_exactly_once_per_skill_and_has_all_titles(self):
        for name in ('requirements-solver-skill.md', 'requirements-critic-skill.md'):
            text = skill_text(name)
            with self.subTest(skill=name):
                self.assertEqual(text.count(START), 1)
                self.assertEqual(text.count(END), 1)
                for title in KIND_TITLES.values():
                    self.assertIn(title, chapter_contract(name))

    def test_critic_block_precedes_review_focus(self):
        text = skill_text('requirements-critic-skill.md')
        self.assertLess(text.index(END), text.index('审查重心'))

    def test_solver_skill_keeps_draft_first_rules(self):
        self.assertIn('【待确认：', skill_text('requirements-solver-skill.md'))
        self.assertIn('已确认章节', skill_text('requirements-solver-skill.md'))
        self.assertIn('【申请修改已确认章节：', skill_text('requirements-solver-skill.md'))

    def test_critic_skill_keeps_chapter_rules_and_lint_note(self):
        text = skill_text('requirements-critic-skill.md')
        self.assertIn('【待确认', text)
        self.assertIn('lint-', text)
        for rule in ('chapter-misplaced', 'chapter-duplicate', 'chapter-function-violation'):
            self.assertIn(rule, text)
        self.assertIn('locked_sections', text)

    def test_literal_skill_anchors_survive(self):
        for name in ('requirements-solver-skill.md', 'requirements-critic-skill.md'):
            text = skill_text(name)
            with self.subTest(skill=name):
                self.assertIn('ACTIVE_RUNTIME_SKILL:', text)
                self.assertIn('too large to post safely', text)
                self.assertIn('超长输出必须写入结果文件。', text)


class AnalystFacetTests(unittest.TestCase):
    def test_analyst_skill_is_valid_without_a_scout_facet(self):
        text = skill_text('requirements-analyst-skill.md')
        self.assertIn('dispatch_context.scout.facet', text)
        self.assertIn('没有 scout 焦点时', text)
        self.assertIn('按完整分析处理', text)
        self.assertIn('ACTIVE_RUNTIME_SKILL:', text)
        self.assertIn('questions 必须为 []', text)


class CriticContractRuleTests(unittest.TestCase):
    def test_contract_carries_the_two_chapter_rules(self):
        from orchestrator.contracts.requirements_critic import CONTRACT
        rules = CONTRACT.prompt_rules
        self.assertTrue(any(rule.startswith('Judge each chapter against the chapter contract') for rule in rules))
        self.assertTrue(any(rule.startswith('Chapter problems already reported by a lint-* advisory') for rule in rules))
        self.assertTrue(any('pending marker 【待确认】' in rule for rule in rules))


if __name__ == '__main__':
    unittest.main()
