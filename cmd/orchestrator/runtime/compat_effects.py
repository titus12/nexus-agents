"""Boundary adapters for the existing Multica transport and local artifacts.

The compatibility here is deliberately limited to external envelopes.  No
legacy workflow context or state-machine object crosses this boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import hashlib
import json
import logging
from pathlib import Path
import re
import os
import tempfile
from typing import Any

from ..adapters import MulticaAdapter
from ..transport.external import AgentRequest as ExternalAgentRequest
from ..transport import RawTransportReply
from ..structured_output import build_structured_output_spec
from .ports import (
    AgentDispatchRequest,
    DispatchReceipt,
    PollRequest,
    RemoteRunStatus,
)

logger = logging.getLogger(__name__)


class MulticaTransportAdapter:
    """Translate the legacy adapter envelope into the runtime port contract."""

    def __init__(self, adapter: MulticaAdapter) -> None:
        self._adapter = adapter
        self._requests: dict[str, ExternalAgentRequest] = {}

    def dispatch(self, request: AgentDispatchRequest) -> DispatchReceipt:
        external = self._external_request(request)
        receipt = self._adapter.dispatch(external)
        bound = self._bind_receipt(external, receipt)
        self._requests[request.request_id] = bound
        if receipt.request_id:
            self._requests[receipt.request_id] = bound
        return DispatchReceipt(
            operation_id=receipt.operation_id,
            external_message_id=receipt.external_message_id,
            confirmed=receipt.confirmed,
            request_id=receipt.request_id,
            issue_id=str(getattr(receipt, "issue_id", "") or ""),
        )

    @staticmethod
    def _external_request(request: AgentDispatchRequest) -> ExternalAgentRequest:
        dispatch_context = dict(request.context or {})
        spec_context = {"active_runtime_state": request.target_state, **dispatch_context}
        structured_spec = build_structured_output_spec(
            request.phase,
            request.role,
            spec_context,
        )
        external = ExternalAgentRequest(
            task_id=request.task_id,
            request_id=request.request_id,
            agent_id=request.agent_id,
            role=request.role,
            phase=request.phase,
            prompt=str(request.prompt_ref),
            idempotency_key=request.idempotency_key,
            issue_id=request.issue_id,
            sent_after=request.sent_after,
            context={
                "issue_id": request.issue_id,
                "target_state": request.target_state or request.phase,
                "role": request.role,
                "active_runtime_skill": _skill_name(request.target_state),
                "active_runtime_skill_directive": _skill_directive(request.target_state),
                "active_runtime_skill_lock": _skill_lock(request.target_state),
                "active_runtime_phase": request.phase,
                "active_runtime_state": request.target_state,
                "revision_id": request.revision_id,
                "plan_hash": request.plan_hash,
                **dispatch_context,
            },
            target_state=request.target_state or request.phase,
            structured_output=structured_spec.to_dict() if structured_spec is not None else None,
        )
        return external

    def ensure_child_issue(
        self,
        parent_issue_id: str,
        agent_id: str,
        title: str,
        marker: str = "",
    ) -> str:
        ensure = getattr(self._adapter, "ensure_child_issue", None)
        if not callable(ensure):
            raise RuntimeError("agent transport does not support child issues")
        return str(ensure(parent_issue_id, agent_id, title, marker) or "")

    def close_issue(self, issue_id: str, status: str = "done") -> bool:
        close = getattr(self._adapter, "close_issue", None)
        if not callable(close):
            return False
        return bool(close(issue_id, status))

    def find_existing(self, request: AgentDispatchRequest) -> DispatchReceipt | None:
        """Recover a prior dispatch before issuing a second external request."""

        external = self._external_request(request)
        self._requests.setdefault(request.request_id, external)
        finder = getattr(self._adapter, "find_existing_request", None)
        if not callable(finder):
            return None
        receipt = finder(request.idempotency_key, request.issue_id)
        if receipt is None:
            return None
        bound = self._bind_receipt(external, receipt)
        self._requests[request.request_id] = bound
        if receipt.request_id:
            # A recovered provider request may have a provider-assigned id
            # different from the local effect id.  Index both identities so
            # the subsequent status/poll calls can still be correlated.
            self._requests[receipt.request_id] = bound
        return DispatchReceipt(
            operation_id=receipt.operation_id,
            external_message_id=receipt.external_message_id,
            confirmed=receipt.confirmed,
            request_id=receipt.request_id or request.request_id,
            issue_id=str(getattr(receipt, "issue_id", "") or ""),
        )

    @staticmethod
    def _bind_receipt(
        external: ExternalAgentRequest,
        receipt: object,
    ) -> ExternalAgentRequest:
        """Persist provider identities on the request used for later polling."""
        request_id = str(getattr(receipt, "request_id", "") or "")
        external_message_id = str(
            getattr(receipt, "external_message_id", "") or ""
        )
        issue_id = str(getattr(receipt, "issue_id", "") or "")
        return replace(
            external,
            issue_id=issue_id or external.issue_id,
            request_id=request_id or external.request_id,
            dispatch_external_message_id=(
                external_message_id or external.dispatch_external_message_id
            ),
        )

    def poll(self, request: PollRequest) -> tuple[RawTransportReply, ...]:
        external = self._requests.get(request.request_id)
        if external is None:
            raise KeyError(f"unknown agent request: {request.request_id}")
        replies = self._adapter.poll(external)
        return tuple(
            RawTransportReply(
                author_id=str(reply.author_id),
                external_message_id=str(reply.external_id or ""),
                request_id=str(reply.payload.get("request_id") or request.request_id),
                payload=dict(reply.payload),
                received_at="",
                source="multica",
            )
            for reply in replies
        )

    def status(self, request: PollRequest) -> RemoteRunStatus:
        external = self._requests.get(request.request_id)
        if external is None:
            return RemoteRunStatus(
                request_id=request.request_id,
                operation_id=request.operation_id,
                status="UNKNOWN",
                error_code="REQUEST_NOT_INDEXED",
                error_message="agent request is not present in the local transport index",
            )
        raw = str(self._adapter.get_run_status(external) or "").upper()
        if raw in {"FAILED", "ERROR", "FAILURE"}:
            status = "FAILED"
        elif raw in {"COMPLETED", "SUCCEEDED", "DONE", "SUCCESS"}:
            status = "COMPLETED"
        elif raw in {"RUNNING", "PENDING", "IN_PROGRESS", "QUEUED"}:
            status = "RUNNING"
        else:
            status = "UNKNOWN"
        return RemoteRunStatus(
            request_id=request.request_id,
            operation_id=request.operation_id,
            status=status,
            error_code="REMOTE_RUN_FAILED" if status == "FAILED" else None,
            error_message="remote run reported failure" if status == "FAILED" else None,
        )

    def lookup(self, operation_id: str) -> DispatchReceipt | None:
        for request in self._requests.values():
            finder = getattr(self._adapter, "find_existing_request", None)
            if not callable(finder):
                return None
            receipt = finder(request.idempotency_key, request.issue_id)
            if receipt is not None and receipt.operation_id == operation_id:
                return DispatchReceipt(
                    operation_id=receipt.operation_id,
                    external_message_id=receipt.external_message_id,
                    confirmed=receipt.confirmed,
                    request_id=receipt.request_id,
                )
        return None

    def recover_completed(self, request: AgentDispatchRequest) -> RawTransportReply | None:
        external = self._requests.get(request.request_id)
        if external is None:
            external = self._external_request(request)
        try:
            result = self._adapter._recover_result_file(external)
            if result is not None:
                logger.info(
                    "RECOVER_COMPLETED_CANONICAL task_id=%s request_id=%s",
                    request.task_id, request.request_id,
                )
                return RawTransportReply(
                    author_id=str(result.author_id or request.agent_id),
                    external_message_id=str(result.external_id or ""),
                    request_id=request.request_id,
                    payload=dict(result.payload),
                    received_at="",
                    source="multica",
                )
        except Exception as error:
            logger.debug(
                "RECOVER_COMPLETED_CANONICAL_FAILED task_id=%s request_id=%s error=%s",
                request.task_id, request.request_id, str(error)[:200],
            )
        return None


class FileArtifactStore:
    """Content-addressed artifacts constrained to one task root."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, artifact) -> object:
        digest = hashlib.sha256(artifact.content).hexdigest()
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(artifact.name).name)
        safe_name = safe_name or "artifact.bin"
        path = (self.root / f"{digest[:16]}-{safe_name}").resolve()
        if self.root not in path.parents:
            raise ValueError("artifact path escaped root")
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=self.root
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(artifact.content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        from .ports import ArtifactReceipt

        return ArtifactReceipt(artifact_id=str(path), digest=digest)

    def read(self, task_id: str, artifact_id: str) -> bytes:
        path = Path(artifact_id).resolve()
        if self.root not in path.parents:
            raise ValueError("artifact path escaped root")
        return path.read_bytes()


__all__ = ["FileArtifactStore", "MulticaTransportAdapter"]


_SKILLS = {
    "ZHONGSHU_ANALYST": "zhongshu-analyst",
    "ZHONGSHU_SOLVER": "zhongshu-solver",
    "ZHONGSHU_CRITIC": "zhongshu-critic",
    "ZHONGSHU_FREEZE_CHECK": "zhongshu-critic",
    "MENXIA_ITEM_SOLVER": "menxia-solver",
    "MENXIA_ITEM_ANALYST": "menxia-analyst",
    "MENXIA_ITEM_CRITIC": "menxia-critic",
    "MENXIA_GROUP_GATE": "menxia-critic",
}


def _skill_name(state: str) -> str:
    return _SKILLS.get(str(state), "")


def _skill_directive(state: str) -> str:
    skill = _skill_name(state)
    if not skill:
        return ""
    phase = "ZHONGSHU" if str(state).startswith("ZHONGSHU") else "MENXIA"
    return f"ACTIVE_RUNTIME_SKILL: {skill}\nACTIVE_RUNTIME_PHASE: {phase}\nACTIVE_RUNTIME_STATE: {state}"


def _skill_lock(state: str) -> dict[str, object]:
    skill = _skill_name(state)
    if not skill:
        return {}
    # compat_effects.py is located at <repo>/cmd/orchestrator/runtime.  The
    # repository root is therefore parents[3], not parents[2] (which is
    # <repo>/cmd).  Using parents[2] silently returned a "missing" lock and
    # blocked every Zhongshu/Menxia dispatch before the prompt was built.
    path = Path(__file__).resolve().parents[3] / "docs" / "multi" / f"{skill}-skill.md"
    if str(state).startswith("ZHONGSHU"):
        path = path.parent / "runtime" / path.name
    try:
        content = path.read_bytes()
    except OSError:
        return {"name": skill, "status": "missing", "source": str(path)}
    digest = hashlib.sha256(content).hexdigest()
    return {
        "name": skill,
        "status": "locked",
        "version": f"sha256-{digest[:12]}",
        "source": str(path),
        "sha256": digest,
        "bytes": len(content),
        "encoding": "utf-8",
        "bom": content.startswith(b"\xef\xbb\xbf"),
    }
