"""Verbatim-quote verification for file-citing Analyst evidence.

task-20260929-c261a8 problem A: the Analyst paraphrased file behavior and the
wrong reading survived review rounds until the Critic caught it.  Every
evidence_update / finding_response whose source cites an existing file must
carry a ``quote`` object transcribing the cited lines; the quoted text must
appear in that file (NFKC + whitespace-normalized containment, so honest line
drift passes).  A cited path that does not exist is exempt: claiming a file is
missing is a legitimate UNKNOWN the gate cannot disprove.  The gate verifies
transcription only; whether the quote supports the conclusion stays a semantic
judgement for the Critic.

This module is pure with respect to the orchestrator: it inspects a payload
and either describes the problems (:func:`format_rejection`) or marks the
offending entries as unverified (:func:`demote_unverified`).  Which of the two
the transport applies is the caller's policy.
"""

from __future__ import annotations

import copy
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

ENTRY_KEYS = ("evidence_updates", "finding_responses")
QUOTE_REQUIRED = "QUOTE_REQUIRED"
QUOTE_MISMATCH = "QUOTE_MISMATCH"
QUOTE_OVERSIZE = "QUOTE_OVERSIZE"
UNVERIFIED_MARK = "[UNVERIFIED-QUOTE]"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FILE_TOKEN_RE = re.compile(
    r"[A-Za-z0-9_./\\-]+\.(?:py|md|json|txt|toml|yaml|yml|cfg|ini|rst)"
)
QUOTE_MAX_CHARS = 4096
QUOTE_MAX_LINES = 30


@dataclass(frozen=True)
class QuoteIssue:
    key: str
    index: int
    label: str
    kind: str
    message: str


def _normalized_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _expected_window(content: str, quote: object) -> str:
    """The lines the quote most likely meant, for rejection echo.

    A QUOTE_MISMATCH that only says "not found" leaves the worker guessing
    which lines to re-transcribe (task-20260929-638bde: every retry fixed some
    quotes and broke others).  When the quote names a line range, echo exactly
    those lines; otherwise echo the best-matching line and its neighbours, so
    the correction becomes copy-work instead of guess-work.
    """

    lines = content.splitlines()
    if not lines:
        return ""
    start = None
    end = None
    if isinstance(quote, dict):
        raw_start = quote.get("line_start")
        raw_end = quote.get("line_end")
        try:
            if raw_start is not None:
                start = max(0, int(raw_start) - 1)
                end = max(start + 1, int(raw_end)) if raw_end is not None else start + 6
        except (TypeError, ValueError):
            start = None
    if start is None:
        text = str(quote.get("text") or "") if isinstance(quote, dict) else ""
        target = _normalized_text(text.split("\n")[0] if text else "")
        best_index, best_score = 0, -1
        for index, line in enumerate(lines):
            norm = _normalized_text(line)
            if not norm:
                continue
            score = 0
            if target and (norm in target or target in norm):
                score += 10
            for expected, actual in zip(target.split(), norm.split()):
                if expected != actual:
                    break
                score += 1
            if score > best_score:
                best_index, best_score = index, score
        start = max(0, best_index - 1)
        end = min(len(lines), best_index + 2)
    window = " | ".join(line.strip() for line in lines[start:end] if line.strip())
    return window[:300]


class _FileReader:
    def __init__(self) -> None:
        self._cache: dict[str, str | None] = {}

    def load(self, path_text: str) -> str | None:
        normalized = path_text.replace("\\", "/").strip()
        if normalized in self._cache:
            return self._cache[normalized]
        candidates = [Path(path_text)]
        for root in (_REPO_ROOT, Path.cwd()):
            candidates.append(root / normalized)
        resolved = next((c for c in candidates if c.is_file()), None)
        content = None
        if resolved is not None:
            try:
                content = resolved.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = None
        self._cache[normalized] = content
        return content


def find_quote_issues(payload: object) -> list[QuoteIssue]:
    """Every file-citing entry whose quote is missing, oversized or not verbatim."""

    body = payload if isinstance(payload, dict) else {}
    reader = _FileReader()
    issues: list[QuoteIssue] = []
    for key in ENTRY_KEYS:
        rows = body.get(key)
        if not isinstance(rows, list):
            continue
        for index, entry in enumerate(rows):
            if not isinstance(entry, dict):
                continue
            issue = _check_entry(key, index, entry, reader)
            if issue is not None:
                issues.append(issue)
    return issues


def _check_entry(
    key: str, index: int, entry: dict, reader: _FileReader, *, ignore_size: bool = False
) -> QuoteIssue | None:
    prose = str(entry.get("conclusion") or entry.get("answer") or "")
    if prose.startswith(UNVERIFIED_MARK):
        return None
    label = str(entry.get("evidence_id") or entry.get("finding_id") or "?")
    source = str(entry.get("source") or "")
    quote = entry.get("quote")
    quote_text = str(quote.get("text") or "") if isinstance(quote, dict) else ""

    def issue(kind: str, message: str) -> QuoteIssue:
        return QuoteIssue(key, index, label, kind, f"{key}[{label}] {message}")

    if not quote_text.strip():
        match = _FILE_TOKEN_RE.search(source)
        if match and reader.load(match.group(0)) is not None:
            return issue(
                QUOTE_REQUIRED,
                f"cites {match.group(0)} but carries no verbatim quote "
                "{path, line_start, line_end, text} transcribing the cited "
                f"lines ({QUOTE_REQUIRED})",
            )
        return None
    line_count = quote_text.count("\n") + 1
    if not ignore_size and (len(quote_text) > QUOTE_MAX_CHARS or line_count > QUOTE_MAX_LINES):
        # Report the actual size: "exceeds the cap" alone left the worker
        # guessing how much to cut and it re-submitted the same oversized
        # span on the retry (task-20260929-dad75d).
        return issue(
            QUOTE_OVERSIZE,
            f"quote is {line_count} lines / {len(quote_text)} chars but the "
            f"cap is {QUOTE_MAX_LINES} lines / {QUOTE_MAX_CHARS} chars; cut it "
            "to only the cited lines that support the claim and split "
            "multi-spot evidence into separate entries, each with one "
            "contiguous quote",
        )
    quoted_path = str(quote.get("path") or "").strip()
    if not quoted_path:
        match = _FILE_TOKEN_RE.search(source)
        if not match:
            return None
        quoted_path = match.group(0)
    content = reader.load(quoted_path)
    if content is None:
        return None
    normalized_quote = _normalized_text(quote_text)
    if normalized_quote and normalized_quote in _normalized_text(content):
        return None
    expected = _expected_window(content, quote)
    return issue(
        QUOTE_MISMATCH,
        f"quoted text not found in {quoted_path}; transcribe the exact cited "
        "lines verbatim as one contiguous span with no ellipsis and no "
        f"skipped lines ({QUOTE_MISMATCH})"
        + (f"; the cited lines read: {expected}" if expected else ""),
    )


def repair_verified_quote_spans(payload: object) -> object:
    """Split only verified continuous source spans, keeping every cited line.

    This is formatting repair, never a semantic rewrite or transcription fix.
    Up to four parts per entry; unverified/oversized single lines stay rejected.
    All response references expand to the complete set of parts.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get('evidence_updates'), list):
        return payload
    reader = _FileReader()
    rows = []
    links = {}
    used_ids = {str(row.get('evidence_id')) for row in payload['evidence_updates'] if isinstance(row, dict)}
    for index, entry in enumerate(payload['evidence_updates']):
        if not isinstance(entry, dict):
            rows.append(entry)
            continue
        quote = entry.get('quote')
        text = str(quote.get('text') or '') if isinstance(quote, dict) else ''
        eid = str(entry.get('evidence_id') or '')
        if (not eid or str(entry.get('conclusion') or '').startswith(UNVERIFIED_MARK)
                or not text or (text.count('\n') + 1 <= QUOTE_MAX_LINES and len(text) <= QUOTE_MAX_CHARS)):
            rows.append(entry)
            continue
        path = str(quote.get('path') or '')
        if not path:
            match = _FILE_TOKEN_RE.search(str(entry.get('source') or ''))
            path = match.group(0) if match else ''
        if (not path or reader.load(path) is None
                or _check_entry('evidence_updates', index, entry, reader, ignore_size=True) is not None):
            rows.append(entry)
            continue
        chunks = []
        chunk = []
        unsafe = False
        for line in text.split('\n'):
            if len(line) > QUOTE_MAX_CHARS:
                unsafe = True
                break
            if chunk and (len(chunk) >= QUOTE_MAX_LINES or len('\n'.join([*chunk, line])) > QUOTE_MAX_CHARS):
                chunks.append('\n'.join(chunk))
                chunk = []
            chunk.append(line)
        if chunk:
            chunks.append('\n'.join(chunk))
        ids = [eid] + [f'{eid}:quote-part-{part:02d}' for part in range(2, len(chunks) + 1)]
        if unsafe or len(chunks) > 4 or any(value in used_ids for value in ids[1:]):
            rows.append(entry)
            continue
        links[eid] = ids
        used_ids.update(ids)
        offset = 0
        for part, (part_id, content) in enumerate(zip(ids, chunks), 1):
            row = copy.deepcopy(entry)
            row.update(evidence_id=part_id, quote_group_id=eid, quote_part_index=part, quote_part_count=len(chunks))
            row['quote']['text'] = content
            if isinstance(quote.get('line_start'), int):
                row['quote']['line_start'] = quote['line_start'] + offset
                row['quote']['line_end'] = row['quote']['line_start'] + content.count('\n')
            rows.append(row)
            offset += content.count('\n') + 1
    if not links:
        return payload
    result = copy.deepcopy(payload)
    result['evidence_updates'] = rows
    for row in result.get('finding_responses') or []:
        if isinstance(row, dict) and isinstance(row.get('evidence_ids'), list):
            row['evidence_ids'] = list(dict.fromkeys(part for eid in row['evidence_ids'] for part in links.get(eid, [eid])))
    return result


def format_rejection(issues: list[QuoteIssue]) -> str:
    """The retry feedback for a reply bounced on ``issues`` ('' when none)."""

    if not issues:
        return ""
    shown = "; ".join(item.message for item in issues[:8])
    if len(issues) > 8:
        shown += "; remaining defects: " + ", ".join(f"{item.label}:{item.kind}" for item in issues[8:])
    return (
        "verbatim quote gate: " + shown
        + ". Every file-citing evidence_update or finding_response needs a "
        "verbatim quote: one contiguous span copied exactly (no ellipsis, no "
        "skipped lines, max 30 lines / 4096 chars); split multi-spot evidence "
        "into separate entries. Return one complete structured result."
    )


def demote_unverified(payload: dict, issues: list[QuoteIssue]) -> dict:
    """Copy of ``payload`` with each flagged entry marked unverified.

    The unusable quote is dropped, the entry's prose is prefixed with
    :data:`UNVERIFIED_MARK` plus the reason so the Critic cannot mistake it
    for verified evidence, and a finding response can no longer suggest CLOSE.
    One bad quote thus costs that entry its verified status, not the whole
    reply its worker dispatch.
    """

    result = copy.deepcopy(payload)
    for item in issues:
        entry = result[item.key][item.index]
        entry.pop("quote", None)
        reason = f"{UNVERIFIED_MARK} {item.kind}: no verbatim quote backs this entry."
        prose_key = "conclusion" if item.key == "evidence_updates" else "answer"
        entry[prose_key] = f"{reason} {entry.get(prose_key) or ''}".rstrip()
        if item.key == "finding_responses" and entry.get("suggested_disposition") == "CLOSE":
            entry["suggested_disposition"] = "REVISE"
    return result
