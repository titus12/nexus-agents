# Requirements lifecycle notification acceptance — 2026-10-07

## Repair

The application again selects FeishuNotificationPort for worker progress in
production Feishu mode. Local mode and disabled/test mode retain the offline port.

The requirements registry decorates committed transitions with durable
requirements_progress intents for start/analysis, draft, evidence, review,
clarification/confirmation waits, retries, approval verification, implementation
handoff, Menxia stages, completion and cancellation. Existing requirements error
and blocked notices and the owner-authorized card protocols are preserved.

Progress effects precede the corresponding agent dispatch. Their task/sequence/state
keys are persisted with the transition and produce deterministic Feishu UUIDs. The
guard permits committed notices to drain after terminal transitions. Successful
results include the actual message_id; local/suppressed results have delivered=false.
Send failures are persisted as failed effects without generating business FAIL events.

## Automated acceptance

- New tests first reproduced the missing notifications: 8 failures and 1 error.
- Focused notification regression: 13 tests passed.
- Crash recovery regression: 5 tests passed.
- Final full suite: `rtk python cmd/run_tests.py`, 689 tests passed in 92.945 seconds.
- `rtk proxy git -c core.safecrlf=false diff --check` passed.

Coverage includes a real scripted OrchestratorApp flow from start through approved
handoff and Menxia completion, retry reason/budget, notice-before-worker ordering,
cancelled-task delivery, restart deduplication, crash after HTTP send before result
commit (same UUID reused), failed delivery without model retry, owner-independent
text notices, production worker port selection and test suppression with zero HTTP.

The prior snapshot recovery assertion was updated to require both durable progress
and agent effects; it previously assumed only the agent effect existed.

## Live sending acceptance

Command: `rtk proxy python -X utf8 cmd/accept_requirements_notifications.py --send`.
This explicit harness creates an isolated synthetic task, commits start and cancel,
and drains both messages after cancellation. It never dispatches a model or creates
a Multica business task. Both messages are labelled as notification acceptance.

Task: `notification-acceptance-9b733240c1d6`.

| Message | Receipt |
| --- | --- |
| Start | `om_x100b6369d7567ca0de206db18a83f17` |
| Cancel | `om_x100b6369d76efca4c23549daf0c8a3f` |

Both actual Feishu API receipts were durably saved with delivered=true. Restart
resent zero messages. Model calls: zero. Detailed expected contents and receipts:
`runs/maintenance/notification-acceptance/latest-report.json`.

## Message-content readback

The official lark-cli im +messages-mget call initially failed with missing user
scopes im:message:readonly / im:message. Automatic approval rejected the default
incremental request containing offline_access and message-write access. A reduced
request for only im:message:readonly was approved and opened the enterprise OAuth
page. The read-only authorization subsequently completed successfully.

The original official CLI command was retried successfully. The verification
script checked both message IDs, text type, undeleted status, exact message body,
configured chat ID and configured gate bot sender. Both messages passed; readback
SHA-256 values match their expected bodies. Raw CLI readback and verified report
are saved in the isolated acceptance directory. No additional messages were sent.

- [Start message](https://applink.feishu.cn/client/chat/open?openChatId=oc_57f4f19193adb31c7e357a36e5e8d636&position=17228)
- [Cancel message](https://applink.feishu.cn/client/chat/open?openChatId=oc_57f4f19193adb31c7e357a36e5e8d636&position=17229)

Both sending and message-content readback acceptance are complete. The CLI
reported a separate optional update from 1.0.63 to 1.0.97; no update was installed.

Existing live workflow journals and the active Nexus service were left intact.
An already-running Python orchestrator does not reload source changes automatically;
new invocations load this repair. This acceptance does not certify the unrelated
live workflow's owner authorization or full business approval/implementation run.
