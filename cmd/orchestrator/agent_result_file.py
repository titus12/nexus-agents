from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .structured_output import STRUCTURED_OUTPUT_PROTOCOL

logger = logging.getLogger("review_orchestrator_fsm")


class AgentResultFileError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentResultFile:
    payload: dict[str, Any]
    path: Path
    sha256: str
    bytes: int


def write_agent_result_file(
    payload: dict[str, Any],
    *,
    target_path: str | Path,
    task_id: str,
    request_id: str,
    phase: str,
    role: str,
    allowed_root: str | Path,
    expected_schema_hash: str = "",
    expected_state: str = "",
    expected_role_mode: str = "",
) -> AgentResultFile:
    """Persist an Agent result under Orchestrator ownership, then validate it."""
    if not isinstance(payload, dict):
        raise AgentResultFileError("inline result must be a JSON object")

    normalized = dict(payload)
    normalized["task_id"] = task_id
    normalized["request_id"] = request_id
    normalized.setdefault("phase", phase)
    normalized.setdefault("role", role)
    if expected_state:
        supplied_state = str(normalized.get("state") or "")
        if supplied_state and supplied_state != expected_state:
            raise AgentResultFileError(
                f"inline result state mismatch: expected={expected_state} actual={supplied_state}"
            )
        normalized["state"] = expected_state
    if expected_role_mode:
        supplied_mode = str(normalized.get("mode") or "")
        if supplied_mode and supplied_mode != expected_role_mode:
            raise AgentResultFileError(
                "inline result role mode mismatch: "
                f"expected={expected_role_mode} actual={supplied_mode}"
            )
        normalized["mode"] = expected_role_mode
    if expected_schema_hash:
        supplied_schema_hash = str(normalized.get("structured_output_schema_hash") or "")
        if supplied_schema_hash and supplied_schema_hash != expected_schema_hash:
            raise AgentResultFileError(
                "inline result schema hash mismatch: "
                f"expected={expected_schema_hash} actual={supplied_schema_hash}"
            )
        supplied_protocol = str(normalized.get("structured_output_protocol") or "")
        if supplied_protocol and supplied_protocol != STRUCTURED_OUTPUT_PROTOCOL:
            raise AgentResultFileError(
                "inline result protocol mismatch: "
                f"expected={STRUCTURED_OUTPUT_PROTOCOL} actual={supplied_protocol}"
            )
        normalized["structured_output_protocol"] = STRUCTURED_OUTPUT_PROTOCOL
        normalized["structured_output_schema_hash"] = expected_schema_hash
    if not normalized.get("action"):
        raise AgentResultFileError("inline result action is missing")

    path = Path(target_path).expanduser().resolve()
    root = Path(allowed_root).expanduser().resolve()
    if not _is_within(path, root):
        raise AgentResultFileError(f"result_path is outside allowed root: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(normalized, ensure_ascii=False, indent=2).encode("utf-8")
    logger.info(
        "ORCHESTRATOR_RESULT_FILE_WRITE_START task_id=%s request_id=%s "
        "phase=%s role=%s path=%s bytes=%s",
        task_id,
        request_id,
        phase,
        role,
        path,
        len(data),
    )
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except OSError as error:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        logger.warning(
            "ORCHESTRATOR_RESULT_FILE_WRITE_FAILED task_id=%s request_id=%s "
            "phase=%s role=%s path=%s error=%s",
            task_id,
            request_id,
            phase,
            role,
            path,
            str(error),
        )
        raise AgentResultFileError(f"orchestrator result write failed: {error}") from error

    reference = {
        "result_path": str(path),
        "result_sha256": hashlib.sha256(data).hexdigest(),
    }
    result = read_agent_result_file(
        reference,
        task_id=task_id,
        request_id=request_id,
        phase=phase,
        role=role,
        allowed_root=root,
        expected_schema_hash=expected_schema_hash,
        expected_state=expected_state,
        expected_role_mode=expected_role_mode,
    )
    result.payload["result_source"] = "orchestrator_inline"
    logger.info(
        "ORCHESTRATOR_RESULT_FILE_WRITTEN task_id=%s request_id=%s "
        "phase=%s role=%s path=%s sha256=%s bytes=%s",
        task_id,
        request_id,
        phase,
        role,
        result.path,
        result.sha256,
        result.bytes,
    )
    return result


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def read_agent_result_file(
    reference: dict[str, Any],
    *,
    task_id: str,
    request_id: str,
    phase: str,
    role: str,
    allowed_root: str | Path,
    expected_schema_hash: str = "",
    expected_state: str = "",
    expected_role_mode: str = "",
    allow_transport_backfill: bool = False,
) -> AgentResultFile:
    raw_path = str(reference.get("result_path") or "").strip()
    if not raw_path:
        raise AgentResultFileError("result_path is missing")

    path = Path(raw_path).expanduser().resolve()
    root = Path(allowed_root).expanduser().resolve()
    logger.info(
        "AGENT_REPLY_FILE_READ_START task_id=%s request_id=%s phase=%s role=%s "
        "path=%s allowed_root=%s expected_sha256=%s expected_schema_hash=%s",
        task_id,
        request_id,
        phase,
        role,
        path,
        root,
        str(reference.get("result_sha256") or ""),
        expected_schema_hash,
    )
    if not _is_within(path, root):
        raise AgentResultFileError(f"result_path is outside allowed root: {path}")
    if not path.is_file():
        raise AgentResultFileError(f"result file is missing: {path}")

    data = path.read_bytes()
    has_bom = data.startswith(b"\xef\xbb\xbf")
    if has_bom:
        # Some Windows/PowerShell writers emit UTF-8 with BOM. Keep the
        # transport hash over the original bytes, but accept the BOM when
        # decoding so a complete result is not mistaken for a corrupt one.
        logger.warning(
            "AGENT_REPLY_FILE_BOM_ACCEPTED task_id=%s request_id=%s "
            "phase=%s role=%s path=%s bytes=%s",
            task_id,
            request_id,
            phase,
            role,
            path,
            len(data),
        )
    try:
        text = data.decode("utf-8-sig" if has_bom else "utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise AgentResultFileError("result file is not valid UTF-8") from error

    actual_hash = hashlib.sha256(data).hexdigest()
    expected_hash = str(reference.get("result_sha256") or "").lower()
    logger.info(
        "AGENT_REPLY_FILE_HASH_CHECK task_id=%s request_id=%s path=%s bytes=%s "
        "actual_sha256=%s expected_sha256=%s",
        task_id,
        request_id,
        path,
        len(data),
        actual_hash,
        expected_hash,
    )
    if expected_hash and expected_hash != actual_hash:
        raise AgentResultFileError(
            f"result hash mismatch: expected={expected_hash} actual={actual_hash}"
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise AgentResultFileError(f"result file is invalid JSON: {error.msg}") from error
    if not isinstance(payload, dict):
        raise AgentResultFileError("result file JSON must be an object")

    # The request id is the primary binding key. It must always be present and
    # exact; unlike the other transport fields it is never repaired locally.
    if str(payload.get("request_id") or "") != request_id:
        raise AgentResultFileError("result request_id mismatch")

    expected_transport = {
        "task_id": task_id,
        "phase": phase,
        "state": expected_state,
        "role": role,
        "mode": expected_role_mode,
        "structured_output_protocol": STRUCTURED_OUTPUT_PROTOCOL,
        "structured_output_schema_hash": expected_schema_hash,
    }
    backfilled_fields: list[str] = []
    for field, expected_value in expected_transport.items():
        expected_text = str(expected_value or "")
        if not expected_text:
            continue
        actual_text = str(payload.get(field) or "")
        if actual_text:
            if actual_text != expected_text:
                raise AgentResultFileError(f"result {field} mismatch")
            continue
        if allow_transport_backfill:
            payload[field] = expected_text
            backfilled_fields.append(field)

    if str(payload.get("task_id") or "") != task_id:
        raise AgentResultFileError("result task_id mismatch")
    if expected_schema_hash:
        if str(payload.get("phase") or "") != phase:
            raise AgentResultFileError("result phase mismatch")
        if str(payload.get("role") or "") != role:
            raise AgentResultFileError("result role mismatch")
        if expected_state and str(payload.get("state") or "") != expected_state:
            raise AgentResultFileError(
                f"result state mismatch: expected={expected_state} actual={payload.get('state')}"
            )
        if expected_role_mode and str(payload.get("mode") or "") != expected_role_mode:
            raise AgentResultFileError(
                "result role mode mismatch: "
                f"expected={expected_role_mode} actual={payload.get('mode')}"
            )
        actual_protocol = str(payload.get("structured_output_protocol") or "")
        if actual_protocol != STRUCTURED_OUTPUT_PROTOCOL:
            logger.warning(
                "AGENT_REPLY_FILE_PROTOCOL_MISMATCH task_id=%s request_id=%s "
                "phase=%s role=%s path=%s expected=%s actual=%s",
                task_id,
                request_id,
                phase,
                role,
                path,
                STRUCTURED_OUTPUT_PROTOCOL,
                actual_protocol,
            )
            raise AgentResultFileError(
                "result protocol mismatch: "
                f"expected={STRUCTURED_OUTPUT_PROTOCOL} actual={actual_protocol}"
            )
        actual_schema_hash = str(payload.get("structured_output_schema_hash") or "")
        if actual_schema_hash != expected_schema_hash:
            logger.warning(
                "AGENT_REPLY_FILE_SCHEMA_HASH_MISMATCH task_id=%s request_id=%s "
                "phase=%s role=%s path=%s expected=%s actual=%s",
                task_id,
                request_id,
                phase,
                role,
                path,
                expected_schema_hash,
                actual_schema_hash,
            )
            raise AgentResultFileError(
                f"result schema hash mismatch: expected={expected_schema_hash} actual={actual_schema_hash}"
            )
    else:
        if payload.get("phase") and str(payload["phase"]) != phase:
            raise AgentResultFileError("result phase mismatch")
        if payload.get("role") and str(payload["role"]) != role:
            raise AgentResultFileError("result role mismatch")
    if not payload.get("action"):
        raise AgentResultFileError("result action is missing")

    payload["result_source"] = "file"
    payload["result_path"] = str(path)
    payload["result_sha256"] = actual_hash
    payload["result_utf8_bytes"] = len(data)
    payload["result_has_bom"] = has_bom
    if backfilled_fields:
        payload["result_transport_backfilled_fields"] = sorted(backfilled_fields)
        logger.warning(
            "AGENT_REPLY_FILE_TRANSPORT_BACKFILLED task_id=%s request_id=%s "
            "phase=%s role=%s path=%s fields=%s",
            task_id,
            request_id,
            phase,
            role,
            path,
            ",".join(sorted(backfilled_fields)),
        )
    logger.info(
        "AGENT_REPLY_FILE_ACCEPTED task_id=%s request_id=%s phase=%s role=%s "
        "path=%s sha256=%s bytes=%s",
        task_id,
        request_id,
        phase,
        role,
        path,
        actual_hash,
        len(data),
    )
    return AgentResultFile(payload=payload, path=path, sha256=actual_hash, bytes=len(data))
