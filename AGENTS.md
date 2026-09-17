<!-- OPENWIKI:START -->

## OpenWiki

This repository uses OpenWiki for recurring code documentation. Start with `openwiki/quickstart.md`, then follow its links to architecture, workflows, domain concepts, operations, integrations, testing guidance, and source maps.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->

## Local verification

- When running automated tests, smoke checks, or other validation for this repository, use a separate port from the active Nexus service. Keep the current conversation's Nexus instance available on its configured port (normally `8766`); use another port such as `18766` for the validation instance instead of stopping or rebinding the active service.
- Always run the Python test suite through `python cmd/run_tests.py` (or set `NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS=1`) instead of a bare `python -m unittest`. The environment exports real `FEISHU_*` credentials and `HUMAN_GATE_CHAT_ID`, so tests that build an `OrchestratorApp` would otherwise post real messages to the Feishu chat. The runner forces the in-process `NullNotificationPort` and makes `FeishuHttpAdapter` refuse to send.
- Validate workflow mechanisms with the scripted-adapter tests (`test_linear_fsm_entrypoint.py`, `test_zhongshu_convergence_e2e.py`): they drive the real `OrchestratorApp` in seconds with zero model tokens and cover the freeze, the approval ratchet, deferral, stalls, and the follow-up freeze. Reserve live runs for questions a script cannot answer (model behaviour), because each live round re-reads a ~100k-token prompt bundle per worker and a run can spend hundreds of millions of prompt tokens before reaching the same conclusion.
