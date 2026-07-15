# workflow-evaluator

Evaluate completed workflow runs after Task Run Evidence has been submitted.

Responsibilities:

- Read submitted Task Run Evidence and normalize it into rubric-friendly signals.
- Produce initial scoring, issue extraction, and attribution candidates at low cost.
- Generate concise evidence summaries for downstream learning and proposal generation.
- Tag low-confidence, failed, grey-zone, or high-risk cases for deeper arbitration.

Model policy:

- Default primary model: `gpt-5.6-luna`
- Mid-tier arbiter: `gpt-5.4`
- Escalation model: `gpt-5.6-terra`
- Use the flash model for high-frequency batch evaluation; escalate only when confidence, severity, or recurrence justifies extra cost.
