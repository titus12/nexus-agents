"""Stability measurement for approved requirement drafts (roadmap S5a).

Pure functions over parsed approved documents; standard library only.  The
literal-similarity numbers measure textual proximity only: a low score does
not mean the wording differs semantically, and low pairs are listed for a
human or review agent to judge.
"""
import json
import re
from dataclasses import replace
from pathlib import Path

from .document import KIND_TITLES, render_markdown
from .lint import lint_draft
from .model import ConsensusRevision, DraftRevision, DraftSection, text_digest
from .rubric import TEST_TYPE_WORDS, _entries

TITLE_TO_KIND = {title: kind for kind, title in KIND_TITLES.items()}
HEADING_RE = re.compile(r'^##\s+(.+?)\s*$', re.MULTILINE)
LITERAL_KINDS = ('constraints', 'scope', 'acceptance', 'non_goals')
LOW_SIMILARITY = 0.5


def parse_approved_markdown(markdown):
    """Rebuild a DraftRevision from rendered requirements.md.

    ``## `` headings map back to kinds via KIND_TITLES; unrecognized titles
    become ``topic`` sections so odd documents still parse.
    """
    matches = list(HEADING_RE.finditer(markdown or ''))
    first_line = (markdown or '').split('\n', 1)[0]
    title = first_line.lstrip('#').strip() or '需求文档'
    sections = []
    for index, match in enumerate(matches):
        heading = match.group(1).strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        body = markdown[match.end():end].strip()
        kind = TITLE_TO_KIND.get(heading, 'topic')
        sections.append(DraftSection(f'S_{kind}-{index + 1}', kind, heading, body))
    draft = DraftRevision(1, title, tuple(sections), '', '')
    return replace(draft, document_digest=text_digest(render_markdown(draft)))


def char_bigrams(text):
    cleaned = ''.join(ch for ch in (text or '') if ch.isalnum())
    return frozenset(cleaned[i:i + 2] for i in range(len(cleaned) - 1))


def jaccard(left, right):
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def best_match_similarity(entries, others):
    """Per entry: the best char-bigram Jaccard against ``others``."""
    scores = []
    for entry in entries:
        grams = char_bigrams(entry)
        best = max((jaccard(grams, char_bigrams(other)) for other in others), default=0.0)
        scores.append((entry, best))
    return scores


def literal_comparison(entries_left, entries_right):
    """Symmetric mean best-match similarity plus the low pairs (< 0.5)."""
    low, means = [], []
    for entries, others in ((entries_left, entries_right), (entries_right, entries_left)):
        if not entries or not others:
            means.append(0.0 if entries or others else 1.0)
            low.extend((entry, '', round(score, 2)) for entry, score in best_match_similarity(entries, others)
                       if score < LOW_SIMILARITY)
            continue
        scores = best_match_similarity(entries, others)
        means.append(sum(score for _, score in scores) / len(scores))
        best_of = {entry: max(others, key=lambda other: jaccard(char_bigrams(entry), char_bigrams(other)))
                   for entry, _ in scores}
        low.extend((entry, best_of[entry], round(score, 2)) for entry, score in scores if score < LOW_SIMILARITY)
    return sum(means) / len(means), tuple(low)


def load_run(directory):
    """Read one approved run directory into its parsed draft and metrics."""
    path = Path(directory)
    markdown_path = path / 'requirements.md'
    if not markdown_path.exists():
        raise ValueError(f'{path}: 缺少 requirements.md')
    draft = parse_approved_markdown(markdown_path.read_text(encoding='utf-8-sig'))
    issues = lint_draft(draft, ConsensusRevision())
    lint_counts = {}
    for issue in issues:
        slug = issue.rule[len('lint-'):]
        lint_counts[slug] = lint_counts.get(slug, 0) + 1
    acceptance = '\n'.join(s.markdown for s in draft.sections if s.kind == 'acceptance')
    entry_counts = {}
    for section in draft.sections:
        key = section.kind if section.kind in KIND_TITLES else f'topic:{section.title}'
        entry_counts[key] = len(_entries(section.markdown))
    manifest = None
    manifest_path = path / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    return {'name': path.name or str(path), 'path': str(path), 'draft': draft,
            'sequence': tuple(s.kind for s in draft.sections),
            'sequence_labels': tuple(KIND_TITLES.get(s.kind, s.title) for s in draft.sections),
            'entry_counts': entry_counts, 'lint_counts': lint_counts,
            'test_types': tuple(sorted(word for word in TEST_TYPE_WORDS if word in acceptance)),
            'manifest': manifest}


def render_report(directories):
    """Build the S5a Markdown report over one or more approved run directories."""
    runs = [load_run(directory) for directory in directories]
    names = [run['name'] for run in runs]
    lines = ['# 需求产出稳定性报告（S5a）', '',
             f'- 运行次数：{len(runs)}（' + '、'.join(names) + '）',
             '- 说明：条目字面一致度只衡量文本接近程度，低分不等于语义不同；'
             '相似度低的条目对请交人工或审查 agent 判断是否同义。', '']
    lines.extend(_structure_section(runs))
    lines.extend(_table_section(runs, '## 2. 各章条目数', '', '章节',
                                lambda run: run['entry_counts'], _chapter_label))
    lines.extend(_table_section(runs, '## 3. lint 提示数（advisory）',
                                '无来源文本可对照，`unconfirmed-number` 按"无来源"处理；只对比各次的相对值。',
                                'slug', lambda run: run['lint_counts']))
    lines.extend(_test_type_section(runs, names))
    lines.extend(_literal_section(runs, names))
    lines.extend(_manifest_section(runs))
    return '\n'.join(lines).rstrip() + '\n'


def _structure_section(runs):
    lines = ['## 1. 结构一致性', '']
    sequences = {run['sequence'] for run in runs}
    if len(sequences) == 1:
        lines.append('各次运行的章节序列完全一致：' + ' → '.join(runs[0]['sequence_labels']) + '。')
    else:
        lines.append('各次运行的章节序列不一致：')
        lines.append('')
        lines.append('| 运行 | 章节序列 |')
        lines.append('--- | ---')
        reference = runs[0]['sequence']
        for run in runs:
            mark = '' if run['sequence'] == reference else '（与第一次不同）'
            lines.append(f"| {run['name']}{mark} | {' → '.join(run['sequence_labels'])} |")
    lines.append('')
    return lines


def _table_section(runs, heading, intro, label_header, values_of, label_of=None):
    """Per-key table with per-run integer values plus min/max."""
    rows = [values_of(run) for run in runs]
    keys = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    lines = [heading, '']
    if intro:
        lines.append(intro)
        lines.append('')
    lines.append('| ' + label_header + ' | ' + ' | '.join(run['name'] for run in runs) + ' | 最少 | 最多 |')
    lines.append('--- | ' + ' | '.join('---' for _ in runs) + ' | --- | ---')
    for key in keys:
        values = [row.get(key, 0) for row in rows]
        lines.append(f'| {(label_of or (lambda value: value))(key)} | ' +
                     ' | '.join(str(value) for value in values) +
                     f' | {min(values)} | {max(values)} |')
    lines.append('')
    return lines


def _chapter_label(key):
    return KIND_TITLES.get(key, key)


def _test_type_section(runs, names):
    lines = ['## 4. 验收测试类型集合', '']
    for run in runs:
        joined = '、'.join(run['test_types']) if run['test_types'] else '（未写明测试类型）'
        lines.append(f"- {run['name']}：{joined}")
    lines.append('')
    if len(runs) > 1:
        lines.append('| 对比 | Jaccard |')
        lines.append('--- | ---')
        for i in range(len(runs)):
            for j in range(i + 1, len(runs)):
                score = jaccard(set(runs[i]['test_types']), set(runs[j]['test_types']))
                lines.append(f'| {names[i]} vs {names[j]} | {score:.2f} |')
        lines.append('')
    return lines


def _literal_section(runs, names):
    lines = ['## 5. 条目字面一致度（约束 / 责任边界 / 验收 / 非目标）', '',
             '每个条目取与对方最相似条目的字符二元组 Jaccard，取均值；只衡量字面接近，低分不等于语义不同。', '']
    any_pair = False
    for kind in LITERAL_KINDS:
        pairs = []
        for i in range(len(runs)):
            for j in range(i + 1, len(runs)):
                left, right = _entries_of_kind(runs[i], kind), _entries_of_kind(runs[j], kind)
                if left is None or right is None:
                    continue
                mean, low = literal_comparison(left, right)
                pairs.append((names[i], names[j], mean, low))
        if not pairs:
            continue
        any_pair = True
        lines.append(f"### {KIND_TITLES[kind]}")
        lines.append('')
        lines.append('| 对比 | 平均字面相似度 |')
        lines.append('--- | ---')
        for left_name, right_name, mean, _ in pairs:
            lines.append(f'| {left_name} vs {right_name} | {mean:.2f} |')
        low_rows = [(left_name, right_name, row) for left_name, right_name, _, low in pairs for row in low]
        if low_rows:
            lines.append('')
            lines.append(f'相似度 < {LOW_SIMILARITY} 的条目对（需人工判断是否同义）：')
            lines.append('')
            for left_name, right_name, (entry, other, score) in low_rows:
                lines.append(f'- {left_name} vs {right_name}（{score:.2f}）：' + _clip_text(entry) +
                             (' ⟷ ' + _clip_text(other) if other else '（对方无相近条目）'))
        lines.append('')
    if not any_pair:
        lines.append('四章条目不足以两两比较。')
        lines.append('')
    return lines


def _entries_of_kind(run, kind):
    sections = [s for s in run['draft'].sections if s.kind == kind]
    if not sections:
        return None
    return [entry for section in sections for entry in _entries(section.markdown)]


def _manifest_section(runs):
    lines = ['## 6. 版本信息', '', '| 运行 | policy_version | 文档版本 |', '--- | --- | ---']
    for run in runs:
        manifest = run['manifest']
        if manifest is None:
            lines.append(f"| {run['name']} | （无 manifest.json） | （无 manifest.json） |")
        else:
            lines.append(f"| {run['name']} | {manifest.get('policy_version', '')} | {manifest.get('version', '')} |")
    lines.append('')
    return lines


def _clip_text(text, limit=60):
    text = ' '.join(str(text).split())
    return text if len(text) <= limit else text[:limit] + '…'
