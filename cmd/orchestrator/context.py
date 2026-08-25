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
    recoverable: bool = True
    blocked_reason: Optional[str] = None
    entered_at: Optional[str] = None
    updated_at: Optional[str] = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    schema_version: str = CURRENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "StateContext":
        fields = cls.__dataclass_fields__
        data = {name: value[name] for name in fields if name in value}
        return cls(**data)

    def finding_objects(self) -> list[Finding]:
        return [Finding.from_dict(item) for item in self.findings]

    def replace_findings(self, findings: list[Finding]) -> None:
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

    def merge_findings(self, updates: list[Finding]) -> None:
        merged = {finding.finding_id: finding for finding in self.finding_objects()}
        for finding in updates:
            merged[finding.finding_id] = finding
        self.replace_findings(list(merged.values()))

    def checkpoint(self, **updates: Any) -> "StateContext":
        for key, value in updates.items():
            if key not in self.__dataclass_fields__:
                raise KeyError(f"unknown StateContext field: {key}")
            setattr(self, key, value)
        return self
