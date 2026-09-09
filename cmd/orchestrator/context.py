from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .models import Finding

CURRENT_SCHEMA_VERSION = "3.1"


@dataclass
class StateContext:
    task_id: str
    issue_id: str = ""
    workflow_state: str = "REQUEST_INTAKE"
    sequence: int = 0
    raw_request: str = ""
    request_payload: dict[str, Any] = field(default_factory=dict)
    zhongshu_parallel: dict[str, Any] = field(
        default_factory=lambda: {
            "enabled": True,
            "analyst_max_workers": 3,
            "analyst_default_workers": 3,
            "critic_max_workers": 6,
            "critic_default_workers": 6,
            "global_max_workers": 6,
            "per_task_max_workers": 6,
        }
    )
    menxia_parallel: dict[str, Any] = field(default_factory=dict)
    state_version: int = 0
    last_agent_payload: dict[str, Any] = field(default_factory=dict)
    sent_notification_keys: list[str] = field(default_factory=list)
    heartbeat_count: int = 0
    last_heartbeat_epoch: float = 0.0
    project_type: str = "unknown"
    task_type: str = "review"
    current_phase: str = ""
    current_role: str = ""
    expected_agent_id: str = ""
    active_request_id: str = ""
    last_sent_at: str = ""
    active_group_id: Optional[str] = None
    active_item_id: Optional[str] = None
    group_index: Optional[int] = None
    item_index: Optional[int] = None
    zhongshu_revision_round: int = 0
    max_zhongshu_revision_rounds: int = 8
    freeze_check_attempt: int = 0
    max_freeze_check_attempts: int = 2
    item_revision_round: int = 0
    max_item_revision_rounds: int = 3
    active_decision_id: Optional[str] = None
    resume_state: Optional[str] = None
    gate_message_id: Optional[str] = None
    last_artifact_id: Optional[str] = None
    active_finding_ids: list[str] = field(default_factory=list)
    pending_solver_finding_ids: list[str] = field(default_factory=list)
    resolved_finding_ids: list[str] = field(default_factory=list)
    dispatch_status: str = "none"
    dispatch_operation_id: Optional[str] = None
    dispatch_external_message_id: Optional[str] = None
    dispatch_idempotency_key: Optional[str] = None
    dispatch_attempt: int = 0
    external_retry_count: int = 0
    reply_retry_count: int = 0
    max_external_retries: int = 3
    max_reply_retries: int = 3
    max_state_write_retries: int = 3
    timeout_retry_count: int = 0
    max_timeout_retries: int = 3
    timeout_phase: Optional[str] = None
    timeout_role: Optional[str] = None
    timeout_request_id: Optional[str] = None
    last_error: Optional[dict[str, Any]] = None
    reply_history: list[dict[str, Any]] = field(default_factory=list)
    last_reply_fingerprint: str = ""
    no_progress_count: int = 0
    max_no_progress: int = 3
    recoverable: bool = True
    blocked_reason: Optional[str] = None
    entered_at: Optional[str] = None
    updated_at: Optional[str] = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    schema_version: str = CURRENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def validate_serialized(cls, value: Any) -> None:
        """Reject valid-JSON documents whose FSM containers are corrupted."""
        if not isinstance(value, dict):
            raise TypeError("state must be a JSON object")
        dict_fields = (
            "request_payload",
            "zhongshu_parallel",
            "menxia_parallel",
            "last_agent_payload",
        )
        list_fields = (
            "sent_notification_keys",
            "active_finding_ids",
            "pending_solver_finding_ids",
            "resolved_finding_ids",
            "reply_history",
            "findings",
        )
        for name in dict_fields:
            if name in value and not isinstance(value[name], dict):
                raise TypeError(f"state field {name} must be an object")
        request_payload = value.get("request_payload")
        if isinstance(request_payload, dict):
            review_queue = request_payload.get("zhongshu_task_review_queue")
            if review_queue is not None and not isinstance(review_queue, dict):
                raise TypeError(
                    "request_payload field zhongshu_task_review_queue must be an object"
                )
            if isinstance(review_queue, dict):
                try:
                    from .zhongshu_review_queue import TaskReviewQueue

                    TaskReviewQueue.from_dict(review_queue)
                except (TypeError, ValueError, KeyError) as error:
                    raise TypeError(
                        "request_payload field zhongshu_task_review_queue is invalid"
                    ) from error
        for name in list_fields:
            if name in value and not isinstance(value[name], list):
                raise TypeError(f"state field {name} must be an array")
        findings = value.get("findings", [])
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict):
                raise TypeError(f"state finding {index} must be an object")
        last_error = value.get("last_error")
        if last_error is not None and not isinstance(last_error, dict):
            raise TypeError("state field last_error must be an object or null")
        for name in (
            "task_id", "issue_id", "workflow_state", "raw_request", "project_type",
            "task_type", "current_phase", "current_role", "expected_agent_id",
            "active_request_id", "last_sent_at", "dispatch_status",
            "last_reply_fingerprint", "schema_version",
        ):
            if name in value and not isinstance(value[name], str):
                raise TypeError(f"state field {name} must be a string")
        for name in (
            "active_group_id", "active_item_id", "active_decision_id",
            "resume_state", "gate_message_id", "last_artifact_id",
            "dispatch_operation_id", "dispatch_external_message_id",
            "dispatch_idempotency_key", "timeout_phase", "timeout_role",
            "timeout_request_id", "blocked_reason", "entered_at", "updated_at",
        ):
            if name in value and value[name] is not None and not isinstance(value[name], str):
                raise TypeError(f"state field {name} must be a string or null")
        for name in (
            "sequence", "state_version", "heartbeat_count", "group_index", "item_index",
            "zhongshu_revision_round", "max_zhongshu_revision_rounds",
            "freeze_check_attempt", "max_freeze_check_attempts", "item_revision_round",
            "max_item_revision_rounds", "dispatch_attempt", "external_retry_count",
            "reply_retry_count", "max_external_retries", "max_reply_retries",
            "max_state_write_retries", "timeout_retry_count", "max_timeout_retries",
            "no_progress_count", "max_no_progress",
        ):
            if name in value and value[name] is not None and type(value[name]) is not int:
                raise TypeError(f"state field {name} must be an integer")
        for name in ("last_heartbeat_epoch",):
            if name in value and (
                isinstance(value[name], bool)
                or not isinstance(value[name], (int, float))
            ):
                raise TypeError(f"state field {name} must be a number")
        if "recoverable" in value and not isinstance(value["recoverable"], bool):
            raise TypeError("state field recoverable must be a boolean")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "StateContext":
        cls.validate_serialized(value)
        fields = cls.__dataclass_fields__
        data = {name: value[name] for name in fields if name in value}
        return cls(**data)

    def finding_objects(self) -> list[Finding]:
        return [Finding.from_dict(item) for item in self.findings]

    def finding_objects_for(
        self,
        group_id: str | None = None,
        item_id: str | None = None,
        *,
        include_global: bool = False,
    ) -> list[Finding]:
        """Return findings visible to one Menxia scope.

        Item gates intentionally receive only the exact item scope. Group
        gates receive all findings in that group and, when requested, global
        Zhongshu findings. Callers that need the complete task view should
        continue using ``finding_objects``.
        """
        group = str(group_id or "").strip()
        item = str(item_id or "").strip()
        if item and not group:
            raise ValueError("finding query item_id requires group_id")
        findings = self.finding_objects()
        if item:
            return [
                finding
                for finding in findings
                if finding.group_id == group and finding.item_id == item
            ]
        if group:
            return [
                finding
                for finding in findings
                if finding.group_id == group
                or (
                    include_global
                    and not finding.group_id
                    and not finding.item_id
                )
            ]
        if include_global:
            return [
                finding
                for finding in findings
                if not finding.group_id and not finding.item_id
            ]
        return []

    def replace_findings(self, findings: list[Finding]) -> None:
        seen: set[tuple[str, str, str]] = set()
        for finding in findings:
            identity = finding.identity_key()
            if identity in seen:
                raise ValueError(
                    "duplicate finding identity: "
                    f"{identity[0]}/{identity[1]}/{identity[2]}"
                )
            seen.add(identity)
        self.findings = [finding.to_dict() for finding in findings]
        self.active_finding_ids = [finding.finding_id for finding in findings if finding.active]
        self.pending_solver_finding_ids = [
            finding.finding_id
            for finding in findings
            if finding.owner_role == "review-solver" and finding.active
        ]
        self.resolved_finding_ids = [
            finding.finding_id for finding in findings if finding.resolved
        ]

    @property
    def blocking_finding_ids(self) -> list[str]:
        """Return active P0/P1 findings that must prevent a freeze."""
        return [
            finding.finding_id
            for finding in self.finding_objects()
            if finding.active and finding.severity.upper() in {"P0", "P1"}
        ]

    def merge_findings(self, updates: list[Finding]) -> None:
        merged = {
            finding.identity_key(): finding
            for finding in self.finding_objects()
        }
        for finding in updates:
            merged[finding.identity_key()] = finding
        self.replace_findings(list(merged.values()))

    def checkpoint(self, **updates: Any) -> "StateContext":
        for key, value in updates.items():
            if key not in self.__dataclass_fields__:
                raise KeyError(f"unknown StateContext field: {key}")
            setattr(self, key, value)
        return self
