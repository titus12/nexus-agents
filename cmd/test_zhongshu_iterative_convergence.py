"""Pure regression checks for the Zhongshu bounded-convergence protocol.

This module intentionally imports no application runner or notification
adapter.  It can be executed locally without dispatching an Agent or sending
Feishu messages.
"""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

from orchestrator.states import (  # noqa: E402
    _solver_pending_resolution_entries,
    _solver_revision_resolutions_error,
    _solver_unresolved_finding_ids,
)
from orchestrator.zhongshu_review import (  # noqa: E402
    aggregate_reviews,
    repair_route,
)


def _requirements() -> list[dict[str, object]]:
    return [
        {
            "finding_id": f"finding-{index:02d}",
            "status": "OPEN",
            "severity": "P1",
            "owner_role": "review-solver",
            "next_action": "REQUEST_SOLVER_REVISION",
        }
        for index in range(8)
    ]


def _partial_solver_payload() -> dict[str, object]:
    selected = [
        "finding-00", "finding-01", "finding-02",
        "finding-03", "finding-04", "finding-05",
    ]
    remaining = ["finding-06", "finding-07"]
    return {
        "action": "READY_FOR_CRITIC",
        "finding_resolutions": [
            {
                "finding_id": finding_id,
                "status": "resolved",
                "response": "bounded graph correction",
                "owner_role": "review-solver",
                "next_action": "READY_FOR_CRITIC",
            }
            for finding_id in selected
        ],
        "finding_batch": {
            "selected_finding_ids": selected,
            "remaining_finding_ids": remaining,
            "next_action": "READY_FOR_CRITIC",
            "progress": {
                "open_before": 8,
                "resolved": 6,
                "remaining": 2,
                "open_after": 2,
            },
        },
    }


def _test_partial_batch_and_explicit_remainder() -> None:
    requirements = _requirements()
    payload = _partial_solver_payload()
    assert _solver_revision_resolutions_error(payload, requirements) == ""
    assert _solver_unresolved_finding_ids(payload, requirements) == [
        "finding-06", "finding-07"
    ]

    missing_payload = _partial_solver_payload()
    missing_payload["finding_batch"] = {
        **missing_payload["finding_batch"],
            "remaining_finding_ids": ["finding-06"],
            "progress": {
                "open_before": 8,
                "resolved": 6,
                "remaining": 1,
                "open_after": 1,
        },
    }
    error = _solver_revision_resolutions_error(missing_payload, requirements)
    assert error.startswith("SOLVER_FINDING_BATCH_COVERAGE_INCOMPLETE:finding-07"), error


def _test_remainder_routes_by_owner() -> None:
    requirements = _requirements()
    requirements[6]["owner_role"] = "review-analyst"
    requirements[6]["next_action"] = "REQUEST_ANALYST_EVIDENCE"
    entries = _solver_pending_resolution_entries(_partial_solver_payload(), requirements)
    assert {str(item["finding_id"]) for item in entries} == {
        "finding-06", "finding-07"
    }
    assert repair_route(entries) == "REQUEST_ANALYST_EVIDENCE"


def _test_duplicate_critic_findings_are_consolidated() -> None:
    results = []
    for index in range(1, 4):
        worker_id = f"critic-{index}"
        results.append({
            "payload": {
                "worker_id": worker_id,
                "logical_request_id": f"request-{index}",
                "revision_id": "revision-1",
                "plan_revision_id": "revision-1",
                "plan_hash": "a" * 64,
                "reviewed_plan_hash": "a" * 64,
                "action": "REQUEST_SOLVER_REVISION",
                "review_summary": f"independent lens {index}",
                "findings": [{
                    "finding_id": f"{worker_id}:runtime-004",
                    "severity": "P1",
                    "category": "scope",
                    "target": "global",
                    "claim": "implementation tasks exceed the approved read-only scope",
                    "required_action": "convert implementation tasks to assessment tasks",
                    "status": "OPEN",
                }],
            }
        })
    review = aggregate_reviews(
        "revision-1",
        "a" * 64,
        results,
        quorum=2,
        expected_workers={"critic-1", "critic-2", "critic-3"},
    )
    assert len(review["findings"]) == 1, review["findings"]
    finding = review["findings"][0]
    assert len(finding["source_finding_ids"]) == 3, finding
    assert finding["source_workers"] == ["critic-1", "critic-2", "critic-3"], finding
    assert finding["quorum_support"] == 3, finding
    assert len(finding["observations"]) == 3, finding


def main() -> None:
    _test_partial_batch_and_explicit_remainder()
    _test_remainder_routes_by_owner()
    _test_duplicate_critic_findings_are_consolidated()
    print("zhongshu iterative convergence self-test: PASS (no notification adapter invoked)")


if __name__ == "__main__":
    main()
