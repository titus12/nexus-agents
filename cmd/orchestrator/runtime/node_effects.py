"""Bridge a durable node effect to the bounded node executor."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
import logging
from typing import Callable

from ..domain.decisions import EffectRequest
from ..domain.errors import FailureRecord, InvariantViolation
from ..zhongshu_review_queue import queue_from_dispatch_contexts
from .effects import EffectOutcome
from .nodes import (
    NodeContext,
    NodeExecutor,
    NodeResult,
    ReviewNode,
    WorkerBinding,
)


logger = logging.getLogger("review_orchestrator_fsm")


class NodeEffectRunner:
    """Execute one immutable ReviewNode described by an effect payload."""

    def __init__(
        self,
        executor: NodeExecutor | None = None,
        *,
        executor_factory: Callable[[ReviewNode, NodeContext], NodeExecutor] | None = None,
    ) -> None:
        if (executor is None) == (executor_factory is None):
            raise ValueError("provide exactly one node executor or executor_factory")
        self._executor = executor
        self._executor_factory = executor_factory

    def run_once(self, request: EffectRequest) -> EffectOutcome:
        payload = request.payload
        node = self._node(payload, request)
        revision_id = str(payload.get("revision_id") or request.idempotency_key)
        plan_hash = str(payload.get("plan_hash") or "")
        dispatch_mode = str(payload.get("dispatch_mode") or "")
        raw_requirements = payload.get("canonical_requirements")
        canonical_requirements = (
            tuple(dict(item) for item in raw_requirements if isinstance(item, Mapping))
            if isinstance(raw_requirements, (list, tuple))
            else ()
        )
        review_queue = None
        if dispatch_mode == "task_review":
            review_queue = queue_from_dispatch_contexts(
                (binding.dispatch_context for binding in node.bindings),
                revision_id,
                plan_hash,
            )
            logger.info(
                "NODE_TASK_REVIEW_QUEUE task_id=%s node_run_id=%s jobs=%s",
                request.task_id,
                node.node_run_id,
                len(review_queue.jobs) if review_queue is not None else 0,
            )
        context = NodeContext(
            task_id=request.task_id,
            node_run_id=node.node_run_id,
            revision_id=revision_id,
            group_id=str(payload["group_id"]) if payload.get("group_id") else None,
            item_id=str(payload["item_id"]) if payload.get("item_id") else None,
            state=str(payload.get("state") or ""),
            sequence=int(payload.get("sequence") or 0),
            prompt_ref=str(payload.get("prompt_ref") or request.payload_ref or ""),
            issue_id=str(payload.get("issue_id") or ""),
            plan_hash=plan_hash,
            dispatch_mode=dispatch_mode,
            review_queue=review_queue,
            canonical_requirements=canonical_requirements,
        )
        executor = (
            self._executor_factory(node, context)
            if self._executor_factory is not None
            else self._executor
        )
        assert executor is not None
        result = executor.execute(node, context)
        if result.status != "SUCCEEDED":
            failure = result.failure or FailureRecord(
                failure_id=f"{request.effect_id}:NODE_FAILED",
                stage="node",
                owner_component="node_executor",
                task_id=request.task_id,
                state=context.state,
                sequence=context.sequence,
                node_run_id=node.node_run_id,
                worker_id=None,
                effect_id=request.effect_id,
                error_code="NODE_FAILED",
                retryable=False,
                message="node execution failed",
                cause_type="NodeResult",
            )
            failed_payload = (
                dict(result.aggregate)
                if isinstance(result.aggregate, Mapping)
                else {}
            )
            return EffectOutcome(
                status="FAILED",
                event_name="FAIL",
                event_payload={
                    # Carry any per-task verdicts gathered before the failure so
                    # the retry only re-dispatches the tasks still missing.
                    **failed_payload,
                    "node_run_id": node.node_run_id,
                    "retryable": failure.retryable,
                },
                failure=failure,
            )
        aggregate_payload = (
            dict(result.aggregate) if isinstance(result.aggregate, Mapping) else {}
        )
        return EffectOutcome(
            status="SUCCEEDED",
            event_name="NODE_COMPLETED",
            event_payload={
                **aggregate_payload,
                "node_run_id": node.node_run_id,
                "worker_results": [asdict(worker) for worker in result.worker_results],
                "aggregate": result.aggregate,
                "action": aggregate_payload.get("action"),
                "group_id": payload.get("group_id") or aggregate_payload.get("group_id"),
                "item_id": payload.get("item_id") or aggregate_payload.get("item_id"),
                "revision_id": payload.get("revision_id") or aggregate_payload.get("revision_id"),
                "plan_hash": payload.get("plan_hash") or aggregate_payload.get("plan_hash"),
            },
        )

    @staticmethod
    def _node(payload: Mapping[str, object], request: EffectRequest) -> ReviewNode:
        node_run_id = str(payload.get("node_run_id") or request.effect_id)
        phase = str(payload.get("phase") or "")
        raw_bindings = payload.get("bindings")
        if not isinstance(raw_bindings, (list, tuple)) or not raw_bindings:
            raise InvariantViolation("node effect requires at least one worker binding")
        bindings = []
        for raw in raw_bindings:
            if not isinstance(raw, Mapping):
                raise InvariantViolation("node worker binding must be an object")
            dispatch_context = raw.get("dispatch_context")
            bindings.append(
                WorkerBinding(
                    worker_id=str(raw.get("worker_id") or ""),
                    agent_id=str(raw.get("agent_id") or ""),
                    task_id=str(raw.get("task_id") or request.task_id),
                    request_id=str(raw.get("request_id") or ""),
                    role=str(raw.get("role") or ""),
                    phase=str(raw.get("phase") or phase),
                    group_id=(
                        str(raw["group_id"]) if raw.get("group_id") else None
                    ),
                    item_id=(str(raw["item_id"]) if raw.get("item_id") else None),
                    prompt_ref=str(raw.get("prompt_ref") or ""),
                    issue_id=str(raw.get("issue_id") or ""),
                    fanout_parent_id=str(raw.get("fanout_parent_id") or ""),
                    fanout_title=str(raw.get("fanout_title") or ""),
                    dispatch_context=(
                        dict(dispatch_context)
                        if isinstance(dispatch_context, Mapping)
                        else {}
                    ),
                )
            )
        return ReviewNode(node_run_id, phase, tuple(bindings))


__all__ = ["NodeEffectRunner"]
