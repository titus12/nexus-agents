from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol
import uuid
import json
import hashlib
import logging
import os
import re
import subprocess
import threading
import time
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .quote_gate import demote_unverified, find_quote_issues, format_rejection
from .prompt_bundle import PromptBundleBuilder, emit_prompt_bundle_report
from .agent_result_file import (
    AgentResultFileError,
    read_agent_result_file,
    write_agent_result_file,
)
from .comment_feed import IncrementalCommentFeed, comment_order_key, timestamp_order_key
from .domain.errors import (
    CONTRACT_REJECTED_EVENT,
    UNSTRUCTURED_REPLY_EVENT,
    is_runtime_failure_notice,
)

logger = logging.getLogger("review_orchestrator_fsm")


def _fingerprint_text(text: str) -> dict[str, object]:
    """Return durable boundary metadata without logging business content."""
    encoded = text.encode("utf-8", errors="replace")
    stripped = text.lstrip("\ufeff\u200b").strip()
    return {
        "chars": len(text),
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "first_char": text[:1],
        "last_char": text[-1:] if text else "",
        "starts_with_json": stripped.startswith("{") or stripped.startswith("["),
        "ends_with_json": stripped.endswith("}") or stripped.endswith("]"),
    }


def _json_parse_diagnostic(text: str) -> dict[str, object]:
    subject = str(text).lstrip("﻿​").strip()
    _, status, error_message, error_position = _parse_json_candidate(subject)
    if status == "invalid" and "```" in subject:
        # Diagnose the body the extractor actually parses, not the fence line.
        unfenced = subject.replace("```json", "").replace("```", "").strip()
        _, status, error_message, error_position = _parse_json_candidate(unfenced)
        subject = unfenced
    context = ""
    if status == "invalid" and error_position is not None:
        start = max(0, error_position - 30)
        context = subject[start : error_position + 30]
    return {
        "json_status": status,
        "json_error": error_message,
        "json_error_position": error_position,
        "json_error_context": context,
    }


def _strip_trailing_commas(text: str) -> str | None:
    """Remove structural trailing commas, never touching string literals.

    Returns ``None`` when no trailing comma exists, so the caller only retries
    a parse when the transform actually changed something.
    """

    out: list[str] = []
    in_string = False
    escaped = False
    changed = False
    for index, char in enumerate(text):
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            out.append(char)
            continue
        if char == "," and text[index + 1:].lstrip()[:1] in ("}", "]"):
            changed = True
            continue
        out.append(char)
    return "".join(out) if changed else None


_VALUE_START = frozenset('"{[-0123456789tfn')


def _escape_inner_quotes(text: str) -> str | None:
    """Escape ASCII double quotes that sit inside a JSON string value.

    A quote closes its string only when the next significant character is
    structural (``: , } ]`` followed by a value start, or end of text); any
    other quote is prose such as ``实施的"整体时长优化"进行审查`` and gets a
    backslash.  Returns ``None`` when nothing was escaped, so the caller
    retries a parse only when the transform changed something.
    """

    out: list[str] = []
    in_string = False
    escaped = False
    changed = False
    for index, char in enumerate(text):
        if not in_string:
            out.append(char)
            if char == '"':
                in_string = True
            continue
        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == "\\":
            out.append(char)
            escaped = True
            continue
        if char != '"':
            out.append(char)
            continue
        rest = text[index + 1 :].lstrip()
        following = rest[:1]
        if following == ",":
            after = rest[1:].lstrip()[:1]
            closes = after in _VALUE_START or after in ("}", "]")
        else:
            closes = following in ("", ":", "}", "]")
        if closes:
            in_string = False
            out.append(char)
        else:
            out.append('\\"')
            changed = True
    return "".join(out) if changed else None


_VALID_JSON_ESCAPES = frozenset('"\\/bfnrt')
_CONTROL_CHAR_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
_HEX4 = re.compile(r"[0-9a-fA-F]{4}")


def _fix_string_escapes(text: str) -> str | None:
    """Repair escape slips inside JSON strings.

    Windows paths and regexes leave backslashes JSON does not know (``\\w``,
    ``\\U``), and multi-line quotes leave raw newlines or tabs in a string.
    Both are unambiguous to repair: an unknown backslash is doubled so it
    survives as a literal backslash, a raw control character becomes its
    escape.  Returns ``None`` when nothing changed.
    """

    out: list[str] = []
    in_string = False
    changed = False
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if not in_string:
            out.append(char)
            in_string = char == '"'
            index += 1
            continue
        if char == '"':
            in_string = False
            out.append(char)
            index += 1
            continue
        if char == "\\":
            following = text[index + 1 : index + 2]
            if following == "u" and _HEX4.fullmatch(text[index + 2 : index + 6]):
                out.append(text[index : index + 6])
                index += 6
            elif following and following in _VALID_JSON_ESCAPES:
                out.append(char + following)
                index += 2
            else:
                out.append("\\\\")
                changed = True
                index += 1
            continue
        if ord(char) < 0x20:
            out.append(_CONTROL_CHAR_ESCAPES.get(char) or f"\\u{ord(char):04x}")
            changed = True
        else:
            out.append(char)
        index += 1
    return "".join(out) if changed else None


# Quote repair must run before escape repair: an unescaped inner quote flips
# the in-string state, so the escape scan would misjudge what follows it.
_ESCAPE_REPAIR_STAGES = (
    ("INVALID_ESCAPE_OR_CONTROL_CHAR", (_fix_string_escapes,)),
    (
        "UNESCAPED_INNER_QUOTE+INVALID_ESCAPE",
        (_escape_inner_quotes, _fix_string_escapes),
    ),
)


def _parse_json_candidate(
    text: str,
) -> tuple[object | None, str, str, int | None]:
    """Parse one Agent JSON document with narrowly scoped framing recovery.

    The external comment transport is text, so the Agent can occasionally emit
    a valid root object followed by one accidental closing brace, or trailing
    commas before a closing bracket. Recover only those exact shapes. In
    particular, never accept the first document from concatenated JSON or
    discard arbitrary trailing content because doing so could silently lose a
    decision or finding.
    """
    candidate = str(text).lstrip("\ufeff\u200b").strip()
    try:
        return json.loads(candidate), "valid", "", None
    except json.JSONDecodeError as error:
        strict_error = error
    except (TypeError, ValueError) as error:
        return None, "invalid", str(error)[:200], None

    try:
        value, end = json.JSONDecoder().raw_decode(candidate)
    except (TypeError, ValueError, json.JSONDecodeError):
        value, end = None, 0

    if isinstance(value, dict):
        trailing = candidate[end:].strip()
        if trailing == "}":
            return value, "repaired", "EXTRA_CLOSING_BRACE", end
        # The model can close the root object one brace early and continue
        # with more fields: {"...":"..."},"questions":[...]}  Accept only
        # when the merged document parses as one complete JSON object, so
        # nothing the model wrote is dropped and malformed tails still
        # fall through to the invalid path.
        if trailing.startswith(",") and candidate[:end].rstrip().endswith("}"):
            merged = candidate[:end].rstrip()[:-1].rstrip().rstrip(",")
            joined = merged + "," + trailing[1:]
            for extra_braces in range(4):
                try:
                    return json.loads(joined + "}" * extra_braces), "repaired", "PREMATURE_OBJECT_CLOSE", end
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue

    stripped = _strip_trailing_commas(candidate)
    if stripped is not None:
        try:
            return json.loads(stripped), "repaired", "TRAILING_COMMA", None
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    escaped_quotes = _escape_inner_quotes(candidate)
    if escaped_quotes is not None:
        for attempt in (escaped_quotes, _strip_trailing_commas(escaped_quotes)):
            if attempt is None:
                continue
            try:
                return json.loads(attempt), "repaired", "UNESCAPED_INNER_QUOTE", None
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
    for label, steps in _ESCAPE_REPAIR_STAGES:
        repaired_text = candidate
        changed = False
        for step in steps:
            step_result = step(repaired_text)
            if step_result is not None:
                repaired_text = step_result
                changed = True
        if not changed:
            continue
        for attempt in (repaired_text, _strip_trailing_commas(repaired_text)):
            if attempt is None:
                continue
            try:
                return json.loads(attempt), "repaired", label, None
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
    return None, "invalid", strict_error.msg, strict_error.pos


from .transport.external import (
    AgentRequest,
    DispatchReceipt,
    ExternalMessage,
    DeliveryReceipt,
    HumanGate,
    HumanReply,
)


class RequestLookupError(RuntimeError):
    """The external request index could not be queried safely."""


def _fanout_child_title(context: object, request_id: str) -> str:
    """Compose the deterministic fan-out child issue title for one dispatch.

    The title embeds the request_id so a retry carrying a fresh attempt gets a
    brand-new child issue: ``ensure_child_issue`` can only fire the assignment
    run at creation time, so a reused completed/failed child issue would
    silently swallow the retry.  Re-dispatching the same request_id (crash
    recovery) still resolves to the same title and reuses the child.
    """

    base_title = ""
    if isinstance(context, dict):
        base_title = str(context.get("fanout_title") or "").strip()
    if base_title:
        return f"{base_title} · {request_id}"
    return f"fanout {request_id}"


def _declared_role_modes(structured_output: object) -> tuple[str, ...]:
    """Role modes the dispatched role contract declares as legal.

    A reply may legitimately use any declared mode (which materialization runs is
    decided by the reply content), so the validators accept every declared mode
    instead of only the one the orchestrator happened to dispatch.
    """

    if not isinstance(structured_output, dict):
        return ()
    modes = structured_output.get("role_modes")
    if not isinstance(modes, (list, tuple)):
        return ()
    return tuple(str(item) for item in modes if str(item or ""))


def _evidence_demand_rejection(request: AgentRequest, payload: object) -> str:
    """Mechanical check that demanded evidence targets were actually cited.

    The dispatch carried an ``evidence_demand`` (the Critic's evidence_targets).
    A target that exists in the workspace and is not cited anywhere in the
    reply means the agent dodged the demand.  Wording is irrelevant — only
    coverage of concrete, verifiable file/symbol references counts.  A target
    that does not exist on disk is skipped: claiming it is unavailable is then
    a legitimate UNKNOWN, not an excuse.  Each demanded finding (one that
    carries evidence_targets) must also have a ``finding_responses`` answer so
    the Critic has something to disposition next round.
    """

    context = request.context or {}
    demand = context.get("evidence_demand")
    if not isinstance(demand, dict):
        return ""
    body = payload if isinstance(payload, dict) else {}
    from .domain.policies.evidence_repair import evidence_scope_rejection
    scope_error = evidence_scope_rejection(body, demand)
    if scope_error:
        return scope_error
    raw_targets = demand.get("evidence_targets")
    demands = demand.get("active_findings")
    if not isinstance(raw_targets, list) and not isinstance(demands, list):
        return ""
    try:
        coverage = payload
        if demand.get("strict_item_scope"):
            coverage = [{"source": row.get("source"), "quote": row.get("quote")}
                        for row in body.get("evidence_updates") or [] if isinstance(row, dict)]
        text = json.dumps(coverage, ensure_ascii=False).casefold()
    except (TypeError, ValueError):
        return ""
    # A JSON-encoded backslash is the two-character escape `\\`, so a
    # Windows-style citation (`cmd\\app.py`) must be folded to forward
    # slashes before the substring match or it reads as uncited.
    text = text.replace("\\\\", "/")
    missing: list[str] = []
    if isinstance(raw_targets, list):
        for target in raw_targets:
            if not isinstance(target, dict):
                continue
            path = str(target.get("path") or "").strip()
            if not path:
                continue
            normalized = path.replace("\\", "/").strip("/")
            candidates = [Path(path), Path(normalized)]
            resolved = next((c for c in candidates if c.is_file()), None)
            if resolved is None:
                continue
            symbol = str(target.get("symbol") or "").strip()
            if (
                normalized.casefold() in text
                or resolved.as_posix().casefold() in text
                or (symbol and symbol in text)
            ):
                continue
            size = resolved.stat().st_size
            missing.append(
                f"{path} exists at {resolved} ({size}B); cite it as file:line"
            )
    if isinstance(demands, list):
        answered = {
            str(entry.get("finding_id") or "").strip()
            for entry in body.get("finding_responses") or []
            if isinstance(entry, dict)
        }
        for finding in demands:
            if not isinstance(finding, dict) or not finding.get("evidence_targets"):
                continue
            finding_id = str(finding.get("finding_id") or "").strip()
            if finding_id and finding_id not in answered:
                missing.append(
                    f"finding {finding_id} has evidence_targets but no "
                    "finding_responses answer; answer every demanded finding "
                    "(finding_id + answer + evidence_ids + suggested_disposition)"
                )
    if not missing:
        return ""
    detail = "; ".join(missing)
    if len(missing) > 3:
        detail = "; ".join(missing[:3]) + f"; (+{len(missing) - 3} more unmet demands)"
    return (
        "evidence targets unmet: " + detail
        + ". Cite each existing file as file:line (locate by globbing the "
        "filename / grepping the symbol; the workspace is mounted read-only "
        "and remote git has no credentials by design). Return one complete "
        "structured result."
    )


def _apply_quote_gate(request, payload):
    return payload


class MulticaAdapter(Protocol):
    def dispatch(self, request: AgentRequest) -> DispatchReceipt: ...
    def poll(self, request: AgentRequest) -> list[ExternalMessage]: ...
    def get_run_status(self, request: AgentRequest) -> str: ...
    def find_existing_request(self, idempotency_key: str, issue_id: str = "") -> DispatchReceipt | None: ...


class FeishuAdapter(Protocol):
    def send_gate(self, gate: HumanGate) -> DeliveryReceipt: ...
    def poll_reply(self, gate: HumanGate) -> list[HumanReply]: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FakeMulticaAdapter:
    def __init__(self) -> None:
        self.dispatched: list[AgentRequest] = []
        self.replies: dict[str, list[ExternalMessage]] = {}
        self.run_statuses: dict[str, str] = {}

    def dispatch(self, request: AgentRequest) -> DispatchReceipt:
        self.dispatched.append(request)
        return DispatchReceipt(operation_id=f"op-{uuid.uuid4().hex}", external_message_id=request.request_id)

    def poll(self, request: AgentRequest) -> list[ExternalMessage]:
        return self.replies.pop(request.request_id, [])

    def get_run_status(self, request: AgentRequest) -> str:
        key = request.dispatch_external_message_id or request.request_id
        return self.run_statuses.get(key, "completed")

    def find_existing_request(self, idempotency_key: str, issue_id: str = "") -> DispatchReceipt | None:
        for request in self.dispatched:
            if request.idempotency_key == idempotency_key:
                return DispatchReceipt(
                    operation_id=f"existing-{request.request_id}",
                    external_message_id=request.request_id,
                    request_id=request.request_id,
                )
        return None

    def queue_reply(self, request_id: str, message: ExternalMessage) -> None:
        self.replies.setdefault(request_id, []).append(message)


class FakeFeishuAdapter:
    def __init__(self) -> None:
        self.gates: list[HumanGate] = []
        self.replies: dict[str, list[HumanReply]] = {}

    def send_gate(self, gate: HumanGate) -> DeliveryReceipt:
        self.gates.append(gate)
        return DeliveryReceipt(channel="feishu", message_id=f"msg-{gate.decision_id}")

    def poll_reply(self, gate: HumanGate) -> list[HumanReply]:
        return self.replies.pop(gate.decision_id, [])

    def queue_reply(self, decision_id: str, reply: HumanReply) -> None:
        self.replies.setdefault(decision_id, []).append(reply)


class NullFeishuAdapter:
    """No-network adapter used by local regression tests and dry runs."""

    def send_gate(self, gate: HumanGate) -> DeliveryReceipt:
        return DeliveryReceipt(channel="disabled", message_id=f"disabled-{gate.decision_id}")

    def poll_reply(self, gate: HumanGate) -> list[HumanReply]:
        return []


class MulticaCliAdapter:
    """Production adapter over the installed multica CLI."""

    def __init__(self, log_dir: str | Path = "logs") -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.prompt_bundle_builder = PromptBundleBuilder(self.log_dir / "prompt-bundles")
        self.agent_result_allowed_root = Path(os.environ.get("AGENT_RESULT_ALLOWED_ROOT", str(self.log_dir.parent)))
        self.orchestrator_result_write_enabled = (
            os.environ.get("ORCHESTRATOR_RESULT_WRITE_ENABLED", "true").lower()
            not in {"0", "false", "no", "off"}
        )
        self.inline_result_max_bytes = max(
            1,
            int(os.environ.get("INLINE_RESULT_MAX_BYTES", str(64 * 1024))),
        )
        logger.info(
            "PROMPT_TRANSPORT_CONFIG mode=prompt_file bundle_root=%s result_allowed_root=%s "
            "prompt_encoding=utf-8 prompt_bom=false result_encoding=utf-8 "
            "result_bom=writer_expected_false_reader_tolerant "
            "orchestrator_result_write_enabled=%s inline_result_max_bytes=%s",
            self.log_dir / "prompt-bundles",
            self.agent_result_allowed_root,
            self.orchestrator_result_write_enabled,
            self.inline_result_max_bytes,
        )
        self.last_issue_id = ""
        self._run_failures: dict[str, str] = {}
        self.cli_timeout_seconds = float(os.environ.get("MULTICA_CLI_TIMEOUT_SEC", "30"))
        self.cli_read_timeout_seconds = float(
            os.environ.get("MULTICA_CLI_READ_TIMEOUT_SEC", "60")
        )
        self.cli_read_retries = max(
            0,
            int(os.environ.get("MULTICA_CLI_READ_RETRIES", "1")),
        )
        self.comment_feed = IncrementalCommentFeed(
            overlap_seconds=max(0, int(os.environ.get("MULTICA_COMMENT_CURSOR_OVERLAP_SEC", "2"))),
            state_path=self.log_dir / "comment-cursor.json",
        )
        self._dispatch_index_path = self.log_dir / "dispatch-index.json"
        self._dispatch_index_lock = threading.RLock()
        self._dispatch_index: dict[str, dict[str, str]] = {}
        self._run_watermarks: dict[str, list[str]] = {}
        self._load_dispatch_index()

    def _load_dispatch_index(self) -> None:
        try:
            value = json.loads(self._dispatch_index_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        entries = value.get("requests", {}) if isinstance(value, dict) else {}
        if not isinstance(entries, dict):
            return
        for key, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            operation_id = str(entry.get("operation_id") or "")
            external_message_id = str(entry.get("external_message_id") or "")
            request_id = str(entry.get("request_id") or "")
            if key and (operation_id or external_message_id):
                self._dispatch_index[str(key)] = {
                    "operation_id": operation_id,
                    "external_message_id": external_message_id,
                    "request_id": request_id,
                }
        watermarks = value.get("run_watermarks", {}) if isinstance(value, dict) else {}
        if isinstance(watermarks, dict):
            for key, seen in watermarks.items():
                if isinstance(seen, list):
                    self._run_watermarks[str(key)] = [
                        str(item) for item in seen if str(item)
                    ]

    def _save_dispatch_index(self) -> None:
        value = {
            "version": 1,
            "requests": self._dispatch_index,
            "run_watermarks": self._run_watermarks,
        }
        self._dispatch_index_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=self._dispatch_index_path.name + ".",
            suffix=".tmp",
            dir=self._dispatch_index_path.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._dispatch_index_path)
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass

    def _capture_run_watermark(
        self, issue_id: str, idempotency_key: str
    ) -> set[str] | None:
        """Record the issue's run ids immediately before a dispatch.

        Switching the issue assignee makes Multica create a *direct* run that
        carries no ``trigger_comment_id``.  Such a run can only be recognised
        by the delta against this snapshot, which avoids comparing the
        orchestrator's sub-second dispatch time with Multica's
        second-granularity ``created_at``.  Returns the captured ids (or
        ``None`` when the snapshot failed) so the dispatcher can also cancel
        stale live runs predating this dispatch.
        """

        if not issue_id or not idempotency_key:
            return None
        run_ids = self._issue_run_ids(issue_id)
        if run_ids is None:
            return None
        with self._dispatch_index_lock:
            self._run_watermarks[str(idempotency_key)] = sorted(run_ids)
            self._save_dispatch_index()
        return run_ids

    def _cancel_stale_live_runs(
        self,
        issue_id: str,
        request: AgentRequest,
        *,
        known_runs: set[str] | None = None,
    ) -> None:
        """Cancel live runs for the same agent before the trigger comment lands.

        Any run that is alive when this dispatch fires (a manual issue
        assignment, or the *direct* run our own assign step just created) was
        started without the structured payload and can never see it: the
        trigger comment does not exist yet.  Multica merges runs sharing one
        ``(issue, agent)`` target by cancelling or queueing the newer comment
        run behind the older one, so a live run starves the fresh dispatch
        (live incidents task-20260920-ac132f and task-20260920-599525).  The
        comment run that follows this sweep runs standalone and reads the
        payload from the trigger comment, which is the pattern proven by
        task-20260920-3c6b68.

        ``known_runs`` is the pre-dispatch run-id watermark: runs outside it
        are the assignment placeholder this dispatch just created, so their
        cancellation is expected bookkeeping (INFO), while a run predating the
        dispatch is a genuine stray (WARNING).
        """

        if not request.agent_id:
            return
        try:
            value = self._run_with_read_retry(
                "issue", "runs", issue_id, "--output", "json"
            )
        except Exception as error:
            logger.warning(
                "AGENT_STRAY_RUN_LOOKUP_FAILED task_id=%s request_id=%s "
                "issue_id=%s error=%s",
                request.task_id,
                request.request_id,
                issue_id,
                str(error)[:300],
            )
            return
        runs = value if isinstance(value, list) else (
            value.get("runs", value.get("items", []))
            if isinstance(value, dict)
            else []
        )
        for run in runs:
            if not isinstance(run, dict):
                continue
            run_id = str(run.get("id") or "")
            if not run_id:
                continue
            if str(run.get("agent_id") or "") != str(request.agent_id):
                continue
            status = self._normalize_run_status(run.get("status"))
            if status in {"completed", "failed"}:
                continue
            try:
                self._run(
                    "issue", "cancel-task", run_id, "--issue", issue_id,
                    "--output", "json",
                )
            except Exception as error:
                logger.warning(
                    "AGENT_STRAY_RUN_CANCEL_FAILED task_id=%s request_id=%s "
                    "issue_id=%s run_id=%s status=%s error=%s",
                    request.task_id,
                    request.request_id,
                    issue_id,
                    run_id,
                    status,
                    str(error)[:300],
                )
                continue
            if known_runs is not None and run_id not in known_runs:
                logger.info(
                    "AGENT_ASSIGN_RUN_CANCELLED task_id=%s request_id=%s "
                    "issue_id=%s run_id=%s kind=%s previous_status=%s",
                    request.task_id,
                    request.request_id,
                    issue_id,
                    run_id,
                    str(run.get("kind") or ""),
                    status,
                )
            else:
                logger.warning(
                    "AGENT_STRAY_RUN_CANCELLED task_id=%s request_id=%s "
                    "issue_id=%s run_id=%s kind=%s previous_status=%s",
                    request.task_id,
                    request.request_id,
                    issue_id,
                    run_id,
                    str(run.get("kind") or ""),
                    status,
                )

    def _issue_run_ids(self, issue_id: str) -> set[str] | None:
        try:
            value = self._run_with_read_retry(
                "issue", "runs", issue_id, "--output", "json"
            )
        except Exception as error:
            logger.warning(
                "RUN_WATERMARK_LOOKUP_FAILED issue_id=%s error=%s",
                issue_id,
                str(error)[:300],
            )
            return None
        runs = value if isinstance(value, list) else (
            value.get("runs", value.get("items", []))
            if isinstance(value, dict)
            else []
        )
        run_ids: set[str] = set()
        for run in runs:
            if isinstance(run, dict):
                run_id = str(run.get("id") or "")
                if run_id:
                    run_ids.add(run_id)
        return run_ids

    def ensure_child_issue(
        self,
        parent_issue_id: str,
        agent_id: str,
        title: str,
        marker: str = "",
    ) -> str:
        """Return a child issue id, creating it under ``parent_issue_id`` once.

        Fan-out workers must each live in their own issue conversation so a
        single agent identity can run them concurrently.  The lookup by
        deterministic title makes this idempotent across retries and restarts.
        """

        parent = str(parent_issue_id or "").strip()
        title = str(title or "").strip()
        if not parent or not title:
            raise RuntimeError("ensure_child_issue requires parent issue and title")
        existing = self._find_child_issue(parent, title)
        if existing:
            return existing
        description = marker or (
            "Nexus orchestrator fan-out worker. The actionable request is "
            "delivered as a follow-up comment; this assignment trigger is only "
            "a placeholder."
        )
        digest = hashlib.sha1(f"{parent}|{title}".encode("utf-8")).hexdigest()[:16]
        description_path = self.log_dir / f"fanout_{digest}.md"
        description_path.write_text(description, encoding="utf-8")
        create_args = [
            "issue", "create",
            "--title", title,
            "--description-file", str(description_path),
            "--parent", parent,
            "--assignee-id", str(agent_id or ""),
        ]
        project_id = self._parent_project_id(parent)
        if project_id:
            create_args += ["--project", project_id]
        create_args += ["--output", "json"]
        value = self._run_with_local_file(create_args, "--description-file", description_path)
        child_id = _extract_issue_id(value)
        if not child_id:
            raise RuntimeError("issue create returned no child issue id")
        logger.info(
            "FANOUT_CHILD_ISSUE_CREATED parent=%s child=%s agent_id=%s",
            parent,
            child_id,
            agent_id,
        )
        return child_id

    def _find_child_issue(self, parent_issue_id: str, title: str) -> str:
        try:
            value = self._run_with_read_retry(
                "issue", "children", parent_issue_id, "--output", "json"
            )
        except Exception as error:
            logger.warning(
                "FANOUT_CHILD_LOOKUP_FAILED parent=%s error=%s",
                parent_issue_id,
                str(error)[:300],
            )
            return ""
        for child in _iter_child_issues(value):
            if str(child.get("title") or "").strip() == title:
                child_id = str(child.get("id") or child.get("issue_id") or "")
                if child_id:
                    logger.info(
                        "FANOUT_CHILD_ISSUE_REUSED parent=%s child=%s",
                        parent_issue_id,
                        child_id,
                    )
                    return child_id
        return ""

    def _parent_project_id(self, parent_issue_id: str) -> str:
        """Best-effort project inheritance so a child issue lands in the right project."""

        cache = getattr(self, "_fanout_project_cache", None)
        if cache is None:
            cache = {}
            self._fanout_project_cache = cache
        if parent_issue_id in cache:
            return cache[parent_issue_id]
        project_id = ""
        try:
            value = self._run_with_read_retry(
                "issue", "get", parent_issue_id, "--output", "json"
            )
            if isinstance(value, dict):
                project_id = str(value.get("project_id") or "")
        except Exception as error:
            logger.warning(
                "FANOUT_PARENT_PROJECT_LOOKUP_FAILED parent=%s error=%s",
                parent_issue_id,
                str(error)[:200],
            )
        cache[parent_issue_id] = project_id
        return project_id

    def close_issue(self, issue_id: str, status: str = "done") -> bool:
        issue = str(issue_id or "").strip()
        if not issue:
            return False
        try:
            self._run("issue", "status", issue, str(status or "done"), "--output", "json")
        except Exception as error:
            logger.warning(
                "FANOUT_CHILD_CLOSE_FAILED issue_id=%s status=%s error=%s",
                issue,
                status,
                str(error)[:300],
            )
            return False
        logger.info("FANOUT_CHILD_CLOSED issue_id=%s status=%s", issue, status)
        return True

    def _index_dispatch_comments(self, comments: list[dict[str, Any]]) -> None:
        changed = False
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            payload = _extract_json(
                str(comment.get("content") or comment.get("body") or "")
            )
            if not isinstance(payload, dict):
                continue
            transport = payload.get("transport")
            stored_key = payload.get("idempotency_key") or (
                transport.get("idempotency_key")
                if isinstance(transport, dict)
                else None
            )
            if not stored_key:
                continue
            comment_id = str(comment.get("id") or comment.get("comment_id") or "")
            if not comment_id:
                continue
            entry = {
                "operation_id": comment_id,
                "external_message_id": comment_id,
                "request_id": str(
                    payload.get("request_id")
                    or (
                        payload.get("transport", {}).get("request_id")
                        if isinstance(payload.get("transport"), dict)
                        else ""
                    )
                    or ""
                ),
            }
            if self._dispatch_index.get(str(stored_key)) != entry:
                self._dispatch_index[str(stored_key)] = entry
                changed = True
        if changed:
            self._save_dispatch_index()

    @staticmethod
    def _is_read_command(args: tuple[str, ...]) -> bool:
        return (
            len(args) >= 2
            and args[:2] == ("issue", "get")
        ) or (
            len(args) >= 3
            and args[:3] == ("issue", "comment", "list")
        ) or (
            len(args) >= 2
            and args[:2] in {("issue", "runs"), ("issue", "children")}
        )

    def _run_with_local_file(
        self, args: Sequence[str], file_flag: str, path: Path
    ) -> object:
        """Run a CLI command whose ``--xxx-file`` argument must be inside its CWD.

        The multica CLI rejects file arguments that resolve outside the
        subprocess working directory (a stale-shared-file safety check).  The
        orchestrator's scratch files live in its own log directory, so the CLI
        runs from there and the file is referenced by name; every file is
        freshly written per request, which preserves the property the check
        protects.
        """

        path = Path(path)
        command = list(args)
        replaced = False
        for index, value in enumerate(command):
            if value == file_flag:
                command[index + 1] = path.name
                replaced = True
                break
        if not replaced:
            raise RuntimeError(f"{file_flag} is required for this command")
        return self._run(*command, cwd=path.parent)

    def _run_with_read_retry(self, *args: str) -> object:
        attempts = self.cli_read_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                return self._run(*args)
            except RuntimeError as error:
                is_timeout = str(error).startswith("multica CLI timed out")
                if not is_timeout or attempt >= attempts:
                    raise
                backoff_seconds = 2
                logger.warning(
                    "MULTICA_CLI_RETRY operation=read attempt=%s next_attempt=%s "
                    "backoff_seconds=%s reason=timeout",
                    attempt,
                    attempt + 1,
                    backoff_seconds,
                )
                time.sleep(backoff_seconds)
        raise AssertionError("unreachable")

    def _run(self, *args: str, cwd: Path | None = None) -> object:
        command = ["multica", *args]
        started = time.monotonic()
        command_text = subprocess.list2cmdline(command)
        timeout_seconds = (
            self.cli_read_timeout_seconds
            if self._is_read_command(args)
            else self.cli_timeout_seconds
        )
        logger.info(
            "MULTICA_CLI_START command=%s timeout_seconds=%s operation=%s cwd=%s",
            command_text,
            timeout_seconds,
            "read" if self._is_read_command(args) else "write",
            cwd or "",
        )
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout_seconds,
                cwd=str(cwd) if cwd else None,
            )
        except subprocess.TimeoutExpired as error:
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.error(
                "MULTICA_CLI_TIMEOUT command=%s duration_ms=%s timeout_seconds=%s operation=%s",
                command_text,
                duration_ms,
                timeout_seconds,
                "read" if self._is_read_command(args) else "write",
            )
            raise RuntimeError(
                f"multica CLI timed out after {timeout_seconds}s: {command_text}"
            ) from error
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "MULTICA_CLI_END command=%s rc=%s duration_ms=%s stdout_chars=%s stderr_chars=%s",
            command_text,
            result.returncode,
            duration_ms,
            len(result.stdout or ""),
            len(result.stderr or ""),
        )
        stdout_text = result.stdout or ""
        stdout_meta = _fingerprint_text(stdout_text)
        logger.info(
            "MULTICA_CLI_RESPONSE_FINGERPRINT command=%s operation=%s rc=%s "
            "chars=%s bytes=%s sha256=%s first_char=%r last_char=%r "
            "starts_with_json=%s ends_with_json=%s",
            command_text,
            "read" if self._is_read_command(args) else "write",
            result.returncode,
            stdout_meta["chars"],
            stdout_meta["bytes"],
            stdout_meta["sha256"],
            stdout_meta["first_char"],
            stdout_meta["last_char"],
            stdout_meta["starts_with_json"],
            stdout_meta["ends_with_json"],
        )
        if result.returncode:
            logger.error(
                "MULTICA_CLI_FAILED command=%s rc=%s stderr=%r",
                command_text,
                result.returncode,
                (result.stderr or "")[:500],
            )
            raise RuntimeError(f"multica failed rc={result.returncode}: {result.stderr[:500]}")
        try:
            return json.loads(stdout_text)
        except json.JSONDecodeError:
            logger.warning(
                "MULTICA_CLI_NON_JSON command=%s stdout_preview=%r",
                command_text,
                stdout_text[:300],
            )
            return stdout_text.strip()

    def dispatch(self, request: AgentRequest) -> DispatchReceipt:
        self.last_issue_id = request.issue_id or request.task_id
        skill_lock = request.context.get("active_runtime_skill_lock")
        if request.phase in {"ZHONGSHU", "MENXIA"} and request.role in {
            "review-analyst",
            "review-solver",
            "review-critic",
        }:
            if (
                not isinstance(skill_lock, dict)
                or skill_lock.get("status") != "locked"
                or not skill_lock.get("name")
                or not skill_lock.get("source")
                or not skill_lock.get("version")
                or not skill_lock.get("sha256")
            ):
                raise RuntimeError("ACTIVE_RUNTIME_SKILL_BINDING_MISSING")
        issue_id = request.issue_id or request.task_id
        # Multica Agent runtime is triggered by issue assignment. The comment
        # carries the structured request, but assignment selects the Agent
        # that should consume it.
        bundle = self.prompt_bundle_builder.build(
            task_id=request.task_id,
            request_id=request.request_id,
            phase=request.phase,
            role=request.role,
            prompt=request.prompt,
            revision_id=str(request.context.get("revision_id", "")),
            state=request.target_state
            or str(request.context.get("target_state") or ""),
            context={
                **request.context,
                "task_id": request.task_id,
                "request_id": request.request_id,
                "phase": request.phase,
                "role": request.role,
                "issue_id": request.issue_id,
                "idempotency_key": request.idempotency_key,
                "sent_after": request.sent_after,
                "dispatch_external_message_id": request.dispatch_external_message_id,
                "structured_output": request.structured_output,
            },
        )
        prompt_ref = bundle.reference()
        target_state = (
            request.target_state
            or str(request.context.get("target_state") or "")
        )
        if target_state in {"MENXIA_GROUP_GATE"}:
            # Freeze / gate dispatches are the phase boundaries where the hop
            # metering gets summarized (2026-09-28 efficiency plan T3.1).
            emit_prompt_bundle_report(
                request.task_id, reason=f"dispatch:{target_state}"
            )
        # Some worker models fail to decode the manifest path out of the
        # doubly-escaped issue body (live incident task-20260920-bbd659
        # CRITIC worker-02), so restate the raw absolute locations as a
        # plain-text field and instruct explicit decoding.
        manifest_dir = Path(str(prompt_ref.get("manifest_path", ""))).parent
        prompt_bundle_paths = (
            "manifest.json: " + str(manifest_dir / "manifest.json") + "\n"
            "prompt.txt: " + str(manifest_dir / "prompt.txt") + "\n"
            "context.json: " + str(prompt_ref.get("context_path", "")) + "\n"
        )
        structured_output_instruction = (
            "Do not modify project files. Return exactly one complete structured JSON "
            "result in the reply; the Orchestrator will validate and persist it. "
            "Do not return Markdown, a result pointer, or a partial result, and never "
            "replace the structured JSON with a bare diff/patch; code or diff text "
            "inside the result's own fields is expected where the active Skill demands it."
        )
        prompt_payload = {
            "prompt": (
                f"Read every required file in the prompt bundle manifest as UTF-8, including active-skill.md when present, then follow prompt.txt. "
                f"The bundle file locations are listed verbatim in the prompt_bundle_paths field below; use those absolute paths exactly as given "
                f"(decode JSON escaping if your tooling shows them escaped) and read them with UTF-8 before doing anything else. "
                f"Active runtime Skill: {request.context.get('active_runtime_skill', '')}. "
                f"Skill lock: {json.dumps(request.context.get('active_runtime_skill_lock', {}), ensure_ascii=False, sort_keys=True)}. "
                f"Runtime directive: {request.context.get('active_runtime_skill_directive', '')} "
                f"Active phase/state: {request.context.get('active_runtime_phase', request.phase)}/{request.context.get('active_runtime_state', request.role)}. "
                "Only the active runtime Skill is authoritative; other attached Skills are inactive for this turn. "
                f"{structured_output_instruction}"
            ),
            "prompt_bundle_paths": prompt_bundle_paths,
            "prompt_ref": prompt_ref,
            "structured_output": request.structured_output,
        }
        content = json.dumps({
            **prompt_payload,
            "response_contract": _response_contract_for(request),
            "transport": {
                "task_id": request.task_id,
                "request_id": request.request_id,
                "role": request.role,
                "phase": request.phase,
                "target_state": request.target_state or request.context.get("target_state", ""),
                "target_role": request.target_role or request.context.get("target_role", ""),
                "idempotency_key": request.idempotency_key,
                "reply_correlation_id": request.request_id,
                "reply_mode": "REPLY_TO_TRIGGER_COMMENT",
                "active_runtime_skill_lock": request.context.get(
                    "active_runtime_skill_lock", {}
                ),
            },
        }, ensure_ascii=False, indent=2)
        logger.info(
            "AGENT_DISPATCH_TRANSPORT task_id=%s request_id=%s phase=%s role=%s "
            "mode=%s comment_payload_bytes=%s full_prompt_bytes=%s manifest_path=%s "
            "manifest_hash=%s structured_output_mode=%s "
            "structured_output_schema_hash=%s",
            request.task_id,
            request.request_id,
            request.phase,
            request.role,
            "prompt_file",
            len(content.encode("utf-8")),
            len(request.prompt.encode("utf-8")),
            prompt_payload.get("prompt_ref", {}).get("manifest_path", ""),
            prompt_payload.get("prompt_ref", {}).get("manifest_hash", ""),
            (request.structured_output or {}).get("mode", ""),
            (request.structured_output or {}).get("schema_hash", ""),
        )
        dispatch_meta = _fingerprint_text(content)
        logger.info(
            "AGENT_DISPATCH_PAYLOAD_FINGERPRINT task_id=%s request_id=%s "
            "dispatch_idempotency_key=%s phase=%s role=%s chars=%s bytes=%s "
            "sha256=%s first_char=%r last_char=%r starts_with_json=%s ends_with_json=%s "
            "structured_output_mode=%s structured_output_schema_hash=%s",
            request.task_id,
            request.request_id,
            request.idempotency_key,
            request.phase,
            request.role,
            dispatch_meta["chars"],
            dispatch_meta["bytes"],
            dispatch_meta["sha256"],
            dispatch_meta["first_char"],
            dispatch_meta["last_char"],
            dispatch_meta["starts_with_json"],
            dispatch_meta["ends_with_json"],
            (request.structured_output or {}).get("mode", ""),
            (request.structured_output or {}).get("schema_hash", ""),
        )
        fanout_parent = str(request.context.get("fanout_parent_id") or "").strip()
        if fanout_parent and request.agent_id:
            # Fan-out workers get their own child issue whose description *is*
            # the dispatch payload: creating it assigned fires exactly one
            # assignment run that does the real work.  This avoids the extra
            # placeholder assignment run (and its poisoned session) that a
            # separate follow-up comment would cause.
            # The child title is scoped to the request_id so a retry with a
            # fresh attempt creates a brand-new child issue (a reused
            # completed/failed issue cannot fire another assignment run);
            # re-dispatching the same request_id after a crash still reuses
            # the existing child deterministically.
            title = _fanout_child_title(request.context, request.request_id)
            child_id = self.ensure_child_issue(
                fanout_parent, request.agent_id, title, content
            )
            with self._dispatch_index_lock:
                # A brand-new child issue has no runs yet, so the assignment run
                # it triggers is the only "fresh" run for this dispatch.
                self._run_watermarks[request.idempotency_key] = []
                self._dispatch_index[request.idempotency_key] = {
                    "operation_id": child_id,
                    "external_message_id": "",
                    "request_id": request.request_id,
                    "issue_id": child_id,
                }
                self._save_dispatch_index()
            logger.info(
                "FANOUT_CHILD_ISSUE_DISPATCHED parent=%s child=%s request_id=%s "
                "agent_id=%s description_chars=%s",
                fanout_parent,
                child_id,
                request.request_id,
                request.agent_id,
                len(content),
            )
            return DispatchReceipt(
                operation_id=child_id,
                external_message_id="",
                confirmed=True,
                request_id=request.request_id,
                issue_id=child_id,
            )
        # Multica Agent runtime is triggered by issue assignment, not by the
        # agent_id carried in the comment payload.  Update the assignee to the
        # target agent before posting so the correct Agent consumes the work.
        # The resulting direct run has no trigger_comment_id; snapshot the
        # issue's runs first so the assignment run can be recognised by its
        # position (run-id delta) instead of by clock comparison.
        known_runs = self._capture_run_watermark(issue_id, request.idempotency_key)
        if request.agent_id:
            self._run("issue", "update", issue_id, "--assignee-id", request.agent_id, "--output", "json")
        self._cancel_stale_live_runs(issue_id, request, known_runs=known_runs)
        path = self.log_dir / f"dispatch_{request.request_id.replace(':', '_')}.json"
        path.write_text(content, encoding="utf-8")
        value = self._run_with_local_file(
            [
                "issue", "comment", "add", issue_id,
                "--content-file", str(path),
                "--output", "json",
            ],
            "--content-file",
            path,
        )
        external_id = ""
        if isinstance(value, dict):
            external_id = str(value.get("id") or value.get("comment_id") or value.get("comment", {}).get("id") or "")
        if not external_id:
            external_id = self._resolve_trigger_comment_id(
                issue_id=issue_id,
                content=content,
                request=request,
            )
        if not external_id:
            logger.error(
                "AGENT_DISPATCH_TRIGGER_ID_MISSING task_id=%s request_id=%s "
                "phase=%s role=%s reason=comment_add_output_missing_and_lookup_failed",
                request.task_id,
                request.request_id,
                request.phase,
                request.role,
            )
        else:
            logger.info(
                "AGENT_DISPATCH_TRIGGER_READY task_id=%s request_id=%s "
                "phase=%s role=%s trigger_comment_id=%s",
                request.task_id,
                request.request_id,
                request.phase,
                request.role,
                external_id,
            )
        if external_id:
            with self._dispatch_index_lock:
                self._dispatch_index[request.idempotency_key] = {
                    "operation_id": request.idempotency_key,
                    "external_message_id": external_id,
                    "request_id": request.request_id,
                    "issue_id": issue_id,
                }
                self._save_dispatch_index()
        return DispatchReceipt(
            operation_id=request.idempotency_key,
            external_message_id=external_id,
            request_id=request.request_id,
            issue_id=issue_id,
        )

    def _resolve_trigger_comment_id(
        self,
        issue_id: str,
        content: str,
        request: AgentRequest,
    ) -> str:
        """Resolve the trigger comment id when the CLI add command omits it."""
        try:
            value = self.comment_feed.read(
                f"{issue_id}:dispatch:{request.request_id}",
                lambda since: self._run_with_read_retry(
                    "issue", "comment", "list", issue_id,
                    *(('--since', since) if since else ('--recent', '20')),
                    "--output", "json",
                ),
                initial_since=request.sent_after,
            )
        except Exception as error:
            logger.warning(
                "AGENT_DISPATCH_TRIGGER_LOOKUP_FAILED task_id=%s request_id=%s "
                "issue_id=%s error=%s",
                request.task_id,
                request.request_id,
                issue_id,
                str(error)[:300],
            )
            return ""
        comments = value
        expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        matches: list[dict] = []
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            comment_id = str(comment.get("id") or comment.get("comment_id") or "")
            body = str(comment.get("content") or comment.get("body") or "")
            if not comment_id or not body:
                continue
            body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if body == content or body_hash == expected_hash:
                matches.append(comment)
                continue
            try:
                payload = json.loads(body.lstrip("\ufeff\u200b").strip())
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if (
                isinstance(payload, dict)
                and str(payload.get("transport", {}).get("request_id") or "")
                == request.request_id
            ):
                matches.append(comment)
        if not matches:
            logger.warning(
                "AGENT_DISPATCH_TRIGGER_LOOKUP_MISS task_id=%s request_id=%s "
                "issue_id=%s expected_payload_sha256=%s",
                request.task_id,
                request.request_id,
                issue_id,
                expected_hash,
            )
            return ""
        matches.sort(key=comment_order_key)
        resolved = str(matches[-1].get("id") or matches[-1].get("comment_id") or "")
        logger.info(
            "AGENT_DISPATCH_TRIGGER_LOOKUP_HIT task_id=%s request_id=%s "
            "issue_id=%s trigger_comment_id=%s candidates=%s "
            "expected_payload_sha256=%s",
            request.task_id,
            request.request_id,
            issue_id,
            resolved,
            len(matches),
            expected_hash,
        )
        return resolved

    def _recover_result_file(
        self,
        request: AgentRequest,
        rejections: list[str] | None = None,
    ) -> ExternalMessage | None:
        """Read the agent's result file, if it wrote one.

        ``rejections`` collects the reason when the file exists but the role
        validator refuses it.  The caller turns that into a reply-shape failure
        instead of letting an unusable-but-delivered result look like a missing
        one (which charged the reply to the infrastructure retry budget).
        """
        result_path = self.prompt_bundle_builder.result_path(
            request.task_id,
            request.request_id,
        )
        logger.info(
            "AGENT_REPLY_FILE_RECOVERY_SCAN task_id=%s request_id=%s phase=%s role=%s "
            "path=%s exists=%s",
            request.task_id,
            request.request_id,
            request.phase,
            request.role,
            result_path,
            result_path.is_file(),
        )
        if not result_path.is_file():
            return None
        try:
            file_result = read_agent_result_file(
                {"result_path": str(result_path)},
                workflow=str(request.context.get('workflow') or 'requirements-v1'),
                task_id=request.task_id,
                request_id=request.request_id,
                phase=request.phase,
                role=request.role,
                allowed_root=self.agent_result_allowed_root,
                expected_schema_hash=str(
                    (request.structured_output or {}).get("schema_hash") or ""
                ),
                expected_state=request.target_state
                or str(request.context.get("target_state") or "")
                or str((request.structured_output or {}).get("state") or ""),
                expected_role_mode=str(
                    request.context.get("structured_output_role_mode")
                    or (request.structured_output or {}).get("role_mode")
                    or ""
                ),
                allowed_role_modes=_declared_role_modes(request.structured_output),
                allow_transport_backfill=True,
            )
        except AgentResultFileError as error:
            logger.warning(
                "AGENT_REPLY_FILE_RECOVERY_REJECTED task_id=%s request_id=%s "
                "path=%s reason=%s",
                request.task_id,
                request.request_id,
                result_path,
                str(error),
            )
            if rejections is not None:
                rejections.append(str(error))
            return None
        logger.info(
            "AGENT_REPLY_FILE_RECOVERED task_id=%s request_id=%s phase=%s role=%s "
            "path=%s sha256=%s bytes=%s source=directory_scan",
            request.task_id,
            request.request_id,
            request.phase,
            request.role,
            file_result.path,
            file_result.sha256,
            file_result.bytes,
        )
        rejection = _evidence_demand_rejection(request, file_result.payload)
        if rejection:
            logger.warning(
                "AGENT_REPLY_EVIDENCE_TARGETS_REJECTED task_id=%s request_id=%s "
                "phase=%s role=%s path=%s reason=%s",
                request.task_id,
                request.request_id,
                request.phase,
                request.role,
                file_result.path,
                rejection,
            )
            if rejections is not None:
                rejections.append(rejection)
            return None
        try:
            gated = _apply_quote_gate(request, file_result.payload)
        except AgentResultFileError as error:
            if rejections is not None:
                rejections.append(str(error))
            return None
        return ExternalMessage(
            request.agent_id,
            gated,
            f"file:{request.request_id}",
            "",
        )

    def _persist_inline_result(
        self,
        request: AgentRequest,
        payload: dict,
    ) -> dict:
        inline_payload = payload
        if payload.get("protocol") == "nexus-agent-result-inline-v1":
            nested = payload.get("result")
            if not isinstance(nested, dict):
                raise AgentResultFileError("inline result.result must be an object")
            inline_payload = nested
        rejection = _evidence_demand_rejection(request, inline_payload)
        if rejection:
            logger.warning(
                "AGENT_REPLY_EVIDENCE_TARGETS_REJECTED task_id=%s request_id=%s "
                "phase=%s role=%s reason=%s",
                request.task_id,
                request.request_id,
                request.phase,
                request.role,
                rejection,
            )
            raise AgentResultFileError(rejection)
        gated = _apply_quote_gate(request, inline_payload)
        if gated is not inline_payload:
            if inline_payload is not payload:
                payload = {**payload, "result": gated}
            else:
                payload = gated
            inline_payload = gated
        if not self.orchestrator_result_write_enabled:
            return payload
        encoded_size = len(
            json.dumps(inline_payload, ensure_ascii=False).encode("utf-8")
        )
        if encoded_size > self.inline_result_max_bytes:
            raise AgentResultFileError(
                f"inline result exceeds limit: {encoded_size}>{self.inline_result_max_bytes}"
            )
        result_path = self.prompt_bundle_builder.result_path(
            request.task_id,
            request.request_id,
        )
        file_result = write_agent_result_file(
            inline_payload,
            workflow=str(request.context.get('workflow') or 'requirements-v1'),
            target_path=result_path,
            task_id=request.task_id,
            request_id=request.request_id,
            phase=request.phase,
            role=request.role,
            allowed_root=self.agent_result_allowed_root,
            expected_schema_hash=str(
                (request.structured_output or {}).get("schema_hash") or ""
            ),
            expected_state=request.target_state or str(
                request.context.get("target_state") or ""
            ),
            expected_role_mode=str(
                request.context.get("structured_output_role_mode")
                or (request.structured_output or {}).get("role_mode")
                or ""
            ),
            allowed_role_modes=_declared_role_modes(request.structured_output),
        )
        return file_result.payload

    def _save_raw_reply(self, request: AgentRequest, comment_id: str, body: str) -> str:
        """Keep an unusable reply verbatim; the event payload only carries a preview."""

        try:
            directory = self.log_dir / "raw-replies"
            directory.mkdir(parents=True, exist_ok=True)
            stem = re.sub(
                r"[^A-Za-z0-9._-]+",
                "_",
                f"{request.task_id}_{request.request_id}_{comment_id}",
            )
            path = directory / f"{stem[:180]}.txt"
            if not path.exists():
                path.write_text(body, encoding="utf-8")
            return str(path)
        except OSError as error:
            logger.warning(
                "AGENT_RAW_REPLY_SAVE_FAILED task_id=%s request_id=%s comment_id=%s error=%s",
                request.task_id,
                request.request_id,
                comment_id,
                str(error)[:200],
            )
            return ""

    def poll(self, request: AgentRequest) -> list[ExternalMessage]:
        self.last_issue_id = request.issue_id or request.task_id
        issue_id = request.issue_id or request.task_id

        def comments_from(value: object) -> list[dict]:
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = value.get("comments", value.get("items", []))
                return [item for item in nested if isinstance(item, dict)]
            return []

        def parse_comments(
            comments: list[dict],
            allowed_ids: set[str] | None = None,
            allow_request_fallback: bool = False,
            require_explicit_binding: bool = False,
        ) -> tuple[
            list[ExternalMessage],
            list[str],
            list[ExternalMessage],
            dict[str, int],
            int,
        ]:
            result: list[ExternalMessage] = []
            supplemental_reports: list[str] = []
            unstructured_candidates: list[ExternalMessage] = []
            rejected_candidates: list[ExternalMessage] = []
            inline_result_accepted = False
            stats = {
                "author_mismatch": 0,
                "dispatch_comment": 0,
                "stale": 0,
                "unstructured": 0,
                "request_mismatch": 0,
                "uncorrelated": 0,
                "contract_rejected": 0,
            }
            ordered_comments = sorted(comments, key=comment_order_key)
            for comment in ordered_comments:
                comment_id = str(comment.get("id") or "")
                if allowed_ids is not None and comment_id not in allowed_ids:
                    stats["uncorrelated"] += 1
                    continue
                body = str(comment.get("content") or comment.get("body") or "")
                payload = _extract_json(body)
                inline_result_pending = False
                author_id = str(
                    comment.get("author_id")
                    or comment.get("creator_id")
                    or comment.get("user_id")
                    or ""
                )
                body_meta = _fingerprint_text(body)
                parse_meta = _json_parse_diagnostic(body)
                logger.info(
                    "AGENT_REPLY_COMMENT_READ task_id=%s request_id=%s phase=%s role=%s "
                    "dispatch_idempotency_key=%s dispatch_external_message_id=%s comment_id=%s "
                    "author_id=%s chars=%s bytes=%s sha256=%s first_char=%r last_char=%r "
                    "starts_with_json=%s ends_with_json=%s json_status=%s json_error=%r "
                    "json_error_position=%s",
                    request.task_id,
                    request.request_id,
                    request.phase,
                    request.role,
                    request.idempotency_key,
                    request.dispatch_external_message_id,
                    comment_id,
                    author_id,
                    body_meta["chars"],
                    body_meta["bytes"],
                    body_meta["sha256"],
                    body_meta["first_char"],
                    body_meta["last_char"],
                    body_meta["starts_with_json"],
                    body_meta["ends_with_json"],
                    parse_meta["json_status"],
                    parse_meta["json_error"],
                    parse_meta["json_error_position"],
                )
                if author_id == request.agent_id:
                    logger.info(
                        "AGENT_OUTPUT_FINGERPRINT task_id=%s request_id=%s phase=%s role=%s "
                        "dispatch_external_message_id=%s comment_id=%s chars=%s bytes=%s "
                        "sha256=%s first_char=%r last_char=%r starts_with_json=%s "
                        "ends_with_json=%s json_status=%s json_error=%r json_error_position=%s",
                        request.task_id,
                        request.request_id,
                        request.phase,
                        request.role,
                        request.dispatch_external_message_id,
                        comment_id,
                        body_meta["chars"],
                        body_meta["bytes"],
                        body_meta["sha256"],
                        body_meta["first_char"],
                        body_meta["last_char"],
                        body_meta["starts_with_json"],
                        body_meta["ends_with_json"],
                        parse_meta["json_status"],
                        parse_meta["json_error"],
                        parse_meta["json_error_position"],
                    )
                if comment_id and comment_id == request.dispatch_external_message_id:
                    stats["dispatch_comment"] += 1
                    continue
                structured_output_mode = (
                    isinstance(request.structured_output, dict)
                    and request.structured_output.get("mode")
                )
                if author_id != request.agent_id and not structured_output_mode:
                    stats["author_mismatch"] += 1
                    continue
                created_at = str(comment.get("created_at") or "")
                if (
                    request.sent_after
                    and created_at
                    and timestamp_order_key(created_at) <= timestamp_order_key(request.sent_after)
                ):
                    stats["stale"] += 1
                    continue
                if isinstance(payload, dict) and payload.get("action"):
                    payload_request_id = payload.get("request_id")
                    if payload_request_id and payload_request_id != request.request_id:
                        stats["request_mismatch"] += 1
                        logger.warning(
                            "STALE_INLINE_RESULT issue_id=%s task_id=%s "
                            "request_id=%s comment_id=%s payload_request_id=%s "
                            "reason=payload_binding_mismatch",
                            issue_id,
                            request.task_id,
                            request.request_id,
                            comment_id,
                            payload_request_id,
                        )
                        continue
                    inline_result_pending = True
                if not isinstance(payload, dict) or not payload.get("action"):
                    stats["unstructured"] += 1
                    if author_id == request.agent_id:
                        logger.warning(
                            "AGENT_REPLY_PARSE_FAILED task_id=%s request_id=%s phase=%s role=%s "
                            "comment_id=%s sha256=%s json_status=%s json_error=%r "
                            "json_error_position=%s",
                            request.task_id,
                            request.request_id,
                            request.phase,
                            request.role,
                            comment_id,
                            body_meta["sha256"],
                            parse_meta["json_status"],
                            parse_meta["json_error"],
                            parse_meta["json_error_position"],
                        )
                    if require_explicit_binding:
                        parent_id = str(
                            comment.get("parent_id")
                            or comment.get("parent_comment_id")
                            or comment.get("thread_id")
                            or ""
                        )
                        if parent_id != request.dispatch_external_message_id:
                            stats["uncorrelated"] += 1
                            logger.warning(
                                "REPLY_CORRELATION_FILTERED issue_id=%s task_id=%s request_id=%s "
                                "external_id=%s actual_author_id=%s parent_id=%s "
                                "payload_request_id=%s reason=missing_explicit_binding",
                                issue_id,
                                request.task_id,
                                request.request_id,
                                comment_id,
                                author_id,
                                parent_id,
                                "",
                            )
                            continue
                    if body.strip():
                        supplemental_reports.append(body)
                        if is_runtime_failure_notice(body):
                            logger.warning(
                                "AGENT_RUNTIME_NOTICE_IGNORED task_id=%s request_id=%s "
                                "phase=%s role=%s comment_id=%s chars=%s notice=%r",
                                request.task_id,
                                request.request_id,
                                request.phase,
                                request.role,
                                comment_id,
                                body_meta["chars"],
                                body.strip()[:160],
                            )
                            continue
                        raw_reply_path = self._save_raw_reply(request, comment_id, body)
                        unstructured_candidates.append(
                            ExternalMessage(
                                author_id,
                                {
                                    "action": UNSTRUCTURED_REPLY_EVENT,
                                    "task_id": request.task_id,
                                    "request_id": request.request_id,
                                    "role": request.role,
                                    "phase": request.phase,
                                    "response_source": "comment",
                                    "structured_output_schema_hash": str(
                                        (request.structured_output or {}).get("schema_hash") or ""
                                    ),
                                    "raw_reply": body[:16000],
                                    "raw_reply_truncated": len(body) > 16000,
                                    "raw_reply_path": raw_reply_path,
                                    "raw_reply_chars": body_meta["chars"],
                                    "raw_reply_bytes": body_meta["bytes"],
                                    "raw_reply_sha256": body_meta["sha256"],
                                    "raw_reply_first_char": body_meta["first_char"],
                                    "raw_reply_last_char": body_meta["last_char"],
                                    "json_status": parse_meta["json_status"],
                                    "json_error": parse_meta["json_error"],
                                    "json_error_position": parse_meta["json_error_position"],
                                    "json_error_context": parse_meta["json_error_context"],
                                    "launch_failed": (
                                        "start opencode" in body.lower()
                                        and "too long" in body.lower()
                                    ),
                                },
                                comment_id,
                                body,
                            )
                        )
                    continue
                payload_request_id = payload.get("request_id")
                parent_id = str(
                    comment.get("parent_id")
                    or comment.get("parent_comment_id")
                    or comment.get("thread_id")
                    or ""
                )
                request_bound = payload_request_id == request.request_id
                thread_bound = parent_id == request.dispatch_external_message_id
                parent_field_present = any(
                    key in comment
                    for key in ("parent_id", "parent_comment_id", "thread_id")
                )
                thread_relation_required = (
                    request.dispatch_external_message_id and parent_field_present
                )
                if (
                    (require_explicit_binding or thread_relation_required)
                    and not (request_bound or thread_bound)
                ):
                    stats["uncorrelated"] += 1
                    logger.warning(
                        "REPLY_CORRELATION_FILTERED issue_id=%s task_id=%s request_id=%s "
                        "external_id=%s actual_author_id=%s parent_id=%s payload_request_id=%s "
                        "reason=missing_explicit_binding",
                        issue_id,
                        request.task_id,
                        request.request_id,
                        comment_id,
                        author_id,
                        parent_id,
                        payload_request_id or "",
                    )
                    continue
                if payload_request_id and payload_request_id != request.request_id:
                    stats["request_mismatch"] += 1
                    logger.warning(
                        "REPLY_CORRELATION_FILTERED issue_id=%s task_id=%s request_id=%s "
                        "external_id=%s payload_request_id=%s reason=request_id_mismatch",
                        issue_id,
                        request.task_id,
                        request.request_id,
                        comment_id,
                        payload_request_id,
                    )
                    continue
                payload_task_id = payload.get("task_id")
                if payload_task_id and payload_task_id != request.task_id:
                    stats["request_mismatch"] += 1
                    logger.warning(
                        "REPLY_CORRELATION_FILTERED issue_id=%s task_id=%s request_id=%s "
                        "external_id=%s payload_task_id=%s reason=task_id_mismatch",
                        issue_id,
                        request.task_id,
                        request.request_id,
                        comment_id,
                        payload_task_id,
                    )
                    continue
                if inline_result_pending:
                    if inline_result_accepted:
                        logger.warning(
                            "AGENT_INLINE_RESULT_DUPLICATE task_id=%s request_id=%s "
                            "phase=%s role=%s comment_id=%s reason=already_accepted",
                            request.task_id,
                            request.request_id,
                            request.phase,
                            request.role,
                            comment_id,
                        )
                        continue
                    try:
                        payload = self._persist_inline_result(request, payload)
                        inline_result_accepted = True
                        logger.info(
                            "AGENT_INLINE_RESULT_ACCEPTED task_id=%s request_id=%s "
                            "phase=%s role=%s comment_id=%s",
                            request.task_id,
                            request.request_id,
                            request.phase,
                            request.role,
                            comment_id,
                        )
                    except AgentResultFileError as error:
                        stats["contract_rejected"] += 1
                        logger.warning(
                            "AGENT_INLINE_RESULT_REJECTED task_id=%s request_id=%s "
                            "phase=%s role=%s comment_id=%s reason=%s",
                            request.task_id,
                            request.request_id,
                            request.phase,
                            request.role,
                            comment_id,
                            str(error),
                        )
                        # The agent did answer with valid JSON; only its document
                        # was unusable.  Surface that as a reply-shape candidate so
                        # the effect is reported as a reply contract failure (reply
                        # retry budget) instead of "agent run completed without a
                        # correlated result" (infrastructure retry budget).
                        rejected_candidates.append(
                            ExternalMessage(
                                author_id,
                                {
                                    "action": CONTRACT_REJECTED_EVENT,
                                    "task_id": request.task_id,
                                    "request_id": request.request_id,
                                    "role": request.role,
                                    "phase": request.phase,
                                    "response_source": "comment",
                                    "contract_rejection": str(error),
                                    "raw_reply": body[:16000],
                                    "raw_reply_truncated": len(body) > 16000,
                                    "raw_reply_path": self._save_raw_reply(
                                        request, comment_id, body
                                    ),
                                    "raw_reply_chars": body_meta["chars"],
                                    "raw_reply_bytes": body_meta["bytes"],
                                    "raw_reply_sha256": body_meta["sha256"],
                                    "structured_output_schema_hash": str(
                                        (request.structured_output or {}).get("schema_hash") or ""
                                    ),
                                },
                                comment_id,
                                body,
                            )
                        )
                        continue
                payload["task_id"] = request.task_id
                payload["request_id"] = request.request_id
                payload.setdefault("role", request.role)
                payload.setdefault("phase", request.phase)
                result.append(ExternalMessage(author_id, payload, comment_id, body))
            return (
                result,
                supplemental_reports,
                unstructured_candidates,
                stats,
                len(ordered_comments),
                rejected_candidates,
            )

        if request.dispatch_external_message_id:
            thread_value = self.comment_feed.read(
                f"{issue_id}:thread:{request.dispatch_external_message_id}",
                lambda since: self._run_with_read_retry(
                    "issue", "comment", "list", issue_id,
                    "--thread", request.dispatch_external_message_id,
                    "--tail", "30",
                    *(('--since', since) if since else ()),
                    "--output", "json",
                ),
                initial_since=request.sent_after,
            )
        else:
            thread_value = self.comment_feed.read(
                f"{issue_id}:recent:{request.request_id}",
                lambda since: self._run_with_read_retry(
                    "issue", "comment", "list", issue_id,
                    "--recent", "50",
                    *(('--since', since) if since else ()),
                    "--output", "json",
                ),
                initial_since=request.sent_after,
            )
        (
            result,
            supplemental_reports,
            unstructured_candidates,
            stats,
            comments_total,
            rejected_candidates,
        ) = parse_comments(comments_from(thread_value))
        if not result:
            file_rejections: list[str] = []
            recovered_file_message = self._recover_result_file(
                request, rejections=file_rejections
            )
            if recovered_file_message is not None:
                result = [recovered_file_message]
            elif file_rejections:
                stats["contract_rejected"] += 1
                rejected_candidates.append(
                    ExternalMessage(
                        request.agent_id,
                        {
                            "action": CONTRACT_REJECTED_EVENT,
                            "task_id": request.task_id,
                            "request_id": request.request_id,
                            "role": request.role,
                            "phase": request.phase,
                            "response_source": "result_file",
                            "raw_reply_path": str(self.prompt_bundle_builder.result_path(request.task_id, request.request_id)),
                            "contract_rejection": file_rejections[-1],
                            "structured_output_schema_hash": str(
                                (request.structured_output or {}).get("schema_hash") or ""
                            ),
                        },
                        f"file:{request.request_id}",
                        "",
                    )
                )
        # Multica can publish a short comment while preserving the complete
        # agent response only in the completed execution's result.output.
        # Recover it before classifying the comment as a failed delivery.
        if not result:
            recovered_run = self._recover_run_output(request, rejected_candidates)
            if recovered_run is not None:
                result = [recovered_run]
        recovery_ids: set[str] = set()

        if (
            not result
            and request.dispatch_external_message_id
            and len(unstructured_candidates) != 1
            and (
                comments_total > 0
                or
                stats["author_mismatch"]
                or stats["unstructured"]
                or stats["request_mismatch"]
                or stats["uncorrelated"]
            )
        ):
            root_comments = self.comment_feed.read(
                f"{issue_id}:{request.task_id}:{request.request_id}",
                lambda since: self._run_with_read_retry(
                    "issue", "comment", "list", issue_id,
                    *(('--since', since) if since else ()),
                    "--output", "json",
                ),
                initial_since=request.sent_after,
            )
            (
                root_result,
                root_supplemental,
                root_unstructured_candidates,
                root_stats,
                root_total,
                root_rejected_candidates,
            ) = parse_comments(
                root_comments,
                require_explicit_binding=True,
            )
            unstructured_candidates = root_unstructured_candidates
            rejected_candidates.extend(root_rejected_candidates)
            if root_result:
                result = root_result
                supplemental_reports.extend(root_supplemental)
                for key, value_count in root_stats.items():
                    stats[key] += value_count
                comments_total += root_total
                logger.info(
                    "REPLY_CORRELATION_RECOVERED issue_id=%s task_id=%s "
                    "request_id=%s matched_by=explicit_thread_or_request_binding "
                    "external_id=%s",
                    issue_id,
                    request.task_id,
                    request.request_id,
                    result[0].external_id,
                )
            elif root_supplemental:
                unstructured_candidates = root_unstructured_candidates
                logger.info(
                    "REPLY_SUPPLEMENTAL_ONLY issue_id=%s task_id=%s request_id=%s "
                    "reports=%s reason=unstructured_or_ambiguous",
                    issue_id,
                    request.task_id,
                    request.request_id,
                    len(root_supplemental),
                )

        if not result and request.dispatch_external_message_id:
            recovery_ids = self._find_correlated_delivery_ids(request)
            if recovery_ids:
                logger.info(
                    "REPLY_CORRELATION_MATCH issue_id=%s task_id=%s request_id=%s "
                    "trigger_comment_id=%s delivered_comment_ids=%s",
                    issue_id,
                    request.task_id,
                    request.request_id,
                    request.dispatch_external_message_id,
                    sorted(recovery_ids),
                )
                root_comments = self.comment_feed.read(
                    f"{issue_id}:{request.task_id}:{request.request_id}",
                    lambda since: self._run_with_read_retry(
                        "issue", "comment", "list", issue_id,
                        *(('--since', since) if since else ()),
                        "--output", "json",
                    ),
                    initial_since=request.sent_after,
                )
                (
                    root_result,
                    root_supplemental,
                    root_unstructured_candidates,
                    root_stats,
                    root_total,
                    root_rejected_candidates,
                ) = parse_comments(root_comments, recovery_ids)
                comments_total += root_total
                result = root_result
                supplemental_reports.extend(root_supplemental)
                unstructured_candidates.extend(root_unstructured_candidates)
                rejected_candidates.extend(root_rejected_candidates)
                for key, value_count in root_stats.items():
                    stats[key] += value_count
                if result:
                    logger.warning(
                        "REPLY_ROOT_RECOVERED_BY_EXECUTION issue_id=%s task_id=%s "
                        "request_id=%s trigger_comment_id=%s recovered_ids=%s",
                        issue_id,
                        request.task_id,
                        request.request_id,
                        request.dispatch_external_message_id,
                        [message.external_id for message in result],
                    )
            else:
                logger.info(
                    "REPLY_CORRELATION_PENDING issue_id=%s task_id=%s request_id=%s "
                    "trigger_comment_id=%s",
                    issue_id,
                    request.task_id,
                    request.request_id,
                    request.dispatch_external_message_id,
                )

        if not result and not rejected_candidates and len(unstructured_candidates) == 1:
            candidate = unstructured_candidates[0]
            result = [candidate]
            logger.warning(
                "REPLY_UNSTRUCTURED_REPAIR_CANDIDATE issue_id=%s task_id=%s "
                "request_id=%s external_id=%s",
                issue_id,
                request.task_id,
                request.request_id,
                candidate.external_id,
            )

        if not result and rejected_candidates:
            candidate = rejected_candidates[-1]
            result = [candidate]
            logger.warning(
                "REPLY_CONTRACT_REJECTED_REPAIR_CANDIDATE issue_id=%s task_id=%s "
                "request_id=%s external_id=%s reason=%s",
                issue_id,
                request.task_id,
                request.request_id,
                candidate.external_id,
                candidate.payload.get("contract_rejection"),
            )

        if supplemental_reports:
            for message in result:
                message.payload["supplemental_reports"] = list(supplemental_reports)
        logger.info(
            "MULTICA_POLL_RESULT issue_id=%s task_id=%s request_id=%s comments_total=%s "
            "valid_replies=%s supplemental_reports=%s author_mismatch=%s "
            "dispatch_comment=%s stale=%s unstructured=%s request_mismatch=%s "
            "contract_rejected=%s correlation_ids=%s",
            issue_id,
            request.task_id,
            request.request_id,
            comments_total,
            len(result),
            len(supplemental_reports),
            stats["author_mismatch"],
            stats["dispatch_comment"],
            stats["stale"],
            stats["unstructured"],
            stats["request_mismatch"],
            stats["contract_rejected"],
            sorted(recovery_ids),
        )
        logger.info(
            "AGENT_REPLY_CORRELATION_RESULT task_id=%s request_id=%s phase=%s role=%s "
            "dispatch_external_message_id=%s comments_total=%s valid_replies=%s "
            "unstructured=%s request_mismatch=%s uncorrelated=%s stale=%s "
            "author_mismatch=%s contract_rejected=%s correlation_ids=%s",
            request.task_id,
            request.request_id,
            request.phase,
            request.role,
            request.dispatch_external_message_id,
            comments_total,
            len(result),
            stats["unstructured"],
            stats["request_mismatch"],
            stats["uncorrelated"],
            stats["stale"],
            stats["author_mismatch"],
            stats["contract_rejected"],
            sorted(recovery_ids),
        )
        return result

    def _recover_run_output(
        self, request: AgentRequest, rejections: list[ExternalMessage]
    ) -> ExternalMessage | None:
        """Recover a completed response with explicit request and agent binding.

        Run matching alone is insufficient: coalesced and assignment runs can
        belong to several requests. Never backfill task/request IDs here.
        The same contract/materialization path used by inline comments still
        validates the response before it can advance the workflow.
        """
        for run in self._matching_runs(request):
            if (self._normalize_run_status(run.get('status')) != 'completed'
                or str(run.get('agent_id') or '') != request.agent_id):
                continue
            execution_result = run.get('result')
            body = execution_result.get('output') if isinstance(execution_result, dict) else None
            if not isinstance(body, str) or not body.strip():
                continue
            payload = _extract_json(body)
            if payload is None:
                # Some providers wrap the final object in one JSON fence with
                # an explanatory prefix. Extract only an unambiguous single
                # fence; never select among competing objects or infer fields.
                fences = re.findall(r'```(?:json)?\s*\n?(.*?)```', body, re.DOTALL | re.IGNORECASE)
                if len(fences) == 1:
                    payload = _extract_json(fences[0])
            if (not isinstance(payload, dict) or not payload.get('action')
                or payload.get('task_id') != request.task_id
                or payload.get('request_id') != request.request_id):
                continue
            external_id = 'run:' + str(run.get('id') or request.request_id)
            try:
                validated = self._persist_inline_result(request, payload)
            except AgentResultFileError as error:
                rejections.append(ExternalMessage(request.agent_id, {
                    'action': CONTRACT_REJECTED_EVENT,
                    'task_id': request.task_id, 'request_id': request.request_id,
                    'phase': request.phase, 'role': request.role,
                    'response_source': 'run_output', 'contract_rejection': str(error),
                    'raw_reply': body[:16000], 'raw_reply_truncated': len(body) > 16000,
                    'raw_reply_path': self._save_raw_reply(request, external_id, body),
                }, external_id, body))
                logger.warning('AGENT_RUN_OUTPUT_REJECTED task_id=%s request_id=%s run_id=%s reason=%s',
                               request.task_id, request.request_id, run.get('id'), error)
                continue
            logger.info('AGENT_RUN_OUTPUT_RECOVERED task_id=%s request_id=%s run_id=%s',
                        request.task_id, request.request_id, run.get('id'))
            return ExternalMessage(request.agent_id, validated, external_id, body)
        return None

    def _find_correlated_delivery_ids(self, request: AgentRequest) -> set[str]:
        matches = self._matching_runs(request)
        if not matches:
            logger.info(
                "REPLY_CORRELATION_MISS issue_id=%s task_id=%s request_id=%s "
                "trigger_comment_id=%s",
                request.issue_id or request.task_id,
                request.task_id,
                request.request_id,
                request.dispatch_external_message_id,
            )
            return set()
        run = matches[0]
        if self._normalize_run_status(run.get("status")) != "completed":
            return set()
        delivered = run.get("delivered_comment_ids") or []
        return {str(comment_id) for comment_id in delivered if comment_id}

    @staticmethod
    def _normalize_run_status(value: object) -> str:
        status = str(value or "").strip().lower()
        if status in {"succeeded", "success", "done", "finished"}:
            return "completed"
        if status in {"error", "errored", "cancelled", "canceled"}:
            return "failed"
        if status in {"running", "queued", "pending", "created"}:
            return "running"
        if status in {"completed", "failed"}:
            return status
        return "unknown"

    def _matching_runs(self, request: AgentRequest) -> list[dict[str, object]]:
        issue_id = request.issue_id or request.task_id
        trigger_id = str(request.dispatch_external_message_id or "")
        try:
            value = self._run_with_read_retry(
                "issue", "runs", issue_id, "--output", "json"
            )
        except Exception as error:
            logger.warning(
                "REPLY_CORRELATION_LOOKUP_FAILED issue_id=%s task_id=%s "
                "request_id=%s error=%s",
                issue_id,
                request.task_id,
                request.request_id,
                str(error)[:300],
            )
            return []
        runs = value if isinstance(value, list) else (
            value.get("runs", value.get("items", []))
            if isinstance(value, dict)
            else []
        )
        exact_matches = [
            run for run in runs
            if isinstance(run, dict)
            and trigger_id
            and self._run_correlation_ids(run)
            and trigger_id in self._run_correlation_ids(run)
        ]
        if exact_matches:
            exact_matches.sort(key=lambda run: str(run.get("created_at") or ""), reverse=True)
            return exact_matches

        # Assignment-triggered runs carry no trigger comment id.  Recognise
        # them by position: a direct run id that was absent from the snapshot
        # taken just before dispatch, for the target agent, belongs to this
        # dispatch.  Comment runs are matched by trigger id above, so only
        # assignment runs are claimed here (never steal a concurrent worker's
        # comment run).
        watermark = self._watermark_for(request)
        if watermark is not None:
            fresh = [
                run for run in runs
                if isinstance(run, dict)
                and str(run.get("id") or "") not in watermark
                and str(run.get("agent_id") or "") == str(request.agent_id or "")
                and self._is_assignment_run(run)
            ]
            if fresh:
                fresh.sort(key=lambda run: str(run.get("created_at") or ""), reverse=True)
                logger.info(
                    "AGENT_REMOTE_RUN_CORRELATION_WATERMARK task_id=%s request_id=%s "
                    "agent_id=%s new_runs=%s",
                    request.task_id,
                    request.request_id,
                    request.agent_id,
                    len(fresh),
                )
                return fresh

        # Failed runs can be terminal before Multica has populated
        # trigger/coalesced/delivered comment ids.  Without a snapshot (e.g.
        # after a process restart), or when the assignment run is not yet
        # visible, the issue endpoint is still authoritative for issue scope
        # and the run carries the assigned agent.  Accept this fallback only
        # when one candidate is uniquely identifiable by agent and dispatch
        # time; a broad "latest run" fallback would risk stealing another
        # worker's result.
        candidates = [
            run for run in runs
            if isinstance(run, dict)
            and str(run.get("agent_id") or "") == str(request.agent_id or "")
            and self._run_created_after_dispatch(run, request.sent_after)
        ]
        if len(candidates) == 1:
            logger.warning(
                "AGENT_REMOTE_RUN_CORRELATION_FALLBACK task_id=%s request_id=%s "
                "agent_id=%s reason=unique_agent_and_dispatch_time",
                request.task_id,
                request.request_id,
                request.agent_id,
            )
            return candidates
        if candidates:
            logger.warning(
                "AGENT_REMOTE_RUN_CORRELATION_AMBIGUOUS task_id=%s request_id=%s "
                "agent_id=%s candidates=%s",
                request.task_id,
                request.request_id,
                request.agent_id,
                len(candidates),
            )
        return []


    def _watermark_for(self, request: AgentRequest) -> set[str] | None:
        key = str(request.idempotency_key or "")
        if not key:
            return None
        with self._dispatch_index_lock:
            seen = self._run_watermarks.get(key)
        if seen is None:
            return None
        return set(seen)

    @staticmethod
    def _is_assignment_run(run: dict[str, object]) -> bool:
        """True when the run was triggered by an issue assignment change."""

        if str(run.get("kind") or "").strip().lower() == "direct":
            return True
        attribution = run.get("attribution")
        if isinstance(attribution, dict):
            evidence = attribution.get("evidence")
            if isinstance(evidence, dict):
                return str(evidence.get("kind") or "") == "issue_assignment"
        return False

    @staticmethod
    def _run_correlation_ids(run: dict[str, object]) -> set[str]:
        return {
            str(run.get("trigger_comment_id") or ""),
            *(str(item) for item in (run.get("coalesced_comment_ids") or [])),
            *(str(item) for item in (run.get("delivered_comment_ids") or [])),
            str(run.get("request_id") or ""),
        } - {""}

    @staticmethod
    def _run_created_after_dispatch(run: dict[str, object], sent_after: str) -> bool:
        created_at = str(run.get("created_at") or "")
        if not sent_after or not created_at:
            return True
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            dispatched = datetime.fromisoformat(sent_after.replace("Z", "+00:00"))
        except ValueError:
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if dispatched.tzinfo is None:
            dispatched = dispatched.replace(tzinfo=timezone.utc)
        # Multica records run ``created_at`` with second granularity while the
        # orchestrator records ``sent_after`` with sub-second precision.  A run
        # created in the same wall-clock second therefore truncates to a value
        # that can read as *before* dispatch.  Allow a small slack so the same
        # second still correlates.
        return created >= dispatched - timedelta(seconds=2)

    def get_run_status(self, request: AgentRequest) -> str:
        matches = self._matching_runs(request)
        if not matches:
            logger.info(
                "AGENT_REMOTE_RUN_STATUS_UNKNOWN task_id=%s request_id=%s "
                "trigger_comment_id=%s reason=no_matching_run",
                request.task_id,
                request.request_id,
                request.dispatch_external_message_id,
            )
            return "unknown"
        status = self._normalize_run_status(matches[0].get("status"))
        logger.info(
            "AGENT_REMOTE_RUN_STATUS task_id=%s request_id=%s trigger_comment_id=%s status=%s",
            request.task_id,
            request.request_id,
            request.dispatch_external_message_id,
            status,
        )
        if status in {"failed", "error"}:
            run = matches[0]
            self._run_failures[request.request_id] = "; ".join(
                str(run.get(key) or "") for key in ("error", "failure_reason")
                if run.get(key)
            ) or "remote run reported failure"
            logger.warning(
                "AGENT_REMOTE_RUN_FAILED_DETAIL task_id=%s request_id=%s "
                "issue_id=%s run_id=%s kind=%s duration_ms=%s "
                "error=%r failure_reason=%r",
                request.task_id,
                request.request_id,
                request.issue_id,
                str(run.get("id") or ""),
                str(run.get("kind") or ""),
                run.get("duration_ms"),
                str(run.get("error") or ""),
                str(run.get("failure_reason") or ""),
            )
        return status

    def get_run_failure(self, request: AgentRequest) -> str:
        return self._run_failures.get(request.request_id, "")

    def cancel_stale_runs(self, request: AgentRequest) -> list[str]:
        """Best-effort cancel non-terminal remote runs correlated to a request.

        When the orchestrator gives up on a run (AGENT_TIMEOUT), the remote
        agent keeps consuming tokens and, because runs sharing one
        (issue, agent) target are merged by Multica, the re-dispatched run is
        queued behind the stale one.  Cancelling the correlated non-terminal
        runs before the FSM retries prevents orphaned twin runs.
        """

        issue_id = request.issue_id or request.task_id
        try:
            matches = self._matching_runs(request)
        except Exception as error:
            logger.warning(
                "AGENT_REMOTE_RUN_CANCEL_LOOKUP_FAILED task_id=%s request_id=%s "
                "issue_id=%s error=%s",
                request.task_id,
                request.request_id,
                issue_id,
                str(error)[:300],
            )
            return []
        cancelled: list[str] = []
        for run in matches:
            run_id = str(run.get("id") or "")
            status = self._normalize_run_status(run.get("status"))
            if not run_id or status in {"completed", "failed"}:
                continue
            try:
                self._run(
                    "issue", "cancel-task", run_id, "--issue", issue_id,
                    "--output", "json",
                )
            except Exception as error:
                logger.warning(
                    "AGENT_REMOTE_RUN_CANCEL_FAILED task_id=%s request_id=%s "
                    "run_id=%s status=%s error=%s",
                    request.task_id,
                    request.request_id,
                    run_id,
                    status,
                    str(error)[:300],
                )
                continue
            cancelled.append(run_id)
            logger.warning(
                "AGENT_REMOTE_RUN_CANCELLED task_id=%s request_id=%s "
                "issue_id=%s run_id=%s previous_status=%s",
                request.task_id,
                request.request_id,
                issue_id,
                run_id,
                status,
            )
        return cancelled

    def find_existing_request(self, idempotency_key: str, issue_id: str = "") -> DispatchReceipt | None:
        with self._dispatch_index_lock:
            key = str(idempotency_key or "")
            cached = self._dispatch_index.get(key)
            if cached is not None:
                return DispatchReceipt(
                    operation_id=cached.get("operation_id") or key,
                    external_message_id=cached.get("external_message_id") or "",
                    request_id=cached.get("request_id") or "",
                    issue_id=cached.get("issue_id") or "",
                )
            try:
                issue_id = issue_id or self.last_issue_id
                if not issue_id:
                    return None
                scope = f"{issue_id}:dispatch"
                cursor_snapshot = self.comment_feed.snapshot(scope)
                comments = self.comment_feed.read(
                    scope,
                    lambda since: self._run_with_read_retry(
                        "issue", "comment", "list", issue_id,
                        *(('--since', since) if since else ('--recent', '20')),
                        "--output", "json",
                    ),
                    # The dispatch index is the dependent durable side
                    # effect.  Commit the cursor only after that index is
                    # durably updated, closing the crash window between the
                    # two files.
                    persist=False,
                )
                self._index_dispatch_comments(comments)
                self.comment_feed.commit()
            except Exception as error:
                if "cursor_snapshot" in locals():
                    self.comment_feed.restore(scope, cursor_snapshot)
                logger.warning(
                    "REQUEST_LOOKUP_FAILED issue_id=%s idempotency_key=%s "
                    "error_type=%s error=%s",
                    issue_id,
                    idempotency_key,
                    type(error).__name__,
                    str(error)[:300],
                )
                raise RequestLookupError(
                    f"unable to query existing request for {idempotency_key}"
                ) from error
            cached = self._dispatch_index.get(key)
            if cached is None:
                return None
            return DispatchReceipt(
                operation_id=cached.get("operation_id") or key,
                external_message_id=cached.get("external_message_id") or "",
                request_id=cached.get("request_id") or "",
            )

    def add_comment(self, issue_id: str, text: str) -> str:
        path = self.log_dir / f"fallback_{int(time.time() * 1000)}.md"
        path.write_text(text, encoding="utf-8")
        value = self._run_with_local_file(
            ["issue", "comment", "add", issue_id, "--content-file", str(path), "--output", "json"],
            "--content-file",
            path,
        )
        if isinstance(value, dict):
            return str(value.get("id") or value.get("comment_id") or value.get("comment", {}).get("id") or "")
        return ""

    def create_issue(
        self,
        title: str,
        description: str,
        project_id: str = "",
        allow_duplicate: bool = False,
        assignee_id: str = "",
    ) -> str:
        path = self.log_dir / f"new_issue_{int(time.time() * 1000)}.md"
        path.write_text(description, encoding="utf-8")
        args = ["issue", "create", "--title", title, "--description-file", str(path), "--output", "json"]
        if project_id:
            args += ["--project", project_id]
        if allow_duplicate:
            args.append("--allow-duplicate")
        if assignee_id:
            args += ["--assignee-id", assignee_id]
        value = self._run_with_local_file(args, "--description-file", path)
        issue_id = value.get("id") if isinstance(value, dict) else None
        if not issue_id and isinstance(value, dict):
            issue_id = value.get("issue", {}).get("id")
        if not issue_id:
            raise RuntimeError("multica issue create returned no id")
        return str(issue_id)

    def get_issue(self, issue_id: str) -> dict:
        value = self._run_with_read_retry("issue", "get", issue_id, "--output", "json")
        return value if isinstance(value, dict) else {}


def _response_contract_for(request):
    from .contracts import contract_for_state
    selected = contract_for_state(request.target_state)
    return {'required':list(selected.required_fields), 'optional':[],
            'allowed_actions':list(selected.actions), 'instruction':'\n'.join(selected.prompt_rules),
            'required_by_action':{a:list(selected.required_fields) for a in selected.actions}}


def _external_notifications_disabled_for_tests() -> bool:
    """Hard stop for test runs before any Feishu HTTP request is attempted."""

    return os.environ.get("NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def feishu_notifications_enabled() -> bool:
    """Enable Feishu in production by default; tests may explicitly disable it."""

    if _external_notifications_disabled_for_tests():
        return False
    return os.environ.get("ENABLE_FEISHU_NOTIFICATIONS", "").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


class FeishuHttpAdapter:
    """Production adapter for role-specific bot messages and human gate replies."""

    def __init__(self) -> None:
        self.base_url = os.environ.get("FEISHU_BASE_URL", "https://open.feishu.cn").rstrip("/")
        self.chat_id = os.environ.get("HUMAN_GATE_CHAT_ID", "")
        self._tokens: dict[str, tuple[str, float]] = {}

    def _credentials(self, role: str) -> tuple[str, str]:
        prefix = f"FEISHU_{role.upper()}_APP"
        return (
            os.environ.get(prefix + "_ID") or os.environ.get("FEISHU_GATE_APP_ID", ""),
            os.environ.get(prefix + "_SECRET") or os.environ.get("FEISHU_GATE_APP_SECRET", ""),
        )

    def _request(self, method: str, url: str, payload: dict | None = None, token: str = "") -> dict:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"feishu http {error.code}: {error.read().decode(errors='replace')[:500]}") from error

    def _token(self, role: str) -> str:
        cached = self._tokens.get(role)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        app_id, secret = self._credentials(role)
        if not app_id or not secret:
            raise RuntimeError(f"feishu credentials missing for role={role}")
        value = self._request(
            "POST",
            self.base_url + "/open-apis/auth/v3/tenant_access_token/internal",
            {"app_id": app_id, "app_secret": secret},
        )
        if value.get("code") not in (0, "0"):
            raise RuntimeError(f"feishu token failed: {value.get('msg')}")
        token = str(value.get("tenant_access_token") or "")
        self._tokens[role] = (token, time.time() + int(value.get("expire", 7200)))
        return token

    def send_gate(self, gate: HumanGate) -> DeliveryReceipt:
        return self.send_text(gate.prompt, "gate")

    def send_text(self, text: str, role: str = "gate") -> DeliveryReceipt:
        if not feishu_notifications_enabled():
            raise RuntimeError("FEISHU_EXTERNAL_DISABLED_FOR_TESTS")
        logger.info(
            "FEISHU_HTTP_SEND_START role=%s text_chars=%s chat_configured=%s",
            role,
            len(text),
            bool(self.chat_id),
        )
        if not self.chat_id:
            logger.warning(
                "FEISHU_HTTP_SEND_FAILED role=%s reason=missing_chat_id",
                role,
            )
            return DeliveryReceipt("issue_fallback", delivered=False)
        try:
            value = self._request(
                "POST",
                self.base_url + "/open-apis/im/v1/messages?" + urllib.parse.urlencode({"receive_id_type": "chat_id"}),
                {
                    "receive_id": self.chat_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                },
                self._token(role),
            )
        except Exception as error:
            logger.exception(
                "FEISHU_HTTP_SEND_FAILED role=%s error_type=%s",
                role,
                type(error).__name__,
            )
            raise
        if value.get("code") not in (0, "0"):
            logger.warning(
                "FEISHU_HTTP_SEND_FAILED role=%s reason=api_error code=%s",
                role,
                value.get("code"),
            )
            return DeliveryReceipt("feishu", delivered=False)
        message_id = str(value.get("data", {}).get("message_id") or "")
        logger.info(
            "FEISHU_HTTP_SEND_SUCCESS role=%s message_id=%s delivered=%s",
            role,
            message_id,
            bool(message_id),
        )
        return DeliveryReceipt("feishu", message_id, bool(message_id))

    def notify(self, text: str, role: str = "gate") -> str:
        receipt = self.send_text(text, role)
        return receipt.message_id

    def poll_reply(self, gate: HumanGate) -> list[HumanReply]:
        if not feishu_notifications_enabled():
            raise RuntimeError("FEISHU_EXTERNAL_DISABLED_FOR_TESTS")
        if not self.chat_id:
            return []
        items: list[dict] = []
        seen_messages: set[str] = set()
        seen_tokens: set[str] = set()
        page_token = ""
        token = self._token("gate")
        # Replies can be buried by agent traffic. Scan pages back to the gate,
        # retaining newer replies before recovering a missing gate message id.
        for _ in range(100):
            query = {"container_id_type": "chat", "container_id": self.chat_id,
                     "sort_type": "ByCreateTimeDesc", "page_size": 50}
            if page_token:
                query["page_token"] = page_token
            value = self._request(
                "GET", self.base_url + "/open-apis/im/v1/messages?"
                + urllib.parse.urlencode(query), token=token,
            )
            if value.get("code") not in (None, 0, "0"):
                raise RuntimeError(f"FEISHU_GATE_POLL_API_ERROR:{value.get('code')}")
            data = value.get("data", {})
            reached_gate = False
            for item in data.get("items", []):
                message_id = str(item.get("message_id") or item.get("id") or "")
                if message_id and message_id in seen_messages:
                    continue
                seen_messages.add(message_id)
                items.append(item)
                sender_type = str((item.get("sender") or {}).get("sender_type") or "")
                body = _feishu_message_text(item)
                is_gate_part = bool(gate.decision_id and gate.decision_id in body)
                # Read through the first part to recover all document roots.
                first_or_unsplit = "分段：" not in body or "分段：1/" in body
                if first_or_unsplit and (
                    (gate.message_id and message_id == gate.message_id)
                    or (is_gate_part and sender_type in {"app", "bot"})
                ):
                    reached_gate = True
            next_token = str(data.get("page_token") or "")
            if reached_gate or not data.get("has_more"):
                break
            if not next_token or next_token in seen_tokens:
                raise RuntimeError("FEISHU_GATE_POLL_CURSOR_STALLED")
            seen_tokens.add(next_token)
            page_token = next_token
        else:
            logger.warning("FEISHU_GATE_POLL_PAGE_LIMIT decision_id=%s", gate.decision_id)
        gate_message_ids = {str(gate.message_id)} if gate.message_id else set()
        if gate.decision_id:
            # Every document part is a valid thread root after a restart.
            for item in items:
                body = _feishu_message_text(item)
                sender_type = str((item.get("sender") or {}).get("sender_type") or "")
                if (gate.decision_id in body
                        and (sender_type in {"app", "bot"}
                             or (not sender_type and not body.strip().startswith(gate.decision_id)))):
                    gate_message_ids.add(str(
                        item.get("message_id") or item.get("id") or ""
                    ))
        gate_message_ids.discard("")
        replies: list[HumanReply] = []
        for item in items:
            message_id = str(item.get("message_id") or item.get("id") or "")
            if message_id in gate_message_ids:
                continue
            if str((item.get("sender") or {}).get("sender_type") or "") in {"app", "bot"}:
                continue
            body = _feishu_message_text(item)
            parent_id = str(item.get("parent_id") or "")
            root_id = str(item.get("root_id") or "")
            is_direct_reply = bool(
                gate_message_ids.intersection({parent_id, root_id})
            )
            contains_decision_id = bool(gate.decision_id and gate.decision_id in body)
            if not is_direct_reply and not contains_decision_id:
                continue
            answer = body
            if contains_decision_id:
                # Remove routing decoration only before the answer. Trailing
                # punctuation is user content: "通过，" is revision feedback.
                answer = answer.replace(gate.decision_id, "", 1).lstrip(" ：:，,\n\r\t")
            answer = answer.strip()
            answer = re.sub(r"^(?:@\S+\s+)+", "", answer).strip()
            if not answer:
                continue
            sender = item.get("sender")
            sender_id = ""
            if isinstance(sender, dict):
                sender_id = str(
                    sender.get("id")
                    or sender.get("sender_id")
                    or sender.get("open_id")
                    or ""
                )
            sender_id = sender_id or str(item.get("sender_id") or "")
            replies.append(HumanReply(gate.decision_id, answer, sender_id, message_id))
        logger.info(
            "FEISHU_GATE_POLL_RESULT decision_id=%s gate_message_id=%s messages_total=%s replies=%s",
            gate.decision_id,
            ",".join(sorted(gate_message_ids)),
            len(items),
            len(replies),
        )
        return replies


def _feishu_message_text(item: dict) -> str:
    body = item.get("body")
    content = item.get("content")
    candidates = [body, content]
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate = candidate.get("content") or candidate.get("text") or candidate
        if not isinstance(candidate, str):
            continue
        text = candidate.strip()
        if not text:
            continue
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(decoded, dict):
            return str(decoded.get("text") or decoded.get("content") or text)
        return str(decoded)
    return ""


def _extract_prose_prefixed_json(body: str) -> object | None:
    """Recover one JSON object that the model prefixed with delivery prose.

    The reply-shape feedback demands a pure JSON object, yet retries under
    pressure often open with a short note ("Two prior deliveries were
    discarded...") before the object itself.  Accept only a brace-free prose
    prefix followed by one balanced object that ends the reply, so neither
    concatenated documents nor trailing content can be silently dropped.
    """
    text = str(body).lstrip("\ufeff\u200b").strip()
    start = text.find("{")
    if start <= 0:
        return None
    prefix = text[:start]
    if "{" in prefix or "}" in prefix or "```" in prefix:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                if text[index + 1:].strip():
                    return None
                candidate = text[start:index + 1]
                value, status, _, _ = _parse_json_candidate(candidate)
                if status in {"valid", "repaired"}:
                    logger.warning(
                        "AGENT_REPLY_JSON_REPAIRED repair=PROSE_PREFIX sha256=%s",
                        _fingerprint_text(body)["sha256"],
                    )
                    return value
                return None
    return None


def _extract_json(body: str) -> object:
    # Agent comments may start with a UTF-8 BOM, especially when produced by
    # Windows/PowerShell tooling. Strip it before JSON decoding.
    body = body.lstrip("\ufeff\u200b").strip()
    candidates = [body]
    if "```" in body:
        candidates.append(body.replace("```json", "").replace("```", "").strip())
    for candidate in candidates:
        value, status, repair, _ = _parse_json_candidate(candidate)
        if status in {"valid", "repaired"}:
            if status == "repaired":
                logger.warning(
                    "AGENT_REPLY_JSON_REPAIRED repair=%s sha256=%s",
                    repair or "UNKNOWN",
                    _fingerprint_text(body)["sha256"],
                )
            return value
    repaired = _repair_diff_prefixed_json(body)
    if repaired is not None:
        return repaired
    recovered = _extract_prose_prefixed_json(body)
    if recovered is not None:
        return recovered
    return None


def _extract_issue_id(value: object) -> str:
    """Pull an issue id out of a ``multica issue create`` JSON response."""

    if not isinstance(value, dict):
        return ""
    for key in ("id", "issue_id"):
        candidate = str(value.get(key) or "")
        if candidate:
            return candidate
    nested = value.get("issue")
    if isinstance(nested, dict):
        return str(nested.get("id") or nested.get("issue_id") or "")
    return ""


def _iter_child_issues(value: object) -> list[dict]:
    """Collect issue dicts from the ``issue children`` stage/unstaged payload."""

    found: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if (node.get("id") or node.get("issue_id")) and node.get("title"):
                found.append(node)
                return
            for item in node.values():
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return found


def _repair_diff_prefixed_json(body: str) -> object | None:
    """Repair only an unmistakable line-prefixed diff wrapper around JSON."""
    lines = body.lstrip("\ufeff\u200b").strip().splitlines()
    plus_lines = [line for line in lines if line.startswith("+")]
    if len(plus_lines) < 2:
        return None
    if any(line.startswith(("---", "+++")) for line in lines):
        return None
    repaired = "\n".join(
        line[1:] if line.startswith("+") else line
        for line in lines
    ).strip()
    try:
        payload = json.loads(repaired)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    logger.warning(
        "AGENT_REPLY_DIFF_PREFIX_REPAIRED chars=%s plus_lines=%s",
        len(body),
        len(plus_lines),
    )
    return payload
