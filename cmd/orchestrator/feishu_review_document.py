"""Publish one immutable review version through the official enterprise CLI.

The source snapshot remains the domain authority. Receipts survive notification
failures and process restarts; unknown creation outcomes never trigger a second
create. No tokens or credentials are managed here.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

from .adapters import feishu_notifications_enabled


TRANSIENT_DOCUMENT_CLI_MARKERS = (
    'token_expired', 'Access token expired', 'broker token failed',
    'ECONNRESET', 'ECONNREFUSED', 'ETIMEDOUT', 'socket hang up',
)


class ReviewDocumentError(RuntimeError):
    def __init__(self,message,*,source_ref='',prerequisite_fingerprint=''):
        super().__init__(message)
        self.source_ref,self.prerequisite_fingerprint=source_ref,prerequisite_fingerprint
        self.transient=any(marker in message for marker in TRANSIENT_DOCUMENT_CLI_MARKERS)


def semantic_blocks(content: str) -> tuple:
    """Conservative readback: retain structure, table cells and executable text."""
    content = re.sub(r'^\s*<title(?:\s[^>]*)?>.*?</title>\s*', '', content, count=1, flags=re.S)
    if content.lstrip().startswith('<'):
        raise ReviewDocumentError('REQUIREMENTS_READBACK_FORMAT_UNSUPPORTED')
    blocks, code, language = [], None, ''
    for line in content.replace('\r\n','\n').splitlines():
        fence = re.fullmatch(r'\s*```(.*)',line)
        if fence:
            if code is None:
                code, language = [], fence.group(1).strip()
            else:
                blocks.append(('code',language,'\n'.join(code)))
                code = None
            continue
        if code is not None:
            code.append(line)
            continue
        if not line.strip() or re.fullmatch(r'\s*\|?[ :|-]+\|[ :|-]*',line):
            continue
        heading = re.match(r'^(#{1,6})\s+(.*)',line)
        if heading:
            blocks.append(('heading',len(heading.group(1)),_plain_inline(heading.group(2)).strip()))
        elif line.strip().startswith('|') and line.strip().endswith('|'):
            blocks.append(('table',tuple(_plain_inline(c.strip()) for c in line.strip()[1:-1].split('|'))))
        else:
            # Preserve list markers, numbers, negation, operators, inline code and line order.
            blocks.append(('line',_plain_inline(line).strip()))
    if code is not None:
        raise ReviewDocumentError('REQUIREMENTS_READBACK_UNCLOSED_CODE')
    return tuple(blocks)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _save(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _visible_text(content: str) -> str:
    """Compare rendered text, allowing Markdown escaping and table layout."""
    # CLI Markdown exports prepend the document metadata title as XML.
    content = re.sub(r"^\s*<title(?:\s[^>]*)?>.*?</title>\s*", "", content, count=1, flags=re.S)
    if content.lstrip().startswith("<"):
        try:
            root = ET.fromstring("<root>" + content + "</root>")
            content = "\n".join(root.itertext())
        except ET.ParseError:
            pass
    content = re.sub(r"<!--.*?-->", "", content, flags=re.S)
    lines, fenced = [], False
    for line in content.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            lines.append(line)
            continue
        if re.fullmatch(r"\s*\|?[ :|-]+\|[ :|-]*", line):
            continue
        line = re.sub(r"^\s*#{1,6}\s+", "", line)
        # Code operators (**, ||, backslashes) must not disappear in the
        # readback check. Strip formatting only outside inline code spans.
        pieces, start = [], 0
        for match in re.finditer(r"(`+)(.+?)\1", line):
            pieces.append(_plain_inline(line[start:match.start()]))
            pieces.append(match.group(2))
            start = match.end()
        pieces.append(_plain_inline(line[start:]))
        line = "".join(pieces)
        if line.strip().startswith("|") and line.strip().endswith("|"):
            line = "|".join(cell.strip() for cell in line.strip().split("|"))
        lines.append(line)
    return " ".join("\n".join(lines).split())


def _plain_inline(content: str) -> str:
    content = re.sub(r"\\([\\`*_\[\]$~<>|#+-])", r"\1", content)
    return re.sub(r"\*\*([^*]+)\*\*", r"\1", content)


def _document(result: dict) -> dict:
    """Find the document envelope in CLI and doc-task results."""
    if isinstance(result, dict):
        if isinstance(result.get("document"), dict):
            return result["document"]
        if "content" in result and ("document_id" in result or "revision_id" in result):
            return result
        for value in result.values():
            if isinstance(value, (dict, list)):
                found = _document(value)
                if found:
                    return found
    elif isinstance(result, list):
        for value in result:
            found = _document(value)
            if found:
                return found
    return {}


class FeishuReviewDocumentPublisher:
    def __init__(self, task_root: str | Path, *, cli=None, chat_id: str = "", strict_readback=False):
        self.root = Path(task_root).resolve() / "requirements" / "feishu"
        self.cli = cli or self._run
        self.chat_id = chat_id
        self.strict_readback = strict_readback

    def _run(self, arguments: list[str]) -> dict:
        if not feishu_notifications_enabled():
            raise ReviewDocumentError("FEISHU_EXTERNAL_DISABLED_FOR_TESTS")
        executable = os.environ.get("FEISHU_DOCUMENT_CLI") or (
            str(Path.home() / "plugins/lark-enterprise/scripts/feishu-connector.cmd")
            if os.name == "nt" else "feishu-connector"
        )
        result = subprocess.run([executable, *arguments], cwd=self.root,
                                capture_output=True, encoding="utf-8", errors="replace",
                                timeout=120, shell=False)
        if result.returncode:
            raise ReviewDocumentError(f"FEISHU_DOCUMENT_CLI_FAILED exit={result.returncode}: "
                                     + (result.stderr or result.stdout)[-1600:])
        try:
            value = json.loads(result.stdout)
        except ValueError as error:
            raise ReviewDocumentError("FEISHU_DOCUMENT_CLI_INVALID_JSON") from error
        if value.get("ok") is False or value.get("failed", 0):
            raise ReviewDocumentError("FEISHU_DOCUMENT_CLI_REJECTED: " + str(value.get("error"))[:1600])
        return value

    def _path(self, version: int) -> Path:
        if version < 1:
            raise ReviewDocumentError("PLAN_REVIEW_VERSION_MISSING")
        return self.root / f"v{version:03d}.json"

    def receipt(self, version: int) -> dict:
        path = self._path(version)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def _fetch(self, document_id: str, version: int) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", document_id):
            raise ReviewDocumentError("PLAN_REVIEW_DOCUMENT_ID_INVALID")
        task = f"fetch-v{version:03d}.json"
        _save(self.root / task, {"doc": document_id,
                                "steps": [{"type": "fetch", "doc_format": "markdown"}]})
        found = _document(self.cli(["doc-task", "--file", "./" + task]))
        if not isinstance(found.get("content"), str):
            raise ReviewDocumentError("PLAN_REVIEW_DOCUMENT_READBACK_MISSING")
        return found

    def _recover(self, record: dict, version: int) -> dict:
        result = self.cli(["lark-cli", "--", "drive", "+search", "--as", "user",
                           "--query", record["title"], "--only-title", "--created-by-me",
                           "--doc-types", "docx", "--page-size", "20"])
        data = result.get("data") or {}
        candidates = data.get("results") or data.get("docs") or data.get("items") or data.get("files") or []
        exact = [row for row in candidates if (row.get("title") or row.get("name")) == record["title"]]
        if len(exact) != 1 or data.get("has_more"):
            raise ReviewDocumentError("PLAN_REVIEW_CREATE_OUTCOME_UNKNOWN: recover existing document before retry")
        row = exact[0]
        token = row.get("token") or row.get("document_id") or row.get("docs_token")
        url = row.get("url") or row.get("docs_url")
        if not token or not url:
            raise ReviewDocumentError("PLAN_REVIEW_RECOVERY_METADATA_MISSING")
        record.update(document_id=token, url=url, status="CREATED")
        _save(self._path(version), record)
        return record

    def publish(self, task_id: str, version: int, markdown: str, document_hash: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", task_id):
            raise ReviewDocumentError("PLAN_REVIEW_TASK_ID_INVALID")
        if not markdown or digest(markdown) != document_hash:
            raise ReviewDocumentError("PLAN_REVIEW_SOURCE_HASH_MISMATCH")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(version)
        record = self.receipt(version)
        if record and (record.get("hash") != document_hash or record.get("task_id") != task_id):
            raise ReviewDocumentError("PLAN_REVIEW_VERSION_COLLISION")
        if not record:
            title = f"中书省需求文档 v{version} {task_id}"
            record = {"task_id": task_id, "version": version, "hash": document_hash,
                      "title": title, "status": "CREATING"}
            # The unique title is only for recovery. It is not another body.
            body = f"# {title}\n\n" + markdown
            body = re.sub(r"<!-- requirement-group:[^\n]+ -->", "", body)
            (self.root / f"source-v{version:03d}.md").write_bytes(markdown.encode("utf-8"))
            (self.root / f"body-v{version:03d}.md").write_bytes(body.encode("utf-8"))
            _save(path, record)  # Persist intent before external creation.
            try:
                found = _document(self.cli(["lark-cli", "--", "docs", "+create", "--api-version", "v2",
                                           "--as", "user", "--doc-format", "markdown",
                                           "--title", title,
                                           "--content", f"@./body-v{version:03d}.md"]))
                if not found.get("document_id") or not found.get("url"):
                    raise ReviewDocumentError("PLAN_REVIEW_CREATE_METADATA_MISSING")
                record.update(document_id=found["document_id"], url=found["url"], status="CREATED")
                _save(path, record)
            except Exception:
                # Intent remains on disk. A later attempt must recover, not recreate.
                raise
        elif not record.get("document_id"):
            record = self._recover(record, version)
        if record.get("status") != "VERIFIED":
            found = self._fetch(record["document_id"], version)
            compare = semantic_blocks if self.strict_readback else _visible_text
            expected = compare(f"# {record['title']}\n\n" + markdown)
            actual = compare(found["content"])
            if actual != expected:
                raise ReviewDocumentError("PLAN_REVIEW_DOCUMENT_READBACK_MISMATCH",
                    source_ref=f"cloud:{version}:{record['document_id']}",prerequisite_fingerprint=digest(found['content']))
            record.update(status="VERIFIED", cloud_content_hash=digest(found["content"]))
            _save(path, record)
        return record

    def verify(self, version: int, document_hash: str) -> None:
        record = self.receipt(version)
        if record.get("status") != "VERIFIED" or record.get("hash") != document_hash:
            raise ReviewDocumentError("PLAN_REVIEW_DOCUMENT_NOT_PUBLISHED")
        found = self._fetch(record["document_id"], version)
        if digest(found["content"]) != record.get("cloud_content_hash"):
            raise ReviewDocumentError("PLAN_REVIEW_DOCUMENT_CHANGED: submit changes as review feedback",
                source_ref=f"cloud:{version}:{record['document_id']}",prerequisite_fingerprint=digest(found['content']))

    def record_delivery(self, version: int, document_hash: str, message_id: str,
                        notification_key: str, decision_id: str) -> None:
        record = self.receipt(version)
        if record.get("hash") != document_hash or not message_id:
            raise ReviewDocumentError("PLAN_REVIEW_DELIVERY_INVALID")
        record.update(message_id=message_id, notification_key=notification_key,
                      decision_id=decision_id)
        _save(self._path(version), record)
