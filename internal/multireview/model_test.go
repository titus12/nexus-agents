package multireview

import (
	"encoding/json"
	"errors"
	"testing"
)

func TestCanonicalIDRejectsLegacyValues(t *testing.T) {
	tests := []struct {
		name string
		id   ID
		ok   bool
	}{
		{name: "task", id: "task-000001", ok: true},
		{name: "evidence", id: "ev-000001", ok: true},
		{name: "finding", id: "finding-000001", ok: true},
		{name: "legacy short evidence", id: "ev-001", ok: false},
		{name: "legacy defect", id: "D1", ok: false},
		{name: "uppercase", id: "Task-000001", ok: false},
		{name: "zero padded incorrectly", id: "task-1", ok: false},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			err := ValidateID(test.id)
			if test.ok && err != nil {
				t.Fatalf("ValidateID(%q) returned unexpected error: %v", test.id, err)
			}
			if !test.ok && !errors.Is(err, ErrInvalidID) {
				t.Fatalf("ValidateID(%q) error = %v, want ErrInvalidID", test.id, err)
			}
		})
	}
}

func TestCanonicalWorkflowStatesAndActions(t *testing.T) {
	for _, state := range []WorkflowState{
		StateRequestIntake,
		StateProjectRouting,
		StateZhongshuAnalyst,
		StateZhongshuSolver,
		StateZhongshuCritic,
		StateZhongshuFreezeCheck,
		StateZhongshuPlanFrozen,
		StateMenxiaGroupStart,
		StateMenxiaItemAnalyst,
		StateMenxiaItemSolver,
		StateMenxiaItemCritic,
		StateMenxiaItemRevision,
		StateMenxiaGroupGate,
		StateWaitingHuman,
		StateFinalize,
		StateDone,
		StateBlocked,
	} {
		if state == "" {
			t.Fatal("workflow state must not be empty")
		}
	}

	for _, action := range []Action{
		ActionReadyForSolver,
		ActionNeedsMoreEvidence,
		ActionHumanGate,
		ActionBlocked,
		ActionReadyForCritic,
		ActionRequestAnalystEvidence,
		ActionApproveFreeze,
		ActionRequestSolverRevision,
		ActionRequestRegroup,
		ActionRequestProjectRerouting,
		ActionEvidenceSufficient,
		ActionFeasible,
		ActionRevise,
		ActionSplit,
		ActionMerge,
		ActionRemove,
		ActionApproveItem,
		ActionReviseItem,
		ActionSplitItem,
		ActionMergeItem,
		ActionRemoveItem,
		ActionApproveGroup,
		ActionReviseGroup,
	} {
		if action == "" {
			t.Fatal("agent action must not be empty")
		}
	}
}

func TestArtifactEnvelopeUsesCanonicalJSONFields(t *testing.T) {
	envelope := ArtifactEnvelope{
		SchemaVersion: "2.1",
		ArtifactType:  ArtifactEvidencePacket,
		ArtifactID:    "artifact-000001",
		TaskID:        "task-000001",
		Revision:      1,
		CreatedBy:     ActorReviewAnalyst,
		SkillLockHash: "sha256:skill-lock",
		Payload:       map[string]any{"project_type": "go"},
	}

	data, err := json.Marshal(envelope)
	if err != nil {
		t.Fatalf("marshal envelope: %v", err)
	}

	var decoded map[string]any
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal envelope: %v", err)
	}

	for _, field := range []string{
		"schema_version",
		"artifact_type",
		"artifact_id",
		"task_id",
		"revision",
		"created_by",
		"skill_lock_hash",
		"payload",
	} {
		if _, ok := decoded[field]; !ok {
			t.Fatalf("JSON field %q missing from %s", field, data)
		}
	}
}
