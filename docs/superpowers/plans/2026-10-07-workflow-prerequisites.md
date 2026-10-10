# Workflow prerequisite repair implementation plan

**Goal:** Prevent late readiness failures and make empty-owner tasks recoverable without rerunning analysis.

**Architecture:** CLI preflight before transport, typed audited owner update, existing bounded recovery path.

**Tech Stack:** Python unittest, immutable FSM, SQLite gateway.

- [x] Add CLI prerequisite checks and no-dispatch readiness mode; test zero transport calls.
- [x] Add narrowly scoped empty-owner recovery with immutable reducer guard and journal replay tests.
- [x] Validate gateway readiness, focused tests and full suite through cmd/run_tests.py.
- [x] Record business task recovery separately from automated mechanism acceptance; actual recovery awaits user-selected owner.
