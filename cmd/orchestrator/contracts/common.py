"""Shared primitives for state-specific Orchestrator result contracts."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


STRUCTURED_OUTPUT_PROTOCOL = "nexus-agent-result-file-v2"


def string(*, enum: Sequence[str] | None = None, const: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "string"}
    if enum is not None:
        result["enum"] = list(enum)
    if const is not None:
        result["const"] = const
    return result


def nullable_string() -> dict[str, Any]:
    return {"type": ["string", "null"]}


def array(
    items: dict[str, Any] | None = None,
    *,
    min_items: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "array"}
    if items is not None:
        result["items"] = items
    if min_items is not None:
        result["minItems"] = min_items
    return result


def object_schema(
    properties: Mapping[str, dict[str, Any]] | None = None,
    *,
    required: Sequence[str] = (),
    nullable: bool = False,
    forbidden: Sequence[str] = (),
) -> dict[str, Any]:
    result: dict[str, Any] = {"type": ["object", "null"] if nullable else "object"}
    if properties is not None:
        result["properties"] = dict(properties)
    if required:
        result["required"] = list(required)
    if forbidden:
        result["forbiddenProperties"] = list(forbidden)
    return result


def any_value() -> dict[str, Any]:
    return {}


def contract_schema(
    *,
    contract_id: str,
    state: str,
    phase: str,
    role: str,
    modes: Sequence[str],
    actions: Sequence[str],
    fields: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    properties: dict[str, dict[str, Any]] = {
        "action": string(enum=actions),
        "contract_id": string(const=contract_id),
        "task_id": string(),
        "request_id": string(),
        "phase": string(const=phase),
        "state": string(const=state),
        "role": string(const=role),
        "mode": string(enum=modes),
        "structured_output_protocol": string(const=STRUCTURED_OUTPUT_PROTOCOL),
        "structured_output_schema_hash": {
            "type": "string",
            "minLength": 64,
            "maxLength": 64,
        },
        **dict(fields),
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": list(properties),
        "properties": properties,
        "additionalProperties": True,
        "title": f"Nexus {state} role result",
    }


def _type_matches(value: Any, expected: str | list[str]) -> bool:
    expected_types = expected if isinstance(expected, list) else [expected]
    for expected_type in expected_types:
        if expected_type == "null" and value is None:
            return True
        if expected_type == "string" and isinstance(value, str):
            return True
        if expected_type == "array" and isinstance(value, list):
            return True
        if expected_type == "object" and isinstance(value, dict):
            return True
        if expected_type == "boolean" and isinstance(value, bool):
            return True
        if expected_type == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if expected_type == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
    return False


def validate_schema(value: Any, schema: Mapping[str, Any], path: str, errors: list[str]) -> None:
    expected = schema.get("type")
    if expected is not None and not _type_matches(value, expected):
        errors.append(f"{path}: expected {expected}")
        return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']}")
    enum = schema.get("enum")
    if enum is not None and value not in enum:
        errors.append(f"{path}: invalid enum")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            errors.append(f"{path}: shorter than minLength")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            errors.append(f"{path}: longer than maxLength")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            errors.append(f"{path}: fewer than minItems")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                item_path = f"{path}[{index}]" if path else f"[{index}]"
                validate_schema(item, item_schema, item_path, errors)
    if isinstance(value, dict):
        for key in schema.get("required", ()):
            if key not in value:
                errors.append(f"{path + '.' if path else ''}{key}: required")
        for key in schema.get("forbiddenProperties", ()):
            if key in value:
                errors.append(f"{path + '.' if path else ''}{key}: forbidden")
        properties = schema.get("properties", {})
        if isinstance(properties, Mapping):
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, Mapping):
                    child_path = f"{path + '.' if path else ''}{key}"
                    validate_schema(value[key], child_schema, child_path, errors)


def empty_payload(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Create a prompt template with every required field represented."""
    result: dict[str, Any] = {}
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return result
    for key, child in properties.items():
        if not isinstance(child, Mapping):
            continue
        result[key] = _empty_value(child)
    return result


def _empty_value(schema: Mapping[str, Any]) -> Any:
    expected = schema.get("type")
    if isinstance(expected, list) and "null" in expected:
        return None
    if expected == "array":
        return []
    if expected == "object":
        result: dict[str, Any] = {}
        properties = schema.get("properties", {})
        if isinstance(properties, Mapping):
            for key, child in properties.items():
                if key in schema.get("required", ()) and isinstance(child, Mapping):
                    result[key] = _empty_value(child)
        return result
    if "const" in schema:
        return copy.deepcopy(schema["const"])
    if schema.get("minLength") == 64 and schema.get("maxLength") == 64:
        return "0" * 64
    enum = schema.get("enum")
    if enum:
        return copy.deepcopy(enum[0])
    if expected == "boolean":
        return False
    if expected in {"number", "integer"}:
        return 0
    return ""


@dataclass(frozen=True)
class PhaseContract:
    contract_id: str
    state: str
    phase: str
    role: str
    modes: tuple[str, ...]
    actions: tuple[str, ...]
    schema: dict[str, Any]
    prompt_rules: tuple[str, ...]
    required_fields: tuple[str, ...]
    example_payload: Callable[[], dict[str, Any]]

    def validate(self, payload: Mapping[str, Any]) -> tuple[str, ...]:
        errors: list[str] = []
        validate_schema(payload, self.schema, "", errors)
        return tuple(errors)

    def repair_instructions(self, errors: Sequence[str]) -> tuple[str, ...]:
        bounded = tuple(str(error) for error in errors[:20])
        return (
            f"The previous response was rejected by contract {self.contract_id}.",
            "Repair only these paths and return the complete result object:",
            *(f"- {error}" for error in bounded),
            "Do not change analysis, scope, or unrelated fields.",
        )


def make_contract(
    *,
    contract_id: str,
    state: str,
    phase: str,
    role: str,
    modes: Sequence[str],
    actions: Sequence[str],
    fields: Mapping[str, dict[str, Any]],
    prompt_rules: Sequence[str],
    example_overrides: Mapping[str, Any] | None = None,
) -> PhaseContract:
    schema = contract_schema(
        contract_id=contract_id,
        state=state,
        phase=phase,
        role=role,
        modes=modes,
        actions=actions,
        fields=fields,
    )
    template = empty_payload(schema)
    if example_overrides:
        template.update(copy.deepcopy(dict(example_overrides)))
    return PhaseContract(
        contract_id=contract_id,
        state=state,
        phase=phase,
        role=role,
        modes=tuple(modes),
        actions=tuple(actions),
        schema=schema,
        prompt_rules=tuple(prompt_rules),
        required_fields=tuple(schema["required"]),
        example_payload=lambda: copy.deepcopy(template),
    )
