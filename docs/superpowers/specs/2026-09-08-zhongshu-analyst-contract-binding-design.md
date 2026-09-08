# Zhongshu Analyst Requirement Contract Binding Design

## Goal

Keep the requirement contract authoritative and stable when Analyst workers
produce evidence packets, including `acceptance_signal`, without weakening
validation or allowing an Analyst to redefine requirements.

## Context

The requirement-contract Analyst creates the canonical requirement list. The
parallel evidence Analysts consume that list and return evidence only. The
current prompt explicitly preserves `requirement_ids` and `statements`, but
the fan-in validator also compares `source`, `priority`, `scope`, `kind`, and
`acceptance_signal`. A model may therefore paraphrase an immutable field and
be rejected even though the evidence itself is usable.

## Design

1. Treat the requirement contract produced by the contract worker as the sole
   source of truth. For every evidence packet, the orchestrator binds the
   canonical requirement objects, including `acceptance_signal`, instead of
   relying on the model to reproduce their text exactly.
2. Strengthen the Analyst prompt and response example to say that all
   immutable fields (`requirement_id`, `statement`, `source`, `priority`,
   `scope`, `kind`, and `acceptance_signal`) must be copied verbatim and in
   canonical order. The Analyst may add evidence fields but may not alter the
   contract.
3. Before binding, require the returned requirement IDs to match the
   canonical ID set exactly, with no duplicates. Missing, extra, or unknown
   IDs remain hard failures; no binding may hide a lost or invented
   requirement.
4. When IDs are valid but immutable field values differ or are omitted, replace
   only the requirement list with a deep copy of the canonical list and emit a
   structured diagnostic identifying the worker and affected field. Evidence
   updates continue to come from the Analyst unchanged.
5. Keep the existing strict validator as a final invariant check after
   binding. This preserves detection of malformed packets and prevents a
   malformed contract from reaching Solver.

## Data flow

```text
canonical requirement contract
        |
        +--> Analyst prompt/schema (verbatim-copy instruction)
        |
Analyst evidence packet
        |
        +--> validate IDs (exact set, no duplicates)
        |
        +--> bind canonical requirement objects
        |
        +--> strict evidence-packet validation
        |
        +--> Solver evidence context
```

## Error handling

- Invalid, duplicate, missing, or extra requirement IDs: reject the worker
  result and preserve the existing retry/quorum behavior.
- Valid IDs with changed immutable fields: deterministically repair the
  requirement list, record the repair, and continue.
- Evidence updates, unknowns, conflicts, and source references are never
  silently discarded or rewritten by this binding step.
- No notification behavior changes are included in this fix.

## Verification

Add focused regression coverage for:

- an Analyst packet whose `acceptance_signal` is shortened being accepted with
  the canonical signal restored;
- a packet with a missing or extra requirement ID remaining rejected;
- a packet with valid IDs and distinct evidence updates preserving all updates;
- the prompt/schema containing the verbatim-copy rule for every immutable
  contract field.

The change must not run the live orchestrator or send Feishu notifications.
