from __future__ import annotations

from datetime import datetime, timezone
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

from .prompt_bundle import PromptBundleBuilder
from .agent_result_file import (
    AgentResultFileError,
    read_agent_result_file,
    write_agent_result_file,
)
from .comment_feed import IncrementalCommentFeed, comment_order_key, timestamp_order_key
from .structured_output import (
    role_mode_for,
    state_actions,
    stable_role_fields,
)
from .zhongshu_solver_contract import (
    ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND,
    ZHONGSHU_SOLVER_REQUIRED_ITEM_FIELDS,
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


def _path_identity(path: str | Path) -> str:
    """Normalize a filesystem path for request-boundary comparisons."""
    value = str(path or "").strip()
    if not value:
        return ""
    try:
        value = os.path.abspath(os.path.expanduser(value))
    except (OSError, ValueError):
        pass
    return os.path.normcase(os.path.normpath(value))


def _default_multica_workspaces_root() -> Path:
    """Resolve a safe default without requiring a complete user environment."""
    home = (
        os.environ.get("USERPROFILE")
        or os.environ.get("HOME")
        or (
            f"{os.environ.get('HOMEDRIVE', '')}"
            f"{os.environ.get('HOMEPATH', '')}"
        )
    )
    return Path(home or Path.cwd()) / "multica_workspaces"


def _json_parse_diagnostic(text: str) -> dict[str, object]:
    _, status, error_message, error_position = _parse_json_candidate(text)
    return {
        "json_status": status,
        "json_error": error_message,
        "json_error_position": error_position,
    }


def _parse_json_candidate(
    text: str,
) -> tuple[object | None, str, str, int | None]:
    """Parse one Agent JSON document with narrowly scoped framing recovery.

    The external comment transport is text, so the Agent can occasionally emit
    a valid root object followed by one accidental closing brace. Recover only
    that exact shape. In particular, never accept the first document from
    concatenated JSON or discard arbitrary trailing content because doing so
    could silently lose a decision or finding.
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
        return None, "invalid", strict_error.msg, strict_error.pos

    trailing = candidate[end:].strip()
    if isinstance(value, dict) and trailing == "}":
        return value, "repaired", "", end
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
        self.prompt_transport_mode = os.environ.get("PROMPT_TRANSPORT_MODE", "prompt_file").lower()
        self.prompt_bundle_builder = PromptBundleBuilder(self.log_dir / "prompt-bundles")
        self.agent_result_allowed_root = Path(os.environ.get("AGENT_RESULT_ALLOWED_ROOT", str(self.log_dir.parent)))
        self.multica_workspaces_root = Path(
            os.environ.get(
                "MULTICA_WORKSPACES_ROOT",
                str(_default_multica_workspaces_root()),
            )
        ).expanduser().resolve()
        self.orchestrator_result_write_enabled = (
            os.environ.get("ORCHESTRATOR_RESULT_WRITE_ENABLED", "true").lower()
            not in {"0", "false", "no", "off"}
        )
        self.inline_result_max_bytes = max(
            1,
            int(os.environ.get("INLINE_RESULT_MAX_BYTES", str(64 * 1024))),
        )
        logger.info(
            "PROMPT_TRANSPORT_CONFIG mode=%s bundle_root=%s result_allowed_root=%s "
            "multica_workspaces_root=%s "
            "prompt_encoding=utf-8 prompt_bom=false result_encoding=utf-8 "
            "result_bom=writer_expected_false_reader_tolerant "
            "orchestrator_result_write_enabled=%s inline_result_max_bytes=%s",
            self.prompt_transport_mode,
            self.log_dir / "prompt-bundles",
            self.agent_result_allowed_root,
            self.multica_workspaces_root,
            self.orchestrator_result_write_enabled,
            self.inline_result_max_bytes,
        )
        self.last_issue_id = ""
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

    def _save_dispatch_index(self) -> None:
        value = {"version": 1, "requests": self._dispatch_index}
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
            and args[:2] == ("issue", "runs")
        )

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

    def _run(self, *args: str) -> object:
        command = ["multica", *args]
        started = time.monotonic()
        command_text = subprocess.list2cmdline(command)
        timeout_seconds = (
            self.cli_read_timeout_seconds
            if self._is_read_command(args)
            else self.cli_timeout_seconds
        )
        logger.info(
            "MULTICA_CLI_START command=%s timeout_seconds=%s operation=%s",
            command_text,
            timeout_seconds,
            "read" if self._is_read_command(args) else "write",
        )
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout_seconds,
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
        structured_result_required = bool(
            isinstance(request.structured_output, dict)
            and request.structured_output.get("mode") == "result_file"
            and request.structured_output.get("schema_hash")
        )
        use_legacy_transport = (
            self.prompt_transport_mode == "legacy"
            and not structured_result_required
        )
        if self.prompt_transport_mode == "legacy" and structured_result_required:
            logger.warning(
                "PROMPT_TRANSPORT_LEGACY_BYPASSED task_id=%s request_id=%s "
                "phase=%s role=%s reason=structured_result_requires_prompt_bundle",
                request.task_id,
                request.request_id,
                request.phase,
                request.role,
            )
        if use_legacy_transport:
            prompt_payload = {"prompt": request.prompt}
            if request.phase in {"ZHONGSHU", "MENXIA"} and isinstance(skill_lock, dict):
                skill_bytes = Path(str(skill_lock["source"])).read_bytes()
                if hashlib.sha256(skill_bytes).hexdigest() != skill_lock["sha256"]:
                    raise RuntimeError("ACTIVE_RUNTIME_SKILL_HASH_MISMATCH")
                prompt_payload["active_runtime_skill_content"] = skill_bytes.decode("utf-8")
            logger.warning(
                "PROMPT_TRANSPORT_LEGACY task_id=%s request_id=%s phase=%s role=%s",
                request.task_id,
                request.request_id,
                request.phase,
                request.role,
            )
        else:
            bundle = self.prompt_bundle_builder.build(
                task_id=request.task_id,
                request_id=request.request_id,
                phase=request.phase,
                role=request.role,
                prompt=request.prompt,
                revision_id=str(request.context.get("revision_id", "")),
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
            result_path = str(prompt_ref.get("result_path") or "")
            schema_hash = str((request.structured_output or {}).get("schema_hash") or "")
            if schema_hash:
                structured_output_instruction = (
                    "Do not modify source or project files. The Orchestrator-owned canonical result "
                    f"is {result_path}; do not write that path. Write exactly one complete "
                    "business result to relative result.json in "
                    "the current Agent workspace as UTF-8 JSON without BOM, using a real JSON "
                    "serializer so quotes and control characters are escaped by the serializer. "
                    f"The JSON root must include structured_output_protocol={request.structured_output.get('protocol')}. "
                    f"The JSON root must include structured_output_schema_hash={schema_hash}. "
                    "The root must also include the exact contract_id, task_id, request_id, phase, state, role, and mode from the prompt bundle. "
                    "You may create or replace only that local result file. For result_file mode, "
                    "do not return the business JSON or a transport envelope inline. After the "
                    "file is durably written, return exactly one compact "
                    "nexus-agent-result-ref-v1 pointer containing the exact task_id, request_id, "
                    "and actual local result.json path; do not return ./result.json or Markdown. "
                    "Keep the file until the response has been emitted, and do not return a "
                    "second business result."
                )
            else:
                structured_output_instruction = (
                    "Do not modify project files. Return exactly one complete structured JSON "
                    "result in the reply; the Orchestrator will validate and persist it. "
                    "Do not return Markdown, a result pointer, a diff/patch, or a partial result."
                )
            prompt_payload = {
                "prompt": (
                    f"Read every required file in the prompt bundle manifest as UTF-8, including active-skill.md when present, then follow prompt.txt. "
                    f"Active runtime Skill: {request.context.get('active_runtime_skill', '')}. "
                    f"Skill lock: {json.dumps(request.context.get('active_runtime_skill_lock', {}), ensure_ascii=False, sort_keys=True)}. "
                    f"Runtime directive: {request.context.get('active_runtime_skill_directive', '')} "
                    f"Active phase/state: {request.context.get('active_runtime_phase', request.phase)}/{request.context.get('active_runtime_state', request.role)}. "
                    "Only the active runtime Skill is authoritative; other attached Skills are inactive for this turn. "
                    f"{structured_output_instruction}"
                ),
                "prompt_ref": prompt_ref,
                "structured_output": request.structured_output,
            }
        # Validate/build all local artifacts before assignment can trigger an Agent.
        self._run("issue", "update", issue_id, "--assignee-id", request.agent_id, "--output", "json")
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
            "manifest_hash=%s result_path=%s structured_output_mode=%s "
            "structured_output_schema_hash=%s",
            request.task_id,
            request.request_id,
            request.phase,
            request.role,
            "legacy" if use_legacy_transport else "prompt_file",
            len(content.encode("utf-8")),
            len(request.prompt.encode("utf-8")),
            prompt_payload.get("prompt_ref", {}).get("manifest_path", ""),
            prompt_payload.get("prompt_ref", {}).get("manifest_hash", ""),
            prompt_payload.get("prompt_ref", {}).get("result_path", ""),
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
        path = self.log_dir / f"dispatch_{request.request_id.replace(':', '_')}.json"
        path.write_text(content, encoding="utf-8")
        value = self._run(
            "issue", "comment", "add", issue_id,
            "--content-file", str(path),
            "--output", "json",
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
                }
                self._save_dispatch_index()
        return DispatchReceipt(
            operation_id=request.idempotency_key,
            external_message_id=external_id,
            request_id=request.request_id,
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
                    *(('--since', since) if since else ()),
                    "--output", "json",
                ),
                initial_since=request.sent_after or datetime.now(timezone.utc).isoformat(),
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

    def _recover_result_file(self, request: AgentRequest) -> ExternalMessage | None:
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
        return ExternalMessage(
            request.agent_id,
            file_result.payload,
            f"file:{request.request_id}",
            "",
        )

    def _persist_inline_result(
        self,
        request: AgentRequest,
        payload: dict,
    ) -> dict:
        if not self.orchestrator_result_write_enabled:
            return payload
        inline_payload = payload
        if payload.get("protocol") == "nexus-agent-result-inline-v1":
            nested = payload.get("result")
            if not isinstance(nested, dict):
                raise AgentResultFileError("inline result.result must be an object")
            inline_payload = nested
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
        )
        return file_result.payload

    def _is_remote_result_path(self, raw_path: str | Path) -> bool:
        """Return whether a pointer names a constrained Multica task result."""
        try:
            path = Path(raw_path).expanduser().resolve()
            path.relative_to(self.multica_workspaces_root)
        except (OSError, ValueError):
            return False
        return path.name.lower() == "result.json" and path.parent.name.lower() == "workdir"

    def _discover_remote_result_candidates(
        self,
        request: AgentRequest,
    ) -> list[Path]:
        """Find validly-shaped remote result files bound to this request."""
        candidates: list[Path] = []
        try:
            paths = self.multica_workspaces_root.rglob("result.json")
            for raw_path in paths:
                try:
                    path = raw_path.resolve()
                    path.relative_to(self.multica_workspaces_root)
                except (OSError, ValueError):
                    continue
                if (
                    path.name.lower() != "result.json"
                    or path.parent.name.lower() != "workdir"
                    or not path.is_file()
                ):
                    continue
                try:
                    text = path.read_bytes().decode("utf-8-sig", errors="strict")
                    payload = json.loads(text)
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                    logger.warning(
                        "REMOTE_RESULT_FILE_DISCOVERY_INVALID task_id=%s "
                        "request_id=%s path=%s reason=%s",
                        request.task_id,
                        request.request_id,
                        path,
                        str(error),
                    )
                    continue
                if not isinstance(payload, dict):
                    continue
                if (
                    str(payload.get("task_id") or "") == request.task_id
                    and str(payload.get("request_id") or "") == request.request_id
                ):
                    candidates.append(path)
        except OSError as error:
            logger.warning(
                "REMOTE_RESULT_FILE_DISCOVERY_FAILED task_id=%s request_id=%s "
                "root=%s reason=%s",
                request.task_id,
                request.request_id,
                self.multica_workspaces_root,
                str(error),
            )
        logger.info(
            "REMOTE_RESULT_FILE_DISCOVERY_SCAN task_id=%s request_id=%s "
            "root=%s candidates=%s",
            request.task_id,
            request.request_id,
            self.multica_workspaces_root,
            len(candidates),
        )
        return candidates

    def _bridge_remote_result_file(
        self,
        request: AgentRequest,
        reference: dict[str, Any],
    ) -> dict[str, Any]:
        """Read a same-host Agent result and persist it under Orchestrator ownership."""
        file_result = read_agent_result_file(
            reference,
            task_id=request.task_id,
            request_id=request.request_id,
            phase=request.phase,
            role=request.role,
            allowed_root=self.multica_workspaces_root,
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
            allow_transport_backfill=True,
        )
        payload = self._persist_inline_result(request, file_result.payload)
        payload["result_source"] = "orchestrator_bridge"
        payload["remote_result_path"] = str(file_result.path)
        logger.info(
            "REMOTE_RESULT_FILE_BRIDGED task_id=%s request_id=%s phase=%s role=%s "
            "remote_path=%s canonical_path=%s sha256=%s bytes=%s",
            request.task_id,
            request.request_id,
            request.phase,
            request.role,
            file_result.path,
            payload.get("result_path", ""),
            file_result.sha256,
            file_result.bytes,
        )
        return payload

    def _recover_remote_result_file(
        self,
        request: AgentRequest,
    ) -> ExternalMessage | None:
        """Recover one terminal remote result when no pointer was emitted."""
        candidates = self._discover_remote_result_candidates(request)
        if not candidates:
            logger.info(
                "REMOTE_RESULT_FILE_DISCOVERY_MISSING task_id=%s request_id=%s",
                request.task_id,
                request.request_id,
            )
            return None
        if len(candidates) != 1:
            logger.warning(
                "REMOTE_RESULT_FILE_DISCOVERY_AMBIGUOUS task_id=%s request_id=%s "
                "candidates=%s",
                request.task_id,
                request.request_id,
                ",".join(str(path) for path in candidates),
            )
            return None
        remote_path = candidates[0]
        try:
            payload = self._bridge_remote_result_file(
                request,
                {"result_path": str(remote_path)},
            )
        except (AgentResultFileError, OSError) as error:
            logger.warning(
                "REMOTE_RESULT_FILE_DISCOVERY_REJECTED task_id=%s "
                "request_id=%s path=%s reason=%s",
                request.task_id,
                request.request_id,
                remote_path,
                str(error),
            )
            return None
        logger.info(
            "REMOTE_RESULT_FILE_DISCOVERED task_id=%s request_id=%s "
            "remote_path=%s canonical_path=%s",
            request.task_id,
            request.request_id,
            remote_path,
            payload.get("result_path", ""),
        )
        return ExternalMessage(
            request.agent_id,
            payload,
            f"remote-file:{request.request_id}",
            "",
        )

    def poll(self, request: AgentRequest) -> list[ExternalMessage]:
        self.last_issue_id = request.issue_id or request.task_id
        issue_id = request.issue_id or request.task_id
        structured_result_required = (
            isinstance(request.structured_output, dict)
            and request.structured_output.get("mode") == "result_file"
        )
        remote_run_status = ""

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
            structured_result_required = (
                isinstance(request.structured_output, dict)
                and request.structured_output.get("mode") == "result_file"
            )
            result: list[ExternalMessage] = []
            supplemental_reports: list[str] = []
            unstructured_candidates: list[ExternalMessage] = []
            stats = {
                "author_mismatch": 0,
                "dispatch_comment": 0,
                "stale": 0,
                "unstructured": 0,
                "request_mismatch": 0,
                "uncorrelated": 0,
            }
            ordered_comments = sorted(comments, key=comment_order_key)
            for comment in ordered_comments:
                comment_id = str(comment.get("id") or "")
                if allowed_ids is not None and comment_id not in allowed_ids:
                    stats["uncorrelated"] += 1
                    continue
                body = str(comment.get("content") or comment.get("body") or "")
                payload = _extract_json(body)
                result_was_file = False
                inline_result_pending = False
                if payload is None:
                    payload = _extract_result_pointer(body)
                    if isinstance(payload, dict):
                        logger.info(
                            "AGENT_REPLY_POINTER_PARSED task_id=%s request_id=%s "
                            "phase=%s role=%s comment_id=%s result_path=%s",
                            request.task_id,
                            request.request_id,
                            request.phase,
                            request.role,
                            comment_id,
                            payload.get("result_path", ""),
                        )
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
                if (
                    isinstance(payload, dict)
                    and payload.get("protocol") == "nexus-agent-result-ref-v1"
                ):
                    pointer_task_id = str(payload.get("task_id") or "")
                    pointer_request_id = str(payload.get("request_id") or "")
                    if (
                        pointer_task_id != request.task_id
                        or pointer_request_id != request.request_id
                    ):
                        stats["request_mismatch"] += 1
                        logger.warning(
                            "STALE_RESULT_POINTER issue_id=%s task_id=%s "
                            "request_id=%s comment_id=%s pointer_task_id=%s "
                            "pointer_request_id=%s result_path=%s reason=pointer_binding_mismatch",
                            issue_id,
                            request.task_id,
                            request.request_id,
                            comment_id,
                            pointer_task_id,
                            pointer_request_id,
                            payload.get("result_path", ""),
                        )
                        continue
                    expected_result_path = self.prompt_bundle_builder.result_path(
                        request.task_id,
                        request.request_id,
                    )
                    actual_result_path = str(payload.get("result_path") or "").strip()
                    canonical_result_path = (
                        bool(actual_result_path)
                        and _path_identity(actual_result_path)
                        == _path_identity(expected_result_path)
                    )
                    remote_result_path = (
                        bool(actual_result_path)
                        and self._is_remote_result_path(actual_result_path)
                    )
                    if not canonical_result_path and not remote_result_path:
                        stats["request_mismatch"] += 1
                        logger.warning(
                            "STALE_RESULT_POINTER issue_id=%s task_id=%s "
                            "request_id=%s comment_id=%s pointer_task_id=%s "
                            "pointer_request_id=%s expected_result_path=%s "
                            "actual_result_path=%s reason=result_path_binding_mismatch",
                            issue_id,
                            request.task_id,
                            request.request_id,
                            comment_id,
                            pointer_task_id,
                            pointer_request_id,
                            expected_result_path,
                            actual_result_path,
                        )
                        continue
                    try:
                        if remote_result_path:
                            payload = self._bridge_remote_result_file(request, payload)
                        else:
                            file_result = read_agent_result_file(
                                payload,
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
                                or str(
                                    (request.structured_output or {}).get("state") or ""
                                ),
                                expected_role_mode=str(
                                    request.context.get("structured_output_role_mode")
                                    or (request.structured_output or {}).get("role_mode")
                                    or ""
                                ),
                                allow_transport_backfill=True,
                            )
                            payload = file_result.payload
                        result_was_file = True
                    except AgentResultFileError as error:
                        stats["unstructured"] += 1
                        if remote_result_path:
                            remote_error_event = (
                                "REMOTE_RESULT_FILE_MISSING"
                                if str(error).startswith("result file is missing:")
                                else "REMOTE_RESULT_FILE_REJECTED"
                            )
                            logger.warning(
                                "%s task_id=%s request_id=%s "
                                "phase=%s role=%s comment_id=%s path=%s reason=%s",
                                remote_error_event,
                                request.task_id,
                                request.request_id,
                                request.phase,
                                request.role,
                                comment_id,
                                actual_result_path,
                                str(error),
                            )
                        logger.warning(
                            "AGENT_REPLY_FILE_REJECTED task_id=%s request_id=%s "
                            "phase=%s role=%s comment_id=%s reason=%s",
                            request.task_id,
                            request.request_id,
                            request.phase,
                            request.role,
                            comment_id,
                            str(error),
                        )
                        continue
                if author_id != request.agent_id:
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
                if (
                    isinstance(payload, dict)
                    and payload.get("action")
                    and not result_was_file
                    and payload.get("protocol")
                    != "nexus-agent-result-ref-v1"
                ):
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
                        unstructured_candidates.append(
                            ExternalMessage(
                                author_id,
                                {
                                    "action": "__UNSTRUCTURED_REPLY__",
                                    "task_id": request.task_id,
                                    "request_id": request.request_id,
                                    "role": request.role,
                                    "phase": request.phase,
                                    "response_source": "comment",
                                    "structured_output_schema_hash": str(
                                        (request.structured_output or {}).get("schema_hash") or ""
                                    ),
                                    "raw_reply": body[:500],
                                    "raw_reply_truncated": len(body) > 500,
                                    "raw_reply_chars": body_meta["chars"],
                                    "raw_reply_bytes": body_meta["bytes"],
                                    "raw_reply_sha256": body_meta["sha256"],
                                    "raw_reply_first_char": body_meta["first_char"],
                                    "raw_reply_last_char": body_meta["last_char"],
                                    "json_status": parse_meta["json_status"],
                                    "json_error": parse_meta["json_error"],
                                    "json_error_position": parse_meta["json_error_position"],
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
                    try:
                        payload = self._persist_inline_result(request, payload)
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
        ) = parse_comments(comments_from(thread_value))
        if structured_result_required:
            remote_run_status = self.get_run_status(request)
            if remote_run_status == "failed":
                logger.error(
                    "AGENT_REMOTE_RUN_FAILED task_id=%s request_id=%s "
                    "phase=%s role=%s",
                    request.task_id,
                    request.request_id,
                    request.phase,
                    request.role,
                )
                return [
                    ExternalMessage(
                        request.agent_id,
                        {
                            "action": "__REMOTE_RUN_FAILED__",
                            "task_id": request.task_id,
                            "request_id": request.request_id,
                            "phase": request.phase,
                            "role": request.role,
                            "response_source": "remote_run",
                            "remote_run_status": remote_run_status,
                        },
                        f"remote-run:{request.request_id}",
                        "",
                    )
                ]
            if remote_run_status != "completed":
                logger.warning(
                    "AGENT_REPLY_WAITING_REMOTE_TERMINAL task_id=%s request_id=%s "
                    "phase=%s role=%s status=%s observed_result=%s",
                    request.task_id,
                    request.request_id,
                    request.phase,
                    request.role,
                    remote_run_status,
                    bool(result),
                )
                return []
        if not result:
            recovered_file_message = self._recover_result_file(request)
            if recovered_file_message is not None:
                result = [recovered_file_message]
        if (
            not result
            and structured_result_required
            and remote_run_status == "completed"
        ):
            recovered_remote_message = self._recover_remote_result_file(request)
            if recovered_remote_message is not None:
                result = [recovered_remote_message]
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
            ) = parse_comments(
                root_comments,
                require_explicit_binding=True,
            )
            unstructured_candidates = root_unstructured_candidates
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
                ) = parse_comments(root_comments, recovery_ids)
                comments_total += root_total
                result = root_result
                supplemental_reports.extend(root_supplemental)
                unstructured_candidates.extend(root_unstructured_candidates)
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

        if not result and len(unstructured_candidates) == 1:
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

        if supplemental_reports:
            for message in result:
                message.payload["supplemental_reports"] = list(supplemental_reports)
        logger.info(
            "MULTICA_POLL_RESULT issue_id=%s task_id=%s request_id=%s comments_total=%s "
            "valid_replies=%s supplemental_reports=%s author_mismatch=%s "
            "dispatch_comment=%s stale=%s unstructured=%s request_mismatch=%s "
            "correlation_ids=%s",
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
            sorted(recovery_ids),
        )
        logger.info(
            "AGENT_REPLY_CORRELATION_RESULT task_id=%s request_id=%s phase=%s role=%s "
            "dispatch_external_message_id=%s comments_total=%s valid_replies=%s "
            "unstructured=%s request_mismatch=%s uncorrelated=%s stale=%s "
            "author_mismatch=%s correlation_ids=%s",
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
            sorted(recovery_ids),
        )
        return result

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

        # Failed Reasonix runs can be terminal before Multica has populated
        # trigger/coalesced/delivered comment ids.  When that happens, the
        # issue endpoint is still authoritative for issue scope and the run
        # carries the assigned agent.  Accept this fallback only when one
        # candidate is uniquely identifiable by agent and dispatch time; a
        # broad "latest run" fallback would risk stealing another worker's
        # result.
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
        return created >= dispatched

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
        return status

    def find_existing_request(self, idempotency_key: str, issue_id: str = "") -> DispatchReceipt | None:
        with self._dispatch_index_lock:
            key = str(idempotency_key or "")
            cached = self._dispatch_index.get(key)
            if cached is not None:
                return DispatchReceipt(
                    operation_id=cached.get("operation_id") or key,
                    external_message_id=cached.get("external_message_id") or "",
                    request_id=cached.get("request_id") or "",
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
        value = self._run("issue", "comment", "add", issue_id, "--content-file", str(path), "--output", "json")
        if isinstance(value, dict):
            return str(value.get("id") or value.get("comment_id") or value.get("comment", {}).get("id") or "")
        return ""

    def create_issue(
        self,
        title: str,
        description: str,
        project_id: str = "",
        allow_duplicate: bool = False,
    ) -> str:
        path = self.log_dir / f"new_issue_{int(time.time() * 1000)}.md"
        path.write_text(description, encoding="utf-8")
        args = ["issue", "create", "--title", title, "--description-file", str(path), "--output", "json"]
        if project_id:
            args += ["--project", project_id]
        if allow_duplicate:
            args.append("--allow-duplicate")
        value = self._run(*args)
        issue_id = value.get("id") if isinstance(value, dict) else None
        if not issue_id and isinstance(value, dict):
            issue_id = value.get("issue", {}).get("id")
        if not issue_id:
            raise RuntimeError("multica issue create returned no id")
        return str(issue_id)

    def get_issue(self, issue_id: str) -> dict:
        value = self._run_with_read_retry("issue", "get", issue_id, "--output", "json")
        return value if isinstance(value, dict) else {}


def _response_contract_for(request: AgentRequest) -> dict:
    optional = [
        "notification",
        "evidence_packet",
        "findings",
        "candidate_groups",
        "groups",
        "items",
        "questions_for_user",
    ]
    allowed_actions: list[str] = []
    required_by_action: dict[str, list[str]] = {}
    instruction = (
        "Return one structured JSON response. Put the full analysis in the "
        "business fields; do not send a separate unbound Markdown reply."
    )
    if request.phase == "ZHONGSHU" and request.role == "review-analyst":
        dispatch_mode = str(request.context.get("zhongshu_dispatch_mode") or "evidence_collection")
        if request.context.get("contract_mode") or dispatch_mode == "requirement_contract":
            optional.extend(["requirements", "unknowns", "next_actions"])
            allowed_actions = ["REQUIREMENT_CONTRACT_READY", "HUMAN_GATE", "BLOCKED"]
            instruction = (
                "You are the Zhongshu requirement-contract Analyst. Return only "
                "the atomic requirement contract requested in the prompt. Do not "
                "emit task proposals or implementation details."
            )
            required_by_action = {
                "REQUIREMENT_CONTRACT_READY": ["action", "requirements"],
            }
        elif dispatch_mode == "evidence_supplement":
            optional.extend(["evidence_updates", "unknowns", "unknown_resolutions", "confirmed_facts", "risks"])
            allowed_actions = ["EVIDENCE_SUPPLEMENT_READY", "HUMAN_GATE", "BLOCKED"]
            required_by_action = {"EVIDENCE_SUPPLEMENT_READY": ["action", "evidence_updates"]}
            instruction = "Only supplement the supplied evidence gaps. Preserve the current graph and requirement identities. Return targeted evidence_updates or explicit unknowns, not a new task graph."
        else:
            optional.extend([
                "lens", "requirements", "evidence_updates", "confirmed_facts",
                "constraints", "conflicts", "unknowns", "unknown_requirement_ids",
                "risks", "scope", "questions_for_solver", "questions_for_user",
            ])
            allowed_actions = ["EVIDENCE_PACKET_READY", "HUMAN_GATE", "BLOCKED"]
            instruction = (
                "You are the Zhongshu evidence-only Analyst. Return requirements "
                "and traceable evidence only. Do not emit task proposals, candidate "
                "items, candidate groups, or implementation details."
            )
    elif request.phase == "ZHONGSHU" and request.role == "review-solver":
        optional.extend([
            "plan",
            "changes",
            "dependencies",
            "scope",
            "unknowns",
            "risks",
            "finding_resolutions",
            "finding_batch",
            "next_actions",
        ])
        allowed_actions = [
            "READY_FOR_CRITIC",
            "REQUEST_ANALYST_EVIDENCE",
            "NEEDS_MORE_EVIDENCE",
            "HUMAN_GATE",
            "BLOCKED",
        ]
        instruction = (
            "You are the Zhongshu task-graph Solver. Preserve Analyst requirements "
            "and produce one auditable formal task graph. Do not emit implementation "
            "design, options, comparison, or recommendation fields; do not modify "
            "files; on the initial run return the complete plan, while a revision "
            "with current_formal_plan returns bounded changes (changes=[] is valid) "
            "and finding_resolutions so the orchestrator can materialize the full "
            "plan. A revision must return finding_resolutions only for a bounded "
            "selected batch plus finding_batch.selected_finding_ids and "
            "finding_batch.remaining_finding_ids covering all active findings. "
            "The orchestrator carries the declared remainder to the next round. "
            "For compact full-plan output, groups MUST use item_ids that refer "
            "to complete plan.items; do not emit groups[*].items or duplicate "
            "task objects. Always "
            "finish with exactly one structured JSON response. Preserve every "
            "Analyst candidate item_id in plan.items and plan.groups; do not silently "
            "drop a candidate task during normalization."
        )
    elif request.phase == "ZHONGSHU" and request.role == "review-critic":
        optional.extend([
            "context_summary",
            "requirement_coverage",
            "evidence_alignment",
            "architecture_review",
            "reuse_review",
            "grouping_review",
            "dependency_review",
            "risk_signals",
            "plan_hash",
            "reviewed_plan_hash",
            "findings",
            "next_actions",
            "remaining_blockers",
            "questions_for_solver",
            "questions_for_analyst",
            "questions_for_user",
        ])
        if str(request.context.get("zhongshu_dispatch_mode") or "") == "task_review":
            optional.extend([
                "group_id",
                "item_id",
                "reviewed_task_hash",
                "reviewed_dependency_hash",
                "review_checks",
                "evidence_ids",
                "unknowns",
            ])
            allowed_actions = [
                "TASK_APPROVED",
                "TASK_CHANGES_REQUIRED",
                "REQUEST_ANALYST_EVIDENCE",
                "HUMAN_GATE",
                "BLOCKED",
            ]
            instruction = (
                "You are the Zhongshu task Critic. Review only the assigned "
                "group_id/item_id capsule. Return the exact revision and task hashes, "
                "all review_checks, and task-scoped evidence-backed findings. Do not "
                "review or create findings for any other task, and do not design implementation."
            )
            required_by_action = {
                action: [
                    "action", "revision_id", "group_id", "item_id",
                    "reviewed_task_hash", "reviewed_dependency_hash",
                    "review_checks", "findings", "evidence_ids", "unknowns",
                ]
                for action in allowed_actions
            }
        else:
            allowed_actions = [
                "APPROVE_FREEZE",
                "REQUEST_ANALYST_EVIDENCE",
                "REQUEST_SOLVER_REVISION",
                "REQUEST_REGROUP",
                "HUMAN_GATE",
                "BLOCKED",
            ]
            instruction = (
                "You are the Zhongshu plan Critic. Return one structured JSON "
                "object with the allowed action, the exact reviewed_plan_hash, and "
                "evidence-backed findings; include explicit resolutions of prior findings you reviewed. "
                "Omission does not close history. Independent workers may reach identical conclusions. Do not "
                "modify files or output final approval beyond the action."
            )
            required_by_action = {
                action: ["action", "reviewed_plan_hash", "findings"]
                for action in (
                    "APPROVE_FREEZE",
                    "REQUEST_ANALYST_EVIDENCE",
                    "REQUEST_SOLVER_REVISION",
                    "REQUEST_REGROUP",
                )
            }
    elif request.phase == "MENXIA" and request.role == "review-analyst":
        optional.extend([
            "assessment",
            "requirement_trace",
            "confirmed_facts",
            "evidence",
            "current_behavior",
            "existing_capabilities",
            "dependencies_verified",
            "missing_evidence",
            "conflicts",
            "unknowns",
            "questions_for_solver",
            "questions_for_user",
        ])
        allowed_actions = [
            "EVIDENCE_SUFFICIENT",
            "NEEDS_MORE_EVIDENCE",
            "REQUEST_SOLVER_REVISION",
            "HUMAN_GATE",
            "BLOCKED",
        ]
        instruction = (
            "You are the Menxia item evidence Analyst. Return one structured "
            "JSON object with the allowed action and ItemEvidenceAudit fields."
        )
    elif request.phase == "MENXIA" and request.role == "review-critic":
        optional.extend([
            "item_reasonableness",
            "requirement_review",
            "evidence_review",
            "feasibility_review",
            "architecture_reuse_review",
            "performance_resource_review",
            "compatibility_review",
            "testability_rollback_review",
            "findings",
            "required_changes",
            "verification_plan",
            "rollback_plan",
            "remaining_risks",
            "questions_for_user",
        ])
        allowed_actions = [
            "APPROVE_ITEM",
            "REVISE_ITEM",
            "SPLIT_ITEM",
            "MERGE_ITEM",
            "REMOVE_ITEM",
            "REQUEST_SOLVER_REVISION",
            "APPROVE_GROUP",
            "APPROVE_FREEZE",
            "REQUEST_GROUP_REVISION",
            "REVISE_GROUP",
            "HUMAN_GATE",
            "BLOCKED",
        ]
        instruction = (
            "You are the Menxia item Critic. Return one structured JSON "
            "object with action, evidence-backed findings, and review fields."
        )
    elif request.phase == "MENXIA" and request.role == "review-solver":
        optional.extend([
            "implementation_proposal",
            "files",
            "changes",
            "tests",
            "rollback",
            "next_actions",
        ])
        allowed_actions = ["FEASIBLE", "READY_FOR_ANALYST", "READY_FOR_CRITIC", "HUMAN_GATE", "BLOCKED"]
    if request.phase == "ZHONGSHU":
        from .zhongshu_review import ANALYST_ACTIONS, CRITIC_ACTIONS, TASK_CRITIC_ACTIONS
        if request.role == "review-analyst":
            mode = "requirement_contract" if request.context.get("contract_mode") else str(request.context.get("zhongshu_dispatch_mode") or "evidence_collection")
            if mode == "task_discovery":
                mode = "evidence_collection"
            if mode in ANALYST_ACTIONS:
                allowed_actions = sorted(ANALYST_ACTIONS[mode])
        elif request.role == "review-critic":
            if str(request.context.get("zhongshu_dispatch_mode") or "") == "task_review":
                allowed_actions = sorted(TASK_CRITIC_ACTIONS)
                required_by_action = {
                    action: [
                        "action", "revision_id", "group_id", "item_id",
                        "reviewed_task_hash", "reviewed_dependency_hash",
                        "review_checks", "findings", "evidence_ids", "unknowns",
                    ]
                    for action in allowed_actions
                }
            else:
                allowed_actions = sorted(
                    action for action in CRITIC_ACTIONS
                    if action not in {"TASK_APPROVED", "TASK_CHANGES_REQUIRED"}
                )
                required_by_action = {
                    action: ["action", "reviewed_plan_hash"] + (
                        [] if action in {"HUMAN_GATE", "BLOCKED"} else ["findings"]
                    )
                    for action in allowed_actions
                }
    contract = {
        "format": "json",
        "required": ["action"],
        "optional": list(dict.fromkeys(optional)),
        "allowed_actions": allowed_actions,
        "do_not_echo": ["task_id", "request_id", "role", "phase"],
        "instruction": instruction,
    }
    if (
        isinstance(request.structured_output, dict)
        and request.structured_output.get("mode") == "result_file"
    ):
        contract["format"] = "result_file_json_plus_compact_pointer"
        contract["do_not_echo"] = []
        contract["instruction"] = (
            "Write the complete business JSON object to the exact result_path "
            "in prompt_ref using a real JSON serializer. The file must include "
            "the request binding fields and structured_output_schema_hash. "
            "After the durable file write, reply only with the compact result "
            "pointer; do not put the business JSON in the issue comment."
        )
        contract["contract_id"] = str(
            request.structured_output.get("contract_id") or ""
        )
    if required_by_action:
        contract["required_by_action"] = required_by_action
    if request.phase == "ZHONGSHU" and request.role == "review-solver":
        contract["formal_item_required_fields"] = list(ZHONGSHU_SOLVER_REQUIRED_ITEM_FIELDS)
        contract["formal_item_rule"] = (
            "For READY_FOR_CRITIC, every object in plan.items must include all "
            "formal_item_required_fields. Groups must contain item_ids that reference "
            "plan.items; do not emit groups[*].items or duplicate task objects. Empty "
            "unknowns and risks must be emitted as [] and parallelizable must be a boolean."
        )
        contract["finding_batch_rule"] = (
            "On revisions, select the orchestrator-provided focus_finding_ids. Each Finding is atomic "
            f"and must be included in full; select no more than {ZHONGSHU_SOLVER_MAX_FINDINGS_PER_ROUND} "
            "findings, return resolutions for selected IDs, and explicitly list all remaining IDs in finding_batch."
        )
        if request.context.get("solver_revision_mode") or request.context.get("has_current_plan"):
            contract["required_by_action"] = {
                # Revision accepts either bounded changes or a full plan when
                # the requested topology cannot be represented by a patch.
                # State validation enforces that one of them materializes.
                "READY_FOR_CRITIC": ["action"],
            }
        else:
            contract["required_by_action"] = {
                "READY_FOR_CRITIC": ["action", "plan"],
            }
    stable_fields = stable_role_fields(request.phase, request.role)
    if stable_fields and isinstance(request.structured_output, dict):
        target_state = str(
            request.target_state or request.context.get("target_state") or ""
        )
        valid_state_actions = state_actions(target_state)
        if valid_state_actions:
            allowed_actions = [
                action for action in allowed_actions
                if action in valid_state_actions
            ]
        transport_fields = [
            "task_id", "request_id", "phase", "state", "role", "mode",
            "structured_output_protocol", "structured_output_schema_hash",
        ]
        role_mode = str(
            request.context.get("structured_output_role_mode")
            or request.structured_output.get("role_mode")
            or role_mode_for(request.phase, request.role, request.context)
        )
        contract["required"] = ["action", *transport_fields, *stable_fields]
        contract["optional"] = []
        contract["required_by_action"] = {
            action: ["action", *transport_fields, *stable_fields]
            for action in allowed_actions
        }
        contract["stable_role_protocol"] = {
            "phase": request.phase,
            "role": request.role,
            "state": target_state,
            "mode": role_mode,
            "transport_fields": transport_fields,
            "fields": stable_fields,
            "rule": (
                "Always emit the same fields for this role. Put [] or null in fields "
                "that are not used by the current mode; do not change the root shape."
            ),
        }
        if contract.get("format") == "result_file_json_plus_compact_pointer":
            contract["instruction"] = (
                "Write one complete result using this role's fixed field set to the exact "
                "result_path in prompt_ref. The root must include state, mode, every stable "
                "role field, and the supplied protocol/schema hash. Use [] or null for fields "
                "not used by the current mode. After the durable file write, reply only with "
                "the compact result pointer; do not put business JSON in the issue comment."
            )
    return contract


def _external_notifications_disabled_for_tests() -> bool:
    """Hard stop for test runs before any Feishu HTTP request is attempted."""

    return os.environ.get("NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _external_notifications_explicitly_enabled() -> bool:
    """Require an explicit opt-in before any Feishu network operation."""

    return os.environ.get("ENABLE_FEISHU_NOTIFICATIONS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
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
        if (
            _external_notifications_disabled_for_tests()
            or not _external_notifications_explicitly_enabled()
        ):
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
        if (
            _external_notifications_disabled_for_tests()
            or not _external_notifications_explicitly_enabled()
        ):
            raise RuntimeError("FEISHU_EXTERNAL_DISABLED_FOR_TESTS")
        if not self.chat_id:
            return []
        value = self._request(
            "GET",
            self.base_url + "/open-apis/im/v1/messages?" + urllib.parse.urlencode({
                "container_id_type": "chat",
                "container_id": self.chat_id,
                "sort_type": "ByCreateTimeDesc",
                "page_size": 50,
            }),
            token=self._token("gate"),
        )
        replies: list[HumanReply] = []
        for item in value.get("data", {}).get("items", []):
            message_id = str(item.get("message_id") or item.get("id") or "")
            if gate.message_id and message_id == gate.message_id:
                continue
            body = _feishu_message_text(item)
            parent_id = str(item.get("parent_id") or "")
            root_id = str(item.get("root_id") or "")
            is_direct_reply = bool(
                gate.message_id
                and gate.message_id in {parent_id, root_id}
            )
            contains_decision_id = gate.decision_id in body
            if not is_direct_reply and not contains_decision_id:
                continue
            answer = body.replace(gate.decision_id, "").strip(" ：:，,\n\r\t")
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
            replies.append(HumanReply(gate.decision_id, answer, sender_id))
        logger.info(
            "FEISHU_GATE_POLL_RESULT decision_id=%s gate_message_id=%s messages_total=%s replies=%s",
            gate.decision_id,
            gate.message_id,
            len(value.get("data", {}).get("items", [])),
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


def _extract_json(body: str) -> object:
    # Agent comments may start with a UTF-8 BOM, especially when produced by
    # Windows/PowerShell tooling. Strip it before JSON decoding.
    body = body.lstrip("\ufeff\u200b").strip()
    candidates = [body]
    if "```" in body:
        candidates.append(body.replace("```json", "").replace("```", "").strip())
    for candidate in candidates:
        value, status, _, _ = _parse_json_candidate(candidate)
        if status in {"valid", "repaired"}:
            if status == "repaired":
                logger.warning(
                    "AGENT_REPLY_JSON_REPAIRED repair=EXTRA_CLOSING_BRACE "
                    "sha256=%s",
                    _fingerprint_text(body)["sha256"],
                )
            return value
    repaired = _repair_diff_prefixed_json(body)
    if repaired is not None:
        return repaired
    return None


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


def _extract_result_pointer(body: str) -> dict[str, str] | None:
    """Parse the compact human-readable result pointer emitted by some Agents."""
    lines = [line.strip() for line in body.lstrip("\ufeff\u200b").splitlines() if line.strip()]
    if not lines or lines[0].lower() != "nexus-agent-result-ref-v1":
        return None
    payload: dict[str, str] = {"protocol": "nexus-agent-result-ref-v1"}
    for line in lines[1:]:
        if line.startswith("-"):
            line = line[1:].strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in {
            "task_id",
            "request_id",
            "phase",
            "role",
            "result_path",
            "result_sha256",
            "manifest_hash",
            "structured_output_schema_hash",
            "action",
        }:
            payload[key] = value.strip()
    required = {"task_id", "request_id", "result_path", "action"}
    if not required.issubset(payload):
        return None
    return payload
