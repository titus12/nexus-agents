# Unity Quality Rubric

Use this rubric for Unity reviewers, testers, asset evaluators, and workflow evaluators. It controls local workflow gates; it is not a Nexus TaskRun scoring payload.

## QualityResult

Return one structured result:

```text
role: <reviewer | tester | asset-evaluator | workflow-evaluator>
planItemIds: [P1, P2]
checks:
  - id: <stable check id>
    required: true | false
    severity: blocker | major | minor | info
    status: passed | failed | skipped | manual_pending
    expected: <observable condition>
    actual: <observed result>
    evidence: [<command + exit code>, <file:line>, <Console result>, <manual steps>]
summary:
  requiredChecks: <integer>
  passedChecks: <integer>
  skippedRequiredChecks: <integer>
  blockerFindings: <integer>
  majorFindings: <integer>
  minorFindings: <integer>
  manualAcceptancePending: true | false
  qualityScore: <0-100>
decision: pass | repair | awaiting_user_acceptance | blocked
```

## Severity and Gate Rules

| Severity | Meaning | Required workflow action |
|---|---|---|
| blocker | Compile failure; touched-flow Console exception; failed required plan item; missing required serialized reference; generated/.meta/asset safety violation; unapproved scope change | `repair` or `blocked`; never pass |
| major | Required behavior mismatch, targeted regression, lifecycle/async/data-flow defect, or failed required test | `repair`; never pass |
| minor | Non-blocking maintainability, performance, diagnostic-cleanup, or low-risk edge concern | Record follow-up; does not block by itself |
| info | Observation with no action required | Record only |

Calculate `qualityScore` as `max(0, 100 - 50*blockerFindings - 20*majorFindings - 5*minorFindings - 10*skippedRequiredChecks)`. The score is diagnostic only: a high score never overrides a blocker or major finding.

A quality result is `pass` only when:

```text
blockerFindings == 0
majorFindings == 0
skippedRequiredChecks == 0
passedChecks == requiredChecks
manualAcceptancePending == false
all required checks passed
```

If manual acceptance is the only remaining required evidence, return `awaiting_user_acceptance`; if required evidence cannot be obtained and no new diagnostic path exists, return `blocked`.
