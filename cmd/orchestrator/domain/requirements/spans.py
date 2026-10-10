"""Deterministic source-span atomization (spec T7 / 6.1). Spans are never
persisted: they are recomputed from ``source.text`` on demand, so the same
input always yields the same ids and offsets.
"""
from dataclasses import dataclass

SPAN_VERSION = 'span-v1'
CHINESE_TERMINATORS = '。！？；'
ENGLISH_TERMINATORS = '.!?;'


def normalized_text(source) -> str:
    text = source.get('text', '') if isinstance(source, dict) else source.text
    return str(text or '').replace('\r\n', '\n').replace('\r', '\n')


@dataclass(frozen=True)
class Span:
    id: str
    start: int
    end: int

    def slice_of(self, text: str) -> str:
        return text[self.start:self.end]


def _split_line(line, base):
    pieces = []
    start = 0
    for index, char in enumerate(line):
        cut = char in CHINESE_TERMINATORS
        if not cut and char in ENGLISH_TERMINATORS:
            following = line[index + 1] if index + 1 < len(line) else ''
            cut = not following or following.isspace()
        if cut:
            pieces.append((start, index + 1))
            start = index + 1
    if start < len(line):
        pieces.append((start, len(line)))
    bounds = []
    for first, last in pieces:
        chunk = line[first:last]
        lead = len(chunk) - len(chunk.lstrip())
        trail = len(chunk) - len(chunk.rstrip())
        if first + lead < last - trail:
            bounds.append((base + first + lead, base + last - trail))
    return bounds


def atomize(source) -> tuple:
    """Return ``(Span, ...)`` for one source: sentence-level, contiguous ids."""
    text = normalized_text(source)
    spans = []
    offset = 0
    for line in text.split('\n'):
        base, offset = offset, offset + len(line) + 1
        if not line.strip():
            continue
        for start, end in _split_line(line, base):
            spans.append(Span(f'{_source_id(source)}#{len(spans) + 1}', start, end))
    return tuple(spans)


def _source_id(source) -> str:
    return source.get('source_id', '') if isinstance(source, dict) else source.source_id
