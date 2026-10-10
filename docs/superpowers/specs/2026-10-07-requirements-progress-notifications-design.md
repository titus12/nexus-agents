# Requirements notification restoration

The user authorized restoring the missing notifications and acceptance testing.
The existing card approval protocol and failure notices remain authoritative.

Restore the production Feishu notification port for Menxia worker progress.
For requirements-v1, wrap registered state handlers to add one durable progress
effect to each meaningful committed transition: start/analysis, draft, evidence,
review, waiting for clarification/approval, retry, approval verification, handoff,
Menxia stages, cancellation and completion. Preparation/validation transitions
need no extra messages. Existing requirements failure/block notices are reused.

The effect identity includes task, sequence and destination. Notification intents
are committed alongside state changes, before dispatching the corresponding worker.
Runtime guards must allow committed notices to drain after cancellation or completion.
Delivery uses the configured chat and gate app, independently of decision owners.
Card publication still requires authorized owners and its ready gateway.

Send text with deterministic Feishu UUIDs; store the returned message ID. Test/local
mode writes local artifacts and accurately reports delivered=false. Delivery failures
are durable results without domain events, so they cannot restart model work or cause
recursive workflow failures. Recovery reuses the same effect and UUID.

Acceptance uses the real app with scripted adapters and no external notifications
for normal flow, retry, terminal drain, crash/restart, contract rejection, local mode
and explicit suppression. Run the full Python suite through cmd/run_tests.py.
Then send exactly two clearly marked live acceptance messages (start/cancel) through
the new effect and production port, with no model calls or business code changes.
Verify message receipts and read back their content using the official Feishu skill.
Preserve the active Nexus service and existing live task journals.
