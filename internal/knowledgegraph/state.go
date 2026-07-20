package knowledgegraph

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

type SyncStatus string

const (
	SyncStatusDisabled      SyncStatus = "disabled"
	SyncStatusUninitialized SyncStatus = "uninitialized"
	SyncStatusPending       SyncStatus = "pending"
	SyncStatusSyncing       SyncStatus = "syncing"
	SyncStatusReady         SyncStatus = "ready"
	SyncStatusDegraded      SyncStatus = "degraded"
)

const shadowRunRetention = 500

type SyncState struct {
	ProjectID          string            `json:"projectId"`
	ProjectRoot        string            `json:"projectRoot"`
	SourceID           string            `json:"sourceId"`
	ProviderSourceID   string            `json:"providerSourceId"`
	Status             SyncStatus        `json:"status"`
	Branch             string            `json:"branch,omitempty"`
	LastSyncedRevision string            `json:"lastSyncedRevision,omitempty"`
	PendingRevision    string            `json:"pendingRevision,omitempty"`
	LastSourceHash     string            `json:"lastSourceHash,omitempty"`
	Documents          int               `json:"documents"`
	DocumentHashes     map[string]string `json:"documentHashes"`
	LastAttemptAt      string            `json:"lastAttemptAt,omitempty"`
	LastSuccessfulAt   string            `json:"lastSuccessfulAt,omitempty"`
	LastError          string            `json:"lastError,omitempty"`
}

type SyncRun struct {
	ID               string     `json:"id"`
	ProjectID        string     `json:"projectId"`
	SourceID         string     `json:"sourceId"`
	ProviderSourceID string     `json:"providerSourceId"`
	Status           SyncStatus `json:"status"`
	Revision         string     `json:"revision,omitempty"`
	SourceHash       string     `json:"sourceHash,omitempty"`
	Documents        int        `json:"documents"`
	Created          int        `json:"created"`
	Updated          int        `json:"updated"`
	Deleted          int        `json:"deleted"`
	Unchanged        int        `json:"unchanged"`
	Attempt          int        `json:"attempt"`
	Error            string     `json:"error,omitempty"`
	StartedAt        string     `json:"startedAt"`
	EndedAt          string     `json:"endedAt,omitempty"`
}

type GraphStateStore struct {
	Root string
	mu   sync.RWMutex
}

func DefaultGraphStateRoot() string {
	if value := strings.TrimSpace(os.Getenv("NEXUS_KNOWLEDGE_GRAPH_DIR")); value != "" {
		return value
	}
	if home, err := os.UserHomeDir(); err == nil {
		return filepath.Join(home, ".nexus", "knowledge-graph")
	}
	return filepath.Join(".nexus-agents", "knowledge-graph")
}

func NewGraphStateStore(root string) *GraphStateStore {
	if strings.TrimSpace(root) == "" {
		root = DefaultGraphStateRoot()
	}
	return &GraphStateStore{Root: root}
}

func (s *GraphStateStore) Load(projectID string) (SyncState, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	var state SyncState
	err := readGraphJSON(s.projectPath(projectID, "state.json"), &state)
	if os.IsNotExist(err) {
		return SyncState{
			ProjectID: projectID, Status: SyncStatusUninitialized, DocumentHashes: map[string]string{},
		}, nil
	}
	if state.DocumentHashes == nil {
		state.DocumentHashes = map[string]string{}
	}
	return state, err
}

func (s *GraphStateStore) Save(state SyncState) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if strings.TrimSpace(state.ProjectID) == "" {
		return fmt.Errorf("knowledge graph state project id is empty")
	}
	if state.DocumentHashes == nil {
		state.DocumentHashes = map[string]string{}
	}
	return atomicWriteGraphJSON(s.projectPath(state.ProjectID, "state.json"), state)
}

func (s *GraphStateStore) SaveRun(run SyncRun) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if strings.TrimSpace(run.ProjectID) == "" || strings.TrimSpace(run.ID) == "" {
		return fmt.Errorf("knowledge graph run identity is empty")
	}
	return atomicWriteGraphJSON(s.projectPath(run.ProjectID, "runs", safeGraphID(run.ID)+".json"), run)
}

func (s *GraphStateStore) Runs(projectID string) ([]SyncRun, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	directory := s.projectPath(projectID, "runs")
	entries, err := os.ReadDir(directory)
	if os.IsNotExist(err) {
		return []SyncRun{}, nil
	}
	if err != nil {
		return nil, err
	}
	runs := make([]SyncRun, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		var run SyncRun
		if err := readGraphJSON(filepath.Join(directory, entry.Name()), &run); err == nil {
			runs = append(runs, run)
		}
	}
	sort.Slice(runs, func(i, j int) bool { return runs[i].StartedAt > runs[j].StartedAt })
	return runs, nil
}

func (s *GraphStateStore) SaveShadowRun(run ShadowSearchRun) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if strings.TrimSpace(run.ProjectID) == "" || strings.TrimSpace(run.ID) == "" {
		return fmt.Errorf("shadow search run identity is empty")
	}
	directory := s.projectPath(run.ProjectID, "shadow-runs")
	if err := atomicWriteGraphJSON(filepath.Join(directory, safeGraphID(run.ID)+".json"), run); err != nil {
		return err
	}
	return trimGraphRunDirectory(directory, shadowRunRetention)
}

func (s *GraphStateStore) ShadowRuns(projectID string, limit int) ([]ShadowSearchRun, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	directory := s.projectPath(projectID, "shadow-runs")
	entries, err := os.ReadDir(directory)
	if os.IsNotExist(err) {
		return []ShadowSearchRun{}, nil
	}
	if err != nil {
		return nil, err
	}
	runs := make([]ShadowSearchRun, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		var run ShadowSearchRun
		if err := readGraphJSON(filepath.Join(directory, entry.Name()), &run); err == nil {
			runs = append(runs, run)
		}
	}
	sort.Slice(runs, func(i, j int) bool { return runs[i].StartedAt > runs[j].StartedAt })
	if limit <= 0 || limit > shadowRunRetention {
		limit = shadowRunRetention
	}
	if len(runs) > limit {
		runs = runs[:limit]
	}
	return runs, nil
}

func trimGraphRunDirectory(directory string, retain int) error {
	entries, err := os.ReadDir(directory)
	if err != nil {
		return err
	}
	type runFile struct {
		name    string
		modTime time.Time
	}
	files := make([]runFile, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		files = append(files, runFile{name: entry.Name(), modTime: info.ModTime()})
	}
	sort.Slice(files, func(i, j int) bool { return files[i].modTime.After(files[j].modTime) })
	if len(files) <= retain {
		return nil
	}
	for _, file := range files[retain:] {
		if err := os.Remove(filepath.Join(directory, file.name)); err != nil && !os.IsNotExist(err) {
			return err
		}
	}
	return nil
}

func (s *GraphStateStore) projectPath(projectID string, parts ...string) string {
	values := append([]string{s.Root, safeGraphID(projectID)}, parts...)
	return filepath.Join(values...)
}

func atomicWriteGraphJSON(target string, value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return err
	}
	temp, err := os.CreateTemp(filepath.Dir(target), "."+filepath.Base(target)+".tmp-")
	if err != nil {
		return err
	}
	tempPath := temp.Name()
	defer os.Remove(tempPath)
	if err := temp.Chmod(0o600); err != nil {
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
	if err := os.Rename(tempPath, target); err != nil {
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

func readGraphJSON(target string, value any) error {
	data, err := os.ReadFile(target)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, value)
}

func safeGraphID(value string) string {
	original := strings.ToLower(strings.TrimSpace(value))
	var builder strings.Builder
	lastSeparator := false
	for _, char := range original {
		if (char >= 'a' && char <= 'z') || (char >= '0' && char <= '9') || char == '-' || char == '_' {
			builder.WriteRune(char)
			lastSeparator = false
		} else {
			if !lastSeparator && builder.Len() > 0 {
				builder.WriteByte('_')
				lastSeparator = true
			}
		}
	}
	normalized := strings.Trim(builder.String(), "_-")
	if normalized == "" {
		normalized = "project"
	}
	if normalized != original {
		sum := sha256.Sum256([]byte(original))
		normalized = strings.TrimRight(normalized, "_-") + "-" + hex.EncodeToString(sum[:4])
	}
	return normalized
}

func newGraphRunID(projectID string) string {
	return "graph-sync-" + safeGraphID(projectID) + "-" + time.Now().UTC().Format("20060102T150405.000000000Z")
}
