package catalog

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

const ActiveWorkflowSessionTTL = 24 * time.Hour

type ActiveWorkflowSession struct {
	SessionID          string `json:"sessionId"`
	WorkflowRunID      string `json:"workflowRunId"`
	ProjectID          string `json:"projectId"`
	WorkflowTemplateID string `json:"workflowTemplateId,omitempty"`
	WorkflowCopyID     string `json:"workflowCopyId,omitempty"`
	WorkflowType       string `json:"workflowType"`
	TaskTitle          string `json:"taskTitle,omitempty"`
	CurrentRole        string `json:"currentRole,omitempty"`
	StartedAt          string `json:"startedAt"`
	UpdatedAt          string `json:"updatedAt"`
	ExpiresAt          string `json:"expiresAt"`
	Status             string `json:"status"`
}

type ActiveWorkflowSessionStore struct {
	mu       sync.Mutex
	path     string
	sessions map[string]ActiveWorkflowSession
	now      func() time.Time
}

func NewDefaultActiveWorkflowSessionStore() (*ActiveWorkflowSessionStore, error) {
	return NewActiveWorkflowSessionStore(defaultActiveWorkflowSessionPath())
}

func NewActiveWorkflowSessionStore(path string) (*ActiveWorkflowSessionStore, error) {
	if strings.TrimSpace(path) == "" {
		return nil, fmt.Errorf("active workflow session store path is empty")
	}
	store := &ActiveWorkflowSessionStore{path: path, sessions: map[string]ActiveWorkflowSession{}, now: time.Now}
	if err := store.load(); err != nil {
		return nil, err
	}
	return store, nil
}

func (s *ActiveWorkflowSessionStore) Bind(session ActiveWorkflowSession) error {
	if strings.TrimSpace(session.SessionID) == "" {
		return nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.pruneExpiredLocked()
	now := s.now().UTC()
	session.Status = firstNonEmpty(strings.TrimSpace(session.Status), "active")
	session.UpdatedAt = now.Format(time.RFC3339Nano)
	if strings.TrimSpace(session.StartedAt) == "" {
		session.StartedAt = session.UpdatedAt
	}
	session.ExpiresAt = now.Add(ActiveWorkflowSessionTTL).Format(time.RFC3339Nano)
	s.sessions[session.SessionID] = session
	return s.saveLocked()
}

func (s *ActiveWorkflowSessionStore) Lookup(sessionID string) (ActiveWorkflowSession, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.pruneExpiredLocked()
	session, ok := s.sessions[strings.TrimSpace(sessionID)]
	if !ok {
		return ActiveWorkflowSession{}, false, nil
	}
	return session, true, nil
}

func (s *ActiveWorkflowSessionStore) UpdateRole(sessionID, role string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.pruneExpiredLocked()
	session, ok := s.sessions[strings.TrimSpace(sessionID)]
	if !ok {
		return nil
	}
	now := s.now().UTC()
	session.CurrentRole = strings.TrimSpace(role)
	session.UpdatedAt = now.Format(time.RFC3339Nano)
	session.ExpiresAt = now.Add(ActiveWorkflowSessionTTL).Format(time.RFC3339Nano)
	s.sessions[session.SessionID] = session
	return s.saveLocked()
}

func (s *ActiveWorkflowSessionStore) Complete(sessionID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.pruneExpiredLocked()
	delete(s.sessions, strings.TrimSpace(sessionID))
	return s.saveLocked()
}

func (s *ActiveWorkflowSessionStore) Sessions() ([]ActiveWorkflowSession, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.pruneExpiredLocked()
	items := make([]ActiveWorkflowSession, 0, len(s.sessions))
	for _, session := range s.sessions {
		items = append(items, session)
	}
	sort.Slice(items, func(i, j int) bool { return items[i].UpdatedAt > items[j].UpdatedAt })
	return items, nil
}

func (s *ActiveWorkflowSessionStore) load() error {
	data, err := os.ReadFile(s.path)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("read active workflow session store: %w", err)
	}
	var items []ActiveWorkflowSession
	if err := json.Unmarshal(data, &items); err != nil {
		return fmt.Errorf("parse active workflow session store: %w", err)
	}
	for _, item := range items {
		s.sessions[item.SessionID] = item
	}
	return nil
}

func (s *ActiveWorkflowSessionStore) saveLocked() error {
	if err := os.MkdirAll(filepath.Dir(s.path), 0o755); err != nil {
		return fmt.Errorf("create active workflow session store dir: %w", err)
	}
	items := make([]ActiveWorkflowSession, 0, len(s.sessions))
	for _, session := range s.sessions {
		items = append(items, session)
	}
	sort.Slice(items, func(i, j int) bool { return items[i].UpdatedAt > items[j].UpdatedAt })
	data, err := json.MarshalIndent(items, "", "  ")
	if err != nil {
		return fmt.Errorf("encode active workflow sessions: %w", err)
	}
	return os.WriteFile(s.path, data, 0o644)
}

func (s *ActiveWorkflowSessionStore) pruneExpiredLocked() {
	now := s.now().UTC()
	for sessionID, session := range s.sessions {
		expiresAt, err := time.Parse(time.RFC3339Nano, session.ExpiresAt)
		if err != nil || !expiresAt.After(now) {
			delete(s.sessions, sessionID)
		}
	}
}

func defaultActiveWorkflowSessionPath() string {
	home, err := os.UserHomeDir()
	if err != nil || strings.TrimSpace(home) == "" {
		return filepath.Join(".nexus-evaluation", "active-workflow-sessions.json")
	}
	nexusPath := filepath.Join(home, ".nexus")
	if stat, err := os.Stat(nexusPath); err == nil && stat.IsDir() {
		return filepath.Join(nexusPath, "active-workflow-sessions.json")
	}
	return filepath.Join(home, ".nexus-evaluation", "active-workflow-sessions.json")
}
