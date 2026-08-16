package multireview

import (
	"errors"
	"testing"
	"time"
)

func TestValidateEnvelope(t *testing.T) {
	valid := ArtifactEnvelope{
		SchemaVersion: "2.1",
		ArtifactType:  ArtifactEvidencePacket,
		ArtifactID:    "artifact-000001",
		TaskID:        "task-000001",
		Revision:      1,
		CreatedAt:     time.Date(2026, 8, 16, 0, 0, 0, 0, time.UTC),
		CreatedBy:     ActorReviewAnalyst,
		SkillLockHash: "sha256:skill-lock",
		Payload:       map[string]any{"project_type": "go"},
	}

	tests := []struct {
		name   string
		mutate func(*ArtifactEnvelope)
	}{
		{name: "missing schema version", mutate: func(value *ArtifactEnvelope) { value.SchemaVersion = "" }},
		{name: "wrong schema version", mutate: func(value *ArtifactEnvelope) { value.SchemaVersion = "2.0" }},
		{name: "invalid artifact id", mutate: func(value *ArtifactEnvelope) { value.ArtifactID = "artifact-1" }},
		{name: "invalid creator", mutate: func(value *ArtifactEnvelope) { value.CreatedBy = "agent" }},
		{name: "invalid skill lock", mutate: func(value *ArtifactEnvelope) { value.SkillLockHash = "skill-lock" }},
		{name: "zero revision", mutate: func(value *ArtifactEnvelope) { value.Revision = 0 }},
		{name: "zero timestamp", mutate: func(value *ArtifactEnvelope) { value.CreatedAt = time.Time{} }},
		{name: "nil payload", mutate: func(value *ArtifactEnvelope) { value.Payload = nil }},
	}

	if err := ValidateEnvelope(valid); err != nil {
		t.Fatalf("valid envelope rejected: %v", err)
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			value := valid
			test.mutate(&value)
			if !errors.Is(ValidateEnvelope(value), ErrInvalidArtifact) {
				t.Fatalf("ValidateEnvelope() error = %v, want ErrInvalidArtifact", ValidateEnvelope(value))
			}
		})
	}
}

func TestValidateFinding(t *testing.T) {
	valid := Finding{
		FindingID:          "finding-000001",
		Scope:              FindingScopeItem,
		Severity:           FindingSeverityP1,
		Category:           "evidence",
		Claim:              "Evidence is stale.",
		EvidenceIDs:        []ID{"ev-000001"},
		Impact:             "The item may be infeasible.",
		RequiredResolution: "Refresh the source evidence.",
		Owner:              "review-analyst",
		Status:             FindingStatusOpen,
	}

	if err := ValidateFinding(valid); err != nil {
		t.Fatalf("valid finding rejected: %v", err)
	}

	tests := []struct {
		name   string
		mutate func(*Finding)
	}{
		{name: "invalid id", mutate: func(value *Finding) { value.FindingID = "F1" }},
		{name: "invalid scope", mutate: func(value *Finding) { value.Scope = "task" }},
		{name: "invalid severity", mutate: func(value *Finding) { value.Severity = "P5" }},
		{name: "invalid category", mutate: func(value *Finding) { value.Category = "style" }},
		{name: "missing claim", mutate: func(value *Finding) { value.Claim = "" }},
		{name: "invalid evidence id", mutate: func(value *Finding) { value.EvidenceIDs = []ID{"ev-001"} }},
		{name: "invalid owner", mutate: func(value *Finding) { value.Owner = "human-ish" }},
		{name: "invalid status", mutate: func(value *Finding) { value.Status = "closed" }},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			value := valid
			test.mutate(&value)
			if !errors.Is(ValidateFinding(value), ErrInvalidFinding) {
				t.Fatalf("ValidateFinding() error = %v, want ErrInvalidFinding", ValidateFinding(value))
			}
		})
	}
}

func TestValidateScore(t *testing.T) {
	valid := Score{
		RequirementCoverage:   1.5,
		EvidenceSufficiency:   1.5,
		ArchitectureReuse:     1.0,
		PerformanceResourceGC: 1.0,
		Compatibility:         1.0,
		TestabilityOperations: 0.8,
		SecurityRisk:          0.4,
		Total:                 7.2,
	}

	if err := ValidateScore(valid); err != nil {
		t.Fatalf("valid score rejected: %v", err)
	}

	tests := []struct {
		name   string
		mutate func(*Score)
	}{
		{name: "coverage too high", mutate: func(value *Score) { value.RequirementCoverage = 2.1 }},
		{name: "negative evidence", mutate: func(value *Score) { value.EvidenceSufficiency = -0.1 }},
		{name: "compatibility too high", mutate: func(value *Score) { value.Compatibility = 1.6 }},
		{name: "total too high", mutate: func(value *Score) { value.Total = 10.1 }},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			value := valid
			test.mutate(&value)
			if !errors.Is(ValidateScore(value), ErrInvalidScore) {
				t.Fatalf("ValidateScore() error = %v, want ErrInvalidScore", ValidateScore(value))
			}
		})
	}
}

func TestBlockingFindings(t *testing.T) {
	tests := []struct {
		name     string
		status   FindingStatus
		blocking bool
	}{
		{name: "open", status: FindingStatusOpen, blocking: true},
		{name: "deferred", status: FindingStatusDeferred, blocking: true},
		{name: "needs human", status: FindingStatusNeedsHuman, blocking: true},
		{name: "accepted", status: FindingStatusAccepted, blocking: false},
		{name: "resolved", status: FindingStatusResolved, blocking: false},
		{name: "rejected", status: FindingStatusRejectedWithEvidence, blocking: false},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			finding := Finding{Severity: FindingSeverityP1, Status: test.status}
			if got := HasBlockingFindings([]Finding{finding}); got != test.blocking {
				t.Fatalf("HasBlockingFindings() = %t, want %t", got, test.blocking)
			}
		})
	}

	nonBlockingP2 := Finding{Severity: FindingSeverityP2, Status: FindingStatusOpen}
	if HasBlockingFindings([]Finding{nonBlockingP2}) {
		t.Fatal("open P2 finding must not block approval")
	}
}

func TestValidateFrozenPlan(t *testing.T) {
	valid := FrozenPlan{
		PlanID:              "plan-000001",
		Version:             1,
		ContentHash:         "sha256:plan",
		EvidenceSnapshotIDs: []ID{"artifact-000001"},
		SkillLockHash:       "sha256:skill-lock",
		FrozenAt:            time.Date(2026, 8, 16, 0, 0, 0, 0, time.UTC),
		Groups: []Group{{
			ID:          "group-000001",
			Objective:   "Build the runtime core.",
			ContentHash: "sha256:group",
			Items: []Item{{
				ID:          "item-000001",
				Objective:   "Define canonical models.",
				ContentHash: "sha256:item",
			}},
		}},
	}

	if err := ValidateFrozenPlan(valid); err != nil {
		t.Fatalf("valid frozen plan rejected: %v", err)
	}

	tests := []struct {
		name   string
		mutate func(*FrozenPlan)
	}{
		{name: "invalid plan id", mutate: func(value *FrozenPlan) { value.PlanID = "plan-1" }},
		{name: "zero version", mutate: func(value *FrozenPlan) { value.Version = 0 }},
		{name: "missing evidence snapshot", mutate: func(value *FrozenPlan) { value.EvidenceSnapshotIDs = nil }},
		{name: "missing group objective", mutate: func(value *FrozenPlan) { value.Groups[0].Objective = "" }},
		{name: "missing item", mutate: func(value *FrozenPlan) { value.Groups[0].Items = nil }},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			value := valid
			test.mutate(&value)
			if !errors.Is(ValidateFrozenPlan(value), ErrInvalidFrozenPlan) {
				t.Fatalf("ValidateFrozenPlan() error = %v, want ErrInvalidFrozenPlan", ValidateFrozenPlan(value))
			}
		})
	}
}
