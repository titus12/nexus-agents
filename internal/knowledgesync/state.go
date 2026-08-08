package knowledgesync

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type SyncStatus string

const (
	StatusUninitialized   SyncStatus = "uninitialized"
	StatusReady           SyncStatus = "ready"
	StatusChecking        SyncStatus = "checking"
	StatusUpdateAvailable SyncStatus = "update_available"
	StatusProposalPending SyncStatus = "proposal_pending"
	StatusApplying        SyncStatus = "applying"
	StatusUpToDate        SyncStatus = "up_to_date"
	StatusFailed          SyncStatus = "failed"
)

type SyncStage string

const (
	StagePreparing                SyncStage = "preparing"
	StageLoadingProfile           SyncStage = "loading_profile"
	StageReadingGit               SyncStage = "reading_git"
	StageScanningRepository       SyncStage = "scanning_repository"
	StageAnalyzingCodeGraph       SyncStage = "analyzing_codegraph"
	StageFetchingExternalEvidence SyncStage = "fetching_external_evidence"
	StagePreparingKnowledge       SyncStage = "preparing_knowledge"
	StageCompilingOpenWiki        SyncStage = "compiling_openwiki"
	StageValidatingProposal       SyncStage = "validating_proposal"
	StageGeneratingProposal       SyncStage = "generating_proposal"
	StageCompleted                SyncStage = "completed"
	StageFailed                   SyncStage = "failed"
)

type KnowledgeSyncState struct {
	ProjectID           string     `json:"projectId"`
	ProjectRoot         string     `json:"projectRoot"`
	Branch              string     `json:"branch"`
	LastProcessedCommit string     `json:"lastProcessedCommit"`
	LastKnowledgeHash   string     `json:"lastKnowledgeHash"`
	LastCheckedAt       string     `json:"lastCheckedAt"`
	LastSuccessfulAt    string     `json:"lastSuccessfulAt"`
	Status              SyncStatus `json:"status"`
	PendingProposalID   string     `json:"pendingProposalId,omitempty"`
	CompilerVersion     string     `json:"compilerVersion"`
	ProfileHash         string     `json:"profileHash"`
	LastError           string     `json:"lastError,omitempty"`
}

type SyncRun struct {
	ID             string          `json:"id"`
	ProjectID      string          `json:"projectId"`
	Kind           string          `json:"kind"`
	Status         string          `json:"status"`
	Stage          SyncStage       `json:"stage,omitempty"`
	StageMessage   string          `json:"stageMessage,omitempty"`
	Progress       int             `json:"progress,omitempty"`
	Branch         string          `json:"branch"`
	BaseRevision   string          `json:"baseRevision,omitempty"`
	TargetRevision string          `json:"targetRevision"`
	ChangeClass    ChangeClass     `json:"changeClass,omitempty"`
	ReasonCode     string          `json:"reasonCode,omitempty"`
	Reason         string          `json:"reason,omitempty"`
	NextAction     string          `json:"nextAction,omitempty"`
	ProposalID     string          `json:"proposalId,omitempty"`
	Warnings       []string        `json:"warnings"`
	Error          string          `json:"error,omitempty"`
	StartedAt      string          `json:"startedAt"`
	UpdatedAt      string          `json:"updatedAt,omitempty"`
	EndedAt        string          `json:"endedAt,omitempty"`
	Metadata       json.RawMessage `json:"metadata,omitempty"`
}

const maxRetainedKnowledgeRecords = 10

type StateStore struct {
	Root string
}

func DefaultStateRoot() string {
	if value := strings.TrimSpace(os.Getenv("NEXUS_KNOWLEDGE_SYNC_DIR")); value != "" {
		return value
	}
	if home, err := os.UserHomeDir(); err == nil {
		return filepath.Join(home, ".nexus", "knowledge-sync")
	}
	return filepath.Join(".nexus-agents", "knowledge-sync")
}

func NewStateStore(root string) *StateStore {
	if strings.TrimSpace(root) == "" {
		root = DefaultStateRoot()
	}
	return &StateStore{Root: root}
}

func (s *StateStore) LoadState(projectID string) (KnowledgeSyncState, error) {
	var state KnowledgeSyncState
	err := readJSONFile(s.projectPath(projectID, "state.json"), &state)
	if os.IsNotExist(err) {
		return KnowledgeSyncState{ProjectID: projectID, Status: StatusUninitialized}, nil
	}
	return state, err
}

func (s *StateStore) SaveState(state KnowledgeSyncState) error {
	if strings.TrimSpace(state.ProjectID) == "" {
		return fmt.Errorf("state project id is empty")
	}
	return atomicWriteJSON(s.projectPath(state.ProjectID, "state.json"), state)
}

func (s *StateStore) SaveRun(run SyncRun) error {
	if run.Warnings == nil {
		run.Warnings = []string{}
	}
	target := s.projectPath(run.ProjectID, "runs", safeID(run.ID)+".json")
	if err := atomicWriteJSON(target, run); err != nil {
		return err
	}
	trimKnowledgeRuns(filepath.Dir(target), maxRetainedKnowledgeRecords)
	return nil
}

func (s *StateStore) ListRuns(projectID string) ([]SyncRun, error) {
	dir := s.projectPath(projectID, "runs")
	entries, err := os.ReadDir(dir)
	if os.IsNotExist(err) {
		return []SyncRun{}, nil
	}
	if err != nil {
		return nil, err
	}
	var runs []SyncRun
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		var run SyncRun
		if err := readJSONFile(filepath.Join(dir, entry.Name()), &run); err == nil {
			runs = append(runs, run)
		}
	}
	sort.Slice(runs, func(i, j int) bool { return runs[i].StartedAt > runs[j].StartedAt })
	if len(runs) > maxRetainedKnowledgeRecords {
		trimKnowledgeRuns(dir, maxRetainedKnowledgeRecords)
		runs = runs[:maxRetainedKnowledgeRecords]
	}
	return runs, nil
}

func trimKnowledgeRuns(directory string, limit int) {
	if limit <= 0 {
		return
	}
	entries, err := os.ReadDir(directory)
	if err != nil {
		return
	}
	type record struct {
		path      string
		startedAt time.Time
		modTime   time.Time
	}
	records := make([]record, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		info, infoErr := entry.Info()
		if infoErr != nil {
			continue
		}
		var run SyncRun
		if readErr := readJSONFile(filepath.Join(directory, entry.Name()), &run); readErr != nil {
			continue
		}
		startedAt, parseErr := time.Parse(time.RFC3339, run.StartedAt)
		if parseErr != nil {
			startedAt = info.ModTime()
		}
		records = append(records, record{
			path: filepath.Join(directory, entry.Name()), startedAt: startedAt, modTime: info.ModTime(),
		})
	}
	sort.Slice(records, func(i, j int) bool {
		if records[i].startedAt.Equal(records[j].startedAt) {
			return records[i].modTime.After(records[j].modTime)
		}
		return records[i].startedAt.After(records[j].startedAt)
	})
	for _, stale := range records[minimumInt(limit, len(records)):] {
		_ = os.Remove(stale.path)
	}
}

func minimumInt(left, right int) int {
	if left < right {
		return left
	}
	return right
}

func (s *StateStore) projectPath(projectID string, parts ...string) string {
	all := append([]string{s.Root, safeID(projectID)}, parts...)
	return filepath.Join(all...)
}

func safeID(value string) string {
	value = strings.TrimSpace(strings.ToLower(value))
	if value == "" {
		return "project"
	}
	var builder strings.Builder
	for _, r := range value {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || r == '-' || r == '_' {
			builder.WriteRune(r)
		} else {
			builder.WriteByte('_')
		}
	}
	return builder.String()
}

func atomicWriteJSON(target string, value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	return atomicWriteFile(target, data, 0o600)
}

func atomicWriteFile(target string, data []byte, mode os.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return err
	}
	temp, err := os.CreateTemp(filepath.Dir(target), "."+filepath.Base(target)+".tmp-*")
	if err != nil {
		return err
	}
	tempName := temp.Name()
	defer os.Remove(tempName)
	if err := temp.Chmod(mode); err != nil {
		_ = temp.Close()
		return err
	}
	if _, err := temp.Write(data); err != nil {
		_ = temp.Close()
		return err
	}
	if err := temp.Sync(); err != nil {
		_ = temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	backup := target + ".backup"
	_ = os.Remove(backup)
	hadTarget := false
	if _, err := os.Stat(target); err == nil {
		hadTarget = true
		if err := os.Rename(target, backup); err != nil {
			return err
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	if err := os.Rename(tempName, target); err != nil {
		if hadTarget {
			_ = os.Rename(backup, target)
		}
		return err
	}
	if hadTarget {
		_ = os.Remove(backup)
	}
	return nil
}

func readJSONFile(target string, value any) error {
	data, err := os.ReadFile(target)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, value)
}

func newRunID(projectID, kind string) string {
	return safeID(kind) + "-" + safeID(projectID) + "-" + time.Now().UTC().Format("20060102T150405.000000000Z")
}
