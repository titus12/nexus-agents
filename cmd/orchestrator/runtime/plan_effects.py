"""Persist the orchestrator-owned canonical plan as a durable artifact.

Materialization happens in the pure domain layer, so the canonical plan is
already carried by the workflow state.  This runner is a *sink* effect: it
writes one immutable artifact for provenance/auditing and intentionally returns
no follow-up domain event, so it never advances the FSM.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging
import re

from ..domain.decisions import EffectRequest
from .effects import EffectOutcome
from .ports import ArtifactInput, ArtifactPort


logger = logging.getLogger("review_orchestrator_fsm")


def _safe_component(value: str) -> str:
    component = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip(" .")
    return component or "plan"


class PlanArtifactRunner:
    """Write the canonical plan JSON without producing a domain event."""

    def __init__(self, artifacts: ArtifactPort | None = None) -> None:
        self._artifacts = artifacts

    def run_once(self, request: EffectRequest) -> EffectOutcome:
        payload = request.payload
        plan = payload.get("plan")
        if self._artifacts is None or not isinstance(plan, Mapping):
            return EffectOutcome(status="SUCCEEDED")
        revision_id = str(payload.get("revision_id") or request.idempotency_key)
        try:
            content = json.dumps(
                plan, ensure_ascii=False, sort_keys=True
            ).encode("utf-8")
            receipt = self._artifacts.write(
                ArtifactInput(
                    task_id=request.task_id,
                    name=f"plan/{_safe_component(revision_id)}.json",
                    content=content,
                )
            )
        except Exception:
            # A provenance write must never wedge the review workflow.
            logger.exception("PLAN_ARTIFACT_WRITE_FAILED task_id=%s", request.task_id)
            return EffectOutcome(status="SUCCEEDED")
        logger.info(
            "PLAN_ARTIFACT_WRITTEN task_id=%s plan_hash=%s artifact_id=%s bytes=%s",
            request.task_id,
            payload.get("plan_hash") or "",
            receipt.artifact_id,
            len(content),
        )
        return EffectOutcome(status="SUCCEEDED")


__all__ = ["PlanArtifactRunner"]
