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
from pathlib import Path

from ..domain.decisions import EffectRequest
from .effects import EffectOutcome
from .ports import ArtifactInput, ArtifactPort


logger = logging.getLogger("review_orchestrator_fsm")


def _safe_component(value: str) -> str:
    component = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip(" .")
    return component or "plan"


class PlanArtifactRunner:
    """Write the canonical plan JSON without producing a domain event."""

    def __init__(
        self,
        artifacts: ArtifactPort | None = None,
        *,
        task_root: str | Path | None = None,
    ) -> None:
        self._artifacts = artifacts
        self._task_root = Path(task_root) if task_root is not None else None

    def run_once(self, request: EffectRequest) -> EffectOutcome:
        if request.effect_type == "plan_review_doc":
            return self._write_review_doc(request)
        return self._write_plan_artifact(request)

    def _write_review_doc(self, request: EffectRequest) -> EffectOutcome:
        """Persist the human-facing plan-review document at a stable path.

        The document is the Zhongshu hand-off artifact for the human gate, so
        it lands at ``<task_root>/zhongshu-plan-review.md`` -- stable and easy
        to open -- instead of the hash-prefixed provenance artifact names.
        """

        content = str(request.payload.get("content") or "")
        if not content.strip():
            return EffectOutcome(status="SUCCEEDED")
        root = self._task_root
        if root is None:
            # Fallback: derive the task root from the provenance artifact
            # layout (runs/<task_id>/artifacts/...).
            receipt = self._write_artifact(
                request.task_id, "plan-review/zhongshu-plan-review.md", content.encode("utf-8")
            )
            if receipt is not None:
                root = Path(receipt.artifact_id).parent.parent
        if root is None:
            logger.warning(
                "ZHONGSHU_PLAN_DOC_WRITE_SKIPPED task_id=%s reason=no_task_root",
                request.task_id,
            )
            return EffectOutcome(status="SUCCEEDED")
        try:
            root.mkdir(parents=True, exist_ok=True)
            path = root / "zhongshu-plan-review.md"
            path.write_text(content, encoding="utf-8")
        except Exception:
            # A provenance write must never wedge the review workflow.
            logger.exception("ZHONGSHU_PLAN_DOC_WRITE_FAILED task_id=%s", request.task_id)
            return EffectOutcome(status="SUCCEEDED")
        logger.info(
            "ZHONGSHU_PLAN_DOC_WRITTEN task_id=%s path=%s bytes=%s",
            request.task_id,
            path,
            len(content.encode("utf-8")),
        )
        return EffectOutcome(status="SUCCEEDED")

    def _write_artifact(
        self, task_id: str, name: str, content: bytes
    ) -> object | None:
        if self._artifacts is None:
            return None
        try:
            return self._artifacts.write(
                ArtifactInput(task_id=task_id, name=name, content=content)
            )
        except Exception:
            logger.exception("PLAN_ARTIFACT_WRITE_FAILED task_id=%s", task_id)
            return None

    def _write_plan_artifact(self, request: EffectRequest) -> EffectOutcome:
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
