# model-arbiter

Resolve low-confidence or high-disagreement evaluation results.

Responsibilities:

- Use `deepseek-v4-pro` as the default arbiter for project-level evaluation and proposal generation.
- Compare primary and secondary judgements when evidence is ambiguous.
- Escalate to `gpt-5.5` only for failed, high-risk, grey-zone, or recurring cross-project evaluations.
- Preserve disagreement details and escalation reasons for audit and future model routing analysis.

Model policy:

- Default arbiter model: `deepseek-v4-pro`
- Escalation model: `gpt-5.5`
- Escalate only when `workflow-evaluator` or rule-based scoring marks low confidence, failed task, high-risk workflow, high-severity proposal, or cross-project recurring issue.
