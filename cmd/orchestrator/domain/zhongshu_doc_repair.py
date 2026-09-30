"""Deterministic repairs for a submitted group requirement document.

A reply whose document differs from the canonical form only in ways the
orchestrator already knows the answer to (title, section names, orphan
acceptance blocks) is normalized here instead of being bounced back to the
model, which cost a full Solver re-dispatch per slip.  Every repair is
lossless with respect to what the validators accept: nothing is invented and
content that belongs to a member item is never dropped.  Anything that is not
mechanically decidable (missing sections, wording, acceptance closure of a
member item) is left untouched so the normal violation feedback still fires.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass

from .zhongshu_doc import (
    DOC_TITLE_PATTERN,
    ITEM_SUBSECTION_PATTERN,
    SECTION_ACCEPTANCE,
    SECTION_PATTERN,
    SECTION_TITLES,
    strip_md_emphasis,
)

_H1_PATTERN = re.compile(r"^#(?!#)")
_GROUP_TOKEN = re.compile(r"group-\d+")
_DECLARED_VERSION = re.compile(r"\[\s*v(\d+)\s*\]")


@dataclass(frozen=True)
class DocRepair:
    markdown: str
    repairs: tuple[str, ...] = ()


def repair_group_doc(
    markdown: str,
    *,
    group_id: str,
    version: int,
    member_item_ids: Collection[str],
) -> DocRepair:
    """Return the canonical form of ``markdown`` plus a tag per repair made.

    ``group_id`` is the orchestrator-owned identity of the document;
    ``version`` is only used when the title declares no version at all.
    """

    lines = markdown.splitlines()
    repairs: list[str] = []
    lines = _canonical_title(lines, group_id, version, repairs)
    lines = _canonical_section_names(lines, repairs)
    lines = _drop_acceptance_orphans(lines, frozenset(member_item_ids), repairs)
    if not repairs:
        return DocRepair(markdown)
    return DocRepair("\n".join(lines), tuple(repairs))


def _canonical_title(
    lines: list[str], group_id: str, version: int, repairs: list[str]
) -> list[str]:
    start = next((index for index, line in enumerate(lines) if line.strip()), None)
    if start is None or _H1_PATTERN.match(lines[start].lstrip()) is None:
        return lines
    title = lines[start].strip()
    if set(_GROUP_TOKEN.findall(title)) - {group_id}:
        # A title naming another group is a real defect, not a formatting slip.
        return lines
    # A version the model declared is kept: a wrong one is a real defect
    # (stale base) that the version-arithmetic check must still report.
    declared = _DECLARED_VERSION.search(title)
    canonical = f"# {group_id} 需求文档 [v{declared.group(1) if declared else version}]"
    if start == 0 and DOC_TITLE_PATTERN.match(title) and title == canonical:
        return lines
    repairs.append("title")
    return [canonical, *lines[start + 1 :]]


def _canonical_section_names(lines: list[str], repairs: list[str]) -> list[str]:
    repaired: list[str] = []
    for line in lines:
        heading = SECTION_PATTERN.match(line)
        if heading is not None:
            index = int(heading.group("index"))
            if 1 <= index <= len(SECTION_TITLES):
                canonical = SECTION_TITLES[index - 1]
                if heading.group("name") != canonical:
                    repairs.append(f"section_name:{index}")
                    line = f"## {index}. {canonical}"
        repaired.append(line)
    return repaired


def _drop_acceptance_orphans(
    lines: list[str], members: frozenset[str], repairs: list[str]
) -> list[str]:
    """Drop §8 text that no member item owns (subsection format only)."""

    if not members:
        return lines
    kept: list[str] = []
    dropped: list[str] = []
    seen_member = False
    section: int | None = None
    keep_block: bool | None = None
    for line in lines:
        heading = SECTION_PATTERN.match(line)
        if heading is not None:
            section = int(heading.group("index"))
            keep_block = None
            kept.append(line)
            continue
        if section != SECTION_ACCEPTANCE:
            kept.append(line)
            continue
        subsection = ITEM_SUBSECTION_PATTERN.match(line)
        if subsection is not None:
            item_id = strip_md_emphasis(subsection.group("item_id"))
            keep_block = item_id in members
            seen_member = seen_member or keep_block
            if not keep_block:
                dropped.append(f"acceptance_orphan:{item_id}")
                continue
        elif keep_block is None:
            if line.strip():
                dropped.append("acceptance_preamble")
                continue
        elif not keep_block:
            continue
        kept.append(line)
    # With no member subsection left there is nothing to salvage: leave the
    # document alone so the closure violation reports what is missing.
    if not dropped or not seen_member:
        return lines
    repairs.extend(dropped)
    return kept
