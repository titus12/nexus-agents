package multireview

import (
	"fmt"
	"math"
	"strings"
	"time"
)

func ValidateEnvelope(envelope ArtifactEnvelope) error {
	if envelope.SchemaVersion != "2.1" {
		return fmt.Errorf("%w: schema_version must be 2.1", ErrInvalidArtifact)
	}
	if !isKnownArtifactType(envelope.ArtifactType) {
		return fmt.Errorf("%w: unknown artifact_type %q", ErrInvalidArtifact, envelope.ArtifactType)
	}
	if err := ValidateID(envelope.ArtifactID); err != nil {
		return fmt.Errorf("%w: artifact_id: %v", ErrInvalidArtifact, err)
	}
	if err := ValidateID(envelope.TaskID); err != nil {
		return fmt.Errorf("%w: task_id: %v", ErrInvalidArtifact, err)
	}
	if err := validateOptionalID(envelope.PlanID, "plan_id"); err != nil {
		return fmt.Errorf("%w: %v", ErrInvalidArtifact, err)
	}
	if err := validateOptionalID(envelope.GroupID, "group_id"); err != nil {
		return fmt.Errorf("%w: %v", ErrInvalidArtifact, err)
	}
	if err := validateOptionalID(envelope.ItemID, "item_id"); err != nil {
		return fmt.Errorf("%w: %v", ErrInvalidArtifact, err)
	}
	if envelope.PlanVersion != nil && *envelope.PlanVersion < 1 {
		return fmt.Errorf("%w: plan_version must be positive", ErrInvalidArtifact)
	}
	if envelope.Revision < 1 {
		return fmt.Errorf("%w: revision must be positive", ErrInvalidArtifact)
	}
	if envelope.CreatedAt.IsZero() {
		return fmt.Errorf("%w: created_at is required", ErrInvalidArtifact)
	}
	if !isKnownActor(envelope.CreatedBy) {
		return fmt.Errorf("%w: unknown created_by %q", ErrInvalidArtifact, envelope.CreatedBy)
	}
	if !isHash(envelope.SkillLockHash) {
		return fmt.Errorf("%w: invalid skill_lock_hash", ErrInvalidArtifact)
	}
	if envelope.Payload == nil {
		return fmt.Errorf("%w: payload is required", ErrInvalidArtifact)
	}
	return nil
}

func ValidateFinding(finding Finding) error {
	if err := ValidateID(finding.FindingID); err != nil {
		return fmt.Errorf("%w: finding_id: %v", ErrInvalidFinding, err)
	}
	if !containsString([]FindingScope{FindingScopePlan, FindingScopeGroup, FindingScopeItem}, finding.Scope) {
		return fmt.Errorf("%w: unknown scope %q", ErrInvalidFinding, finding.Scope)
	}
	if !containsString([]FindingSeverity{
		FindingSeverityP0,
		FindingSeverityP1,
		FindingSeverityP2,
		FindingSeverityP3,
	}, finding.Severity) {
		return fmt.Errorf("%w: unknown severity %q", ErrInvalidFinding, finding.Severity)
	}
	if !containsString([]FindingCategory{
		"requirement",
		"evidence",
		"logic",
		"architecture",
		"reuse",
		"performance",
		"resource",
		"gc",
		"security",
		"compatibility",
		"testability",
		"operations",
	}, finding.Category) {
		return fmt.Errorf("%w: unknown category %q", ErrInvalidFinding, finding.Category)
	}
	if strings.TrimSpace(finding.Claim) == "" {
		return fmt.Errorf("%w: claim is required", ErrInvalidFinding)
	}
	if strings.TrimSpace(finding.Impact) == "" {
		return fmt.Errorf("%w: impact is required", ErrInvalidFinding)
	}
	if strings.TrimSpace(finding.RequiredResolution) == "" {
		return fmt.Errorf("%w: required_resolution is required", ErrInvalidFinding)
	}
	for _, evidenceID := range finding.EvidenceIDs {
		if err := ValidateID(evidenceID); err != nil {
			return fmt.Errorf("%w: evidence_id: %v", ErrInvalidFinding, err)
		}
	}
	if !containsString([]FindingOwner{
		FindingOwner(ActorReviewAnalyst),
		FindingOwner(ActorReviewSolver),
		FindingOwner(ActorReviewCritic),
		FindingOwner(ActorHuman),
	}, finding.Owner) {
		return fmt.Errorf("%w: unknown owner %q", ErrInvalidFinding, finding.Owner)
	}
	if !containsString([]FindingStatus{
		FindingStatusOpen,
		FindingStatusAccepted,
		FindingStatusRejectedWithEvidence,
		FindingStatusResolved,
		FindingStatusNeedsHuman,
		FindingStatusDeferred,
	}, finding.Status) {
		return fmt.Errorf("%w: unknown status %q", ErrInvalidFinding, finding.Status)
	}
	if err := validateOptionalID(finding.ResolutionArtifactID, "resolution_artifact_id"); err != nil {
		return fmt.Errorf("%w: %v", ErrInvalidFinding, err)
	}
	return nil
}

func ValidateScore(score Score) error {
	if !within(score.RequirementCoverage, 0, 2) ||
		!within(score.EvidenceSufficiency, 0, 2) ||
		!within(score.ArchitectureReuse, 0, 1.5) ||
		!within(score.PerformanceResourceGC, 0, 1.5) ||
		!within(score.Compatibility, 0, 1.5) ||
		!within(score.TestabilityOperations, 0, 1) ||
		!within(score.SecurityRisk, 0, 0.5) ||
		!within(score.Total, 0, 10) {
		return fmt.Errorf("%w: score dimension outside allowed range", ErrInvalidScore)
	}
	return nil
}

func HasBlockingFindings(findings []Finding) bool {
	for _, finding := range findings {
		if (finding.Severity == FindingSeverityP0 || finding.Severity == FindingSeverityP1) &&
			(finding.Status == FindingStatusOpen ||
				finding.Status == FindingStatusDeferred ||
				finding.Status == FindingStatusNeedsHuman) {
			return true
		}
	}
	return false
}

func ValidateFrozenPlan(plan FrozenPlan) error {
	if err := ValidateID(plan.PlanID); err != nil {
		return fmt.Errorf("%w: plan_id: %v", ErrInvalidFrozenPlan, err)
	}
	if plan.Version < 1 {
		return fmt.Errorf("%w: version must be positive", ErrInvalidFrozenPlan)
	}
	if !isHash(plan.ContentHash) {
		return fmt.Errorf("%w: invalid content_hash", ErrInvalidFrozenPlan)
	}
	if !isHash(plan.SkillLockHash) {
		return fmt.Errorf("%w: invalid skill_lock_hash", ErrInvalidFrozenPlan)
	}
	if plan.FrozenAt.IsZero() {
		return fmt.Errorf("%w: frozen_at is required", ErrInvalidFrozenPlan)
	}
	if len(plan.EvidenceSnapshotIDs) == 0 {
		return fmt.Errorf("%w: evidence_snapshot_ids are required", ErrInvalidFrozenPlan)
	}
	if err := validateUniqueIDs(plan.EvidenceSnapshotIDs, "evidence_snapshot_ids"); err != nil {
		return fmt.Errorf("%w: %v", ErrInvalidFrozenPlan, err)
	}
	if len(plan.Groups) == 0 {
		return fmt.Errorf("%w: at least one group is required", ErrInvalidFrozenPlan)
	}

	groupIDs := make(map[ID]struct{}, len(plan.Groups))
	itemIDs := make(map[ID]struct{})
	for _, group := range plan.Groups {
		if err := ValidateID(group.ID); err != nil {
			return fmt.Errorf("%w: group_id: %v", ErrInvalidFrozenPlan, err)
		}
		if _, exists := groupIDs[group.ID]; exists {
			return fmt.Errorf("%w: duplicate group_id %q", ErrInvalidFrozenPlan, group.ID)
		}
		groupIDs[group.ID] = struct{}{}
		if strings.TrimSpace(group.Objective) == "" {
			return fmt.Errorf("%w: group objective is required", ErrInvalidFrozenPlan)
		}
		if group.ContentHash != "" && !isHash(group.ContentHash) {
			return fmt.Errorf("%w: invalid group content_hash", ErrInvalidFrozenPlan)
		}
		if len(group.Items) == 0 {
			return fmt.Errorf("%w: group %q must contain an item", ErrInvalidFrozenPlan, group.ID)
		}
		for _, item := range group.Items {
			if err := ValidateID(item.ID); err != nil {
				return fmt.Errorf("%w: item_id: %v", ErrInvalidFrozenPlan, err)
			}
			if _, exists := itemIDs[item.ID]; exists {
				return fmt.Errorf("%w: duplicate item_id %q", ErrInvalidFrozenPlan, item.ID)
			}
			itemIDs[item.ID] = struct{}{}
			if strings.TrimSpace(item.Objective) == "" {
				return fmt.Errorf("%w: item objective is required", ErrInvalidFrozenPlan)
			}
			if item.ContentHash != "" && !isHash(item.ContentHash) {
				return fmt.Errorf("%w: invalid item content_hash", ErrInvalidFrozenPlan)
			}
			if err := validateIDList(item.Dependencies, "item dependencies"); err != nil {
				return fmt.Errorf("%w: %v", ErrInvalidFrozenPlan, err)
			}
		}
		if err := validateIDList(group.Dependencies, "group dependencies"); err != nil {
			return fmt.Errorf("%w: %v", ErrInvalidFrozenPlan, err)
		}
	}
	return nil
}

func validateOptionalID(id *ID, field string) error {
	if id == nil {
		return nil
	}
	if err := ValidateID(*id); err != nil {
		return fmt.Errorf("%s: %v", field, err)
	}
	return nil
}

func validateIDList(ids []ID, field string) error {
	for _, id := range ids {
		if err := ValidateID(id); err != nil {
			return fmt.Errorf("%s: %v", field, err)
		}
	}
	return nil
}

func validateUniqueIDs(ids []ID, field string) error {
	seen := make(map[ID]struct{}, len(ids))
	for _, id := range ids {
		if err := ValidateID(id); err != nil {
			return fmt.Errorf("%s: %v", field, err)
		}
		if _, exists := seen[id]; exists {
			return fmt.Errorf("%s contains duplicate %q", field, id)
		}
		seen[id] = struct{}{}
	}
	return nil
}

func isKnownActor(actor Actor) bool {
	return containsString([]Actor{
		ActorOrchestrator,
		ActorReviewAnalyst,
		ActorReviewSolver,
		ActorReviewCritic,
		ActorHuman,
	}, actor)
}

func isKnownArtifactType(artifactType ArtifactType) bool {
	return containsString([]ArtifactType{
		ArtifactEvidencePacket,
		ArtifactFrozenPlan,
		ArtifactFindingPacket,
		ArtifactReviewAmendment,
		ArtifactItemReviewDecision,
		ArtifactGroupReviewDecision,
		ArtifactHumanGate,
		ArtifactTaskState,
	}, artifactType)
}

func isHash(value string) bool {
	return strings.HasPrefix(strings.TrimSpace(value), "sha256:") &&
		len(strings.TrimSpace(value)) > len("sha256:")
}

func within(value, min, max float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0) && value >= min && value <= max
}

func containsString[T ~string](values []T, target T) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

var _ = time.Time{}
