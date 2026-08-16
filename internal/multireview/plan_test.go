package multireview

import (
	"errors"
	"testing"
	"time"
)

func TestFreezePlanCreatesImmutableSnapshot(t *testing.T) {
	original := testPlan()
	frozen, err := FreezePlan(original)
	if err != nil {
		t.Fatalf("FreezePlan() error = %v", err)
	}
	if !frozen.IsFrozen() {
		t.Fatal("FreezePlan() returned a mutable plan")
	}

	original.Groups[0].Objective = "mutated after freeze"
	original.Groups[0].Items[0].Objective = "mutated item"
	if frozen.Groups[0].Objective == original.Groups[0].Objective {
		t.Fatal("frozen group shares mutable storage with input")
	}
	if frozen.Groups[0].Items[0].Objective == original.Groups[0].Items[0].Objective {
		t.Fatal("frozen item shares mutable storage with input")
	}

	if _, err := FreezePlan(frozen); !errors.Is(err, ErrImmutablePlan) {
		t.Fatalf("refreezing plan error = %v, want ErrImmutablePlan", err)
	}
}

func TestValidateAmendment(t *testing.T) {
	frozen, err := FreezePlan(testPlan())
	if err != nil {
		t.Fatalf("FreezePlan() error = %v", err)
	}

	valid := ReviewAmendment{
		AmendmentID: "amendment-000001",
		PlanID:      frozen.PlanID,
		PlanVersion: frozen.Version,
		Operation:   AmendmentUpdateItem,
		TargetIDs:   []ID{"item-000001"},
		Reason:      "Clarify the item implementation boundary.",
		Invalidates: []ID{"item-000001", "group-000001"},
	}
	if err := ValidateAmendment(frozen, valid); err != nil {
		t.Fatalf("valid amendment rejected: %v", err)
	}

	tests := []struct {
		name   string
		mutate func(*ReviewAmendment)
	}{
		{name: "wrong plan", mutate: func(value *ReviewAmendment) { value.PlanID = "plan-000002" }},
		{name: "wrong version", mutate: func(value *ReviewAmendment) { value.PlanVersion = 2 }},
		{name: "missing target", mutate: func(value *ReviewAmendment) { value.TargetIDs = nil }},
		{name: "unknown target", mutate: func(value *ReviewAmendment) { value.TargetIDs = []ID{"item-000099"} }},
		{name: "missing reason", mutate: func(value *ReviewAmendment) { value.Reason = "" }},
		{name: "structural group update", mutate: func(value *ReviewAmendment) { value.Operation = AmendmentUpdateGroup }},
		{name: "dependency update", mutate: func(value *ReviewAmendment) { value.Operation = AmendmentUpdateDependency }},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			value := valid
			test.mutate(&value)
			if !errors.Is(ValidateAmendment(frozen, value), ErrInvalidAmendment) {
				t.Fatalf("ValidateAmendment() error = %v, want ErrInvalidAmendment", ValidateAmendment(frozen, value))
			}
		})
	}
}

func TestInvalidationScopeIncludesDependentsAndLaterGroups(t *testing.T) {
	plan := testPlan()
	plan.Groups[0].Items = append(plan.Groups[0].Items, Item{
		ID:           "item-000002",
		Objective:    "Use the canonical model.",
		Dependencies: []ID{"item-000001"},
		ContentHash:  "sha256:item-2",
	})
	plan.Groups = append(plan.Groups,
		Group{
			ID:           "group-000002",
			Objective:    "Implement validation.",
			Dependencies: []ID{"group-000001"},
			ContentHash:  "sha256:group-2",
			Items: []Item{{
				ID:          "item-000003",
				Objective:   "Validate artifacts.",
				ContentHash: "sha256:item-3",
			}},
		},
		Group{
			ID:           "group-000003",
			Objective:    "Run the pilot.",
			Dependencies: []ID{"group-000002"},
			ContentHash:  "sha256:group-3",
			Items: []Item{{
				ID:          "item-000004",
				Objective:   "Run one review.",
				ContentHash: "sha256:item-4",
			}},
		},
	)

	frozen, err := FreezePlan(plan)
	if err != nil {
		t.Fatalf("FreezePlan() error = %v", err)
	}
	scope, err := InvalidationScope(frozen, ReviewAmendment{
		AmendmentID: "amendment-000001",
		PlanID:      frozen.PlanID,
		PlanVersion: frozen.Version,
		Operation:   AmendmentUpdateItem,
		TargetIDs:   []ID{"item-000001"},
		Reason:      "Update item detail.",
	})
	if err != nil {
		t.Fatalf("InvalidationScope() error = %v", err)
	}

	for _, id := range []ID{
		"item-000001",
		"item-000002",
		"group-000001",
		"group-000002",
		"group-000003",
	} {
		if !containsID(scope, id) {
			t.Fatalf("invalidation scope %v does not contain %q", scope, id)
		}
	}
}

func testPlan() FrozenPlan {
	return FrozenPlan{
		PlanID:              "plan-000001",
		Version:             1,
		ContentHash:         "sha256:plan",
		EvidenceSnapshotIDs: []ID{"artifact-000001"},
		SkillLockHash:       "sha256:skill-lock",
		FrozenAt:            time.Date(2026, 8, 16, 0, 0, 0, 0, time.UTC),
		Groups: []Group{{
			ID:          "group-000001",
			Objective:   "Build the runtime core.",
			ContentHash: "sha256:group-1",
			Items: []Item{{
				ID:          "item-000001",
				Objective:   "Define canonical models.",
				ContentHash: "sha256:item-1",
			}},
		}},
	}
}

func containsID(ids []ID, target ID) bool {
	for _, id := range ids {
		if id == target {
			return true
		}
	}
	return false
}
