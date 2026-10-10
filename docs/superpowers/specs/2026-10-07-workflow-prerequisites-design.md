# Workflow prerequisite repair

Latest task b6bcc7 completed analysis, then blocked publishing clarification because owners were empty and no gateway was running. Text-notification acceptance did not validate interactive workflow readiness.

Check Feishu owners, credentials, chat, and a live gateway before constructing Multica transport or creating an issue. Provide a check-only CLI mode with no external mutations. Keep local mode explicit.

Allow an explicit local operator command to fill empty decision owners only on a communication-prerequisite blocked task before any delivered card, answer, or approval. Journal the change through the normal decision/reducer path; do not patch snapshots or reset budgets. Existing owners remain immutable. Verify readiness before accepting recovery, reuse the existing analysis and clarification round.

Acceptance: missing prerequisites spend zero model calls/create zero issues; scripted recovery preserves facts and budgets, survives restart, publishes a card and accepts only configured owners. Check live gateway independently. Actual owner selection requires the user's identity instruction.
