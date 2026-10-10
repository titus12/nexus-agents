"""Deterministic advisory draft lint (spec T3: rules never block routing).

Every issue is ``severity='advisory'`` and ``owner='solver'``: lint findings
are hints recorded in the issue ledger, not mechanical gates.  Blocking
enforcement stays in ``document.validate_draft`` (including ``PENDING_MARKER``).
Word lists live in ``rubric.py`` so scoring and lint cannot drift apart.
"""
import re

from .model import ReviewIssue, normalized_quote
from .document import CANONICAL_ORDER
from .rubric import (_entries, NUMBER_RE, TEST_TYPE_WORDS, INTERNAL_ID_RE,
                     PLACEHOLDER_TOKENS, HEADING_RE)

VAGUE_PHRASES = ('正常工作', '工作正常', '运行正常', '稳定', '合理', '完善', '良好', '友好',
                 '提升体验', '用户体验', '尽快', '及时', '较快', '高效', '灵活')
PLAN_AS_RESULT = ('已通过', '已执行', '已验证', '测试通过', '全部通过')
SCOPE_CUT_PREFIXES = ('不做', '不负责', '不包含', '不支持', '不提供', '暂不')
LINTED_KINDS = ('goals', 'acceptance', 'constraints', 'non_goals', 'scope', 'background', 'behavior')
PLACEHOLDER_RE = re.compile('|'.join(re.escape(token) for token in PLACEHOLDER_TOKENS))

# slug -> (claim, impact, acceptance), one Chinese sentence each, per spec table.
MESSAGES = {
    'vague-phrase': ('该条使用了无法判定的表述', '读者无法据此判断完成标准，验收会流于主观',
                     '改写为可判定的结果或补上具体数值，未确定处标为待定'),
    'acceptance-no-test-type': ('验收标准没有写明测试类型', '验收无法按可执行的方式判定通过与否',
                                '为每条验收写明测试类型，如单元测试、回归测试、端到端'),
    'plan-as-result': ('把计划中的测试写成了已执行的结果', '审批者会误以为验证已经完成',
                       '改为计划表述，例如"应通过回归测试"'),
    'unconfirmed-number': ('数字未经用户或事实来源确认，应标为待定', '未经确认的数值可能与用户意图不符',
                           '补充 decision/fact 依据或把数值改写为待定'),
    'constraint-is-scope-cut': ('本次范围取舍不属于约束，应归责任边界或非目标',
                                '把范围取舍写成约束会误导实现取舍',
                                '把该条移到责任边界或非目标章节'),
    'non-goal-duplicates-scope': ('非目标与责任边界重复', '同一边界重复表述会造成理解歧义',
                                  '保留一处表述，删除另一处'),
    'section-order': ('章节顺序偏离规范', '章节顺序偏离模板会影响评审与渲染',
                      '按背景、目标、当前问题、约束、核心功能清单、责任边界、验收标准、非目标、风险排序'),
    'internal-id-in-text': ('正文出现内部编号', '内部编号会泄漏到用户文档且对读者无意义',
                            '改写为业务表述，不引用 req-/decision-/fact- 等内部编号'),
    'heading-in-body': ('章节内不应再写标题行', '渲染时标题会与章节标题重复',
                        '删除章节内的 # 标题行，用条目表述'),
    'placeholder': ('正文含占位符', '占位符残留说明内容尚未完成',
                    '替换为具体内容，未确定处改写为待定'),
}


def _kinds(draft):
    return tuple(s for s in draft.sections if s.kind in LINTED_KINDS)


def lint_draft(draft, consensus, sources=()):
    """Return advisory ``ReviewIssue`` rows; identical input yields identical output."""
    if not draft:
        return ()
    issues = []
    counts = {}

    def emit(slug, section_ids):
        claim, impact, acceptance = MESSAGES[slug]
        counts[slug] = counts.get(slug, 0) + 1
        issues.append(ReviewIssue(f'lint-{slug}-{counts[slug]}', 'lint-' + slug,
                                  tuple(section_ids), (), claim, impact, 'advisory', 'solver', acceptance))

    def entries(section):
        return _entries(section.markdown)

    corpus = ''.join(''.join(source.text.split()) for source in sources)

    def confirmed(number_text):
        return ''.join(number_text.split()) in corpus

    for section in _kinds(draft):
        if not section.markdown.strip():
            continue
        if section.kind in ('goals', 'acceptance'):
            for entry in entries(section):
                if any(phrase in entry for phrase in VAGUE_PHRASES) \
                        and not any(char.isdigit() for char in entry) and '待定' not in entry:
                    emit('vague-phrase', (section.section_id,))
                if NUMBER_RE.search(entry) and '待定' not in entry \
                        and not section.decision_ids and not section.fact_ids \
                        and not any(confirmed(match.group()) for match in NUMBER_RE.finditer(entry)):
                    emit('unconfirmed-number', (section.section_id,))
        if section.kind == 'acceptance':
            if not any(word in section.markdown for word in TEST_TYPE_WORDS):
                emit('acceptance-no-test-type', (section.section_id,))
            if any(phrase in section.markdown for phrase in PLAN_AS_RESULT):
                emit('plan-as-result', (section.section_id,))
        if section.kind == 'constraints':
            for entry in entries(section):
                if entry.startswith(SCOPE_CUT_PREFIXES):
                    emit('constraint-is-scope-cut', (section.section_id,))
        if INTERNAL_ID_RE.search(section.markdown):
            emit('internal-id-in-text', (section.section_id,))
        if HEADING_RE.search(section.markdown):
            emit('heading-in-body', (section.section_id,))
        if PLACEHOLDER_RE.search(section.markdown):
            emit('placeholder', (section.section_id,))

    by_kind = {s.kind: s for s in draft.sections if s.kind in ('scope', 'non_goals')}
    scope_section, non_goal_section = by_kind.get('scope'), by_kind.get('non_goals')
    if scope_section and non_goal_section and scope_section.markdown.strip() and non_goal_section.markdown.strip():
        scope_items = {normalized_quote(entry) for entry in entries(scope_section)}
        seen = set()
        for entry in entries(non_goal_section):
            row = normalized_quote(entry)
            if row in scope_items and row not in seen:
                seen.add(row)
                emit('non-goal-duplicates-scope', (non_goal_section.section_id, scope_section.section_id))

    ordered = tuple(s for s in draft.sections if s.kind in CANONICAL_ORDER)
    if ordered:
        ranks = [CANONICAL_ORDER.index(s.kind) for s in ordered]
        if ranks != sorted(ranks):
            emit('section-order', (s.section_id for s in ordered))
    return tuple(issues)
