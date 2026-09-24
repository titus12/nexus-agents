"""Menxia group review document model (pure domain, no I/O).

The group review loop exchanges content through one versioned markdown
document per group (design 终稿 §2):

- the plan body is owned by the Solver,
- the suggestion section is append-only for Analyst/Critic,
- absorption is the Solver's only way to remove a suggestion, and every
  removal must be traceable (implemented change or reasoned rejection),
- a rejected P0/P1 stays visible until its author confirms the rejection.

This module owns the suggestion ledger: parsing the canonical block format,
lifecycle transitions, convergence accounting, and the absorption checks the
joiner runs after each Solver round.  It deliberately has no filesystem,
runtime, or transport dependencies so the review rules stay unit-testable in
isolation; persistence lives in the artifact runners, routing in the state
machine.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from typing import Mapping


DOC_TITLE_PATTERN = re.compile(r"^#\s*(?P<title>.+?)\s*\[v(?P<version>\d+)\]\s*$")
SECTION_PATTERN = re.compile(r"^##\s*(?P<index>\d+)\.\s*(?P<name>.+?)\s*$")
SUGGESTION_PATTERN = re.compile(
    r"^\[(?P<id>S-\d+)\]\[(?P<author>analyst|critic)\]"
    r"\[v(?P<base>\d+) base\]\[(?P<severity>P[0-3])\]"
    r"\[(?P<status>open|rejected-pending|deferred)\]\s*(?P<body>.*)$"
)
REPLY_PATTERN = re.compile(
    r"^\s*↳\s*\[solver\]\[(?P<disposition>不采纳|延后)\]\s*(?P<reason>.*)$"
)
SIGNOFF_PATTERN = re.compile(
    r"^\[(?P<author>analyst|critic)\]\s*v(?P<version>\d+)(?P<rest>.*)$"
)
LEDGER_ID_PATTERN = re.compile(r"S-\d+")

SUGGESTION_OPEN = "open"
SUGGESTION_REJECTED_PENDING = "rejected-pending"
SUGGESTION_DEFERRED = "deferred"
SUGGESTION_CLOSED_ACCEPTED = "closed-accepted"
SUGGESTION_CLOSED_AUTO = "closed-auto"
SUGGESTION_ABSORBED = "absorbed"

DOCUMENT_STATUSES = (
    SUGGESTION_OPEN,
    SUGGESTION_REJECTED_PENDING,
    SUGGESTION_DEFERRED,
)

SUGGESTION_SEVERITIES = ("P0", "P1", "P2", "P3")
BLOCKING_SEVERITIES = frozenset({"P0", "P1"})
SUGGESTION_AUTHORS = ("analyst", "critic")

SECTION_BASELINE = 0
SECTION_BODY = 1
SECTION_SUGGESTIONS = 2
SECTION_LEDGER = 3
SECTION_SIGNOFF = 4

_PASS_MARKERS = ("通过", "approved")

# The plan body must carry these six ``###`` subsections, in this order
# (requirements §9: 可实施方案形态).  The Solver fills them; everything
# else in the body is free-form.
BODY_PART_TITLES = (
    "目标与设计决策",
    "现状与目标行为",
    "统一约束",
    "任务分解",
    "验收映射",
    "风险与兼容",
)

# Markers that mean a decision is still open; none may survive into an
# approved plan body.
UNDECIDED_MARKERS = ("待定", "待决策", "TODO")

_BODY_HEADING_PATTERN = re.compile(r"^###\s+(.+?)\s*$")
_MAP_ROW_PATTERN = re.compile(r"^-\s*(?P<left>.+?)\s*->\s*(?P<right>.+)$")


class MenxiaDocError(ValueError):
    """Raised when a group document violates the canonical format."""


@dataclass(frozen=True)
class Suggestion:
    """One review entry in the suggestion section (design 终稿 §2/§3)."""

    suggestion_id: str
    author: str
    base_version: int
    severity: str
    status: str
    body: str
    reply: str = ""
    status_round: int = 0
    deferred_target: int | None = None

    @property
    def blocking(self) -> bool:
        return self.severity in BLOCKING_SEVERITIES

    @property
    def resolved(self) -> bool:
        return self.status in (
            SUGGESTION_CLOSED_ACCEPTED,
            SUGGESTION_CLOSED_AUTO,
            SUGGESTION_ABSORBED,
        )

    def render(self) -> str:
        line = (
            f"[{self.suggestion_id}][{self.author}]"
            f"[v{self.base_version} base][{self.severity}][{self.status}] "
            f"{self.body}"
        )
        if self.reply:
            line = f"{line}\n  ↳ [solver][不采纳] {self.reply}"
        return line


@dataclass(frozen=True)
class LedgerEntry:
    """One ``v<N>: ...`` line of the absorption record section."""

    version: int
    text: str
    suggestion_ids: tuple[str, ...]


@dataclass(frozen=True)
class Signoff:
    """One reviewer stamp in the sign-off section."""

    author: str
    version: int
    approved: bool
    note: str = ""


@dataclass(frozen=True)
class MenxiaGroupDoc:
    """Parsed view of one group review document at one version."""

    group_id: str
    version: int
    title: str
    baseline_markdown: str
    body_markdown: str
    suggestions: tuple[Suggestion, ...]
    ledger: tuple[LedgerEntry, ...]
    signoffs: tuple[Signoff, ...]
    extra_sections: tuple[tuple[int, str, str], ...] = ()

    # ------------------------------------------------------------------ parse

    @classmethod
    def parse(cls, markdown: str, *, group_id: str = "") -> "MenxiaGroupDoc":
        if not isinstance(markdown, str) or not markdown.strip():
            raise MenxiaDocError("group document must be non-empty markdown")
        lines = markdown.splitlines()
        title_match = DOC_TITLE_PATTERN.match(lines[0]) if lines else None
        if title_match is None:
            raise MenxiaDocError(
                "first line must be '# <group> 门下省方案 [v<N>]'"
            )
        version = int(title_match.group("version"))
        if version < 1:
            raise MenxiaDocError(f"document version must be >= 1, got {version}")

        known_sections = (
            SECTION_BASELINE, SECTION_BODY, SECTION_SUGGESTIONS,
            SECTION_LEDGER, SECTION_SIGNOFF,
        )
        sections: dict[int, tuple[str, list[str]]] = {}
        extras: list[tuple[int, str, str]] = []
        index: int | None = None
        name = ""
        buffer: list[str] = []
        for line in lines[1:]:
            heading = SECTION_PATTERN.match(line)
            if heading is None:
                if index is None:
                    continue
                buffer.append(line)
                continue
            if index is not None:
                text = "\n".join(buffer)
                if index in known_sections:
                    sections[index] = (name, buffer)
                else:
                    extras.append((index, name, text))
            index = int(heading.group("index"))
            name = heading.group("name")
            buffer = []
        if index is not None:
            text = "\n".join(buffer)
            if index in known_sections:
                sections[index] = (name, buffer)
            else:
                extras.append((index, name, text))

        for required in (SECTION_BODY, SECTION_SUGGESTIONS):
            if required not in sections:
                raise MenxiaDocError(f"document is missing section {required}")

        suggestions = _parse_suggestions(
            "\n".join(sections[SECTION_SUGGESTIONS][1])
        )
        return cls(
            group_id=group_id,
            version=version,
            title=title_match.group("title"),
            baseline_markdown="\n".join(sections.get(SECTION_BASELINE, ("", []))[1]).strip("\n"),
            body_markdown="\n".join(sections[SECTION_BODY][1]).strip("\n"),
            suggestions=tuple(suggestions),
            ledger=tuple(_parse_ledger("\n".join(sections.get(SECTION_LEDGER, ("", []))[1]))),
            signoffs=tuple(_parse_signoffs("\n".join(sections.get(SECTION_SIGNOFF, ("", []))[1]))),
            extra_sections=tuple(extras),
        )

    # ----------------------------------------------------------------- render

    def render(self) -> str:
        parts = [f"# {self.title}  [v{self.version}]", ""]
        parts.append(f"## {SECTION_BASELINE}. 基线")
        parts.append(self.baseline_markdown.rstrip())
        parts.append("")
        parts.append(f"## {SECTION_BODY}. 方案正文")
        parts.append(self.body_markdown.rstrip())
        parts.append("")
        parts.append(f"## {SECTION_SUGGESTIONS}. 建议段")
        for suggestion in self.suggestions:
            parts.append(suggestion.render())
        parts.append("")
        parts.append(f"## {SECTION_LEDGER}. 吸收记录")
        for entry in self.ledger:
            parts.append(f"v{entry.version}: {entry.text}")
        parts.append("")
        parts.append(f"## {SECTION_SIGNOFF}. 签核区")
        for signoff in self.signoffs:
            marker = "通过" if signoff.approved else "已审"
            note = f" {signoff.note}" if signoff.note else ""
            parts.append(f"[{signoff.author}] v{signoff.version} {marker}{note}")
        for index, name, text in self.extra_sections:
            parts.append("")
            parts.append(f"## {index}. {name}")
            parts.append(text.rstrip())
        return "\n".join(parts).rstrip() + "\n"

    def content_hash(self) -> str:
        return hashlib.sha256(self.render().encode("utf-8")).hexdigest()

    # ----------------------------------------------------------- convergence

    def open_blocking(self) -> tuple[Suggestion, ...]:
        """Open P0/P1 suggestions: they block any approval."""
        return tuple(
            suggestion for suggestion in self.suggestions
            if suggestion.status == SUGGESTION_OPEN and suggestion.blocking
        )

    def pending_rejections(self) -> tuple[Suggestion, ...]:
        """Rejected P0/P1 suggestions their author has not confirmed yet."""
        return tuple(
            suggestion for suggestion in self.suggestions
            if suggestion.status == SUGGESTION_REJECTED_PENDING
            and suggestion.blocking
        )

    def open_count(self) -> int:
        return sum(1 for s in self.suggestions if s.status == SUGGESTION_OPEN)

    def signoff(self, author: str) -> Signoff | None:
        result: Signoff | None = None
        for entry in self.signoffs:
            if entry.author == author:
                result = entry
        return result

    def approved_by(self, author: str) -> bool:
        """True when ``author`` stamped the current version as approved."""
        entry = self.signoff(author)
        return entry is not None and entry.version == self.version and entry.approved

    def converged(self) -> bool:
        """Both reviewers approved the current version with nothing open."""
        return (
            not self.open_blocking()
            and not self.pending_rejections()
            and self.approved_by("analyst")
            and self.approved_by("critic")
        )

    # ------------------------------------------------------------- mutations

    def with_version(self, version: int) -> "MenxiaGroupDoc":
        if version < self.version:
            raise MenxiaDocError(
                f"document version cannot move backwards {self.version}->{version}"
            )
        return replace(self, version=version)

    def add_suggestion(
        self,
        *,
        author: str,
        severity: str,
        body: str,
        base_version: int | None = None,
        status_round: int = 0,
    ) -> "MenxiaGroupDoc":
        if author not in SUGGESTION_AUTHORS:
            raise MenxiaDocError(f"suggestion author must be one of {SUGGESTION_AUTHORS}")
        if severity not in SUGGESTION_SEVERITIES:
            raise MenxiaDocError(f"unknown severity {severity!r}")
        known = {s.suggestion_id for s in self.suggestions}
        for entry in self.ledger:
            known.update(entry.suggestion_ids)
        index = 1
        while f"S-{index:03d}" in known:
            index += 1
        suggestion = Suggestion(
            suggestion_id=f"S-{index:03d}",
            author=author,
            base_version=base_version if base_version is not None else self.version,
            severity=severity,
            status=SUGGESTION_OPEN,
            body=body.strip(),
            status_round=status_round,
        )
        return replace(self, suggestions=self.suggestions + (suggestion,))

    def _suggestion(self, suggestion_id: str) -> Suggestion:
        for suggestion in self.suggestions:
            if suggestion.suggestion_id == suggestion_id:
                return suggestion
        raise MenxiaDocError(f"unknown suggestion {suggestion_id!r}")

    def absorb(
        self,
        suggestion_id: str,
        *,
        note: str,
        ledger_version: int | None = None,
        status_round: int = 0,
    ) -> "MenxiaGroupDoc":
        """Implement a suggestion: it leaves the section into the ledger."""
        suggestion = self._suggestion(suggestion_id)
        if suggestion.resolved:
            raise MenxiaDocError(f"suggestion {suggestion_id} is already resolved")
        removed = tuple(
            s for s in self.suggestions if s.suggestion_id != suggestion_id
        )
        version = ledger_version if ledger_version is not None else self.version
        appended = LedgerEntry(
            version=version,
            text=f"{suggestion_id}→已吸收({note.strip()})",
            suggestion_ids=(suggestion_id,),
        )
        merged: list[LedgerEntry] = []
        for entry in self.ledger:
            if entry.version == version:
                merged.append(LedgerEntry(
                    version=entry.version,
                    text=f"{entry.text}; {appended.text}",
                    suggestion_ids=entry.suggestion_ids + appended.suggestion_ids,
                ))
            else:
                merged.append(entry)
        if not any(entry.version == version for entry in self.ledger):
            merged.append(appended)
        return replace(self, suggestions=removed, ledger=tuple(merged))

    def reject(
        self,
        suggestion_id: str,
        *,
        reason: str,
        status_round: int = 0,
    ) -> "MenxiaGroupDoc":
        """Decline a suggestion; P0/P1 stay visible until their author confirms."""
        suggestion = self._suggestion(suggestion_id)
        if suggestion.resolved:
            raise MenxiaDocError(f"suggestion {suggestion_id} is already resolved")
        updated = replace(
            suggestion,
            status=SUGGESTION_REJECTED_PENDING,
            reply=reason.strip(),
            status_round=status_round,
        )
        return replace(self, suggestions=tuple(
            updated if s.suggestion_id == suggestion_id else s
            for s in self.suggestions
        ))

    def confirm_rejection(
        self, suggestion_id: str, *, accept: bool, status_round: int = 0
    ) -> "MenxiaGroupDoc":
        """Author verdict on a rejection: close it, or insist (back to open)."""
        suggestion = self._suggestion(suggestion_id)
        if suggestion.status != SUGGESTION_REJECTED_PENDING:
            raise MenxiaDocError(
                f"suggestion {suggestion_id} is not awaiting confirmation"
            )
        if accept:
            status = SUGGESTION_CLOSED_ACCEPTED
        elif suggestion.blocking:
            status = SUGGESTION_OPEN
        else:
            status = SUGGESTION_CLOSED_AUTO
        updated = replace(suggestion, status=status, status_round=status_round)
        return replace(self, suggestions=tuple(
            updated if s.suggestion_id == suggestion_id else s
            for s in self.suggestions
        ))

    def auto_close_stale_rejections(self) -> "MenxiaGroupDoc":
        """P2/P3 rejections unanswered for one full round close on their own."""
        updated = []
        for suggestion in self.suggestions:
            if (
                suggestion.status == SUGGESTION_REJECTED_PENDING
                and not suggestion.blocking
            ):
                updated.append(replace(
                    suggestion,
                    status=SUGGESTION_CLOSED_AUTO,
                    status_round=suggestion.status_round + 1,
                ))
            else:
                updated.append(suggestion)
        return replace(self, suggestions=tuple(updated))

    def defer(
        self, suggestion_id: str, *, target_version: int, status_round: int = 0
    ) -> "MenxiaGroupDoc":
        suggestion = self._suggestion(suggestion_id)
        if suggestion.blocking:
            raise MenxiaDocError("P0/P1 suggestions cannot be deferred")
        updated = replace(
            suggestion,
            status=SUGGESTION_DEFERRED,
            deferred_target=target_version,
            status_round=status_round,
        )
        return replace(self, suggestions=tuple(
            updated if s.suggestion_id == suggestion_id else s
            for s in self.suggestions
        ))

    def prune_closed(self) -> "MenxiaGroupDoc":
        """Drop resolved entries from the section (ledger keeps the trace)."""
        kept = tuple(
            s for s in self.suggestions if not s.resolved
        )
        return replace(self, suggestions=kept)

    def sign_doc(self, *, author: str, approved: bool, note: str = "") -> "MenxiaGroupDoc":
        if author not in SUGGESTION_AUTHORS:
            raise MenxiaDocError(f"signoff author must be one of {SUGGESTION_AUTHORS}")
        kept = tuple(s for s in self.signoffs if s.author != author)
        kept = kept + (Signoff(
            author=author, version=self.version, approved=approved, note=note.strip(),
        ),)
        return replace(self, signoffs=kept)


def render_initial(
    *,
    group_id: str,
    baseline_markdown: str,
    body_markdown: str = "",
) -> str:
    """Render the v1 skeleton a Solver starts from."""
    doc = MenxiaGroupDoc(
        group_id=group_id,
        version=1,
        title=f"{group_id} 门下省方案",
        baseline_markdown=baseline_markdown.strip(),
        body_markdown=body_markdown.strip(),
        suggestions=(),
        ledger=(),
        signoffs=(),
    )
    return doc.render()


def verify_absorption(
    previous: MenxiaGroupDoc,
    current: MenxiaGroupDoc,
    *,
    absorbed_ids: tuple[str, ...] = (),
    rejected_ids: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Ledger checks the joiner runs after a Solver round (design §5).

    Every suggestion that left the section must be claimed as absorbed or
    rejected; every P0/P1 rejection must still sit in the section awaiting
    its author; a claimed id that neither left nor appeared is a contract
    breach.  Returns human-readable violations, empty when clean.
    """

    errors: list[str] = []
    before = {s.suggestion_id: s for s in previous.suggestions}
    after = {s.suggestion_id: s for s in current.suggestions}
    absorbed = tuple(dict.fromkeys(absorbed_ids))
    rejected = tuple(dict.fromkeys(rejected_ids))
    known_ids = set(before) | {
        entry_id for entry in previous.ledger for entry_id in entry.suggestion_ids
    }
    vanished = [
        suggestion_id for suggestion_id in before
        if suggestion_id not in after
    ]
    claimed = set(absorbed) | set(rejected)
    for suggestion_id in vanished:
        if suggestion_id not in claimed:
            errors.append(
                f"{suggestion_id} left the suggestion section without an "
                "absorbed/rejected claim"
            )
    for suggestion_id in absorbed:
        if suggestion_id not in known_ids:
            errors.append(f"absorbed id {suggestion_id} was never a suggestion")
        elif suggestion_id in after:
            errors.append(f"absorbed id {suggestion_id} is still in the section")
        elif (
            suggestion_id in before
            and before[suggestion_id].status == SUGGESTION_REJECTED_PENDING
        ):
            errors.append(
                f"{suggestion_id} was rejected-pending; the solver cannot "
                "absorb it before its author confirms"
            )
    for suggestion_id in rejected:
        if suggestion_id not in known_ids:
            errors.append(f"rejected id {suggestion_id} was never a suggestion")
            continue
        still = after.get(suggestion_id)
        if still is None:
            continue
        if still.severity in BLOCKING_SEVERITIES and (
            still.status != SUGGESTION_REJECTED_PENDING
        ):
            errors.append(
                f"rejected P0/P1 id {suggestion_id} must stay in the section "
                f"as rejected-pending, found status {still.status!r}"
            )
    for suggestion_id, suggestion in after.items():
        if (
            suggestion.status == SUGGESTION_REJECTED_PENDING
            and suggestion.blocking
            and suggestion_id in before
            and before[suggestion_id].status == SUGGESTION_OPEN
            and suggestion_id not in rejected
        ):
            errors.append(
                f"{suggestion_id} flipped to rejected-pending without a claim"
            )
    return tuple(errors)


def changed_body_sections(
    previous: MenxiaGroupDoc, current: MenxiaGroupDoc
) -> tuple[str, ...]:
    """Body section headings whose content changed between two versions.

    Used by the joiner's scope check: a Solver diff that touches body
    sections no suggestion pointed at must be declared, not silent.
    """

    def slices(markdown: str) -> dict[str, str]:
        result: dict[str, str] = {}
        heading: str | None = None
        buffer: list[str] = []
        for line in markdown.splitlines():
            match = re.match(r"^#{2,3}\s+(.*)$", line)
            if match:
                if heading is not None:
                    result[heading] = "\n".join(buffer)
                heading = match.group(1).strip()
                buffer = []
            elif heading is not None:
                buffer.append(line)
        if heading is not None:
            result[heading] = "\n".join(buffer)
        return result

    before = slices(previous.body_markdown)
    after = slices(current.body_markdown)
    changed = [
        name for name, text in after.items() if before.get(name) != text
    ]
    for name, text in before.items():
        if name not in after and text.strip():
            changed.append(name)
    return tuple(sorted(set(changed)))


def body_part_violations(body_markdown: str) -> tuple[str, ...]:
    """Body checks every Solver reply must pass (requirements §9).

    The plan body must carry the six fixed ``###`` subsections in the
    canonical order; missing parts and reordering are mechanical violations.
    """

    headings = [
        match.group(1).strip()
        for match in (
            _BODY_HEADING_PATTERN.match(line)
            for line in body_markdown.splitlines()
        )
        if match
    ]
    errors: list[str] = []
    positions: list[int] = []
    for title in BODY_PART_TITLES:
        if title not in headings:
            errors.append(f"body_missing_part:{title}")
        else:
            positions.append(headings.index(title))
    if (
        len(positions) == len(BODY_PART_TITLES)
        and positions != sorted(positions)
    ):
        errors.append("body_part_order")
    return tuple(errors)


def _body_part_slices(body_markdown: str) -> dict[str, str]:
    slices: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in body_markdown.splitlines():
        match = _BODY_HEADING_PATTERN.match(line)
        if match:
            if current is not None:
                slices[current] = "\n".join(buffer)
            current = match.group(1).strip()
            buffer = []
        elif current is not None:
            buffer.append(line)
    if current is not None:
        slices[current] = "\n".join(buffer)
    return slices


def _requirement_acceptance_lines(
    requirement_markdown: str,
) -> set[str] | None:
    """The normalized §8 lines of the group requirement document.

    ``None`` means the requirement markdown is absent or unparseable, in
    which case the map closure is skipped (legacy bindings).
    """

    from .zhongshu_doc import (
        SECTION_ACCEPTANCE,
        ZhongshuDocError,
        ZhongshuRequirementDoc,
        _normalize,
    )

    try:
        doc = ZhongshuRequirementDoc.parse(requirement_markdown)
    except ZhongshuDocError:
        return None
    return {
        _normalize(line)
        for line in doc.section(SECTION_ACCEPTANCE).splitlines()
        if _normalize(line)
    }


def menxia_approval_blockers(
    body_markdown: str,
    *,
    requirement_markdown: str = "",
) -> tuple[str, ...]:
    """Approval-gate checks for a critic APPROVE_GROUP (requirements §9).

    A plan body that still carries an undecided marker, an exception row
    without its three elements (the exception plus its alternative
    verification and backfill trigger), or an acceptance-map entry the
    requirement document never stated must not be approved; the joiner
    folds these into a solver revision demand.  An empty
    ``requirement_markdown`` (legacy binding) skips the map closure.
    """

    errors: list[str] = []
    for marker in UNDECIDED_MARKERS:
        if marker in body_markdown:
            errors.append(f"undecided_marker:{marker}")
    for line in body_markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("-") and "例外" in stripped:
            for element in ("替代验证", "补测触发"):
                if element not in stripped:
                    errors.append(f"exception_row_missing:{element}")
    map_lines = [
        stripped
        for stripped in (
            line.strip()
            for line in _body_part_slices(body_markdown)
            .get("验收映射", "")
            .splitlines()
        )
        if stripped
    ]
    allowed = (
        _requirement_acceptance_lines(requirement_markdown)
        if requirement_markdown.strip()
        else None
    )
    for line in map_lines:
        match = _MAP_ROW_PATTERN.match(line)
        if match is None:
            errors.append(f"acceptance_map_format:{line}")
            continue
        if allowed is None:
            continue
        from .zhongshu_doc import _normalize

        left = _normalize(match.group("left"))
        if left and left not in allowed:
            errors.append(f"acceptance_map_orphan:{match.group('left').strip()}")
    return tuple(errors)


_MAX_REPLY_DOC_CHARS = 131_072
_GROUP_ROLES = ("solver", "analyst", "critic")


def verify_group_reply(
    previous: MenxiaGroupDoc | None,
    *,
    role: str,
    reply: Mapping[str, object],
) -> tuple[str, ...]:
    """Mechanical checks one group reply must pass before it folds (design §5).

    ``role`` is the pipeline role of the replying worker; ``reply`` is its
    raw envelope.  The checks are purely structural — they never judge plan
    quality, only the document rules:

    - the reply carries a parseable, size-bounded document,
    - the version arithmetic matches the role (the Solver advances the
      version by exactly one; reviewers never move it),
    - reviewers changed only the suggestion section (the body, baseline,
      ledger, and sign-off sections are frozen for them),
    - the Solver left every unclaimed suggestion untouched, removed only
      claimed ones (``verify_absorption``), and declared every body section
      it touched.

    Returns human-readable violations; empty means the reply may fold.
    """

    role = str(role or "").strip()
    if role not in _GROUP_ROLES:
        return (f"unknown group role {role!r}",)
    doc_markdown = reply.get("doc_markdown")
    if not isinstance(doc_markdown, str) or not doc_markdown.strip():
        return ("reply carries no doc_markdown",)
    if len(doc_markdown) > _MAX_REPLY_DOC_CHARS:
        return (
            f"doc_markdown exceeds {_MAX_REPLY_DOC_CHARS} chars "
            f"({len(doc_markdown)})",
        )
    try:
        current = MenxiaGroupDoc.parse(doc_markdown)
    except MenxiaDocError as error:
        return (f"doc_markdown does not parse: {error}",)

    if previous is None:
        if role == "solver":
            claimed = reply.get("doc_version")
            if claimed not in (None, "", current.version):
                return (
                    f"claimed doc_version {claimed!r} does not match the "
                    f"document title version {current.version}",
                )
            return ()
        return ("reviewer reply has no previous document to review",)

    if role == "solver":
        return _verify_solver_reply(previous, current, reply)
    return _verify_reviewer_reply(previous, current, role, reply)


def _verify_solver_reply(
    previous: MenxiaGroupDoc,
    current: MenxiaGroupDoc,
    reply: Mapping[str, object],
) -> tuple[str, ...]:
    errors: list[str] = []
    # The pristine v1 skeleton (empty body, no suggestions, no ledger) gets
    # one special round: filling it is the round's whole job.  The reply may
    # keep the skeleton version or advance it by one, but it must actually
    # write a body.  The touched_scope check is vacuous here — there was no
    # body content to edit silently — and demanding exact leaf-section names
    # for a first fill rejected every real run (task-20260923-613874: all
    # three groups parked as MENXIA_GROUP_DOC_BREACH on their first solver
    # wave).  Revision rounds (below) keep the strict rules.
    initial_fill = (
        not previous.body_markdown.strip()
        and not previous.suggestions
        and not previous.ledger
    )
    if initial_fill:
        if not current.body_markdown.strip():
            errors.append("solver must fill the empty document body")
        if current.version not in (previous.version, previous.version + 1):
            errors.append(
                f"solver must keep or advance the version {previous.version} "
                f"-> {previous.version + 1}, found {current.version}"
            )
        errors.extend(body_part_violations(current.body_markdown))
        return tuple(errors)
    if current.version != previous.version + 1:
        errors.append(
            f"solver must advance the version {previous.version} -> "
            f"{previous.version + 1}, found {current.version}"
        )
    if current.title != previous.title:
        errors.append("solver must not change the document title")
    # The absorption ledger only grows: prior entries stay verbatim.
    for index, entry in enumerate(previous.ledger):
        if index >= len(current.ledger):
            errors.append(f"ledger entry {index + 1} was removed")
            continue
        kept = current.ledger[index]
        if kept.version != entry.version or kept.text != entry.text:
            errors.append(f"ledger entry {index + 1} was rewritten")
    absorbed_ids = reply.get("absorbed_ids")
    rejected_ids = reply.get("rejected_ids")
    errors.extend(verify_absorption(
        previous,
        current,
        absorbed_ids=tuple(absorbed_ids) if isinstance(absorbed_ids, (list, tuple)) else (),
        rejected_ids=tuple(rejected_ids) if isinstance(rejected_ids, (list, tuple)) else (),
    ))
    # Unclaimed suggestions must survive byte-for-byte; a claimed rejection
    # may only gain an inline solver reply under it.
    rejected = (
        {str(item) for item in rejected_ids}
        if isinstance(rejected_ids, (list, tuple)) else set()
    )
    before = {s.suggestion_id: s for s in previous.suggestions}
    for suggestion in current.suggestions:
        original = before.get(suggestion.suggestion_id)
        if original is None:
            errors.append(
                f"solver added suggestion {suggestion.suggestion_id}; "
                "only reviewers may add suggestions"
            )
            continue
        if suggestion != original:
            if (
                suggestion.status == SUGGESTION_REJECTED_PENDING
                and suggestion.suggestion_id in rejected
                and replace(suggestion, status=original.status, reply=original.reply)
                == original
            ):
                continue
            errors.append(
                f"suggestion {suggestion.suggestion_id} was edited; the "
                "solver may only append an inline rejection reply"
            )
    # Declared scope must cover every body section the reply actually changed.
    touched_scope = reply.get("touched_scope")
    touched = (
        {str(item).strip() for item in touched_scope if str(item).strip()}
        if isinstance(touched_scope, (list, tuple)) else set()
    )
    undeclared = [
        section for section in changed_body_sections(previous, current)
        if section not in touched
    ]
    if undeclared:
        errors.append(
            "body sections changed without a touched_scope entry: "
            + ", ".join(undeclared)
        )
    # The six-part plan shape must survive every revision wave, not only
    # the initial fill (requirements §9: 可实施方案形态).
    errors.extend(body_part_violations(current.body_markdown))
    return tuple(errors)


def _verify_reviewer_reply(
    previous: MenxiaGroupDoc,
    current: MenxiaGroupDoc,
    role: str,
    reply: Mapping[str, object],
) -> tuple[str, ...]:
    errors: list[str] = []
    if current.version != previous.version:
        errors.append(
            f"reviewer must keep the version at {previous.version}, "
            f"found {current.version}"
        )
    if (
        current.baseline_markdown != previous.baseline_markdown
        or current.body_markdown != previous.body_markdown
        or current.ledger != previous.ledger
        or current.signoffs != previous.signoffs
        or current.extra_sections != previous.extra_sections
    ):
        errors.append(
            f"{role} changed frozen sections; only the suggestion section "
            "may grow"
        )
    new = current.suggestions[len(previous.suggestions):]
    for old, kept in zip(previous.suggestions, current.suggestions):
        if old != kept:
            errors.append(f"suggestion {old.suggestion_id} was edited by the {role}")
    for suggestion in new:
        if suggestion.author != role:
            errors.append(
                f"suggestion {suggestion.suggestion_id} claims author "
                f"{suggestion.author!r}, expected {role!r}"
            )
        if suggestion.base_version != previous.version:
            errors.append(
                f"suggestion {suggestion.suggestion_id} bases on v"
                f"{suggestion.base_version}, expected v{previous.version}"
            )
        if suggestion.status != SUGGESTION_OPEN:
            errors.append(
                f"suggestion {suggestion.suggestion_id} is not open on arrival"
            )
    claimed = reply.get("added_suggestion_ids")
    claimed_ids = (
        [str(item) for item in claimed if str(item).strip()]
        if isinstance(claimed, (list, tuple)) else []
    )
    added_ids = [suggestion.suggestion_id for suggestion in new]
    if sorted(claimed_ids) != sorted(added_ids):
        errors.append(
            "added_suggestion_ids does not match the appended suggestions: "
            f"claimed {claimed_ids}, found {added_ids}"
        )
    return tuple(errors)


def _parse_suggestions(markdown: str) -> list[Suggestion]:
    suggestions: list[Suggestion] = []
    current: Suggestion | None = None
    for line in markdown.splitlines():
        header = SUGGESTION_PATTERN.match(line)
        if header:
            if current is not None:
                suggestions.append(current)
            base = int(header.group("base"))
            if base < 1:
                raise MenxiaDocError(
                    f"{header.group('id')}: base version must be >= 1"
                )
            current = Suggestion(
                suggestion_id=header.group("id"),
                author=header.group("author"),
                base_version=base,
                severity=header.group("severity"),
                status=header.group("status"),
                body=header.group("body").strip(),
            )
            continue
        reply = REPLY_PATTERN.match(line)
        if reply and current is not None:
            current = replace(current, reply=reply.group("reason").strip())
    if current is not None:
        suggestions.append(current)
    seen: set[str] = set()
    for suggestion in suggestions:
        if suggestion.suggestion_id in seen:
            raise MenxiaDocError(
                f"duplicate suggestion id {suggestion.suggestion_id}"
            )
        seen.add(suggestion.suggestion_id)
        if suggestion.status == SUGGESTION_DEFERRED and suggestion.blocking:
            raise MenxiaDocError(
                f"{suggestion.suggestion_id}: P0/P1 suggestions cannot be deferred"
            )
    return suggestions


def _parse_ledger(markdown: str) -> list[LedgerEntry]:
    entries: list[LedgerEntry] = []
    for line in markdown.splitlines():
        match = re.match(r"^v(\d+):\s*(.*)$", line.strip())
        if not match:
            continue
        text = match.group(2).strip()
        entries.append(LedgerEntry(
            version=int(match.group(1)),
            text=text,
            suggestion_ids=tuple(dict.fromkeys(LEDGER_ID_PATTERN.findall(text))),
        ))
    return entries


def _parse_signoffs(markdown: str) -> list[Signoff]:
    signoffs: list[Signoff] = []
    for line in markdown.splitlines():
        match = SIGNOFF_PATTERN.match(line.strip())
        if not match:
            continue
        rest = match.group("rest")
        signoffs.append(Signoff(
            author=match.group("author"),
            version=int(match.group("version")),
            approved=any(marker in rest.lower() for marker in _PASS_MARKERS),
            note=rest.strip(" ,，"),
        ))
    return signoffs


__all__ = [
    "BLOCKING_SEVERITIES",
    "BODY_PART_TITLES",
    "LedgerEntry",
    "MenxiaDocError",
    "MenxiaGroupDoc",
    "SECTION_BASELINE",
    "SECTION_BODY",
    "SECTION_LEDGER",
    "SECTION_SIGNOFF",
    "SECTION_SUGGESTIONS",
    "Signoff",
    "SUGGESTION_AUTHORS",
    "SUGGESTION_CLOSED_ACCEPTED",
    "SUGGESTION_CLOSED_AUTO",
    "SUGGESTION_DEFERRED",
    "SUGGESTION_OPEN",
    "SUGGESTION_REJECTED_PENDING",
    "SUGGESTION_SEVERITIES",
    "Suggestion",
    "UNDECIDED_MARKERS",
    "body_part_violations",
    "changed_body_sections",
    "menxia_approval_blockers",
    "render_initial",
    "verify_absorption",
    "verify_group_reply",
]
