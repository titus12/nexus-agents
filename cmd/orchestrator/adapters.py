from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol
import uuid
import json
import logging
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger("review_orchestrator_fsm")


from .models import (
    AgentRequest,
    DispatchReceipt,
    ExternalMessage,
    DeliveryReceipt,
    HumanGate,
    HumanReply,
)


class MulticaAdapter(Protocol):
    def dispatch(self, request: AgentRequest) -> DispatchReceipt: ...
    def poll(self, request: AgentRequest) -> list[ExternalMessage]: ...
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

    def dispatch(self, request: AgentRequest) -> DispatchReceipt:
        self.dispatched.append(request)
        return DispatchReceipt(operation_id=f"op-{uuid.uuid4().hex}", external_message_id=request.request_id)

    def poll(self, request: AgentRequest) -> list[ExternalMessage]:
        return self.replies.pop(request.request_id, [])

    def find_existing_request(self, idempotency_key: str, issue_id: str = "") -> DispatchReceipt | None:
        for request in self.dispatched:
            if request.idempotency_key == idempotency_key:
                return DispatchReceipt(operation_id=f"existing-{request.request_id}", external_message_id=request.request_id)
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


class MulticaCliAdapter:
    """Production adapter over the installed multica CLI."""

    def __init__(self, log_dir: str | Path = "logs") -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.last_issue_id = ""
        self.cli_timeout_seconds = float(os.environ.get("MULTICA_CLI_TIMEOUT_SEC", "30"))
        self.cli_read_timeout_seconds = float(
            os.environ.get("MULTICA_CLI_READ_TIMEOUT_SEC", "60")
        )
        self.cli_read_retries = max(
            0,
            int(os.environ.get("MULTICA_CLI_READ_RETRIES", "1")),
        )

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
        if result.returncode:
            logger.error(
                "MULTICA_CLI_FAILED command=%s rc=%s stderr=%r",
                command_text,
                result.returncode,
                (result.stderr or "")[:500],
            )
            raise RuntimeError(f"multica failed rc={result.returncode}: {result.stderr[:500]}")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            logger.warning(
                "MULTICA_CLI_NON_JSON command=%s stdout_preview=%r",
                command_text,
                (result.stdout or "")[:300],
            )
            return result.stdout.strip()

    def dispatch(self, request: AgentRequest) -> DispatchReceipt:
        self.last_issue_id = request.issue_id or request.task_id
        issue_id = request.issue_id or request.task_id
        # Multica Agent runtime is triggered by issue assignment. The comment
        # carries the structured request, but assignment selects the Agent
        # that should consume it.
        self._run(
            "issue",
            "update",
            issue_id,
            "--assignee-id",
            request.agent_id,
            "--output",
            "json",
        )
        content = json.dumps({
            "prompt": request.prompt,
            "response_contract": _response_contract_for(request),
            "transport": {
                "task_id": request.task_id,
                "request_id": request.request_id,
                "role": request.role,
                "phase": request.phase,
                "idempotency_key": request.idempotency_key,
                "reply_correlation_id": request.request_id,
                "reply_mode": "REPLY_TO_TRIGGER_COMMENT",
            },
        }, ensure_ascii=False, indent=2)
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
        return DispatchReceipt(operation_id=request.idempotency_key, external_message_id=external_id)

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
        ) -> tuple[list[ExternalMessage], list[str], dict[str, int], int]:
            result: list[ExternalMessage] = []
            supplemental_reports: list[str] = []
            stats = {
                "author_mismatch": 0,
                "dispatch_comment": 0,
                "stale": 0,
                "unstructured": 0,
                "request_mismatch": 0,
                "uncorrelated": 0,
            }
            ordered_comments = sorted(
                comments,
                key=lambda item: str(item.get("created_at") or ""),
            )
            for comment in ordered_comments:
                comment_id = str(comment.get("id") or "")
                if allowed_ids is not None and comment_id not in allowed_ids:
                    stats["uncorrelated"] += 1
                    continue
                body = str(comment.get("content") or comment.get("body") or "")
                payload = _extract_json(body)
                author_id = str(
                    comment.get("author_id")
                    or comment.get("creator_id")
                    or comment.get("user_id")
                    or ""
                )
                if author_id != request.agent_id:
                    stats["author_mismatch"] += 1
                    continue
                if comment_id and comment_id == request.dispatch_external_message_id:
                    stats["dispatch_comment"] += 1
                    continue
                created_at = str(comment.get("created_at") or "")
                if request.sent_after and created_at and created_at <= request.sent_after:
                    stats["stale"] += 1
                    continue
                if not isinstance(payload, dict) or not payload.get("action"):
                    stats["unstructured"] += 1
                    if body.strip():
                        supplemental_reports.append(body)
                    continue
                if payload.get("request_id") and payload.get("request_id") != request.request_id:
                    stats["request_mismatch"] += 1
                    continue
                payload.setdefault("task_id", request.task_id)
                payload.setdefault("request_id", request.request_id)
                payload.setdefault("role", request.role)
                payload.setdefault("phase", request.phase)
                result.append(ExternalMessage(author_id, payload, comment_id, body))
            return result, supplemental_reports, stats, len(ordered_comments)

        thread_args = ["issue", "comment", "list", issue_id]
        if request.dispatch_external_message_id:
            thread_args += ["--thread", request.dispatch_external_message_id, "--tail", "30"]
            if request.sent_after:
                thread_args += ["--since", request.sent_after]
        else:
            thread_args += ["--recent", "50"]
        thread_args += ["--output", "json"]
        value = self._run_with_read_retry(*thread_args)
        result, supplemental_reports, stats, comments_total = parse_comments(
            comments_from(value)
        )
        recovery_ids: set[str] = set()

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
                root_value = self._run_with_read_retry(
                    "issue", "comment", "list", issue_id,
                    "--recent", "100", "--output", "json",
                )
                root_result, root_supplemental, root_stats, root_total = parse_comments(
                    comments_from(root_value), recovery_ids
                )
                comments_total += root_total
                result = root_result
                supplemental_reports.extend(root_supplemental)
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
        return result

    def _find_correlated_delivery_ids(self, request: AgentRequest) -> set[str]:
        issue_id = request.issue_id or request.task_id
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
            return set()
        runs = value if isinstance(value, list) else (
            value.get("runs", value.get("items", []))
            if isinstance(value, dict)
            else []
        )
        matches = [
            run for run in runs
            if isinstance(run, dict)
            and str(run.get("trigger_comment_id") or "")
            == request.dispatch_external_message_id
        ]
        if not matches:
            logger.info(
                "REPLY_CORRELATION_MISS issue_id=%s task_id=%s request_id=%s "
                "trigger_comment_id=%s",
                issue_id,
                request.task_id,
                request.request_id,
                request.dispatch_external_message_id,
            )
            return set()
        matches.sort(key=lambda run: str(run.get("created_at") or ""), reverse=True)
        run = matches[0]
        if str(run.get("status") or "") != "completed":
            return set()
        delivered = run.get("delivered_comment_ids") or []
        return {str(comment_id) for comment_id in delivered if comment_id}

    def find_existing_request(self, idempotency_key: str, issue_id: str = "") -> DispatchReceipt | None:
        try:
            issue_id = issue_id or self.last_issue_id
            if not issue_id:
                return None
            value = self._run_with_read_retry("issue", "comment", "list", issue_id, "--recent", "100", "--output", "json")
        except Exception:
            return None
        comments = value if isinstance(value, list) else value.get("comments", value.get("items", [])) if isinstance(value, dict) else []
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            payload = _extract_json(str(comment.get("content") or comment.get("body") or ""))
            stored_key = (
                payload.get("idempotency_key")
                or payload.get("transport", {}).get("idempotency_key")
                if isinstance(payload, dict)
                else None
            )
            if stored_key == idempotency_key:
                return DispatchReceipt(
                    operation_id=str(comment.get("id") or idempotency_key),
                    external_message_id=str(comment.get("id") or ""),
                )
        return None

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
    instruction = (
        "Return one structured JSON response. Put the full analysis in the "
        "business fields; do not send a separate unbound Markdown reply."
    )
    if request.phase == "ZHONGSHU" and request.role == "review-analyst":
        optional.extend(["plan", "next_actions"])
        allowed_actions = ["READY_FOR_SOLVER", "HUMAN_GATE", "BLOCKED"]
        instruction = (
            "You are the Zhongshu evidence Analyst. Return the single strict "
            "new-format plan contract supplied in the prompt. Do not emit legacy "
            "evidence_packet, analyst_draft, or top-level candidate_groups fields."
        )
    elif request.phase == "ZHONGSHU" and request.role == "review-solver":
        optional.extend([
            "plan",
            "requirements",
            "options",
            "comparison",
            "recommendation",
            "dependencies",
            "scope",
            "assumptions",
            "unknowns",
            "risk_signals",
            "candidate_verification_questions",
            "source_revalidation",
            "context_check",
            "next_actions",
        ])
        allowed_actions = ["READY_FOR_CRITIC", "HUMAN_GATE", "BLOCKED"]
        instruction = (
            "You are the Zhongshu planning Solver. Produce a read-only solution "
            "plan only; do not modify files or implement code. Reuse upstream "
            "evidence, stop repeated searching when evidence is sufficient, "
            "and always finish with exactly one structured JSON response."
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
            "findings",
            "next_actions",
            "remaining_blockers",
            "questions_for_solver",
            "questions_for_analyst",
            "questions_for_user",
        ])
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
            "object with the allowed action and evidence-backed findings; do "
            "not modify files or output final approval beyond the action."
        )
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
    return {
        "format": "json",
        "required": ["action"],
        "optional": list(dict.fromkeys(optional)),
        "allowed_actions": allowed_actions,
        "do_not_echo": ["task_id", "request_id", "role", "phase"],
        "instruction": instruction,
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
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None
