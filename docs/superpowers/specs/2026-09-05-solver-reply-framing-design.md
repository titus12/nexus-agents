# Solver Reply Framing Repair Design

## Goal

Prevent a Solver run from being blocked when the Agent returns one complete JSON object followed by the single, unambiguous extra closing brace observed in the latest logs, without accepting truncated, concatenated, or semantically unbound replies.

## Evidence and scope

The latest run repeatedly reported `json_error='Extra data'` at the final character while the reply began and ended with JSON delimiters. The current transport prompt already requires exactly one JSON object, so changing prompt wording alone is not sufficient. The repair is limited to the reply framing boundary; it does not relax task, identity, phase, role, action, or business-schema validation.

## Design

1. Add one shared JSON candidate parser used by both reply extraction and parse diagnostics.
2. Parse the complete candidate strictly first.
3. If strict parsing fails, use `JSONDecoder.raw_decode` to determine whether a complete root object exists at the beginning.
4. Repair only when the remainder is exactly one extra `}` after whitespace normalization. This is the observed deterministic corruption. Never take the first object from concatenated JSON, discard Markdown, or truncate arbitrary suffixes.
5. Keep the original reply in the existing rejection history. Log a repair marker and continue through the existing identity and schema validators.
6. Make the retry feedback explicitly identify a framing failure and require one root JSON object with no surrounding characters.

## Error handling

Replies with multiple JSON documents, incomplete JSON, arbitrary trailing text, Markdown prose, wrong task/request identity, invalid action, or invalid business fields remain rejected. The existing retry limit and blocked-state behavior remain unchanged for genuinely invalid replies.

## Verification

Add focused parser regression cases for BOM-prefixed valid JSON, the observed single extra brace, concatenated JSON rejection, arbitrary suffix rejection, and fenced/non-object content. Do not run the test suite in this change; perform only static inspection as requested.

