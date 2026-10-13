"""S5a stability measurement: parsing, comparisons and report differences."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from orchestrator.domain.requirements.document import KIND_TITLES, render_markdown, text_digest
from orchestrator.domain.requirements.model import DraftRevision, DraftSection
from orchestrator.domain.requirements.stability import (LITERAL_KINDS, char_bigrams, jaccard,
                                                        literal_comparison, load_run,
                                                        parse_approved_markdown, render_report)


SAMPLE_BODIES = {
    'background': '当前上传接口在高并发下偶发失败，需要自动重试。',
    'goals': '- 网络请求失败后自动重试，直到成功或返回最终错误。',
    'problem': '现有客户端在瞬时故障时直接报错，影响体验。',
    'constraints': '- 上传接口已支持幂等键\n- 不增加第三方依赖',
    'behavior': '- 触发条件：请求失败且错误可重试；系统行为：按指数退避重试；可观察结果：最终成功或明确报错。',
    'scope': '- 重试到成功或返回最终错误为止，不负责界面展示',
    'acceptance': '- 单元测试：重试次数达到上限后返回最终错误\n- 集成测试：幂等键重复提交只生效一次',
    'non_goals': '- 断点续传',
    'risks': '- 重试放大流量；通过上限次数确认。',
}


def sample_markdown(**overrides):
    order = ('background', 'goals', 'problem', 'constraints', 'behavior', 'scope', 'acceptance', 'non_goals', 'risks')
    sections = []
    for kind in order:
        body = overrides.get(kind, SAMPLE_BODIES[kind])
        if body is None:
            continue
        sections.append(DraftSection('S_' + kind, kind, KIND_TITLES[kind], body))
    draft = DraftRevision(1, '上传自动重试', tuple(sections), '', '')
    return render_markdown(replace(draft, document_digest=text_digest(render_markdown(draft))))


def write_run(root, name, markdown, manifest=None):
    directory = Path(root) / name
    directory.mkdir(parents=True)
    (directory / 'requirements.md').write_text(markdown, encoding='utf-8')
    if manifest is not None:
        import json
        (directory / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return directory


class ParseTests(unittest.TestCase):
    def test_round_trip_restores_all_kinds(self):
        markdown = sample_markdown()
        draft = parse_approved_markdown(markdown)
        self.assertEqual(tuple(s.kind for s in draft.sections),
                         ('background', 'goals', 'problem', 'constraints', 'behavior', 'scope',
                          'acceptance', 'non_goals', 'risks'))
        self.assertEqual(draft.title, '上传自动重试')
        by_kind = {s.kind: s for s in draft.sections}
        self.assertEqual(by_kind['constraints'].markdown, SAMPLE_BODIES['constraints'])
        self.assertEqual(by_kind['acceptance'].title, '验收标准')

    def test_unknown_title_becomes_topic_without_crash(self):
        markdown = '# 标题\n\n## 背景\n\n内容。\n\n## 附录\n\n补充材料。\n'
        draft = parse_approved_markdown(markdown)
        self.assertEqual([s.kind for s in draft.sections], ['background', 'topic'])
        self.assertEqual(draft.sections[1].title, '附录')

    def test_missing_body_and_empty_markdown_do_not_crash(self):
        self.assertEqual(parse_approved_markdown('# 空文档\n').sections, ())
        self.assertEqual(parse_approved_markdown('').title, '需求文档')


class ComparisonTests(unittest.TestCase):
    def test_identical_entries_score_one(self):
        entries = ['- 上传接口已支持幂等键', '- 不增加第三方依赖']
        mean, low = literal_comparison(entries, list(entries))
        self.assertEqual(mean, 1.0)
        self.assertEqual(low, ())

    def test_disjoint_entries_score_low_and_are_listed(self):
        mean, low = literal_comparison(['- 完全不同的内容甲'], ['- 毫无重叠的条目乙'])
        self.assertLess(mean, 0.5)
        self.assertEqual(len(low), 2)

    def test_jaccard_edges(self):
        self.assertEqual(jaccard(frozenset(), frozenset()), 1.0)
        self.assertEqual(jaccard(frozenset('ab'), frozenset()), 0.0)
        self.assertEqual(jaccard(frozenset('ab'), frozenset('ab')), 1.0)
        self.assertEqual(char_bigrams('，。你好！'), char_bigrams('你好'))


class ReportTests(unittest.TestCase):
    def test_identical_runs_report_full_agreement(self):
        markdown = sample_markdown()
        with tempfile.TemporaryDirectory() as root:
            first = write_run(root, 'run-a', markdown, manifest={'policy_version': 'requirements-policy-v1', 'version': 3})
            second = write_run(root, 'run-b', markdown)
            report = render_report([first, second])
        self.assertIn('各次运行的章节序列完全一致', report)
        self.assertIn('| 约束 | 2 | 2 | 2 | 2 |', report)
        for kind in LITERAL_KINDS:
            self.assertIn(f'### {KIND_TITLES[kind]}', report)
        self.assertIn('| run-a vs run-b | 1.00 |', report)
        self.assertIn('requirements-policy-v1', report)
        self.assertNotIn('相似度 <', report)

    def test_structural_drift_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            first = write_run(root, 'run-a', sample_markdown())
            second = write_run(root, 'run-b', sample_markdown(risks=None))
            third = write_run(root, 'run-c', sample_markdown(acceptance='- 回归测试：重试上限后返回最终错误。'))
            report = render_report([first, second, third])
        self.assertIn('各次运行的章节序列不一致', report)
        self.assertIn('（与第一次不同）', report)
        self.assertIn('| 非目标 | 1 | 1 | 1 | 1 | 1 |', report)
        self.assertIn('| 验收标准 | 2 | 2 | 1 | 1 | 2 |', report)
        self.assertLess(report.index('（与第一次不同）'), report.index('## 2.'))

    def test_changed_entry_and_unknown_title_are_handled(self):
        with tempfile.TemporaryDirectory() as root:
            first = write_run(root, 'run-a', sample_markdown())
            second = write_run(root, 'run-b', sample_markdown(
                non_goals='- 整个需求不做断点续传与秒传'))
            third = write_run(root, 'run-c', sample_markdown(non_goals='- 断点续传') + '\n## 术语表\n\n重试：再次尝试。\n')
            report = render_report([first, second, third])
        self.assertIn('topic:术语表', report)
        self.assertIn('相似度 <', report)
        self.assertIn('断点续传', report)


class LoadRunTests(unittest.TestCase):
    def test_missing_requirements_md_is_a_clean_error(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                load_run(Path(root) / 'empty')

    def test_manifest_is_optional_and_read(self):
        with tempfile.TemporaryDirectory() as root:
            bare = load_run(write_run(root, 'bare', sample_markdown()))
            self.assertIsNone(bare['manifest'])
            self.assertNotIn('unconfirmed-number', bare['lint_counts'])
            stamped = load_run(write_run(root, 'stamped', sample_markdown(),
                                         manifest={'policy_version': 'p1', 'version': 2}))
            self.assertEqual(stamped['manifest']['policy_version'], 'p1')
            self.assertIn('单元测试', stamped['test_types'])


if __name__ == '__main__':
    unittest.main()
