"""Closed transition graph for the linear review workflow.

This module is deliberately independent from the runtime and transport layers.
It contains only the names and guards that define which workflow transitions
are legal.  In particular, callers cannot supply an arbitrary target state.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from .errors import InvariantViolation


class TransitionError(InvariantViolation):
    """Raised when a workflow action or target is outside the closed graph."""

    error_code = "INVALID_TRANSITION"


BUSINESS_STATES = (
    "REQUEST_INTAKE",
    "ZHONGSHU_ANALYST",
    "ZHONGSHU_SOLVER",
    "ZHONGSHU_CRITIC",
    "ZHONGSHU_FREEZE_CHECK",
    "MENXIA_ITEM_SOLVER",
    "MENXIA_ITEM_ANALYST",
    "MENXIA_ITEM_CRITIC",
    "MENXIA_GROUP_GATE",
    "DONE",
)

SYSTEM_STATES = (
    "HUMAN_GATE",
    "RETRY_WAIT",
    "BLOCKED",
    "CANCELLED",
    "FAILED",
    "PERSISTENCE_DEGRADED",
)

ALL_STATES = BUSINESS_STATES + SYSTEM_STATES
TERMINAL_STATES = ("DONE", "CANCELLED", "FAILED")
RESUMABLE_BUSINESS_STATES = BUSINESS_STATES[:-1]
RECOVERABLE_SYSTEM_STATES = (
    "HUMAN_GATE",
    "RETRY_WAIT",
    "BLOCKED",
    "PERSISTENCE_DEGRADED",
)


_MAINLINE_EDGES = (
    ("REQUEST_INTAKE", "START", "ZHONGSHU_ANALYST"),
    ("ZHONGSHU_ANALYST", "READY_FOR_SOLVER", "ZHONGSHU_SOLVER"),
    ("ZHONGSHU_SOLVER", "READY_FOR_CRITIC", "ZHONGSHU_CRITIC"),
    ("ZHONGSHU_CRITIC", "APPROVE_CRITIC", "ZHONGSHU_FREEZE_CHECK"),
    ("ZHONGSHU_FREEZE_CHECK", "FREEZE_APPROVED", "MENXIA_ITEM_SOLVER"),
    ("MENXIA_ITEM_SOLVER", "READY_FOR_ANALYST", "MENXIA_ITEM_ANALYST"),
    ("MENXIA_ITEM_ANALYST", "READY_FOR_CRITIC", "MENXIA_ITEM_CRITIC"),
    ("MENXIA_ITEM_CRITIC", "APPROVE_ITEM", "MENXIA_GROUP_GATE"),
    ("MENXIA_GROUP_GATE", "COMPLETE", "DONE"),
)


_ROLE_EDGES = (
    ("REQUEST_INTAKE", "HUMAN_GATE", "HUMAN_GATE"),
    ("REQUEST_INTAKE", "BLOCKED", "BLOCKED"),
    ("ZHONGSHU_ANALYST", "EVIDENCE_PACKET_READY", "ZHONGSHU_SOLVER"),
    ("ZHONGSHU_ANALYST", "REQUIREMENT_CONTRACT_READY", "ZHONGSHU_SOLVER"),
    ("ZHONGSHU_ANALYST", "EVIDENCE_SUPPLEMENT_READY", "ZHONGSHU_SOLVER"),
    ("ZHONGSHU_SOLVER", "REQUEST_ANALYST_EVIDENCE", "ZHONGSHU_ANALYST"),
    ("ZHONGSHU_SOLVER", "NEEDS_MORE_EVIDENCE", "ZHONGSHU_ANALYST"),
    ("ZHONGSHU_CRITIC", "REQUEST_ANALYST_EVIDENCE", "ZHONGSHU_ANALYST"),
    ("ZHONGSHU_CRITIC", "REQUEST_SOLVER_REVISION", "ZHONGSHU_SOLVER"),
    ("ZHONGSHU_CRITIC", "REQUEST_REGROUP", "ZHONGSHU_SOLVER"),
    ("ZHONGSHU_CRITIC", "TASK_APPROVED", "ZHONGSHU_FREEZE_CHECK"),
    ("ZHONGSHU_CRITIC", "TASK_CHANGES_REQUIRED", "ZHONGSHU_SOLVER"),
    ("ZHONGSHU_FREEZE_CHECK", "APPROVE_FREEZE", "MENXIA_ITEM_SOLVER"),
    ("ZHONGSHU_FREEZE_CHECK", "FREEZE_OK", "MENXIA_ITEM_SOLVER"),
    ("ZHONGSHU_FREEZE_CHECK", "FREEZE_REJECTED", "ZHONGSHU_SOLVER"),
    ("MENXIA_ITEM_SOLVER", "FEASIBLE", "MENXIA_ITEM_ANALYST"),
    ("MENXIA_ITEM_SOLVER", "READY_FOR_CRITIC", "MENXIA_ITEM_CRITIC"),
    ("MENXIA_ITEM_ANALYST", "EVIDENCE_SUFFICIENT", "MENXIA_ITEM_CRITIC"),
    ("MENXIA_ITEM_ANALYST", "NEEDS_MORE_EVIDENCE", "MENXIA_ITEM_SOLVER"),
    ("MENXIA_ITEM_ANALYST", "REQUEST_SOLVER_REVISION", "MENXIA_ITEM_SOLVER"),
    ("MENXIA_ITEM_CRITIC", "REVISE_ITEM", "MENXIA_ITEM_SOLVER"),
    ("MENXIA_ITEM_CRITIC", "SPLIT_ITEM", "MENXIA_ITEM_SOLVER"),
    ("MENXIA_ITEM_CRITIC", "MERGE_ITEM", "MENXIA_ITEM_SOLVER"),
    ("MENXIA_ITEM_CRITIC", "REMOVE_ITEM", "MENXIA_ITEM_SOLVER"),
    ("MENXIA_ITEM_CRITIC", "REQUEST_SOLVER_REVISION", "MENXIA_ITEM_SOLVER"),
    ("MENXIA_GROUP_GATE", "APPROVE_GROUP", "DONE"),
    ("MENXIA_GROUP_GATE", "NEXT_ITEM", "MENXIA_ITEM_SOLVER"),
    ("MENXIA_GROUP_GATE", "APPROVE_FREEZE", "DONE"),
    ("MENXIA_GROUP_GATE", "REQUEST_GROUP_REVISION", "MENXIA_ITEM_SOLVER"),
)


class TransitionRegistry:
    """Immutable lookup table for legal workflow transitions.

    Static edges are stored as exact source/action pairs.  Recovery from a
    system state is the only variable-target operation and is still closed:
    the supplied target must be the recorded ``resume_state`` and must be a
    business state.  The registry never accepts a caller-provided arbitrary
    target for a normal transition.
    """

    def __init__(
        self,
        transitions: Mapping[tuple[str, str], str],
        resume_sources: tuple[str, ...] = RECOVERABLE_SYSTEM_STATES,
    ) -> None:
        self._transitions = MappingProxyType(dict(transitions))
        self._resume_sources = tuple(resume_sources)
        self._validate_graph()

    @classmethod
    def default(cls) -> "TransitionRegistry":
        """Build and validate the production linear graph."""

        transitions: dict[tuple[str, str], str] = {
            (source, action): target
            for source, action, target in _MAINLINE_EDGES
        }
        transitions.update(
            {(source, action): target for source, action, target in _ROLE_EDGES}
        )

        nonterminal_states = tuple(
            state for state in ALL_STATES if state not in TERMINAL_STATES
        )
        for source in nonterminal_states:
            transitions[(source, "CANCEL")] = "CANCELLED"
            transitions[(source, "FAIL")] = "FAILED"

        for source in BUSINESS_STATES[:-1]:
            transitions[(source, "RETRY")] = "RETRY_WAIT"
            transitions[(source, "BLOCK")] = "BLOCKED"
            transitions[(source, "OPEN_HUMAN_GATE")] = "HUMAN_GATE"
            transitions[(source, "HUMAN_GATE")] = "HUMAN_GATE"
            transitions[(source, "BLOCKED")] = "BLOCKED"

        return cls(transitions)

    @property
    def transitions(self) -> Mapping[tuple[str, str], str]:
        """Expose a read-only view for diagnostics and contract tests."""

        return self._transitions

    def target_for(
        self,
        source: str,
        action: str,
        resume_state: str | None = None,
    ) -> str:
        """Resolve one legal target or raise ``TransitionError``.

        ``resume_state`` is accepted only for the explicit ``RESUME`` action
        from a recoverable system state.  It is not a general target override.
        """

        self._validate_state_name(source, "source")
        if not isinstance(action, str) or not action:
            raise TransitionError("transition action must be a non-empty string")

        if action == "RESUME" and source in self._resume_sources:
            if resume_state is None:
                raise TransitionError(
                    f"{source} recovery requires an explicit resume_state"
                )
            self.validate_resume_state(source, resume_state)
            return resume_state

        if resume_state is not None:
            raise TransitionError(
                "resume_state is only valid for RESUME from a recoverable system state"
            )

        target = self._transitions.get((source, action))
        if target is None:
            raise TransitionError(
                f"unknown transition action {action!r} from {source!r}"
            )
        self.validate_target(source, target)
        return target

    def validate_target(self, source: str, target: str) -> None:
        """Validate a target allowed from ``source``.

        For recoverable system states, business states are valid only as the
        separately validated recorded resume target.  This method does not
        turn arbitrary caller input into a transition.
        """

        self._validate_state_name(source, "source")
        self._validate_state_name(target, "target")

        allowed_targets = {
            registered_target
            for (registered_source, _), registered_target in self._transitions.items()
            if registered_source == source
        }
        if source in self._resume_sources:
            if target in allowed_targets:
                return
            if target in RESUMABLE_BUSINESS_STATES:
                return
            raise TransitionError(
                f"system state {source!r} can resume only to a business state "
                "or a registered terminal boundary"
            )
        if target not in allowed_targets:
            raise TransitionError(
                f"target {target!r} is not registered from source {source!r}"
            )

    def validate_resume_state(self, source: str, resume_state: str) -> None:
        """Validate the recorded recovery destination for a system state."""

        self._validate_state_name(source, "source")
        if source not in self._resume_sources:
            raise TransitionError(f"state {source!r} is not recoverable")
        self._validate_state_name(resume_state, "resume_state")
        if resume_state not in RESUMABLE_BUSINESS_STATES:
            raise TransitionError(
                f"resume_state {resume_state!r} must be a business state"
            )

    @staticmethod
    def _validate_state_name(state: str, field_name: str) -> None:
        if state not in ALL_STATES:
            raise TransitionError(f"unknown {field_name} {state!r}")

    def _validate_graph(self) -> None:
        for (source, action), target in self._transitions.items():
            self._validate_state_name(source, "source")
            if not isinstance(action, str) or not action:
                raise TransitionError("registered action must be a non-empty string")
            self._validate_state_name(target, "target")

        for source, action, target in _MAINLINE_EDGES:
            if self._transitions.get((source, action)) != target:
                raise TransitionError(
                    f"missing or invalid mainline edge: {source} + {action} -> {target}"
                )

        for source in self._resume_sources:
            if source not in SYSTEM_STATES:
                raise TransitionError(
                    f"resume source {source!r} must be a declared system state"
                )


__all__ = [
    "ALL_STATES",
    "BUSINESS_STATES",
    "RECOVERABLE_SYSTEM_STATES",
    "RESUMABLE_BUSINESS_STATES",
    "SYSTEM_STATES",
    "TERMINAL_STATES",
    "TransitionError",
    "TransitionRegistry",
]
