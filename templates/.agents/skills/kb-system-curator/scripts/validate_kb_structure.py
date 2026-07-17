#!/usr/bin/env python3
"""Validate the structural OKF gates used by kb-system-curator."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import deque
from pathlib import Path
from typing import Iterable


RESERVED_NAMES = {"index.md", "log.md"}
MOJIBAKE_RE = re.compile(r"\uFFFD|鈥|Ã|â")
H1_RE = re.compile(r"^# (?!#)", re.MULTILINE)
AI_COMMENT_RE = re.compile(r"<!-- AI:(?:ROUTING|RULES|TEMPLATE) -")
RESOURCE_RE = re.compile(r"^resource:\s*(.+?)\s*$", re.MULTILINE)
TYPE_RE = re.compile(r"^type:\s*\S+", re.MULTILINE)
FRONTMATTER_RE = re.compile(r"\A---\r?\n[\s\S]*?\r?\n---\r?\n")
LINK_RE = re.compile(r"\]\(([^)]+)\)")


def to_posix(path: Path) -> str:
    return path.as_posix()


def markdown_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.md") if path.is_file())


def local_link_target(raw_target: str) -> str | None:
    target = raw_target.strip()
    if not target or target.startswith("#"):
        return None
    if "://" in target or target.startswith(("mailto:", "tel:")):
        return None
    return target.split("#", 1)[0]


def content_directories(root: Path) -> Iterable[Path]:
    yield root
    for directory in sorted(path for path in root.rglob("*") if path.is_dir()):
        entries = list(directory.iterdir())
        if any(entry.is_dir() or entry.suffix.lower() == ".md" for entry in entries):
            yield directory


def validate(root: Path, project_dir_name: str, flat_threshold: int) -> dict:
    root = root.resolve()
    errors: list[dict] = []
    warnings: list[dict] = []

    if not root.is_dir():
        return {
            "ok": False,
            "root": str(root),
            "errors": [{"code": "missing_root", "path": str(root)}],
            "warnings": [],
            "summary": {"documents": 0, "errors": 1, "warnings": 0},
        }

    documents = markdown_files(root)
    document_set = {path.resolve() for path in documents}
    graph: dict[Path, set[Path]] = {path.resolve(): set() for path in documents}

    for directory in content_directories(root):
        index = directory / "index.md"
        if not index.is_file():
            errors.append(
                {
                    "code": "missing_index",
                    "path": to_posix(directory.relative_to(root)),
                    "message": "Knowledge directory has content but no index.md.",
                }
            )

    for path in documents:
        relative = path.relative_to(root)
        label = to_posix(relative)
        raw = path.read_bytes()
        has_bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8", errors="replace")

        if has_bom:
            errors.append({"code": "utf8_bom", "path": label})
        if MOJIBAKE_RE.search(text):
            errors.append({"code": "mojibake", "path": label})
        if len(H1_RE.findall(text)) != 1:
            errors.append({"code": "invalid_h1_count", "path": label})

        is_project_document = relative.parts and relative.parts[0] == project_dir_name
        if is_project_document and path.name not in RESERVED_NAMES:
            if not FRONTMATTER_RE.match(text):
                errors.append({"code": "missing_frontmatter", "path": label})
            if not TYPE_RE.search(text):
                errors.append({"code": "missing_type", "path": label})
            if not AI_COMMENT_RE.search(text):
                errors.append({"code": "missing_ai_comment", "path": label})
            resource = RESOURCE_RE.search(text)
            expected_resource = to_posix(Path(root.name) / relative)
            if not resource or resource.group(1) != expected_resource:
                errors.append(
                    {
                        "code": "resource_mismatch",
                        "path": label,
                        "expected": expected_resource,
                    }
                )

        for match in LINK_RE.finditer(text):
            target = local_link_target(match.group(1))
            if target is None:
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                errors.append(
                    {
                        "code": "broken_local_link",
                        "path": label,
                        "target": target,
                    }
                )
            elif resolved in document_set:
                graph[path.resolve()].add(resolved)

    root_index = (root / "index.md").resolve()
    if root_index not in document_set:
        errors.append({"code": "missing_root_index", "path": "index.md"})
        reachable: set[Path] = set()
    else:
        reachable = set()
        queue: deque[Path] = deque([root_index])
        while queue:
            current = queue.popleft()
            if current in reachable:
                continue
            reachable.add(current)
            queue.extend(sorted(graph.get(current, set())))

    project = root / project_dir_name
    if not project.is_dir():
        errors.append(
            {
                "code": "missing_project_directory",
                "path": to_posix(Path(project_dir_name)),
            }
        )
    else:
        for path in markdown_files(project):
            if path.resolve() not in reachable:
                errors.append(
                    {
                        "code": "orphan_project_document",
                        "path": to_posix(path.relative_to(root)),
                    }
                )

        direct_concepts = [
            path
            for path in project.glob("*.md")
            if path.name not in RESERVED_NAMES | {"routing.md"}
        ]
        domains = project / "domains"
        if len(direct_concepts) > flat_threshold and not domains.is_dir():
            errors.append(
                {
                    "code": "flat_project_requires_domains",
                    "path": to_posix(Path(project_dir_name)),
                    "concept_count": len(direct_concepts),
                    "flat_threshold": flat_threshold,
                }
            )
        elif len(direct_concepts) > flat_threshold:
            warnings.append(
                {
                    "code": "flat_project_concepts",
                    "path": to_posix(Path(project_dir_name)),
                    "concept_count": len(direct_concepts),
                    "flat_threshold": flat_threshold,
                }
            )

    return {
        "ok": not errors,
        "root": str(root),
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "documents": len(documents),
            "errors": len(errors),
            "warnings": len(warnings),
            "reachable_documents": len(reachable),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate KB structure, links, metadata, and project-domain gates."
    )
    parser.add_argument("--root", default="KnowledgeBase", help="KnowledgeBase root path.")
    parser.add_argument(
        "--project-dir",
        default="project",
        help="Project knowledge directory relative to --root.",
    )
    parser.add_argument(
        "--flat-threshold",
        type=int,
        default=8,
        help="Maximum direct project concept pages allowed before domains are required.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    args = parser.parse_args()

    result = validate(Path(args.root), args.project_dir, args.flat_threshold)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        summary = result["summary"]
        print(
            "KB structure validation: "
            f"{'PASS' if result['ok'] else 'FAIL'} "
            f"({summary['documents']} documents, {summary['errors']} errors, "
            f"{summary['warnings']} warnings)"
        )
        for issue in result["errors"]:
            print(f"ERROR {issue['code']}: {issue['path']}")
        for issue in result["warnings"]:
            print(f"WARNING {issue['code']}: {issue['path']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
