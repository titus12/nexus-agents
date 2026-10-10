"""Immutable canonical plan value and verified persistence envelope."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

LEGACY_PLAN_SCHEMA = "legacy-v1"
CANONICAL_PLAN_SCHEMA = "canonical-v1"
PLAN_POLICY_VERSION = "structure-v1"


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PlanRevision:
    number: int
    parent_ref: str
    content_hash: str
    plan_json: str
    schema_version: str = CANONICAL_PLAN_SCHEMA
    policy_version: str = PLAN_POLICY_VERSION

    def __post_init__(self):
        if self.schema_version != CANONICAL_PLAN_SCHEMA or self.policy_version != PLAN_POLICY_VERSION:
            raise ValueError("unsupported PlanRevision schema or policy")
        if type(self.number) is not int or self.number < 1 or not isinstance(self.parent_ref, str):
            raise ValueError("invalid PlanRevision lineage")
        if (self.number == 1 and self.parent_ref) or (self.number > 1 and not re.fullmatch(
                rf"{CANONICAL_PLAN_SCHEMA}:{self.number - 1}:[a-f0-9]{{64}}", self.parent_ref)):
            raise ValueError("invalid PlanRevision parent")
        value = json.loads(self.plan_json)
        if canonical_json(value) != self.plan_json or content_hash(value) != self.content_hash:
            raise ValueError("invalid PlanRevision serialized content")

    @property
    def reference(self):
        return f"{self.schema_version}:{self.number}:{self.content_hash}"

    @property
    def plan(self):
        return json.loads(self.plan_json)

    def to_dict(self):
        return dict(number=self.number, parent_ref=self.parent_ref, content_hash=self.content_hash,
                    plan=self.plan, schema_version=self.schema_version, policy_version=self.policy_version)

    @classmethod
    def from_dict(cls, value, *, approved_requirements=None):
        from .compiler import compile_plan
        if (value.get("schema_version") != CANONICAL_PLAN_SCHEMA
                or value.get("policy_version") != PLAN_POLICY_VERSION):
            raise ValueError("unsupported PlanRevision schema or policy")
        number = value.get("number")
        parent = value.get("parent_ref")
        if type(number) is not int or number < 1 or not isinstance(parent, str):
            raise ValueError("invalid PlanRevision lineage")
        if (number == 1 and parent) or (number > 1 and not parent.startswith(f"{CANONICAL_PLAN_SCHEMA}:{number - 1}:")):
            raise ValueError("invalid PlanRevision parent")
        plan = compile_plan(value.get("plan"),approved_requirements=approved_requirements)
        if plan != value.get("plan") or content_hash(plan) != value.get("content_hash"):
            raise ValueError("PlanRevision content hash or normalization mismatch")
        return cls(number, parent, content_hash(plan), canonical_json(plan))
