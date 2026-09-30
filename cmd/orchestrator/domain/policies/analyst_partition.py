"""Work split of the Critic's open findings across the Analyst fan-out.

When the Critic demands evidence, every Analyst worker used to receive the
same full finding list and had to answer all of it, so N workers repeated one
investigation N times.  The split is deterministic (sorted by finding id,
round-robin) so a retried wave, and the salvage of an earlier wave, see the
same slice per worker.  Workers left without a slice are not dispatched.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..context import ReviewState


def partition_active_findings(
    review: ReviewState | None, worker_count: int
) -> dict[int, tuple[object, ...]]:
    """1-based worker index -> its slice of the open findings.

    Empty when there is nothing to split (no review, no open finding, or a
    single worker): every worker then keeps the whole-wave brief.
    """

    if review is None or worker_count <= 1:
        return {}
    active = sorted(
        (finding for finding in review.findings if finding.active),
        key=lambda finding: str(finding.finding_id),
    )
    if not active:
        return {}
    return {
        index + 1: tuple(active[index::worker_count])
        for index in range(min(worker_count, len(active)))
    }


def expected_analyst_lenses(worker_count: int, active_findings: Iterable[object]) -> int:
    """How many Analyst workers a wave over ``active_findings`` dispatches."""

    open_count = sum(1 for _ in active_findings)
    if worker_count > 1 and open_count:
        return min(worker_count, open_count)
    return worker_count
