# Requirements result validation design

The user authorized a systematic repair after three saved model responses exposed schema/domain mismatch. Keep current checkout and existing task records.

The model schema shares numeric revision bounds with the Question domain record. Role prompts describe response-wide local-key uniqueness and require repairing all reported paths in one response. Pure preflight checks collect per-record, identity, reference, dependency and human-quote failures; the runtime additionally checks all independently readable file evidence. Valid analysis is dry-run against the immutable dispatched state to preserve existing authority invariants before domain commit.

Rejected transport JSON is inspected only for diagnostics: task/request/state/role must match, paths must resolve inside the configured runs root and be size bounded. A rejected transport result cannot become success. Persisted failure feedback lists indexed paths, and logs contain counts rather than raw model/source content.

Keep two repair attempts, exact contiguous evidence matching and human approvals. Verified code recovery creates a fresh work epoch/request and restores the failed analysis stage with the previous failure/result reference; it does not skip analysis because the original request is pending.

Acceptance uses a portable fixture extracted from the actual failed response, scripted real OrchestratorApp repair/recovery tests, and the complete isolated-notification Python suite. Live task recovery is separate evidence and may still require human clarification or document authorization.
