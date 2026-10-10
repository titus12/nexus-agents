# Retry alignment repair plan

Goal: report all independently detectable assumption permission and file-evidence defects together, and expose agent-write permissions in the dispatched schema and rules.

Preserve the state enum: an existing accepted assumption may be echoed unchanged. New or previously unaccepted assumptions may not become accepted through a model reply. Match the immutable dispatch consensus by assumption_id, never topic or alias. Keep the domain commit guard as defense in depth. Precheck permission independently of file evidence, with exact indexed disposition paths and actionable fixes. Never rewrite returned fields or weaken quotation validation.

- [x] Add real failed-reply fixture and regression tests for mixed quote/permission failures, exact repair feedback, one-round correction, and existing accepted decisions.
- [x] Add assumption permission precheck plus dispatched field descriptions and prompt rules. Improve repair guidance and local analyst skill instructions.
- [x] Run guarded focused tests and Python suite (742 passed), document results, and recover only the latest idle failed task using a verified runtime fingerprint change. Reload idle waiting runners.
