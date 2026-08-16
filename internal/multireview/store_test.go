package multireview

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestMemoryStoreCommitIsAtomic(t *testing.T) {
	store := NewMemoryStore()
	taskID := ID("task-000001")
	artifact := testArtifact(taskID, "artifact-000001", 1)
	state := TaskState{
		TaskID:        taskID,
		WorkflowState: StateRequestIntake,
		UpdatedAt:     time.Date(2026, 8, 16, 0, 0, 0, 0, time.UTC),
	}
	transition := TransitionRecord{
		TaskID:    taskID,
		From:      StateRequestIntake,
		To:        StateProjectRouting,
		Action:    ActionStartProjectRouting,
		CreatedAt: state.UpdatedAt,
	}
	decisionID := ID("decision-000001")
	message := &MessageConsumption{
		DecisionID: decisionID,
		MessageID:  "message-1",
		ConsumedAt: state.UpdatedAt,
	}

	err := store.Commit(context.Background(), Commit{
		TaskID:          taskID,
		State:           state,
		Artifacts:       []ArtifactEnvelope{artifact},
		Transition:      transition,
		ConsumedMessage: message,
	})
	if err != nil {
		t.Fatalf("Commit() error = %v", err)
	}

	loaded, err := store.LoadTask(context.Background(), taskID)
	if err != nil {
		t.Fatalf("LoadTask() error = %v", err)
	}
	if loaded.WorkflowState != StateRequestIntake {
		t.Fatalf("stored workflow state = %q, want %q", loaded.WorkflowState, StateRequestIntake)
	}

	history, err := store.ArtifactHistory(context.Background(), taskID)
	if err != nil {
		t.Fatalf("ArtifactHistory() error = %v", err)
	}
	if len(history) != 1 || history[0].ArtifactID != artifact.ArtifactID {
		t.Fatalf("artifact history = %#v, want one artifact", history)
	}

	consumed, err := store.IsMessageConsumed(context.Background(), taskID, message.MessageID)
	if err != nil {
		t.Fatalf("IsMessageConsumed() error = %v", err)
	}
	if !consumed {
		t.Fatal("message should be marked consumed")
	}
}

func TestMemoryStoreRejectsDuplicateArtifactRevisionWithoutPartialCommit(t *testing.T) {
	store := NewMemoryStore()
	taskID := ID("task-000001")
	artifact := testArtifact(taskID, "artifact-000001", 1)
	state := TaskState{TaskID: taskID, WorkflowState: StateRequestIntake, UpdatedAt: time.Date(2026, 8, 16, 0, 0, 0, 0, time.UTC)}

	first := Commit{
		TaskID:     taskID,
		State:      state,
		Artifacts:  []ArtifactEnvelope{artifact},
		Transition: TransitionRecord{TaskID: taskID, From: StateRequestIntake, To: StateProjectRouting, Action: ActionStartProjectRouting, CreatedAt: state.UpdatedAt},
	}
	if err := store.Commit(context.Background(), first); err != nil {
		t.Fatalf("first Commit() error = %v", err)
	}

	secondState := state
	secondState.WorkflowState = StateProjectRouting
	err := store.Commit(context.Background(), Commit{
		TaskID:     taskID,
		State:      secondState,
		Artifacts:  []ArtifactEnvelope{artifact},
		Transition: TransitionRecord{TaskID: taskID, From: StateProjectRouting, To: StateZhongshuAnalyst, Action: ActionStartZhongshuAnalyst, CreatedAt: state.UpdatedAt},
	})
	if !errors.Is(err, ErrDuplicateArtifactRevision) {
		t.Fatalf("duplicate Commit() error = %v, want ErrDuplicateArtifactRevision", err)
	}

	loaded, err := store.LoadTask(context.Background(), taskID)
	if err != nil {
		t.Fatalf("LoadTask() error = %v", err)
	}
	if loaded.WorkflowState != StateRequestIntake {
		t.Fatalf("state changed after rejected commit: %q", loaded.WorkflowState)
	}
	history, err := store.ArtifactHistory(context.Background(), taskID)
	if err != nil {
		t.Fatalf("ArtifactHistory() error = %v", err)
	}
	if len(history) != 1 {
		t.Fatalf("artifact history length = %d, want 1", len(history))
	}
}

func TestMemoryStoreRejectsDuplicateMessageConsumption(t *testing.T) {
	store := NewMemoryStore()
	taskID := ID("task-000001")
	state := TaskState{TaskID: taskID, WorkflowState: StateWaitingHuman, UpdatedAt: time.Date(2026, 8, 16, 0, 0, 0, 0, time.UTC)}
	message := &MessageConsumption{
		DecisionID: "decision-000001",
		MessageID:  "message-1",
		ConsumedAt: time.Date(2026, 8, 16, 0, 0, 0, 0, time.UTC),
	}

	commit := Commit{
		TaskID:          taskID,
		State:           state,
		ConsumedMessage: message,
	}
	if err := store.Commit(context.Background(), commit); err != nil {
		t.Fatalf("first Commit() error = %v", err)
	}
	err := store.Commit(context.Background(), commit)
	if !errors.Is(err, ErrDuplicateMessageConsumption) {
		t.Fatalf("duplicate Commit() error = %v, want ErrDuplicateMessageConsumption", err)
	}

	loaded, err := store.LoadTask(context.Background(), taskID)
	if err != nil {
		t.Fatalf("LoadTask() error = %v", err)
	}
	if loaded.WorkflowState != StateWaitingHuman {
		t.Fatalf("state changed after duplicate message: %q", loaded.WorkflowState)
	}
}

func testArtifact(taskID, artifactID ID, revision int) ArtifactEnvelope {
	return ArtifactEnvelope{
		SchemaVersion: "2.1",
		ArtifactType:  ArtifactTaskState,
		ArtifactID:    artifactID,
		TaskID:        taskID,
		Revision:      revision,
		CreatedAt:     time.Date(2026, 8, 16, 0, 0, 0, 0, time.UTC),
		CreatedBy:     ActorOrchestrator,
		SkillLockHash: "sha256:skill-lock",
		Payload:       map[string]any{"workflow_state": "REQUEST_INTAKE"},
	}
}
