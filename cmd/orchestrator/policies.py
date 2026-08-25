from __future__ import annotations

from dataclasses import dataclass

from .context import StateContext


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int
    backoff_seconds: tuple[int, ...] = (1, 3, 9)
    jitter: bool = True
    escalation_target: str = "MULTICA_ERROR"

    def delay_for(self, retry_number: int) -> int:
        if retry_number <= 0:
            return 0
        return self.backoff_seconds[min(retry_number - 1, len(self.backoff_seconds) - 1)]


DEFAULT_RETRY_POLICIES = {
    "multica_dispatch": RetryPolicy(3),
    "multica_poll": RetryPolicy(5),
    "feishu_send": RetryPolicy(3, escalation_target="HUMAN_GATE_ERROR"),
    "feishu_poll": RetryPolicy(5, escalation_target="HUMAN_GATE_TIMEOUT"),
    "state_write": RetryPolicy(3, jitter=False, escalation_target="STATE_CORRUPTED"),
}


def normalize_critic_action(ctx: StateContext, action: str) -> str:
    if action == "BLOCKED" and ctx.active_finding_ids and ctx.zhongshu_revision_round < ctx.max_zhongshu_revision_rounds:
        return "REQUEST_SOLVER_REVISION"
    if action == "APPROVE_FREEZE" and ctx.active_finding_ids:
        return "REQUEST_SOLVER_REVISION"
    return action
