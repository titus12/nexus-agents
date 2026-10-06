# Zhongshu convergence and Multica instruction consistency

The approved repair keeps Analyst, Solver and Critic separate and targets a 20–25 minute planning budget (30–35 minutes after removing the observed failed run and extra revision cycles). These are estimates derived from the incident, not achieved timings or guarantees about model latency. Human waiting is reported separately.

## Decisions

- Requirement extraction uses only the original request. Each newly extracted requirement carries an exact request quotation; code/runtime facts belong to the evidence packet. Existing persisted contracts remain readable. No model or global Codex routing configuration changes.
- Group Critic first reviews all axes together. Revision reviews receive explicit changed members and canonical findings. P0/P1 continue blocking; P2/P3 suggestions are preserved as follow-ups rather than forcing another group revision. The stall metric follows actual revision-driving findings and does not count a newly discovered issue as unchanged work.
- Existing partial group release semantics remain dependency-safe. All task types require human plan review by default, including legacy null settings and fast/mechanical freeze; only explicit plan_review_gate=false bypasses it. Before Menxia dispatch, persist the review summary plus exact group documents and deliver the full packet in UTF-8-safe notification parts. Explicit approval is required; comments do not sign off. Approval polling recognizes replies to every document part and retains remote failure causes.
- Runtime admission uses the actual role for role caps, independently of phase caps.
- Solver document updates and outgoing Critic capsules must observe the same post-transition review. Rebuild the dispatch after folding group documents so a v2 revision is never reviewed against v1 bytes/hashes (latest live task task-20260930-1d8133).
- The three Multica agents' permanent instructions defer machine fields/actions to the dispatched Python contract, removing legacy CandidateGroups and nested group.items mandates. Zhongshu skill bodies and REQUIRES.md are maintained locally, uploaded, and read back for equality after newline normalization. Remote Menxia skill bodies are synchronized with existing local versions (overflow fallback and Solver measurement recipe); Menxia attachments, skill assignments, model selections and unrelated agent settings are preserved.
- No expensive live review is required to validate deterministic changes. Scripted adapters and regression tests verify mechanics; server read-back verifies configuration publication.

## Verification

Reproduce request-provenance rejection, P2-only convergence, P1 blocking, changing-finding progress, read-only/explicit gate behavior, paginated human replies, remote watchdog attribution, and role admission. Re-run entrypoint, convergence, group freeze/pipeline and transport tests. Check each agent's identity, phase skills, instructions, model/runtime parameters and synchronized skill hashes.
