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

import re
from dataclasses import dataclass

DOC_TITLE_PATTERN = re.compile(
    r"^#\s*(?P<group_id>group-\d+)\s+需求文档\s*\[v(?P<version>\d+)\]\s*$"
)
SECTION_PATTERN = re.compile(r"^##\s*(?P<index>\d+)\.\s*(?P<name>.+?)\s*$")

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


class ZhongshuDocError(ValueError):
    """Raised when a group requirement document violates the canonical format."""


def _normalize(text: object) -> str:
    return " ".join(str(text).split())


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

        errors: list[str] = []
        found = tuple(index for index, _, _ in self.sections)
        if found != SECTION_INDEXES:
            missing = [i for i in SECTION_INDEXES if i not in found]
            for number in missing:
                errors.append(f"missing_section:{number}")
            if not missing and list(found) != sorted(found):
                errors.append("section_order")
        for index, name, _ in self.sections:
            if 1 <= index <= len(SECTION_TITLES) and name != SECTION_TITLES[index - 1]:
                errors.append(f"section_name:{index}")
        goals = self.section(SECTION_GOALS)
        for phrase in UNDECIDABLE_PHRASES:
            if phrase in goals:
                errors.append(f"undecidable_wording:{SECTION_GOALS}:{phrase}")
        for index, _, body in self.sections:
            if any(
                line.strip().startswith(marker)
                for marker in IMPLEMENTATION_MARKERS
                for line in body.splitlines()
            ):
                errors.append(f"implementation_marker:{index}")
        errors.extend(self._acceptance_closure(review, group_id))
        return tuple(errors)

    def _acceptance_closure(
        self, review: object, group_id: str
    ) -> list[str]:
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
        errors = [
            f"acceptance_orphan:{line}" for line in sorted(lines - signals)
        ]
        errors.extend(
            f"acceptance_missing:{signal}" for signal in sorted(signals - lines)
        )
        return errors


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

    if not isinstance(markdown, str) or not markdown.strip():
        return ("doc_missing",)
    if len(markdown) > _MAX_DOC_CHARS:
        return (f"doc_too_long:{len(markdown)}",)
    try:
        doc = ZhongshuRequirementDoc.parse(markdown)
    except ZhongshuDocError as error:
        return (f"doc_unparseable:{error}",)
    errors: list[str] = []
    if group_id and doc.group_id != group_id:
        errors.append(f"title_group_mismatch:{doc.group_id}")
    if previous_version is None:
        if doc.version != 1:
            errors.append(f"doc_version_initial:{doc.version}")
    elif doc.version != previous_version + 1:
        errors.append(f"doc_version_jump:{previous_version}->{doc.version}")
    errors.extend(doc.verify(review, group_id))
    return tuple(errors)


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


__all__ = [
    "DOC_TITLE_PATTERN",
    "IMPLEMENTATION_MARKERS",
    "SECTION_ACCEPTANCE",
    "SECTION_GOALS",
    "SECTION_INDEXES",
    "SECTION_TITLES",
    "UNDECIDABLE_PHRASES",
    "ZhongshuDocError",
    "ZhongshuRequirementDoc",
    "group_doc_signals",
    "group_doc_violations",
]
