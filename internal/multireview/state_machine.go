package multireview

import "fmt"

const (
	MaxGlobalRevisionRounds   = 15
	MaxZhongshuRevisionRounds = 5
	MaxGroupRevisionRounds    = 5
	MaxItemRevisionRounds     = 3
)

// These commands are emitted only by the orchestrator. Agents may emit only
// the canonical role actions declared in model.go.
const (
	ActionStartProjectRouting  Action = "START_PROJECT_ROUTING"
	ActionStartZhongshuAnalyst Action = "START_ZHONGSHU_ANALYST"
	ActionFreezePlan           Action = "FREEZE_PLAN"
	ActionStartMenxiaGroup     Action = "START_MENXIA_GROUP"
	ActionStartMenxiaItem      Action = "START_MENXIA_ITEM"
	ActionRetryItem            Action = "RETRY_ITEM"
	ActionResume               Action = "RESUME"
	ActionComplete             Action = "COMPLETE"
)

type TransitionInput struct {
	State               TaskState
	Action              Action
	HasActiveDecision   bool
	HasActiveGroup      bool
	HasActiveItem       bool
	HasNextItem         bool
	HasNextGroup        bool
	HasBlockingFindings bool
	ResumeState         *WorkflowState
}

type TransitionResult struct {
	State       TaskState
	Invalidated []ID
}

func Transition(input TransitionInput) (TransitionResult, error) {
	if !isKnownTransitionAction(input.Action) {
		return TransitionResult{}, fmt.Errorf("%w: %q", ErrInvalidAction, input.Action)
	}

	state := input.State
	activeGroup := input.HasActiveGroup || state.ActiveGroupID != nil
	activeItem := input.HasActiveItem || state.ActiveItemID != nil

	if state.WorkflowState == StateWaitingHuman || state.WorkflowState == StateBlocked {
		if input.Action != ActionResume {
			return TransitionResult{}, fmt.Errorf("%w: %s accepts only RESUME", ErrInvalidTransition, state.WorkflowState)
		}
		if input.ResumeState == nil || *input.ResumeState == StateWaitingHuman || *input.ResumeState == StateBlocked {
			return TransitionResult{}, fmt.Errorf("%w: resume target is required", ErrInvalidTransition)
		}
		if state.WorkflowState == StateWaitingHuman && input.HasActiveDecision {
			return TransitionResult{}, ErrActiveHumanGate
		}
		state.WorkflowState = *input.ResumeState
		return TransitionResult{State: state}, nil
	}

	switch state.WorkflowState {
	case StateRequestIntake:
		return move(state, input.Action, ActionStartProjectRouting, StateProjectRouting)
	case StateProjectRouting:
		return move(state, input.Action, ActionStartZhongshuAnalyst, StateZhongshuAnalyst)
	case StateZhongshuAnalyst:
		switch input.Action {
		case ActionReadyForSolver:
			return moveState(state, StateZhongshuSolver)
		case ActionNeedsMoreEvidence:
			return reviseZhongshu(state)
		case ActionHumanGate:
			return moveState(state, StateWaitingHuman)
		case ActionBlocked:
			return moveState(state, StateBlocked)
		default:
			return invalidTransition(state, input.Action)
		}
	case StateZhongshuSolver:
		switch input.Action {
		case ActionReadyForCritic:
			return moveState(state, StateZhongshuCritic)
		case ActionRequestAnalystEvidence, ActionNeedsMoreEvidence:
			return reviseZhongshuTo(state, StateZhongshuAnalyst)
		case ActionHumanGate:
			return moveState(state, StateWaitingHuman)
		case ActionBlocked:
			return moveState(state, StateBlocked)
		default:
			return invalidTransition(state, input.Action)
		}
	case StateZhongshuCritic:
		switch input.Action {
		case ActionApproveFreeze:
			if input.HasBlockingFindings {
				return TransitionResult{}, ErrBlockingFinding
			}
			return moveState(state, StateZhongshuFreezeCheck)
		case ActionRequestAnalystEvidence:
			return reviseZhongshuTo(state, StateZhongshuAnalyst)
		case ActionRequestSolverRevision, ActionRequestRegroup:
			return reviseZhongshuTo(state, StateZhongshuSolver)
		case ActionRequestProjectRerouting:
			return reviseZhongshuTo(state, StateProjectRouting)
		case ActionHumanGate:
			return moveState(state, StateWaitingHuman)
		case ActionBlocked:
			return moveState(state, StateBlocked)
		default:
			return invalidTransition(state, input.Action)
		}
	case StateZhongshuFreezeCheck:
		if input.Action != ActionFreezePlan {
			return invalidTransition(state, input.Action)
		}
		if input.HasBlockingFindings {
			return TransitionResult{}, ErrBlockingFinding
		}
		return moveState(state, StateZhongshuPlanFrozen)
	case StateZhongshuPlanFrozen:
		return move(state, input.Action, ActionStartMenxiaGroup, StateMenxiaGroupStart)
	case StateMenxiaGroupStart:
		if input.Action != ActionStartMenxiaItem || !activeGroup {
			return invalidTransition(state, input.Action)
		}
		return moveState(state, StateMenxiaItemAnalyst)
	case StateMenxiaItemAnalyst:
		if !activeGroup || !activeItem {
			return invalidTransition(state, input.Action)
		}
		switch input.Action {
		case ActionEvidenceSufficient:
			return moveState(state, StateMenxiaItemSolver)
		case ActionNeedsMoreEvidence:
			return reviseItemTo(state, StateMenxiaItemRevision)
		case ActionHumanGate:
			return moveState(state, StateWaitingHuman)
		case ActionBlocked:
			return moveState(state, StateBlocked)
		default:
			return invalidTransition(state, input.Action)
		}
	case StateMenxiaItemSolver:
		if !activeGroup || !activeItem {
			return invalidTransition(state, input.Action)
		}
		switch input.Action {
		case ActionFeasible, ActionRemove:
			return moveState(state, StateMenxiaItemCritic)
		case ActionRevise, ActionSplit, ActionMerge, ActionNeedsMoreEvidence:
			return reviseItemTo(state, StateMenxiaItemRevision)
		case ActionHumanGate:
			return moveState(state, StateWaitingHuman)
		case ActionBlocked:
			return moveState(state, StateBlocked)
		default:
			return invalidTransition(state, input.Action)
		}
	case StateMenxiaItemCritic:
		if !activeGroup || !activeItem {
			return invalidTransition(state, input.Action)
		}
		switch input.Action {
		case ActionApproveItem, ActionRemoveItem:
			if input.HasNextItem {
				return moveState(state, StateMenxiaItemAnalyst)
			}
			return moveState(state, StateMenxiaGroupGate)
		case ActionReviseItem, ActionSplitItem, ActionMergeItem:
			return reviseItemTo(state, StateMenxiaItemRevision)
		case ActionHumanGate:
			return moveState(state, StateWaitingHuman)
		case ActionBlocked:
			return moveState(state, StateBlocked)
		default:
			return invalidTransition(state, input.Action)
		}
	case StateMenxiaItemRevision:
		if input.Action != ActionRetryItem || !activeGroup || !activeItem {
			return invalidTransition(state, input.Action)
		}
		return moveState(state, StateMenxiaItemAnalyst)
	case StateMenxiaGroupGate:
		if !activeGroup {
			return invalidTransition(state, input.Action)
		}
		switch input.Action {
		case ActionApproveGroup:
			if input.HasBlockingFindings {
				return TransitionResult{}, ErrBlockingFinding
			}
			if input.HasNextGroup {
				return moveState(state, StateMenxiaGroupStart)
			}
			return moveState(state, StateFinalize)
		case ActionReviseGroup:
			return reviseGroupTo(state, StateMenxiaItemRevision)
		case ActionHumanGate:
			return moveState(state, StateWaitingHuman)
		case ActionBlocked:
			return moveState(state, StateBlocked)
		default:
			return invalidTransition(state, input.Action)
		}
	case StateFinalize:
		if input.Action != ActionComplete {
			return invalidTransition(state, input.Action)
		}
		if input.HasActiveDecision {
			return TransitionResult{}, ErrActiveHumanGate
		}
		if input.HasBlockingFindings {
			return TransitionResult{}, ErrBlockingFinding
		}
		return moveState(state, StateDone)
	case StateDone:
		return invalidTransition(state, input.Action)
	default:
		return TransitionResult{}, fmt.Errorf("%w: unknown state %q", ErrInvalidTransition, state.WorkflowState)
	}
}

func move(state TaskState, action Action, expected Action, target WorkflowState) (TransitionResult, error) {
	if action != expected {
		return invalidTransition(state, action)
	}
	return moveState(state, target)
}

func moveState(state TaskState, target WorkflowState) (TransitionResult, error) {
	state.WorkflowState = target
	return TransitionResult{State: state}, nil
}

func invalidTransition(state TaskState, action Action) (TransitionResult, error) {
	return TransitionResult{}, fmt.Errorf("%w: state=%s action=%s", ErrInvalidTransition, state.WorkflowState, action)
}

func reviseZhongshu(state TaskState) (TransitionResult, error) {
	if err := bumpRevision(&state, &state.ZhongshuRevisionRound, MaxZhongshuRevisionRounds); err != nil {
		return TransitionResult{}, err
	}
	return TransitionResult{State: state}, nil
}

func reviseZhongshuTo(state TaskState, target WorkflowState) (TransitionResult, error) {
	if err := bumpRevision(&state, &state.ZhongshuRevisionRound, MaxZhongshuRevisionRounds); err != nil {
		return TransitionResult{}, err
	}
	state.WorkflowState = target
	return TransitionResult{State: state}, nil
}

func reviseItemTo(state TaskState, target WorkflowState) (TransitionResult, error) {
	if err := bumpRevision(&state, &state.ItemRevisionRound, MaxItemRevisionRounds); err != nil {
		return TransitionResult{}, err
	}
	state.WorkflowState = target
	return TransitionResult{State: state}, nil
}

func reviseGroupTo(state TaskState, target WorkflowState) (TransitionResult, error) {
	if err := bumpRevision(&state, &state.GroupRevisionRound, MaxGroupRevisionRounds); err != nil {
		return TransitionResult{}, err
	}
	state.WorkflowState = target
	return TransitionResult{State: state}, nil
}

func bumpRevision(state *TaskState, specific *int, maximum int) error {
	if state.GlobalRevisionRound >= MaxGlobalRevisionRounds || *specific >= maximum {
		return ErrRevisionBudgetExceeded
	}
	state.GlobalRevisionRound++
	*specific++
	return nil
}

func isKnownTransitionAction(action Action) bool {
	switch action {
	case ActionReadyForSolver,
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
		ActionStartProjectRouting,
		ActionStartZhongshuAnalyst,
		ActionFreezePlan,
		ActionStartMenxiaGroup,
		ActionStartMenxiaItem,
		ActionRetryItem,
		ActionResume,
		ActionComplete:
		return true
	default:
		return false
	}
}
