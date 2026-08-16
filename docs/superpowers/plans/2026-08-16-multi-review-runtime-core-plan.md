# Multi-Agent Review Runtime Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Implement task-by-task with tests and review each checkpoint before proceeding.

**Goal:** Build a new, isolated v2.1 Runtime Core for the multi-agent solution review protocol without compatibility work for legacy orchestration.

**Architecture:** Add a focused `internal/multireview` Go package containing canonical models, validation, pure state transitions, FrozenPlan rules, and storage interfaces. Add durable persistence, Skill loading, Agent dispatch, HUMAN_GATE, and final plan assembly in later independent slices.

**Tech Stack:** Go 1.26, standard library for the Runtime Core, existing repository test conventions, JSON artifacts matching `docs/multi/schemas/runtime-artifacts.schema.json`.

---

### Task 1: Create the isolated Runtime Core package

**Files:**
- Create: `internal/multireview/model.go`
- Create: `internal/multireview/errors.go`
- Test: `internal/multireview/model_test.go`

- [ ] **Step 1: Write failing tests for canonical values and IDs**

Add table-driven tests covering valid IDs (`task-000001`, `ev-000001`,
`finding-000001`) and invalid legacy IDs (`D1`, `E-001`, `ev-001`).
Assert that every canonical workflow state and Agent action has a stable
string value.

- [ ] **Step 2: Run the focused test**

Run:

```powershell
go test ./internal/multireview -run 'TestCanonical|TestID' -count=1
```

Expected: FAIL because the package and types do not exist.

- [ ] **Step 3: Implement canonical model types**

Define typed string aliases and constants for:

```go
type WorkflowState string
type Actor string
type Action string
type ArtifactType string
type ID string
```

Define the artifact envelope, task state, Finding, Score, FrozenPlan, Group,
Item, and ReviewAmendment structures with JSON tags matching the v2.1
snake_case contracts.

- [ ] **Step 4: Add typed validation errors**

Define package errors for invalid ID, invalid action, invalid transition,
immutable plan mutation, active HUMAN_GATE, unresolved blocking Finding, and
revision budget exhaustion.

- [ ] **Step 5: Run the focused test**

Run the same command and expect PASS.

- [ ] **Step 6: Commit the isolated model**

```powershell
git add internal/multireview/model.go internal/multireview/errors.go internal/multireview/model_test.go
git commit -m "feat: add multi-review canonical runtime model"
```

### Task 2: Implement artifact and domain validation

**Files:**
- Create: `internal/multireview/validation.go`
- Test: `internal/multireview/validation_test.go`

- [ ] **Step 1: Write failing validation tests**

Cover missing envelope fields, wrong schema version, malformed IDs,
unsupported creators, invalid Skill lock hashes, score dimension overflow,
missing Finding owners, and P0/P1 pass-blocking rules.

- [ ] **Step 2: Run the focused test**

Run:

```powershell
go test ./internal/multireview -run 'TestValidate' -count=1
```

Expected: FAIL because validation functions do not exist.

- [ ] **Step 3: Implement deterministic validators**

Implement:

```go
func ValidateID(id ID) error
func ValidateEnvelope(envelope ArtifactEnvelope) error
func ValidateFinding(f Finding) error
func ValidateScore(score Score) error
func ValidateFrozenPlan(plan FrozenPlan) error
func HasBlockingFindings(findings []Finding) bool
```

Reject legacy values instead of normalizing them.

- [ ] **Step 4: Run the focused test**

Expect PASS.

- [ ] **Step 5: Commit validation**

```powershell
git add internal/multireview/validation.go internal/multireview/validation_test.go
git commit -m "feat: validate multi-review artifacts and findings"
```

### Task 3: Implement pure workflow transitions

**Files:**
- Create: `internal/multireview/state_machine.go`
- Test: `internal/multireview/state_machine_test.go`

- [ ] **Step 1: Write the transition table tests**

Test the complete v2.1 path:

```text
REQUEST_INTAKE
PROJECT_ROUTING
ZHONGSHU_ANALYST
ZHONGSHU_SOLVER
ZHONGSHU_CRITIC
ZHONGSHU_FREEZE_CHECK
ZHONGSHU_PLAN_FROZEN
MENXIA_GROUP_START
MENXIA_ITEM_ANALYST
MENXIA_ITEM_SOLVER
MENXIA_ITEM_CRITIC
MENXIA_GROUP_GATE
FINALIZE
DONE
```

Also test illegal actions, HUMAN_GATE pause/resume, BLOCKED behavior, missing
active item/group, and all revision counter limits.

- [ ] **Step 2: Run the focused test**

Run:

```powershell
go test ./internal/multireview -run 'TestTransition|TestRevision' -count=1
```

Expected: FAIL because the transition function does not exist.

- [ ] **Step 3: Implement the pure transition function**

Implement:

```go
type TransitionInput struct {
    State TaskState
    Action Action
    HasActiveDecision bool
    HasActiveGroup bool
    HasActiveItem bool
    HasBlockingFindings bool
}

type TransitionResult struct {
    State TaskState
    Invalidated []ID
}

func Transition(input TransitionInput) (TransitionResult, error)
```

Only this function may calculate the next workflow state. It must increment
material revision counters and reject transitions beyond the configured
budgets.

- [ ] **Step 4: Run the focused test**

Expect PASS.

- [ ] **Step 5: Commit the state machine**

```powershell
git add internal/multireview/state_machine.go internal/multireview/state_machine_test.go
git commit -m "feat: add multi-review workflow state machine"
```

### Task 4: Enforce FrozenPlan and amendment boundaries

**Files:**
- Create: `internal/multireview/plan.go`
- Test: `internal/multireview/plan_test.go`

- [ ] **Step 1: Write failing plan tests**

Verify that a frozen plan cannot be mutated, local amendments reject changes
to group boundaries/public contracts/primary architecture, and dependency
invalidation includes direct dependents, the containing group, and later
dependent groups.

- [ ] **Step 2: Implement plan checks**

Implement:

```go
func FreezePlan(plan FrozenPlan) (FrozenPlan, error)
func ValidateAmendment(plan FrozenPlan, amendment ReviewAmendment) error
func InvalidationScope(plan FrozenPlan, amendment ReviewAmendment) ([]ID, error)
```

- [ ] **Step 3: Run tests and commit**

```powershell
go test ./internal/multireview -run 'TestPlan' -count=1
git add internal/multireview/plan.go internal/multireview/plan_test.go
git commit -m "feat: enforce frozen plan amendment boundaries"
```

### Task 5: Define storage interfaces and in-memory test store

**Files:**
- Create: `internal/multireview/store.go`
- Test: `internal/multireview/store_test.go`

- [ ] **Step 1: Define the store contract**

The interface must atomically persist TaskState, artifact revisions,
transition records, and consumed HUMAN_GATE message IDs:

```go
type Store interface {
    LoadTask(ctx context.Context, taskID ID) (TaskState, error)
    Commit(ctx context.Context, commit Commit) error
    ArtifactHistory(ctx context.Context, taskID ID) ([]ArtifactEnvelope, error)
    IsMessageConsumed(ctx context.Context, taskID ID, messageID string) (bool, error)
}
```

- [ ] **Step 2: Implement an in-memory test double**

The test double must reject duplicate artifact revisions and duplicate message
consumption, and must expose a single commit operation for transition tests.

- [ ] **Step 3: Run tests and commit**

```powershell
go test ./internal/multireview -run 'TestStore' -count=1
git add internal/multireview/store.go internal/multireview/store_test.go
git commit -m "feat: define multi-review atomic store contract"
```

### Task 6: Add durable persistence and restart recovery

**Files:**
- Create: `internal/multireview/sqlite_store.go`
- Create: `internal/multireview/sqlite_store_test.go`
- Create: `internal/multireview/migrations/001_initial.sql`

- [ ] **Step 1: Add crash/restart tests**

Test that a committed transition, artifact, and consumed message survive
closing and reopening the store, while an incomplete commit leaves all three
unchanged.

- [ ] **Step 2: Implement transactional persistence**

Use one transaction for TaskState, artifact revision, transition log, and
message-consumption record. Keep previous artifact revisions and enforce a
unique `(task_id, artifact_id, revision)` key.

- [ ] **Step 3: Run persistence tests**

```powershell
go test ./internal/multireview -run 'TestSQLite' -count=1
```

- [ ] **Step 4: Commit**

```powershell
git add internal/multireview/sqlite_store.go internal/multireview/sqlite_store_test.go internal/multireview/migrations/001_initial.sql
git commit -m "feat: persist multi-review state transactionally"
```

### Task 7: Add versioned Composite Skill and project appendix loading

**Files:**
- Create: `templates/.agents/skills/zhongshu-analyst/SKILL.md`
- Create: `templates/.agents/skills/zhongshu-solver/SKILL.md`
- Create: `templates/.agents/skills/zhongshu-critic/SKILL.md`
- Create: `templates/.agents/skills/menxia-analyst/SKILL.md`
- Create: `templates/.agents/skills/menxia-solver/SKILL.md`
- Create: `templates/.agents/skills/menxia-critic/SKILL.md`
- Create: `templates/.agents/project-appendices/go.md`
- Create: `templates/.agents/project-appendices/dotnet.md`
- Create: `templates/.agents/project-appendices/unity.md`
- Create: `internal/multireview/skill_lock.go`
- Test: `internal/multireview/skill_lock_test.go`

- [ ] **Step 1: Add loader tests**

Verify exactly one composite Skill, exactly one matching project appendix,
optional task overlay, content hash capture, and rejection of missing or
conflicting bundles.

- [ ] **Step 2: Implement content hashing and lock validation**

Persist protocol version, Skill name/version/hash, project appendix
name/version/hash, and task overlay hash in a task-level Skill lock.

- [ ] **Step 3: Run tests and commit**

```powershell
go test ./internal/multireview -run 'TestSkill' -count=1
git add templates/.agents internal/multireview/skill_lock.go internal/multireview/skill_lock_test.go
git commit -m "feat: add versioned multi-review skill bundles"
```

### Task 8: Add new Agent dispatch and HUMAN_GATE adapters

**Files:**
- Create: `internal/multireview/dispatcher.go`
- Create: `internal/multireview/human_gate.go`
- Test: `internal/multireview/dispatcher_test.go`
- Test: `internal/multireview/human_gate_test.go`

- [ ] **Step 1: Define adapter interfaces**

Keep external calls behind:

```go
type AgentDispatcher interface {
    Dispatch(ctx context.Context, request DispatchRequest) (DispatchResult, error)
}

type HumanGate interface {
    Open(ctx context.Context, gate HumanGateRequest) error
    Consume(ctx context.Context, taskID ID, decisionID ID, messageID string, option string) error
}
```

- [ ] **Step 2: Test scoped context and decision-ID binding**

Reject responses with the wrong phase Skill, wrong task/group/item scope,
unknown decision ID, unauthorized actor, duplicate message ID, or bare option
without the decision ID.

- [ ] **Step 3: Implement adapters**

The adapters may call Multica/Feishu only through interfaces; they must not
change workflow state directly.

- [ ] **Step 4: Run tests and commit**

```powershell
go test ./internal/multireview -run 'TestDispatch|TestHumanGate' -count=1
git add internal/multireview/dispatcher.go internal/multireview/human_gate.go internal/multireview/dispatcher_test.go internal/multireview/human_gate_test.go
git commit -m "feat: add scoped agent and human gate adapters"
```

### Task 9: Run one-group/one-item pilot and finalize output

**Files:**
- Create: `internal/multireview/review.go`
- Create: `internal/multireview/review_test.go`
- Modify: `internal/httpapi/server.go`
- Modify: `internal/httpapi/server_test.go`

- [ ] **Step 1: Add end-to-end pilot test**

Run one deterministic review through intake, Zhongshu freeze, one Menxia item,
group approval, finalization, and ApprovedPlan creation using fake Agent and
HumanGate adapters.

- [ ] **Step 2: Implement orchestration service**

Wire the Runtime Core into a new review-specific service and expose only new
review endpoints. Do not alter legacy workflow-run endpoints.

- [ ] **Step 3: Run all focused tests**

```powershell
go test ./internal/multireview ./internal/httpapi -count=1
```

- [ ] **Step 4: Commit the pilot**

```powershell
git add internal/multireview internal/httpapi/server.go internal/httpapi/server_test.go
git commit -m "feat: run first multi-review end-to-end pilot"
```
