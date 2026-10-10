"""Claim-quote normalization before contract validation (spec T7 / 6.3).

``normalize_claim_quotes(payload, sources)`` rewrites claim quotes in place:
- ``span_ids`` (payload-only field) are resolved against the raw sources and
  the quote becomes the exact ``T[first.start:last.end]`` slice; a model-provided
  ``quote`` is overridden.
- a quote-only claim that fails an exact containment check is aligned against
  the cited sources; success replaces the quote with the real excerpt.
Failures are returned as validation-error strings; the payload is left such
that downstream checks (``EXPLICIT_CLAIM_REQUIRES_HUMAN_QUOTE``) still fire on
unrepaired rows.  Forged quotes therefore never pass.
"""
from ..domain.requirements.model import normalized_quote
from ..domain.requirements.quote_align import align_quote
from ..domain.requirements.spans import atomize, normalized_text

_CLAIM_PATHS = (('analysis', 'claims'), (None, 'claims'))


def _claim_rows(payload):
    for parent, key in _CLAIM_PATHS:
        container = payload.get(parent) if parent else payload
        if isinstance(container, dict) and isinstance(container.get(key), list):
            for index, row in enumerate(container[key]):
                if isinstance(row, dict):
                    label = f'{parent}.{key}[{index}]' if parent else f'{key}[{index}]'
                    yield label, row


def _raw_sources(sources):
    rows = {}
    for source in sources or ():
        identity = source.get('source_id', '') if isinstance(source, dict) else getattr(source, 'source_id', '')
        if identity:
            rows[identity] = source
    return rows


def _spans_by_id(sources):
    table, texts = {}, {}
    for identity, source in _raw_sources(sources).items():
        text = normalized_text(source)
        texts[identity] = text
        for span in atomize(source):
            table[span.id] = (identity, span)
    return table, texts


def normalize_claim_quotes(payload, sources):
    """Backfill/align claim quotes; returns a list of error strings."""
    table, texts = _spans_by_id(sources)
    errors = []
    for label, claim in _claim_rows(payload):
        span_ids = claim.get('span_ids')
        if isinstance(span_ids, list) and span_ids:
            try:
                claim['quote'] = _quote_from_spans(span_ids, table, texts)
                claim.pop('span_ids', None)
            except ValueError as error:
                claim.pop('span_ids', None)
                errors.append(f'{label}.span_ids: {error}')
            continue
        quote = claim.get('quote')
        if not isinstance(quote, str) or not quote.strip():
            continue
        refs = [ref for ref in claim.get('source_ids', ()) or () if isinstance(ref, str)]
        if any(ref in texts and _contains(quote, texts[ref]) for ref in refs):
            continue
        for ref in refs:
            aligned = align_quote(texts.get(ref, ''), quote)
            if aligned:
                claim['quote'] = texts[ref][aligned[0]:aligned[1]]
                break
    return errors


def _contains(quote, text):
    if quote in text:
        return True
    probe, haystack = normalized_quote(quote), normalized_quote(text)
    return bool(probe) and probe in haystack


def _quote_from_spans(span_ids, table, texts):
    resolved = []
    for raw in span_ids:
        identity = raw if isinstance(raw, str) else ''
        prefix, _, number = identity.rpartition('#')
        span = table.get(identity) if number.isdigit() else None
        if span is None:
            raise ValueError(f'SPAN_UNKNOWN: {identity}')
        resolved.append((prefix, int(number), span[1]))
    prefixes = {prefix for prefix, _, _ in resolved}
    if len(prefixes) != 1:
        raise ValueError('SPAN_NOT_CONTIGUOUS: span_ids must reference one source')
    prefix = prefixes.pop()
    numbers = sorted(number for _, number, _ in resolved)
    if numbers != list(range(numbers[0], numbers[-1] + 1)):
        raise ValueError(f'SPAN_NOT_CONTIGUOUS: {numbers}')
    first = table[f'{prefix}#{numbers[0]}'][1]
    last = table[f'{prefix}#{numbers[-1]}'][1]
    text = texts[prefix]
    return text[first.start:last.end]
