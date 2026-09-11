# Test-Driven Change Rule

## Scope and precedence

This rule applies to every code-changing task, whether work is performed
directly or through an explicitly selected `wf-*` workflow. It defines the
minimum test-design decision before production implementation. Language and
framework skills may add commands and examples, but must not weaken this rule.

## Necessary Change Scope

For every code-changing task:

1. Change the smallest necessary set of code, tests, and configuration that
   fully satisfies the confirmed objective and remains understandable and
   maintainable.
2. Prefer the option that fully resolves the confirmed cause or contract with
   the lowest total risk. Do not choose a smaller patch when it would create
   temporary branches, duplicated logic, avoidable technical debt, or new
   boundary risks.
3. Do not bundle unrelated refactors, formatting churn, public-API changes,
   dependency changes, or cross-module cleanup.
4. If the necessary scope expands, state the added impact, reason, validation
   plan, and required approval before proceeding.
5. As part of the same change, if post-change review confirms that old code has
   no callers, Unity message or Inspector/UnityEvent binding, serialization,
   reflection, event-registration, or external-contract purpose, and is fully
   replaced by the new logic, remove it and report the reason, evidence, and
   validation. Do not remove merely suspected redundancy; flag it for review.
6. Reuse established project patterns and dependencies. Do not introduce a
   public API, configuration item, or third-party dependency unless the task
   explicitly requires it.

## Change classification

Classify the change before editing production code:

| Classification | Default requirement |
|---|---|
| Behavioral change | Test first |
| Defect fix | Reproduce first, then fix |
| Contract or boundary change | Test at the contract boundary |
| Non-behavioral change | No new test by default |
| Exception | Record substitute validation |

Behavioral changes include domain rules, state transitions, validation, error
semantics, API behavior, persistence behavior, and observable side effects.
Contract changes include public APIs, schemas, external adapters, authorization,
audit records, monitoring, and parsing pipelines.

## Test Design Decision

For a behavioral change, defect fix, contract change, or exception, record:

1. **Behavior and risk:** What observable behavior changes, and what
   regression does the test protect against?
2. **Existing coverage:** Which existing test already protects it? If one
   exists, why extend it instead of adding a duplicate?
3. **Unique value:** If the proposed test is absent, what realistic regression
   could escape detection?
4. **Lowest effective layer:** Why is unit, contract/integration, or end-to-end
   the lowest-cost layer that can expose the risk?

Do not add a test merely to increase coverage. Prefer the smallest test with
unique regression or contract value.

## Test-first execution

When deterministic automation is practical:

1. Write or extend the smallest behavior-based test.
2. Run it and confirm it fails for the expected missing or incorrect behavior.
3. Implement the necessary production change that makes it pass while keeping
   the solution proportionate to the risk.
4. Refactor only within the changed scope while preserving behavior.
5. Run targeted validation first, then broaden validation in proportion to the
   changed package and risk surface.

A defect reproduction becomes a retained regression test by default. A manual
or diagnostic reproduction must become automated unless an exception is
recorded below.

## Test portfolio management

Retain tests that uniquely protect a production defect, public contract, core
domain rule, state transition, authorization/security property, data integrity
property, irreversible side effect, or high-impact path.

Use table-driven or parameterized cases when inputs express the same rule with
the same setup and assertions. Split tests only when business meaning, failure
cause, or diagnostic ownership differs.

Merge or remove tests that are exploratory-only, fully subsumed by a more
direct test, duplicate a lower-level rule without added cross-boundary risk, or
lock in private calls, local variables, incidental ordering, and other
implementation details. When deleting a test, identify the remaining test that
preserves its unique protection or explicitly record the accepted risk.

## Production-code boundary

Test observable behavior, domain state, and intentional boundary contracts.

- Do not add or export test-only methods, entry points, flags, branches, or APIs
  in production code; tests must verify observable behavior through existing
  production boundaries.
- Do not add test-only debug paths.
- Do not abstract every internal collaboration merely to mock it.
- Introduce a dependency boundary only for a real external, unstable,
  expensive, or non-deterministic dependency when it also improves production
  design.
- Use fakes or mocks for external systems, network, filesystem, time, and
  randomness. Verify intentional boundary contracts, not incidental call
  order.

Tests must be deterministic, independent, and free of avoidable real-network
calls, sleep-based timing, and unmanaged randomness.

## Exceptions and non-contract logging

A comment, formatting, non-contract log wording/level/field change, or
behavior-preserving rename normally needs no new test. It must still record no
observable behavior change and run proportionate existing validation.

Logging is a contract when it affects user-visible output, audit/compliance,
alerts, metrics, monitoring, parsing, control flow, errors, or retries. Treat
such changes as behavioral or boundary changes.

For every other exception, record why automation is not appropriate now, the
actual substitute validation, and the trigger for adding an automated test
later. "Too hard to test" is not sufficient evidence.

## Review gate

Reviewers verify:

1. The change classification is correct.
2. A behavior change or defect has a targeted test or a complete exception.
3. Claimed red evidence fails against the pre-change behavior when applicable.
4. The test verifies behavior or a contract rather than implementation detail.
5. Existing tests were reused, extended, merged, or removed deliberately.
6. The test layer is proportionate and not duplicate without cross-boundary
   value.
7. Mocks, fixtures, time, network, and randomness are controlled.
8. A reasonable behavior-preserving refactor would keep the test passing.
9. Actual validation is distinguished from planned or unavailable checks.

Missing evidence is `Insufficient evidence`. Redundant, brittle, or
implementation-coupled tests are review findings even when all checks pass.

## Evidence format

```text
Test decision: new | extend | no new test | exception.
Behavior and risk:
Existing coverage:
Unique protection:
Selected layer:
Red evidence / exception reason:
Actual validation:
Retain, merge, or delete decision:
Follow-up trigger:
```
