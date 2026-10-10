# Requirements contract and failure notification repair

**Goal:** Prevent schema/domain enum drift and make requirements failures visible in Feishu.

**Design:** Share enum definitions between immutable domain records and generated result schemas. Keep strict rejection rather than guessing model intent. Commit a notification effect when entering REQ_ENGINE_ERROR or REQ_BLOCKED; send a text message with a stable platform UUID, independently of decision-owner card preflight. Record delivery failures without feeding them back into the requirements state machine. Local/test mode never sends externally.

**Validation:** Use the scripted real-app tests and mocked HTTP receipts. Preserve existing worktree changes and the active service on port 8766. Do not resume live model calls or change task decision owners.

- [x] Share record enums and expose them in strict schemas; test every constrained field and the reported invalid values.
- [x] Commit and execute error/block notification effects; test local mode, disabled external sends, delivery rejection, restart deduplication, and missing decision owners.
- [x] Run `python cmd/run_tests.py` for focused modules, then the full Python suite; inspect the final diff.

**Results:** 41 focused tests passed; all 662 Python tests passed in 56.289 seconds. `git diff --check` passed. The recorded third-attempt live result now reports all enum violations at the schema boundary. Feishu delivery is covered with mocked HTTP; no live message or model run was triggered. The original task still needs an explicitly supplied decision owner before confirmation-card publication.
