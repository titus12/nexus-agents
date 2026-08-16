package multireview

import (
	"fmt"
	"regexp"
	"time"
)

type ID string
type WorkflowState string
type Actor string
type Action string
type ArtifactType string
type FindingScope string
type FindingSeverity string
type FindingCategory string
type FindingStatus string
type FindingOwner string
type AmendmentOperation string

const (
	StateRequestIntake       WorkflowState = "REQUEST_INTAKE"
	StateProjectRouting      WorkflowState = "PROJECT_ROUTING"
	StateZhongshuAnalyst     WorkflowState = "ZHONGSHU_ANALYST"
	StateZhongshuSolver      WorkflowState = "ZHONGSHU_SOLVER"
	StateZhongshuCritic      WorkflowState = "ZHONGSHU_CRITIC"
	StateZhongshuFreezeCheck WorkflowState = "ZHONGSHU_FREEZE_CHECK"
	StateZhongshuPlanFrozen  WorkflowState = "ZHONGSHU_PLAN_FROZEN"
	StateMenxiaGroupStart    WorkflowState = "MENXIA_GROUP_START"
	StateMenxiaItemAnalyst   WorkflowState = "MENXIA_ITEM_ANALYST"
	StateMenxiaItemSolver    WorkflowState = "MENXIA_ITEM_SOLVER"
	StateMenxiaItemCritic    WorkflowState = "MENXIA_ITEM_CRITIC"
	StateMenxiaItemRevision  WorkflowState = "MENXIA_ITEM_REVISION"
	StateMenxiaGroupGate     WorkflowState = "MENXIA_GROUP_GATE"
	StateWaitingHuman        WorkflowState = "WAITING_HUMAN"
	StateFinalize            WorkflowState = "FINALIZE"
	StateDone                WorkflowState = "DONE"
	StateBlocked             WorkflowState = "BLOCKED"
)

const (
	ActorOrchestrator  Actor = "orchestrator"
	ActorReviewAnalyst Actor = "review-analyst"
	ActorReviewSolver  Actor = "review-solver"
	ActorReviewCritic  Actor = "review-critic"
	ActorHuman         Actor = "human"
)

const (
	ActionReadyForSolver          Action = "READY_FOR_SOLVER"
	ActionNeedsMoreEvidence       Action = "NEEDS_MORE_EVIDENCE"
	ActionHumanGate               Action = "HUMAN_GATE"
	ActionBlocked                 Action = "BLOCKED"
	ActionReadyForCritic          Action = "READY_FOR_CRITIC"
	ActionRequestAnalystEvidence  Action = "REQUEST_ANALYST_EVIDENCE"
	ActionApproveFreeze           Action = "APPROVE_FREEZE"
	ActionRequestSolverRevision   Action = "REQUEST_SOLVER_REVISION"
	ActionRequestRegroup          Action = "REQUEST_REGROUP"
	ActionRequestProjectRerouting Action = "REQUEST_PROJECT_REROUTING"
	ActionEvidenceSufficient      Action = "EVIDENCE_SUFFICIENT"
	ActionFeasible                Action = "FEASIBLE"
	ActionRevise                  Action = "REVISE"
	ActionSplit                   Action = "SPLIT"
	ActionMerge                   Action = "MERGE"
	ActionRemove                  Action = "REMOVE"
	ActionApproveItem             Action = "APPROVE_ITEM"
	ActionReviseItem              Action = "REVISE_ITEM"
	ActionSplitItem               Action = "SPLIT_ITEM"
	ActionMergeItem               Action = "MERGE_ITEM"
	ActionRemoveItem              Action = "REMOVE_ITEM"
	ActionApproveGroup            Action = "APPROVE_GROUP"
	ActionReviseGroup             Action = "REVISE_GROUP"
)

const (
	ArtifactEvidencePacket      ArtifactType = "EvidencePacket"
	ArtifactFrozenPlan          ArtifactType = "FrozenPlan"
	ArtifactFindingPacket       ArtifactType = "FindingPacket"
	ArtifactReviewAmendment     ArtifactType = "ReviewAmendment"
	ArtifactItemReviewDecision  ArtifactType = "ItemReviewDecision"
	ArtifactGroupReviewDecision ArtifactType = "GroupReviewDecision"
	ArtifactHumanGate           ArtifactType = "HumanGate"
	ArtifactTaskState           ArtifactType = "TaskState"
)

const (
	FindingScopePlan  FindingScope = "plan"
	FindingScopeGroup FindingScope = "group"
	FindingScopeItem  FindingScope = "item"

	FindingSeverityP0 FindingSeverity = "P0"
	FindingSeverityP1 FindingSeverity = "P1"
	FindingSeverityP2 FindingSeverity = "P2"
	FindingSeverityP3 FindingSeverity = "P3"
)

const (
	FindingStatusOpen                 FindingStatus = "open"
	FindingStatusAccepted             FindingStatus = "accepted"
	FindingStatusRejectedWithEvidence FindingStatus = "rejected_with_evidence"
	FindingStatusResolved             FindingStatus = "resolved"
	FindingStatusNeedsHuman           FindingStatus = "needs_human"
	FindingStatusDeferred             FindingStatus = "deferred"
)

const (
	AmendmentUpdateItem       AmendmentOperation = "UPDATE_ITEM"
	AmendmentSplitItem        AmendmentOperation = "SPLIT_ITEM"
	AmendmentMergeItems       AmendmentOperation = "MERGE_ITEMS"
	AmendmentRemoveItem       AmendmentOperation = "REMOVE_ITEM"
	AmendmentUpdateDependency AmendmentOperation = "UPDATE_DEPENDENCY"
	AmendmentUpdateGroup      AmendmentOperation = "UPDATE_GROUP"
)

var canonicalIDPattern = regexp.MustCompile(`^[a-z]+(?:-[a-z]+)*-[0-9]{6}$`)

func ValidateID(id ID) error {
	if !canonicalIDPattern.MatchString(string(id)) {
		return fmt.Errorf("%w: %q", ErrInvalidID, id)
	}
	return nil
}

type ArtifactEnvelope struct {
	SchemaVersion string         `json:"schema_version"`
	ArtifactType  ArtifactType   `json:"artifact_type"`
	ArtifactID    ID             `json:"artifact_id"`
	TaskID        ID             `json:"task_id"`
	PlanID        *ID            `json:"plan_id,omitempty"`
	PlanVersion   *int           `json:"plan_version,omitempty"`
	GroupID       *ID            `json:"group_id,omitempty"`
	ItemID        *ID            `json:"item_id,omitempty"`
	Revision      int            `json:"revision"`
	CreatedAt     time.Time      `json:"created_at"`
	CreatedBy     Actor          `json:"created_by"`
	SkillLockHash string         `json:"skill_lock_hash"`
	Payload       map[string]any `json:"payload"`
}

type TaskState struct {
	TaskID                ID            `json:"task_id"`
	WorkflowState         WorkflowState `json:"workflow_state"`
	PlanID                *ID           `json:"plan_id,omitempty"`
	PlanVersion           *int          `json:"plan_version,omitempty"`
	ActiveGroupID         *ID           `json:"active_group_id,omitempty"`
	ActiveItemID          *ID           `json:"active_item_id,omitempty"`
	GlobalRevisionRound   int           `json:"global_revision_round"`
	ZhongshuRevisionRound int           `json:"zhongshu_revision_round"`
	GroupRevisionRound    int           `json:"group_revision_round"`
	ItemRevisionRound     int           `json:"item_revision_round"`
	AgentCallCount        int           `json:"agent_call_count"`
	ActiveDecisionID      *ID           `json:"active_decision_id,omitempty"`
	LastArtifactID        *ID           `json:"last_artifact_id,omitempty"`
	UpdatedAt             time.Time     `json:"updated_at"`
}

type Finding struct {
	FindingID            ID              `json:"finding_id"`
	Scope                FindingScope    `json:"scope"`
	Severity             FindingSeverity `json:"severity"`
	Category             FindingCategory `json:"category"`
	Claim                string          `json:"claim"`
	EvidenceIDs          []ID            `json:"evidence_ids,omitempty"`
	Impact               string          `json:"impact"`
	RequiredResolution   string          `json:"required_resolution"`
	Owner                FindingOwner    `json:"owner"`
	Status               FindingStatus   `json:"status"`
	ResolutionArtifactID *ID             `json:"resolution_artifact_id,omitempty"`
}

type Score struct {
	RequirementCoverage   float64 `json:"requirement_coverage"`
	EvidenceSufficiency   float64 `json:"evidence_sufficiency"`
	ArchitectureReuse     float64 `json:"architecture_reuse"`
	PerformanceResourceGC float64 `json:"performance_resource_gc"`
	Compatibility         float64 `json:"compatibility"`
	TestabilityOperations float64 `json:"testability_operations"`
	SecurityRisk          float64 `json:"security_risk"`
	Total                 float64 `json:"total"`
}

type Item struct {
	ID           ID     `json:"item_id"`
	Objective    string `json:"objective"`
	Dependencies []ID   `json:"dependencies,omitempty"`
	ContentHash  string `json:"content_hash"`
}

type Group struct {
	ID           ID     `json:"group_id"`
	Objective    string `json:"objective"`
	Dependencies []ID   `json:"dependencies,omitempty"`
	Items        []Item `json:"items"`
	ContentHash  string `json:"content_hash"`
}

type FrozenPlan struct {
	PlanID              ID        `json:"plan_id"`
	Version             int       `json:"version"`
	ContentHash         string    `json:"content_hash"`
	Groups              []Group   `json:"groups"`
	EvidenceSnapshotIDs []ID      `json:"evidence_snapshot_ids"`
	SkillLockHash       string    `json:"skill_lock_hash"`
	FrozenAt            time.Time `json:"frozen_at"`
}

type ReviewAmendment struct {
	AmendmentID      ID                 `json:"amendment_id"`
	PlanID           ID                 `json:"plan_id"`
	PlanVersion      int                `json:"plan_version"`
	Operation        AmendmentOperation `json:"operation"`
	TargetIDs        []ID               `json:"target_ids"`
	ReplacementItems []Item             `json:"replacement_items,omitempty"`
	Reason           string             `json:"reason"`
	FindingIDs       []ID               `json:"finding_ids,omitempty"`
	Invalidates      []ID               `json:"invalidates"`
}
