package multireview

import (
	"errors"
	"testing"
	"time"
)

func TestTransitionHappyPath(t *testing.T) {
	tests := []struct {
		name         string
		state        WorkflowState
		action       Action
		hasGroup     bool
		hasItem      bool
		hasNextItem  bool
		hasNextGroup bool
		want         WorkflowState
	}{
		{name: "intake to routing", state: StateRequestIntake, action: ActionStartProjectRouting, want: StateProjectRouting},
		{name: "routing to analyst", state: StateProjectRouting, action: ActionStartZhongshuAnalyst, want: StateZhongshuAnalyst},
		{name: "analyst to solver", state: StateZhongshuAnalyst, action: ActionReadyForSolver, want: StateZhongshuSolver},
		{name: "solver to critic", state: StateZhongshuSolver, action: ActionReadyForCritic, want: StateZhongshuCritic},
		{name: "critic to freeze check", state: StateZhongshuCritic, action: ActionApproveFreeze, want: StateZhongshuFreezeCheck},
		{name: "freeze check to frozen", state: StateZhongshuFreezeCheck, action: ActionFreezePlan, want: StateZhongshuPlanFrozen},
		{name: "frozen to group", state: StateZhongshuPlanFrozen, action: ActionStartMenxiaGroup, want: StateMenxiaGroupStart},
		{name: "group to item analyst", state: StateMenxiaGroupStart, action: ActionStartMenxiaItem, hasGroup: true, want: StateMenxiaItemAnalyst},
		{name: "item analyst to solver", state: StateMenxiaItemAnalyst, action: ActionEvidenceSufficient, hasGroup: true, hasItem: true, want: StateMenxiaItemSolver},
		{name: "item solver to critic", state: StateMenxiaItemSolver, action: ActionFeasible, hasGroup: true, hasItem: true, want: StateMenxiaItemCritic},
		{name: "item approved with next item", state: StateMenxiaItemCritic, action: ActionApproveItem, hasGroup: true, hasItem: true, hasNextItem: true, want: StateMenxiaItemAnalyst},
		{name: "item approved to group gate", state: StateMenxiaItemCritic, action: ActionApproveItem, hasGroup: true, hasItem: true, want: StateMenxiaGroupGate},
		{name: "group approved with next group", state: StateMenxiaGroupGate, action: ActionApproveGroup, hasGroup: true, hasNextGroup: true, want: StateMenxiaGroupStart},
		{name: "group approved to finalize", state: StateMenxiaGroupGate, action: ActionApproveGroup, hasGroup: true, want: StateFinalize},
		{name: "finalize to done", state: StateFinalize, action: ActionComplete, want: StateDone},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			input := TransitionInput{
				State:          TaskState{WorkflowState: test.state, UpdatedAt: time.Now().UTC()},
				Action:         test.action,
				HasActiveGroup: test.hasGroup,
				HasActiveItem:  test.hasItem,
				HasNextItem:    test.hasNextItem,
				HasNextGroup:   test.hasNextGroup,
			}
			result, err := Transition(input)
			if err != nil {
				t.Fatalf("Transition() error = %v", err)
			}
			if result.State.WorkflowState != test.want {
				t.Fatalf("next state = %q, want %q", result.State.WorkflowState, test.want)
			}
		})
	}
}

func TestTransitionRejectsIllegalActionsAndScopes(t *testing.T) {
	tests := []struct {
		name  string
		input TransitionInput
	}{
		{
			name: "unknown action",
			input: TransitionInput{
				State:  TaskState{WorkflowState: StateZhongshuAnalyst},
				Action: "OLD_ACTION",
			},
		},
		{
			name: "analyst cannot approve freeze",
			input: TransitionInput{
				State:  TaskState{WorkflowState: StateZhongshuAnalyst},
				Action: ActionApproveFreeze,
			},
		},
		{
			name: "item state requires group",
			input: TransitionInput{
				State:         TaskState{WorkflowState: StateMenxiaItemAnalyst},
				Action:        ActionEvidenceSufficient,
				HasActiveItem: true,
			},
		},
		{
			name: "item state requires item",
			input: TransitionInput{
				State:          TaskState{WorkflowState: StateMenxiaItemSolver},
				Action:         ActionFeasible,
				HasActiveGroup: true,
			},
		},
		{
			name: "done is terminal",
			input: TransitionInput{
				State:  TaskState{WorkflowState: StateDone},
				Action: ActionComplete,
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := Transition(test.input)
			if !errors.Is(err, ErrInvalidTransition) && !errors.Is(err, ErrInvalidAction) {
				t.Fatalf("Transition() error = %v, want invalid transition/action", err)
			}
		})
	}
}

func TestTransitionHumanGateAndBlockedResume(t *testing.T) {
	resumeState := StateZhongshuSolver
	result, err := Transition(TransitionInput{
		State:  TaskState{WorkflowState: StateZhongshuAnalyst},
		Action: ActionHumanGate,
	})
	if err != nil {
		t.Fatalf("open human gate: %v", err)
	}
	if result.State.WorkflowState != StateWaitingHuman {
		t.Fatalf("human gate state = %q, want %q", result.State.WorkflowState, StateWaitingHuman)
	}

	resumed, err := Transition(TransitionInput{
		State:             result.State,
		Action:            ActionResume,
		ResumeState:       &resumeState,
		HasActiveDecision: false,
	})
	if err != nil {
		t.Fatalf("resume human gate: %v", err)
	}
	if resumed.State.WorkflowState != resumeState {
		t.Fatalf("resumed state = %q, want %q", resumed.State.WorkflowState, resumeState)
	}

	blocked, err := Transition(TransitionInput{
		State:  TaskState{WorkflowState: StateZhongshuSolver},
		Action: ActionBlocked,
	})
	if err != nil {
		t.Fatalf("enter blocked: %v", err)
	}
	if blocked.State.WorkflowState != StateBlocked {
		t.Fatalf("blocked state = %q, want %q", blocked.State.WorkflowState, StateBlocked)
	}
}

func TestTransitionRevisionBudgets(t *testing.T) {
	tests := []struct {
		name   string
		state  TaskState
		action Action
	}{
		{
			name:   "Zhongshu budget",
			state:  TaskState{WorkflowState: StateZhongshuCritic, ZhongshuRevisionRound: MaxZhongshuRevisionRounds},
			action: ActionRequestSolverRevision,
		},
		{
			name:   "group budget",
			state:  TaskState{WorkflowState: StateMenxiaGroupGate, GroupRevisionRound: MaxGroupRevisionRounds, ActiveGroupID: ptrID("group-000001")},
			action: ActionReviseGroup,
		},
		{
			name:   "item budget",
			state:  TaskState{WorkflowState: StateMenxiaItemCritic, ItemRevisionRound: MaxItemRevisionRounds, ActiveGroupID: ptrID("group-000001"), ActiveItemID: ptrID("item-000001")},
			action: ActionReviseItem,
		},
		{
			name:   "global budget",
			state:  TaskState{WorkflowState: StateZhongshuCritic, GlobalRevisionRound: MaxGlobalRevisionRounds},
			action: ActionRequestSolverRevision,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := Transition(TransitionInput{State: test.state, Action: test.action, HasActiveGroup: true, HasActiveItem: true})
			if !errors.Is(err, ErrRevisionBudgetExceeded) {
				t.Fatalf("Transition() error = %v, want ErrRevisionBudgetExceeded", err)
			}
		})
	}
}

func ptrID(value string) *ID {
	id := ID(value)
	return &id
}
