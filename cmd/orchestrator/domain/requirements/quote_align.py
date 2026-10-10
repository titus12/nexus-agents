"""Quote alignment against a source text (spec T7 / 6.5).

Pure helper: finds the first occurrence of a (whitespace-insensitive, NFKC,
case-folded) quote view inside a source view and maps it back to exact source
offsets.  Callers replace the model-provided quote with
``source_text[start:end]`` so the stored quote is always a real excerpt.
"""
import unicodedata

MIN_ALIGN_CHARS = 6
_DROPPED = {'\u200b', '\u200c', '\u200d', '\ufeff'}


def _view(text):
    if not isinstance(text, str):
        return '', []
    chars, offsets = [], []
    for index, char in enumerate(text):
        if char.isspace() or char in _DROPPED:
            continue
        for expanded in unicodedata.normalize('NFKC', char):
            chars.append(expanded.casefold())
            offsets.append(index)
    return ''.join(chars), offsets


def align_quote(source_text, quote):
    """Return ``(start, end)`` into ``source_text`` for ``quote``, or None."""
    source_view, source_offsets = _view(source_text)
    quote_view, _ = _view(quote)
    if not quote_view or len(quote_view) < MIN_ALIGN_CHARS or not source_view:
        return None
    position = source_view.find(quote_view)
    if position < 0:
        return None
    return source_offsets[position], source_offsets[position + len(quote_view) - 1] + 1
