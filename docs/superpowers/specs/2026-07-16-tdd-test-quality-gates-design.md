# TDD and Test Quality Gates Design

**Date:** 2026-07-16  
**Status:** Approved design; awaiting user review before implementation  
**Scope:** All code-changing work performed through generated Nexus project
templates, including direct edits and explicitly invoked workflows.

## Goal

Make test-first development the default for behavior changes while preventing
redundant, implementation-coupled, or test-only production design. Preserve
small, high-value tests as long-lived regression assets and give reviewers
evidence to assess test quality rather than test quantity.

## Non-goals

- Enforcing a global line-coverage or changed-line-coverage target.
- Requiring new automated tests for every file edit.
- Adding test-only public APIs, production test flags, or a universal mocking
  framework.
- Repeating the same rules in every language-specific workflow.

## Design Summary

The implementation will establish one project-wide **Test-Driven Change
Baseline** in the generated `AGENTS.md`, with a detailed shared rule in
`.claude/rules/`. Feature, bug-fix, and review workflows will consume the
shared rule and add only workflow-specific gates. Existing Go guidance will be
updated to translate the baseline into Go-specific test patterns.

The policy measures the value of a test by its unique regression protection:

> A retained test must protect a behavior, risk, or contract that would
> otherwise be reasonably likely to regress without being detected.

Tests are not counted as progress merely because they increase coverage,
exercise a private method, or duplicate a higher/lower-level test.

## Change Classification

Before editing implementation code, the developer classifies the proposed
change and records the smallest applicable test-design decision.

| Classification | Default requirement | Examples |
|---|---|---|
| Behavioral change | Test first | New business rule, changed state transition, validation, error semantics, API or persistence behavior |
| Defect fix | Reproduce first, then fix | Regression, incorrect result, crash, data inconsistency |
| Contract or boundary change | Test at the contract boundary | Public API, message schema, external adapter, authorization, monitoring or audit output |
| Non-behavioral change | No new test by default | Comments, formatting, non-contract log wording/level/fields, safe renames |
| Exception | Recorded substitute validation | Non-deterministic environment, external-system limitation, temporary diagnostic work |

Logging is non-behavioral only when it does not alter a user-visible output,
audit or compliance record, alert/monitoring contract, parsing pipeline,
control flow, error behavior, retry behavior, or metrics contract. A logging
change with any of those effects is a behavioral or boundary change.

## Test Design Decision

For every behavioral change, defect fix, contract change, or recorded
exception, the change description must answer these four questions before
implementation:

1. **Behavior and risk:** What observable behavior changes, and what regression
   does the test protect against?
2. **Existing coverage:** Which existing test already protects this behavior,
   if any? If one exists, why is extending it preferable to adding a duplicate?
3. **Unique value:** If the proposed test is absent, what realistic regression
   could escape detection?
4. **Lowest effective layer:** Why is the selected unit, contract/integration,
   or end-to-end layer the lowest-cost layer that can expose the risk?

The answers are brief evidence, not a second design document. A workflow task
capsule and review capsule gain a `Test design` section with these fields.

## TDD Execution Model

For changes that can be automated deterministically:

1. Identify the changed behavior and the smallest effective verification layer.
2. Write or extend the minimal test that expresses the behavior.
3. Run it and verify it fails for the expected missing or incorrect behavior.
4. Implement only enough production code to make the test pass.
5. Refactor production and test code while preserving the behavioral contract.
6. Run the targeted test, then broaden validation in proportion to the changed
   package and risk surface.
7. Classify the resulting test as a retained regression/contract asset or an
   exploratory verification that must be removed before delivery.

For a defect fix, the failing reproduction may initially be a reliable manual
path, but it must become an automated regression test unless an approved
exception applies.

## Test Portfolio Rules

### Retain

Keep tests that provide a unique, durable protection for:

- A previously observed production defect.
- A public API, schema, protocol, or cross-module contract.
- Core domain rules, state transitions, and important boundary behavior.
- Authorization, security, data integrity, financial/economic, or irreversible
  side effects.
- A high-frequency user path or a failure mode with material impact.

### Prefer extending or parameterizing

Use table-driven or parameterized tests when cases share one behavior rule and
the same setup/verification shape. Split cases only when failure diagnosis,
business semantics, or ownership differs materially.

### Remove or merge

Remove or merge a test when it is:

- An exploratory/debugging-only probe with no lasting regression value.
- Fully subsumed by a more direct test at the same layer.
- A duplicate of a lower-level rule with no additional cross-boundary risk.
- Coupled solely to private calls, local variables, incidental ordering, or
  other implementation details.

Deletion of a test requires the same short explanation as addition: identify
the remaining test or validation that preserves its unique protection, or
explicitly acknowledge the accepted risk.

## Isolation and Production-Code Boundaries

Tests verify externally observable behavior, domain state, or intentional
boundary contracts. They must not drive unnecessary production-code
abstractions.

- Do not export a production API solely for tests.
- Do not add `if test` branches, test-only runtime flags, or production debug
  paths.
- Do not abstract every internal dependency behind an interface merely to mock
  it.
- Introduce an adapter or injected dependency only at a real unstable,
  expensive, non-deterministic, or external boundary and only when it also
  improves the production design.
- Prefer real lightweight implementations for internal collaboration. Use fakes
  or mocks for network, clock, randomness, filesystem, external service, and
  similarly uncontrollable boundaries.
- Mock assertions must verify an intentional boundary contract, not an
  incidental internal call sequence.

Tests must be deterministic, independent, order-insensitive where practical,
parallel-safe where supported, and free of avoidable real-network calls,
sleep-based timing, and unmanaged randomness.

## Exception Protocol

An exception is allowed, but it must never silently become a path around test
design. The change description records:

1. Why an automated test is not appropriate or feasible now.
2. Whether observable behavior changes and why the normal TDD path cannot
   cover it.
3. The substitute validation actually performed.
4. The trigger for adding an automated test later, if applicable.

For a purely non-contract logging change, the compact record is:

```text
Test decision: no new test.
Reason: non-contract logging only; no control-flow or observable behavior change.
Validation: <actual targeted check>.
Follow-up trigger: none.
```

A reviewer may reject an exception that lacks evidence or uses a vague reason
such as "too hard to test."

## Review Quality Gate

Code review adds a dedicated test-design review in addition to logic,
performance, and security review. The reviewer verifies:

1. The intended change was classified correctly.
2. A behavior change or defect has a targeted test, or has a complete approved
   exception record.
3. The new or changed test fails on the pre-change behavior when that claim is
   applicable and evidence is available.
4. The test verifies a contract or observable behavior rather than an
   implementation detail.
5. Existing tests were reused, extended, merged, or removed deliberately; no
   redundant same-layer test suite was added.
6. The chosen layer is proportionate to the risk and does not duplicate a
   lower-level rule without cross-boundary value.
7. Mocks, fixtures, timing, external dependencies, and randomness do not make
   the test brittle or non-deterministic.
8. A reasonable production refactor preserving behavior would still pass the
   test.
9. The validation evidence distinguishes commands actually run from planned or
   unavailable checks.

Missing required test-design evidence is an `Insufficient evidence` review
outcome. A duplicate or implementation-coupled test is a review finding even
when all tests pass.

## Template Integration

The following template changes will implement the design without duplicating
the policy:

| File | Responsibility |
|---|---|
| `templates/AGENTS.md` | Add the mandatory project-wide Test-Driven Change Baseline for every code-changing task, including direct edits. |
| `templates/.claude/rules/test-driven-change.md` (new) | Define classification, four-question test decision, test portfolio, isolation, exception, and review rules. |
| `templates/.claude/skills/go-testing/SKILL.md` | Align Go-specific testing guidance with the shared policy: table-driven tests, boundary selection, deterministic fakes, and actual-evidence reporting. |
| `templates/.claude/workflows/go-feature-development.md` | Require test design before implementation, red-green evidence for applicable behavior changes, and retained/exploratory test classification. |
| `templates/.claude/workflows/go-bugfix.md` | Tighten the existing regression-test step with the exception protocol and long-term retention rule. |
| `templates/.claude/workflows/go-code-review.md` | Add test-design evidence to the review context and test-quality questions to the quality gate. |
| `templates/.claude/agents/go-worker.md` | Make implementation capsules require the test-design decision and targeted test evidence when applicable. |
| `templates/.claude/agents/go-reviewer-logic.md` | Add a focused checklist for behavior-based, non-duplicative, deterministic tests and exception evidence. |

`AGENTS.md` and the new shared rule make the policy apply to every current and
future code workflow, including Unity and non-Go work, without forcing Go
commands or test-framework assumptions into other stacks. Language-specific
skills and workflows may add commands and examples but may not weaken the
baseline.

The template synchronization policy remains unchanged: these edits occur in
the authoritative `templates/` tree. Imported projects receive the updated
files only when a user explicitly chooses the existing template-sync action.

## Acceptance Criteria

The implementation is complete when:

1. Generated projects have one unambiguous, project-wide TDD/test-quality
   baseline that applies to all code changes.
2. The baseline explicitly exempts non-contract logging changes while defining
   when logging is a contract that needs tests.
3. Feature changes and bug fixes require a minimal test-design decision before
   implementation, with a documented exception path.
4. Reviewers can reject redundant, implementation-coupled, non-deterministic,
   or inadequately justified test changes using an explicit checklist.
5. The policy explicitly supports retaining, parameterizing, merging, and
   deleting tests based on unique regression value.
6. No test-first rule requires production-only-for-testing APIs, flags, or
   architecture.
7. Existing Go-specific material remains consistent with the generic policy.

## Verification Strategy

Because this is template documentation and workflow configuration, verification
will include:

- Inspecting every changed template file for a single consistent rule source.
- Searching templates for conflicting "test" instructions and reconciling
  them.
- Checking Markdown and YAML/TOML syntax where applicable.
- Running the repository's targeted Go tests plus `go test ./...` and the web
  build only if the implementation changes executable project code or existing
  verification conventions require it.
- Using a fixture or temporary copied template tree, if the existing test
  suite has a suitable template-sync test seam, to confirm the new template
  files remain eligible for synchronization.

## Self-review

- **Scope:** The policy applies to every code-changing task, while examples
  remain Go-specific only where current templates have Go-specific roles.
- **No redundancy incentive:** There is no coverage threshold; the unique-value
  test decision and review gate require consolidation of duplicates.
- **No production intrusion:** The rule explicitly disallows test-only APIs,
  flags, and abstractions while permitting real boundary design.
- **Exceptions:** Non-contract logging is handled directly; all other
  exceptions require reason, actual substitute validation, and a future test
  trigger.
- **Consistency:** A shared source carries the normative policy; workflows and
  agents add contextual gates rather than copied policy text.
