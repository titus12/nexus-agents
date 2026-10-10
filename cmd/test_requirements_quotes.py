"""Source spans, quote alignment and claim-quote normalization (T7)."""
import unittest
from dataclasses import asdict

from orchestrator.domain.requirements.model import SourceRecord, normalized_quote
from orchestrator.domain.requirements.quote_align import align_quote
from orchestrator.domain.requirements.result_validation import result_errors, claim_quote_matches
from orchestrator.domain.requirements.spans import SPAN_VERSION, atomize, normalized_text
from orchestrator.runtime.requirements_quotes import normalize_claim_quotes


def source(text, source_id='src-000001', kind='request'):
    return SourceRecord(source_id, kind, text)


def claim_row(**fields):
    row = {'requirement_id': 'req-000001', 'topic_key': 't', 'statement': '第二句',
           'origin': 'explicit', 'source_ids': ['src-000001']}
    row.update(fields)
    return row


class SpanTests(unittest.TestCase):
    def test_crlf_blank_lines_and_sequential_ids(self):
        row = source('第一句。第二句！\r\n\r\n第三句？\r\n最后没有句号')
        spans = atomize(row)
        text = normalized_text(row)
        self.assertEqual(SPAN_VERSION, 'span-v1')
        self.assertEqual([s.id for s in spans],
                         ['src-000001#1', 'src-000001#2', 'src-000001#3', 'src-000001#4'])
        self.assertEqual([s.slice_of(text) for s in spans],
                         ['第一句。', '第二句！', '第三句？', '最后没有句号'])

    def test_english_terminators_need_trailing_whitespace(self):
        row = source('Hello. World\nNext? End; tail\nvalue a.b stays')
        text = normalized_text(row)
        self.assertEqual([s.slice_of(text) for s in atomize(row)],
                         ['Hello.', 'World', 'Next?', 'End;', 'tail', 'value a.b stays'])

    def test_list_markers_and_determinism(self):
        row = source('- 首项。\n* 二项\n1. 三项。')
        text = normalized_text(row)
        spans = atomize(row)
        # '1.' ends an English terminator before whitespace, so the numbered
        # marker legitimately becomes its own span; markers are never stripped.
        self.assertEqual([s.slice_of(text) for s in spans], ['- 首项。', '* 二项', '1.', '三项。'])
        self.assertEqual(spans, atomize(row))

    def test_offset_slice_reconstruction(self):
        row = source('第一句。\n第二句！\nThird. Done')
        text = normalized_text(row)
        for span in atomize(row):
            self.assertTrue(span.slice_of(text).strip())


class QuoteAlignTests(unittest.TestCase):
    def test_exact_substring_maps_to_offsets(self):
        text = '重复登录只建立一次连接。'
        self.assertEqual(align_quote(text, '重复登录只建立一次连接'), (0, 11))
        self.assertEqual(align_quote(text, '建立一次连接'), (5, 11))

    def test_nfkc_whitespace_and_zero_width_are_ignored(self):
        text = '系统最多允许 ＭＡＸ 连接数 10 次。'
        self.assertEqual(align_quote(text, 'max 连接数 10 次'), (7, 19))
        self.assertEqual(align_quote('同\u200b一会话的\ufeff重复登录', '同一会话的重复登录'), (0, 11))

    def test_short_or_missing_quotes_do_not_align(self):
        text = '重复登录只建立一次连接。'
        self.assertIsNone(align_quote(text, '连接'))
        self.assertIsNone(align_quote(text, '完全不存在的引文内容'))
        self.assertIsNone(align_quote('', '重复登录只建立一次连接'))

    def test_aligned_slice_matches_the_quote_view(self):
        text = '第一句。 ＭＡＸ连接数１０次；第二句。'
        bounds = align_quote(text, 'max 连接数 10 次')
        self.assertTrue(bounds)
        self.assertEqual(normalized_quote(text[bounds[0]:bounds[1]]).replace(' ', ''),
                         normalized_quote('max 连接数 10 次').replace(' ', ''))


class NormalizeClaimQuotesTests(unittest.TestCase):
    def test_span_ids_backfill_the_exact_slice_and_override_quote(self):
        payload = {'analysis': {'claims': [claim_row(span_ids=['src-000001#2'], quote='WRONG')]}}
        errors = normalize_claim_quotes(payload, [source('第一句。第二句！')])
        self.assertEqual(errors, [])
        claim = payload['analysis']['claims'][0]
        self.assertEqual(claim['quote'], '第二句！')
        self.assertNotIn('span_ids', claim)

    def test_contiguous_range_covers_the_whole_slice(self):
        payload = {'analysis': {'claims': [claim_row(span_ids=['src-000001#1', 'src-000001#2'])]}}
        normalize_claim_quotes(payload, [source('第一句。第二句！')])
        self.assertEqual(payload['analysis']['claims'][0]['quote'], '第一句。第二句！')

    def test_span_errors_are_reported_and_quote_untouched(self):
        sources = [source('第一句。第二句！第三句？'), source('其他', source_id='src-000002')]
        for span_ids, expected in ((['src-000001#9'], 'SPAN_UNKNOWN'),
                                   (['src-000001#1', 'src-000001#3'], 'SPAN_NOT_CONTIGUOUS'),
                                   (['src-000001#1', 'src-000002#1'], 'SPAN_NOT_CONTIGUOUS')):
            payload = {'analysis': {'claims': [claim_row(span_ids=span_ids, quote='第一句。')]}}
            errors = normalize_claim_quotes(payload, sources)
            self.assertEqual([e for e in errors if expected in e], errors)
            self.assertEqual(payload['analysis']['claims'][0]['quote'], '第一句。')

    def test_whitespace_only_drift_is_aligned_to_the_real_excerpt(self):
        payload = {'analysis': {'claims': [claim_row(quote='MAX连接数10 次')]}}
        errors = normalize_claim_quotes(payload, [source('系统最多允许 MAX 连接数 10 次。')])
        self.assertEqual(errors, [])
        self.assertEqual(payload['analysis']['claims'][0]['quote'], 'MAX 连接数 10 次')

    def test_exact_and_unfixable_quotes_pass_through(self):
        payload = {'analysis': {'claims': [claim_row(quote='第二句！')]}}
        self.assertEqual(normalize_claim_quotes(payload, [source('第一句。第二句！')]), [])
        self.assertEqual(payload['analysis']['claims'][0]['quote'], '第二句！')
        forged = {'analysis': {'claims': [claim_row(quote='彻底编造的引文内容')]}}
        self.assertEqual(normalize_claim_quotes(forged, [source('第一句。第二句！')]), [])
        self.assertEqual(forged['analysis']['claims'][0]['quote'], '彻底编造的引文内容')


class ResultValidationSpanSourcesTests(unittest.TestCase):
    def dispatch_with(self, claim):
        return {'sources': [{'source_id': 'src-000001', 'kind': 'request', 'author_id': 'local-operator',
                             'spans': ['[src-000001#1] 第一句。', '[src-000001#2] 第二句！']}],
                'consensus': {}}

    def test_span_presentation_still_validates_human_quotes(self):
        good = claim_row(quote='第二句！', span_ids=['src-000001#2'])
        self.assertEqual(result_errors({'analysis': {'claims': [good]}}, self.dispatch_with(good)), [])
        forged = claim_row(quote='彻底编造的引文内容')
        errors = result_errors({'analysis': {'claims': [forged]}}, self.dispatch_with(forged))
        self.assertTrue(any('EXPLICIT_CLAIM_REQUIRES_HUMAN_QUOTE' in e for e in errors))

    def test_span_ids_are_popped_before_decode(self):
        row = claim_row(span_ids=['src-000001#2'], quote='第二句！')
        self.assertEqual(result_errors({'analysis': {'claims': [row]}}, self.dispatch_with(row)), [])


if __name__ == '__main__':
    unittest.main()
