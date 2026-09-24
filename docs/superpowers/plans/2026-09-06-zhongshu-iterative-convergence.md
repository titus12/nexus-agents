# Zhongshu iterative convergence

## Goal

Make the Zhongshu Solver/Critic loop converge in bounded rounds. A Solver
revision may resolve only a bounded batch of active findings, but it must
declare the exact remainder so the orchestrator never mistakes omission for
resolution.

## Design

1. Keep the role-specific Solver response stable and add a required
   `finding_batch` envelope for revision responses:
   `selected_finding_ids`, `remaining_finding_ids`, `next_action`, and
   `progress`. The orchestrator chooses a deterministic focus by
   ownership/severity priority and applies a deterministic maximum count
   (currently six); each
   selected Finding remains whole and is never character-truncated.
2. Validate that the selected and remaining IDs partition the active Critic
   requirements. Reject unknown, duplicate, overlapping, or silently omitted
   findings with an explicit contract error.
3. Materialize the current plan plus typed changes, and route the declared
   remainder by explicit ownership. Analyst-owned findings go to evidence;
   Solver-owned findings return to Solver; unresolved human ownership goes to
   the human gate.
4. Preserve the raw Critic finding IDs and observations while consolidating
   exact semantic duplicates into one canonical finding for quorum and
   lifecycle accounting. Worker provenance remains visible.
5. Update the Solver runtime contract, prompt projection, structured role
   schema, and focused pure regression coverage. The self-test must not start
   the service or call notification adapters.

## Verification

Run one focused pure-Python self-test covering canonical finding
consolidation, valid partial batches, rejected omissions, and ownership-based
routing. Do not run the orchestrator or send Feishu notifications.
