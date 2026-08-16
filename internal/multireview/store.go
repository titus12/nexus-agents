package multireview

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"time"
)

var (
	ErrTaskNotFound  = fmt.Errorf("task not found")
	ErrInvalidCommit = fmt.Errorf("invalid store commit")
)

type TransitionRecord struct {
	TaskID    ID            `json:"task_id"`
	From      WorkflowState `json:"from"`
	To        WorkflowState `json:"to"`
	Action    Action        `json:"action"`
	CreatedAt time.Time     `json:"created_at"`
}

type MessageConsumption struct {
	DecisionID ID        `json:"decision_id"`
	MessageID  string    `json:"message_id"`
	ConsumedAt time.Time `json:"consumed_at"`
}

type Commit struct {
	TaskID          ID
	State           TaskState
	Artifacts       []ArtifactEnvelope
	Transition      TransitionRecord
	ConsumedMessage *MessageConsumption
}

type Store interface {
	LoadTask(ctx context.Context, taskID ID) (TaskState, error)
	Commit(ctx context.Context, commit Commit) error
	ArtifactHistory(ctx context.Context, taskID ID) ([]ArtifactEnvelope, error)
	IsMessageConsumed(ctx context.Context, taskID ID, messageID string) (bool, error)
}

type MemoryStore struct {
	mu           sync.Mutex
	tasks        map[ID]TaskState
	artifacts    map[ID][]ArtifactEnvelope
	transitions  map[ID][]TransitionRecord
	consumptions map[string]MessageConsumption
}

func NewMemoryStore() *MemoryStore {
	return &MemoryStore{
		tasks:        make(map[ID]TaskState),
		artifacts:    make(map[ID][]ArtifactEnvelope),
		transitions:  make(map[ID][]TransitionRecord),
		consumptions: make(map[string]MessageConsumption),
	}
}

func (s *MemoryStore) LoadTask(ctx context.Context, taskID ID) (TaskState, error) {
	if err := contextError(ctx); err != nil {
		return TaskState{}, err
	}
	if err := ValidateID(taskID); err != nil {
		return TaskState{}, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	state, ok := s.tasks[taskID]
	if !ok {
		return TaskState{}, ErrTaskNotFound
	}
	return state, nil
}

func (s *MemoryStore) Commit(ctx context.Context, commit Commit) error {
	if err := contextError(ctx); err != nil {
		return err
	}
	if err := validateCommit(commit); err != nil {
		return err
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	existingArtifacts := s.artifacts[commit.TaskID]
	for _, artifact := range commit.Artifacts {
		for _, existing := range existingArtifacts {
			if artifact.ArtifactID == existing.ArtifactID && artifact.Revision == existing.Revision {
				return ErrDuplicateArtifactRevision
			}
		}
	}
	if commit.ConsumedMessage != nil {
		key := consumptionKey(commit.TaskID, commit.ConsumedMessage.MessageID)
		if _, exists := s.consumptions[key]; exists {
			return ErrDuplicateMessageConsumption
		}
	}

	s.tasks[commit.TaskID] = commit.State
	for _, artifact := range commit.Artifacts {
		s.artifacts[commit.TaskID] = append(s.artifacts[commit.TaskID], cloneArtifact(artifact))
	}
	if commit.Transition.TaskID != "" {
		s.transitions[commit.TaskID] = append(s.transitions[commit.TaskID], commit.Transition)
	}
	if commit.ConsumedMessage != nil {
		key := consumptionKey(commit.TaskID, commit.ConsumedMessage.MessageID)
		s.consumptions[key] = *commit.ConsumedMessage
	}
	return nil
}

func (s *MemoryStore) ArtifactHistory(ctx context.Context, taskID ID) ([]ArtifactEnvelope, error) {
	if err := contextError(ctx); err != nil {
		return nil, err
	}
	if err := ValidateID(taskID); err != nil {
		return nil, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	artifacts := s.artifacts[taskID]
	result := make([]ArtifactEnvelope, len(artifacts))
	for index, artifact := range artifacts {
		result[index] = cloneArtifact(artifact)
	}
	return result, nil
}

func (s *MemoryStore) IsMessageConsumed(ctx context.Context, taskID ID, messageID string) (bool, error) {
	if err := contextError(ctx); err != nil {
		return false, err
	}
	if err := ValidateID(taskID); err != nil {
		return false, err
	}
	if strings.TrimSpace(messageID) == "" {
		return false, fmt.Errorf("%w: message_id is required", ErrInvalidCommit)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	_, ok := s.consumptions[consumptionKey(taskID, messageID)]
	return ok, nil
}

func validateCommit(commit Commit) error {
	if err := ValidateID(commit.TaskID); err != nil {
		return fmt.Errorf("%w: task_id: %v", ErrInvalidCommit, err)
	}
	if commit.State.TaskID != commit.TaskID {
		return fmt.Errorf("%w: state task_id does not match commit", ErrInvalidCommit)
	}
	if !isKnownWorkflowState(commit.State.WorkflowState) {
		return fmt.Errorf("%w: unknown workflow state %q", ErrInvalidCommit, commit.State.WorkflowState)
	}
	if commit.State.UpdatedAt.IsZero() {
		return fmt.Errorf("%w: state updated_at is required", ErrInvalidCommit)
	}

	seenArtifacts := make(map[string]struct{}, len(commit.Artifacts))
	for _, artifact := range commit.Artifacts {
		if err := ValidateEnvelope(artifact); err != nil {
			return fmt.Errorf("%w: artifact: %v", ErrInvalidCommit, err)
		}
		if artifact.TaskID != commit.TaskID {
			return fmt.Errorf("%w: artifact task_id does not match commit", ErrInvalidCommit)
		}
		key := fmt.Sprintf("%s:%d", artifact.ArtifactID, artifact.Revision)
		if _, exists := seenArtifacts[key]; exists {
			return fmt.Errorf("%w: duplicate artifact in commit", ErrInvalidCommit)
		}
		seenArtifacts[key] = struct{}{}
	}

	if commit.Transition.TaskID != "" {
		if commit.Transition.TaskID != commit.TaskID ||
			!isKnownWorkflowState(commit.Transition.From) ||
			!isKnownWorkflowState(commit.Transition.To) ||
			!isKnownTransitionAction(commit.Transition.Action) ||
			commit.Transition.CreatedAt.IsZero() {
			return fmt.Errorf("%w: invalid transition record", ErrInvalidCommit)
		}
	}
	if commit.ConsumedMessage != nil {
		if err := ValidateID(commit.ConsumedMessage.DecisionID); err != nil {
			return fmt.Errorf("%w: decision_id: %v", ErrInvalidCommit, err)
		}
		if strings.TrimSpace(commit.ConsumedMessage.MessageID) == "" ||
			commit.ConsumedMessage.ConsumedAt.IsZero() {
			return fmt.Errorf("%w: invalid consumed message", ErrInvalidCommit)
		}
	}
	return nil
}

func isKnownWorkflowState(state WorkflowState) bool {
	switch state {
	case StateRequestIntake,
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
		StateBlocked:
		return true
	default:
		return false
	}
}

func contextError(ctx context.Context) error {
	if ctx == nil {
		return nil
	}
	return ctx.Err()
}

func consumptionKey(taskID ID, messageID string) string {
	return string(taskID) + "\x00" + messageID
}

func cloneArtifact(artifact ArtifactEnvelope) ArtifactEnvelope {
	clone := artifact
	if artifact.Payload != nil {
		clone.Payload = make(map[string]any, len(artifact.Payload))
		for key, value := range artifact.Payload {
			clone.Payload[key] = value
		}
	}
	return clone
}
