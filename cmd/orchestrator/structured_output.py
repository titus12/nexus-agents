"""Adapter for the canonical state-specific machine contracts."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import all_contracts, contract_for_state
from .contracts.common import (
    STRUCTURED_OUTPUT_PROTOCOL,
    PhaseContract,
    empty_payload,
    validate_schema,
)


STRUCTURED_OUTPUT_MODE = "inline"
_ENVELOPE_FIELDS = {
    "action", "contract_id", "task_id", "request_id", "phase", "state", "role", "mode",
    "structured_output_protocol", "structured_output_schema_hash",
}


@dataclass(frozen=True)
class StructuredOutputSpec:
    """Immutable transport contract attached to one Agent request."""

    mode: str
    protocol: str
    schema: dict[str, Any]
    schema_hash: str
    state: str = ""
    role_mode: str = ""

    def to_dict(self) -> dict[str, Any]:
        properties = self.schema.get("properties", {})
        phase = str(properties.get("phase", {}).get("const") or "")
        role = str(properties.get("role", {}).get("const") or "")
        try:
            contract_id = contract_for_state(self.state).contract_id
        except KeyError:
            contract_id = ""
        return {
            "mode": self.mode,
            "protocol": self.protocol,
            "schema": copy.deepcopy(self.schema),
            "schema_hash": self.schema_hash,
            "phase": phase,
            "role": role,
            "state": self.state,
            "role_mode": self.role_mode,
            "contract_id": contract_id,
            "stable_fields": stable_role_fields(phase, role),
            "role_modes": role_modes(phase, role),
        }


def _matching_contracts(phase: str, role: str) -> list[PhaseContract]:
    normalized_phase = str(phase).upper()
    normalized_role = str(role)
    return [
        contract
        for contract in all_contracts()
        if contract.phase == normalized_phase and contract.role == normalized_role
    ]


def _contract_for(
    phase: str,
    role: str,
    *,
    state: str = "",
    context: Mapping[str, Any] | None = None,
) -> PhaseContract | None:
    context = context or {}
    requested_state = str(
        state or context.get("active_runtime_state") or context.get("target_state") or ""
    ).upper()
    if requested_state:
        try:
            candidate = contract_for_state(requested_state)
        except KeyError:
            candidate = None
        if candidate is not None and candidate.phase == str(phase).upper() and candidate.role == str(role):
            return candidate
    candidates = _matching_contracts(phase, role)
    return candidates[0] if candidates else None


def stable_role_fields(phase: str, role: str) -> list[str]:
    contract = _contract_for(phase, role)
    if contract is None:
        return []
    return [field for field in contract.required_fields if field not in _ENVELOPE_FIELDS]


def role_modes(phase: str, role: str) -> list[str]:
    modes: list[str] = []
    for contract in _matching_contracts(phase, role):
        for mode in contract.modes:
            if mode not in modes:
                modes.append(mode)
    return modes


def role_states(phase: str, role: str) -> list[str]:
    return [contract.state for contract in _matching_contracts(phase, role)]


def state_actions(state: str) -> list[str]:
    try:
        return list(contract_for_state(state).actions)
    except KeyError:
        return []


def role_mode_for(
    phase: str,
    role: str,
    context: Mapping[str, Any] | None = None,
) -> str:
    contract = _contract_for(phase, role, context=context)
    if contract is None:
        return ""
    context = context or {}
    if contract.state == "ZHONGSHU_ANALYST":
        if context.get("contract_mode") or str(context.get("zhongshu_dispatch_mode") or "") == "requirement_contract":
            return "REQUIREMENT_CONTRACT_ONLY"
        if str(context.get("zhongshu_dispatch_mode") or "") == "evidence_supplement":
            return "EVIDENCE_SUPPLEMENT"
        return "EVIDENCE_COLLECTION_READ_ONLY"
    if contract.state == "ZHONGSHU_SOLVER":
        return "TASK_GRAPH_FORMALIZATION_READ_ONLY_RESUME" if context.get("solver_resume_mode") else "TASK_GRAPH_FORMALIZATION_READ_ONLY"
    if contract.state == "ZHONGSHU_CRITIC":
        return "REVIEW_ONE_TASK" if str(context.get("zhongshu_dispatch_mode") or "") == "task_review" else "REVIEW_CURRENT_TASK_GRAPH"
    return contract.modes[0] if contract.modes else ""


def _schema_for(
    phase: str,
    role: str,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    contract = _contract_for(phase, role, context=context)
    if contract is None:
        return None
    schema = copy.deepcopy(contract.schema)
    _bind_solver_requirement_schema(schema, context or {})
    return schema


def _bind_solver_requirement_schema(
    schema: dict[str, Any],
    context: Mapping[str, Any],
) -> None:
    """Bind Solver requirement references to the canonical Analyst contract."""
    state = str(
        context.get("active_runtime_state")
        or context.get("target_state")
        or ""
    ).upper()
    if state != "ZHONGSHU_SOLVER":
        return
    analyst_plan = context.get("analyst_plan")
    if not isinstance(analyst_plan, Mapping):
        return
    requirements = analyst_plan.get("requirements")
    if not isinstance(requirements, list):
        return
    requirement_ids = [
        str(item.get("requirement_id") or "").strip()
        for item in requirements
        if isinstance(item, Mapping) and str(item.get("requirement_id") or "").strip()
    ]
    if len(requirement_ids) != len(requirements) or len(set(requirement_ids)) != len(requirement_ids):
        return
    plan_schema = schema.get("properties", {}).get("plan")
    if not isinstance(plan_schema, Mapping):
        return
    requirements_schema = plan_schema.get("properties", {}).get("requirements")
    if not isinstance(requirements_schema, dict):
        return
    requirements_schema["minItems"] = len(requirement_ids)
    requirements_schema["maxItems"] = len(requirement_ids)
    requirement_schema = requirements_schema.get("items")
    if not isinstance(requirement_schema, dict):
        return
    properties = requirement_schema.get("properties")
    if not isinstance(properties, dict):
        return
    requirement_id_schema = properties.get("requirement_id")
    if isinstance(requirement_id_schema, dict):
        requirement_id_schema["enum"] = requirement_ids


def build_structured_output_spec(
    phase: str,
    role: str,
    context: dict[str, Any] | None = None,
) -> StructuredOutputSpec | None:
    """Build the schema that the selected state validator will enforce."""
    context = context or {}
    contract = _contract_for(phase, role, context=context)
    if contract is None:
        return None
    schema = copy.deepcopy(contract.schema)
    _bind_solver_requirement_schema(schema, context)
    encoded = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    state = str(context.get("active_runtime_state") or context.get("target_state") or contract.state)
    return StructuredOutputSpec(
        mode=STRUCTURED_OUTPUT_MODE,
        protocol=STRUCTURED_OUTPUT_PROTOCOL,
        schema=schema,
        schema_hash=hashlib.sha256(encoded).hexdigest(),
        state=state,
        role_mode=role_mode_for(phase, role, context),
    )


def role_result_template(
    phase: str,
    role: str,
    *,
    task_id: str = "",
    request_id: str = "",
    state: str = "",
    role_mode: str = "",
    schema_hash: str = "",
    action: str = "",
) -> dict[str, Any]:
    """Return a complete role envelope generated from one state contract."""
    contract = _contract_for(phase, role, state=state)
    if contract is None:
        return {}
    template = empty_payload(contract.schema)
    template.update(
        {
            "action": action,
            "contract_id": contract.contract_id,
            "task_id": task_id,
            "request_id": request_id,
            "phase": contract.phase,
            "state": state or contract.state,
            "role": contract.role,
            "mode": role_mode or contract.modes[0],
            "structured_output_protocol": STRUCTURED_OUTPUT_PROTOCOL,
            "structured_output_schema_hash": schema_hash or "<exact hash supplied in prompt_ref>",
        }
    )
    return template


def validate_role_result_shape(
    payload: dict[str, Any],
    *,
    phase: str,
    role: str,
    state: str = "",
    role_mode: str = "",
    expected_schema_hash: str = "",
    schema: Mapping[str, Any] | None = None,
) -> str:
    """Validate the selected contract before business-state validation."""
    contract = _contract_for(phase, role, state=state)
    if contract is None:
        return "STRUCTURED_ROLE_UNSUPPORTED"
    missing = sorted(key for key in contract.required_fields if key not in payload)
    if missing:
        return "STRUCTURED_ROLE_FIELDS_MISSING:" + ",".join(missing)
    if str(payload.get("phase") or "").upper() != contract.phase:
        return "STRUCTURED_ROLE_PHASE_MISMATCH"
    if str(payload.get("contract_id") or "") != contract.contract_id:
        return "STRUCTURED_ROLE_CONTRACT_ID_MISMATCH"
    if str(payload.get("role") or "") != contract.role:
        return "STRUCTURED_ROLE_ROLE_MISMATCH"
    if state and str(payload.get("state") or "") != state:
        return "STRUCTURED_ROLE_STATE_MISMATCH"
    if role_mode and str(payload.get("mode") or "") != role_mode:
        return "STRUCTURED_ROLE_MODE_MISMATCH"
    if str(payload.get("structured_output_protocol") or "") != STRUCTURED_OUTPUT_PROTOCOL:
        return "STRUCTURED_ROLE_PROTOCOL_MISMATCH"
    actual_schema_hash = str(payload.get("structured_output_schema_hash") or "")
    if len(actual_schema_hash) != 64 or any(char not in "0123456789abcdef" for char in actual_schema_hash.lower()):
        return "STRUCTURED_ROLE_SCHEMA_HASH_INVALID"
    if expected_schema_hash and actual_schema_hash != expected_schema_hash:
        return "STRUCTURED_ROLE_SCHEMA_HASH_MISMATCH"
    if not isinstance(payload.get("action"), str) or not payload["action"]:
        return "STRUCTURED_ROLE_ACTION_MISSING"
    errors: list[str] = []
    active_schema = schema if isinstance(schema, Mapping) else contract.schema
    validate_schema(payload, active_schema, "", errors)
    if errors:
        return "STRUCTURED_ROLE_CONTRACT_INVALID:" + ";".join(errors[:20])
    return ""
