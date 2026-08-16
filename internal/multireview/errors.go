package multireview

import "errors"

var (
	ErrInvalidID                   = errors.New("invalid canonical id")
	ErrInvalidAction               = errors.New("invalid action")
	ErrInvalidTransition           = errors.New("invalid workflow transition")
	ErrImmutablePlan               = errors.New("frozen plan is immutable")
	ErrActiveHumanGate             = errors.New("human gate is active")
	ErrBlockingFinding             = errors.New("blocking finding remains unresolved")
	ErrRevisionBudgetExceeded      = errors.New("revision budget exceeded")
	ErrInvalidArtifact             = errors.New("invalid artifact")
	ErrInvalidFinding              = errors.New("invalid finding")
	ErrInvalidScore                = errors.New("invalid score")
	ErrInvalidFrozenPlan           = errors.New("invalid frozen plan")
	ErrInvalidAmendment            = errors.New("invalid review amendment")
	ErrDuplicateArtifactRevision   = errors.New("duplicate artifact revision")
	ErrDuplicateMessageConsumption = errors.New("message already consumed")
)
