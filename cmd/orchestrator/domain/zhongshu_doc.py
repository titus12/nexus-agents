"""Zhongshu group requirement document model (pure domain, no I/O).

Every Zhongshu group carries its requirement through the convergence loop
in one versioned markdown document (plan Task 5).  The Solver owns the
whole document; the orchestrator owns only mechanical checks:

- the nine canonical sections must exist, in order, with canonical names,
- the goal section must not hedge with undecidable wording,
- the acceptance section must close both ways against the group's item
  acceptance signals (after whitespace normalization),
- the document must stay free of implementation-level markers,
- the title version must advance by exactly one per revision.

Like the Menxia document model, this module has no filesystem, runtime,
or transport dependencies so the document rules stay unit-testable in
isolation; persistence lives in the artifact runners, routing in the
state machine.
"""

from __future__ import annotations

from collections.abc import Mapping
import copy
import re
from dataclasses import dataclass

DOC_TITLE_PATTERN = re.compile(
    r"^#\s*[*_`]*\s*(?P<group_id>group-\d+)\s*[*_`]*\s+需求文档\s*"
    r"\[\s*v(?P<version>\d+)\s*\]\s*[*_`]*\s*$"
)
SECTION_PATTERN = re.compile(r"^##\s*(?P<index>\d+)\.\s*(?P<name>.+?)\s*$")
ITEM_SUBSECTION_PATTERN = re.compile(r"^###\s+(?P<item_id>[^\s#]+).*$")


def _strip_md_emphasis(text: str) -> str:
    """Peel symmetric Markdown emphasis/code wrappers around an id token.

    Solvers wrapped identifiers in bold or backticks (``### **item-00001**``,
    ``# **group-00001 需求文档 [v1]**``) and the raw token mismatched the
    member set, orphaning every signal under it (task-20260928-f13561
    SOLVER:5/SOLVER:7).  Only edge wrapper pairs are peeled, so internal
    underscores of a real id survive.
    """

    stripped = str(text or "").strip()
    for _ in range(3):
        if (
            len(stripped) >= 3
            and stripped[0] == stripped[-1]
            and stripped[0] in ("*", "_", "`")
        ):
            inner = stripped[1:-1].strip()
            if not inner:
                break
            stripped = inner
            continue
        if (
            len(stripped) >= 4
            and stripped[:2] == stripped[-2:]
            and stripped[:2] in ("**", "__", "``")
        ):
            inner = stripped[2:-2].strip()
            if not inner:
                break
            stripped = inner
            continue
        break
    return stripped

SECTION_TITLES = (
    "背景",
    "目标",
    "标识与范围",
    "状态与边界语义",
    "行为要求",
    "责任边界",
    "交叉不变量",
    "验收标准",
    "非目标",
)
SECTION_INDEXES = tuple(range(1, len(SECTION_TITLES) + 1))
SECTION_GOALS = 2
SECTION_ACCEPTANCE = 8

UNDECIDABLE_PHRASES = ("尽量", "尽可能", "应该更好", "酌情", "视情况", "大概")
IMPLEMENTATION_MARKERS = ("```", "def ", "class ")
_MAX_DOC_CHARS = 131_072
# One §8 line = one checkable assertion (thin-doc rule).  The dense 300+ char
# recipe paragraphs made the documents unreadable for their human reviewer;
# how to measure lives in the Menxia execution plan, not here.
_MAX_ACCEPTANCE_LINE_CHARS = 300


class ZhongshuDocError(ValueError):
    """Raised when a group requirement document violates the canonical format."""


def _normalize(text: object) -> str:
    return " ".join(str(text).split())


@dataclass(frozen=True)
class DocViolation:
    """One mechanical document violation with its expected/actual diff."""

    kind: str
    code: str
    item_id: str = ""
    expected: str = ""
    actual: str = ""

    def render(self, with_expected: bool = False) -> str:
        if not with_expected or not self.expected or self.expected == self.actual:
            return self.code
        label = (
            "expected_rows" if self.kind.startswith("acceptance_") else "expected"
        )
        return f"{self.code};{label}={self.expected}"


@dataclass(frozen=True)
class ZhongshuRequirementDoc:
    """Parsed view of one group requirement document at one version."""

    group_id: str
    version: int
    title: str
    sections: tuple[tuple[int, str, str], ...]

    # ------------------------------------------------------------------ parse

    @classmethod
    def parse(cls, markdown: str) -> "ZhongshuRequirementDoc":
        if not isinstance(markdown, str) or not markdown.strip():
            raise ZhongshuDocError(
                "requirement document must be non-empty markdown"
            )
        lines = markdown.splitlines()
        title_match = DOC_TITLE_PATTERN.match(lines[0]) if lines else None
        if title_match is None:
            raise ZhongshuDocError(
                "first line must be '# <group_id> 需求文档 [v<N>]'"
            )
        version = int(title_match.group("version"))
        if version < 1:
            raise ZhongshuDocError(f"document version must be >= 1, got {version}")
        sections: list[tuple[int, str, str]] = []
        index: int | None = None
        name = ""
        buffer: list[str] = []
        for line in lines[1:]:
            heading = SECTION_PATTERN.match(line)
            if heading is None:
                if index is not None:
                    buffer.append(line)
                continue
            if index is not None:
                if any(existing[0] == index for existing in sections):
                    raise ZhongshuDocError(f"duplicate section {index}")
                sections.append((index, name, "\n".join(buffer)))
            index = int(heading.group("index"))
            name = heading.group("name")
            buffer = []
        if index is not None:
            if any(existing[0] == index for existing in sections):
                raise ZhongshuDocError(f"duplicate section {index}")
            sections.append((index, name, "\n".join(buffer)))
        return cls(
            group_id=title_match.group("group_id"),
            version=version,
            title=lines[0],
            sections=tuple(sections),
        )

    # ----------------------------------------------------------------- access

    def section(self, index: int) -> str:
        for section_index, _, body in self.sections:
            if section_index == index:
                return body
        return ""

    def acceptance_item_signals(self) -> dict[str, tuple[str, ...]]:
        """Parse §8 member-item subsections (``### <item_id>`` blocks).

        Each non-empty line of a subsection body is one acceptance signal of
        that item.  An empty mapping means the document uses the legacy
        flat §8 format (no subsections), where signals stay model-written and
        the line-level closure check applies.
        """

        result: dict[str, list[str]] = {}
        current: str | None = None
        for line in self.section(SECTION_ACCEPTANCE).splitlines():
            heading = ITEM_SUBSECTION_PATTERN.match(line)
            if heading is not None:
                current = _strip_md_emphasis(heading.group("item_id")) or None
                if current is not None:
                    result.setdefault(current, [])
                continue
            if current is None:
                continue
            text = line.strip()
            if text:
                result[current].append(text)
        return {
            item_id: tuple(lines)
            for item_id, lines in result.items()
        }

    def render(self) -> str:
        parts = [self.title]
        for index, name, body in self.sections:
            parts.append(f"## {index}. {name}")
            if body:
                parts.append(body)
        return "\n".join(parts)

    # ----------------------------------------------------------------- verify

    def verify(self, review: object, group_id: str = "") -> tuple[str, ...]:
        """Mechanical document checks; empty means the document may fold."""

        return tuple(v.code for v in self.violation_details(review, group_id))

    def violation_details(
        self, review: object, group_id: str = ""
    ) -> tuple[DocViolation, ...]:
        """Same checks as :meth:`verify`, with expected/actual diffs."""

        errors: list[DocViolation] = []
        found = tuple(index for index, _, _ in self.sections)
        if found != SECTION_INDEXES:
            missing = [i for i in SECTION_INDEXES if i not in found]
            for number in missing:
                errors.append(
                    DocViolation(
                        "missing_section",
                        f"missing_section:{number}",
                        expected=f"## {number}. {SECTION_TITLES[number - 1]}",
                    )
                )
            if not missing and list(found) != sorted(found):
                errors.append(DocViolation("section_order", "section_order"))
        for index, name, _ in self.sections:
            if 1 <= index <= len(SECTION_TITLES) and name != SECTION_TITLES[index - 1]:
                errors.append(
                    DocViolation(
                        "section_name",
                        f"section_name:{index}",
                        expected=SECTION_TITLES[index - 1],
                        actual=name,
                    )
                )
        goals = self.section(SECTION_GOALS)
        for phrase in UNDECIDABLE_PHRASES:
            if phrase in goals:
                errors.append(
                    DocViolation(
                        "undecidable_wording",
                        f"undecidable_wording:{SECTION_GOALS}:{phrase}",
                        actual=phrase,
                    )
                )
        for index, _, body in self.sections:
            if any(
                line.strip().startswith(marker)
                for marker in IMPLEMENTATION_MARKERS
                for line in body.splitlines()
            ):
                errors.append(
                    DocViolation(
                        "implementation_marker", f"implementation_marker:{index}"
                    )
                )
        for line in (self.section(SECTION_ACCEPTANCE) or "").splitlines():
            stripped = line.strip()
            if stripped and len(stripped) > _MAX_ACCEPTANCE_LINE_CHARS:
                errors.append(
                    DocViolation(
                        "acceptance_signal_too_long",
                        f"acceptance_signal_too_long:{len(stripped)}",
                        expected=(
                            f"每条断言一行，单行 <= {_MAX_ACCEPTANCE_LINE_CHARS} 字符"
                        ),
                        actual=f"{len(stripped)} chars: {stripped[:40]}...",
                    )
                )
        errors.extend(self._acceptance_closure(review, group_id))
        return tuple(errors)

    def _acceptance_closure(
        self, review: object, group_id: str
    ) -> list[DocViolation]:
        members = {
            str(getattr(item, "item_id", "") or "").strip()
            for item in getattr(review, "task_items", ()) or ()
            if str(getattr(item, "group_id", "") or "")
            == (group_id or self.group_id)
            and str(getattr(item, "item_id", "") or "").strip()
        }
        by_item = self.acceptance_item_signals()
        if by_item:
            # Subsection format: §8 is the single author of the signals and
            # the closure is guaranteed by projection, so only subsections
            # for non-member items (and lines outside any subsection) are
            # document-form violations.  A member without a subsection
            # surfaces as PLAN_HAS_NO_ACCEPTANCE at projection time.
            expected_members = " | ".join(sorted(members))
            errors = [
                DocViolation(
                    "acceptance_orphan",
                    f"acceptance_orphan:{item_id}",
                    item_id=item_id,
                    expected=expected_members,
                    actual=item_id,
                )
                for item_id in sorted(set(by_item) - members)
            ]
            seen_subsection = False
            for line in self.section(SECTION_ACCEPTANCE).splitlines():
                if ITEM_SUBSECTION_PATTERN.match(line):
                    seen_subsection = True
                    continue
                if not seen_subsection and _normalize(line):
                    errors.append(
                        DocViolation(
                            "acceptance_orphan",
                            f"acceptance_orphan:{_normalize(line)}",
                            expected=" | ".join(sorted(by_item)),
                            actual=_normalize(line),
                        )
                    )
            return errors
        signals = {
            _normalize(signal)
            for item in getattr(review, "task_items", ()) or ()
            if str(getattr(item, "group_id", "") or "")
            == (group_id or self.group_id)
            for signal in (getattr(item, "acceptance_signals", ()) or ())
            if _normalize(signal)
        }
        lines = {
            _normalize(line)
            for line in self.section(SECTION_ACCEPTANCE).splitlines()
            if _normalize(line)
        }
        expected_rows = " | ".join(sorted(signals))
        expected_lines = " | ".join(sorted(lines))
        errors = [
            DocViolation(
                "acceptance_orphan",
                f"acceptance_orphan:{line}",
                expected=expected_rows,
                actual=line,
            )
            for line in sorted(lines - signals)
        ]
        errors.extend(
            DocViolation(
                "acceptance_missing",
                f"acceptance_missing:{signal}",
                expected=expected_lines,
                actual=signal,
            )
            for signal in sorted(signals - lines)
        )
        return errors


def doc_violation_details(
    markdown: object,
    *,
    previous_version: int | None = None,
    review: object = None,
    group_id: str = "",
) -> tuple[DocViolation, ...]:
    """Structured violations one submitted group document carries.

    Mirrors :func:`group_doc_violations` with expected/actual diffs so the
    rejection feedback can tell the Solver exactly what to add or remove
    instead of a bare fragment (f8bde1's ``acceptance_orphan`` retries were
    lost to vague feedback).
    """

    if not isinstance(markdown, str) or not markdown.strip():
        return (DocViolation("doc_missing", "doc_missing"),)
    if len(markdown) > _MAX_DOC_CHARS:
        return (
            DocViolation(
                "doc_too_long",
                f"doc_too_long:{len(markdown)}",
                actual=str(len(markdown)),
            ),
        )
    try:
        doc = ZhongshuRequirementDoc.parse(markdown)
    except ZhongshuDocError as error:
        return (
            DocViolation(
                "doc_unparseable", f"doc_unparseable:{error}", actual=str(error)
            ),
        )
    details: list[DocViolation] = []
    if group_id and doc.group_id != group_id:
        details.append(
            DocViolation(
                "title_group_mismatch",
                f"title_group_mismatch:{doc.group_id}",
                expected=group_id,
                actual=doc.group_id,
            )
        )
    if previous_version is None:
        if doc.version != 1:
            details.append(
                DocViolation(
                    "doc_version_initial",
                    f"doc_version_initial:{doc.version}",
                    expected="1",
                    actual=str(doc.version),
                )
            )
    elif doc.version != previous_version + 1:
        details.append(
            DocViolation(
                "doc_version_jump",
                f"doc_version_jump:{previous_version}->{doc.version}",
                expected=str(previous_version + 1),
                actual=str(doc.version),
            )
        )
    details.extend(doc.violation_details(review, group_id))
    return tuple(details)


def group_doc_violations(
    markdown: object,
    *,
    previous_version: int | None = None,
    review: object = None,
    group_id: str = "",
) -> tuple[str, ...]:
    """All mechanical violations one submitted group document carries.

    Combines the size bound, the parse, the title binding, the version
    arithmetic (``previous_version is None`` means the first document and
    pins the version to 1), and the structural checks of
    :meth:`ZhongshuRequirementDoc.verify`.  Empty means the document may
    fold.
    """

    return tuple(
        violation.code
        for violation in doc_violation_details(
            markdown,
            previous_version=previous_version,
            review=review,
            group_id=group_id,
        )
    )


def group_doc_signals(review: object, group_id: str) -> tuple[str, ...]:
    """The normalized acceptance signals owned by one group's items."""

    return tuple(
        dict.fromkeys(
            _normalize(signal)
            for item in getattr(review, "task_items", ()) or ()
            if str(getattr(item, "group_id", "") or "") == group_id
            for signal in (getattr(item, "acceptance_signals", ()) or ())
            if _normalize(signal)
        )
    )


def project_acceptance_signals(
    plan: object,
    group_docs: Mapping[str, str],
) -> tuple[dict | None, str]:
    """Project §8 item subsections onto plan items' ``acceptance_signals``.

    The group requirement document is the single author of the acceptance
    signals: whatever the solver wrote in the plan field is overwritten by
    the §8 subsection lines, so the two can never diverge (live incidents
    task-20260926-f8bde1 ``acceptance_orphan`` retries and
    task-20260926-35833d plan/task_items divergence).  Documents in the
    legacy flat §8 format (no ``### <item_id>`` subsections) are skipped:
    their signals stay model-written and the line-level closure check
    applies.  A member item without a subsection is rejected as
    ``PLAN_HAS_NO_ACCEPTANCE:<item_ids>``.
    """

    if not isinstance(plan, Mapping):
        return None, "PLAN_NOT_OBJECT"
    items = plan.get("items")
    if not isinstance(items, list):
        return copy.deepcopy(dict(plan)), ""
    parsed: dict[str, dict[str, tuple[str, ...]]] = {}
    projected = copy.deepcopy(dict(plan))
    missing: list[str] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, Mapping):
            continue
        item_id = str(raw.get("item_id") or "").strip()
        group_id = str(raw.get("group_id") or "").strip()
        if not item_id or not group_id:
            continue
        if group_id not in parsed:
            markdown = group_docs.get(group_id)
            parsed[group_id] = (
                _section8_items(markdown) if isinstance(markdown, str) else {}
            )
        by_item = parsed[group_id]
        if not by_item:
            continue
        signals = by_item.get(item_id)
        if not signals:
            missing.append(item_id)
            continue
        projected["items"][index]["acceptance_signals"] = list(signals)
    if missing:
        return None, "PLAN_HAS_NO_ACCEPTANCE:" + ",".join(sorted(set(missing)))
    return projected, ""


def _section8_items(markdown: str) -> dict[str, tuple[str, ...]]:
    try:
        return ZhongshuRequirementDoc.parse(markdown).acceptance_item_signals()
    except ZhongshuDocError:
        return {}


__all__ = [
    "DOC_TITLE_PATTERN",
    "IMPLEMENTATION_MARKERS",
    "ITEM_SUBSECTION_PATTERN",
    "SECTION_ACCEPTANCE",
    "SECTION_GOALS",
    "SECTION_INDEXES",
    "SECTION_TITLES",
    "UNDECIDABLE_PHRASES",
    "DocViolation",
    "ZhongshuDocError",
    "ZhongshuRequirementDoc",
    "doc_violation_details",
    "group_doc_signals",
    "group_doc_violations",
    "project_acceptance_signals",
]
