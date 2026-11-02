package knowledgesync

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"nexus-agents/internal/wikicompiler"
)

func seedBaseline(t *testing.T, profile Profile, files map[string]string) (string, *StateStore, *Service, *fakeCompiler, string) {
	t.Helper()
	project := initTestRepository(t)
	for relative, content := range files {
		writeRepoFile(t, project, relative, content)
	}
	runGit(t, project, "add", ".")
	runGit(t, project, "commit", "-m", "initial")
	if err := SaveProfile(project, profile); err != nil {
		t.Fatal(err)
	}
	baseline, err := ReadGitState(context.Background(), ExecGitRunner{}, project)
	if err != nil {
		t.Fatal(err)
	}
	store := NewStateStore(t.TempDir())
	if err := store.SaveState(KnowledgeSyncState{
		ProjectID: "p1", ProjectRoot: project, Branch: baseline.Branch,
		LastProcessedCommit: baseline.Head, LastSuccessfulAt: time.Now().Format(time.RFC3339),
		Status: StatusReady,
	}); err != nil {
		t.Fatal(err)
	}
	compiler := &fakeCompiler{}
	service := NewService(ServiceOptions{Store: store, Compiler: compiler})
	return project, store, service, compiler, baseline.Head
}

func deltaProfile() Profile {
	profile := DefaultProfile()
	profile.Scan.Rules = []ScanRule{{Pattern: "cmd/**", Action: "include", Priority: "normal"}}
	profile.Scan.Limits.MaxFilesPerUpdate = 10
	return profile
}

func commitChanged(t *testing.T, project string, files map[string]string) string {
	t.Helper()
	for relative, content := range files {
		writeRepoFile(t, project, relative, content)
	}
	runGit(t, project, "add", ".")
	runGit(t, project, "commit", "-m", "change")
	head, err := ReadGitState(context.Background(), ExecGitRunner{}, project)
	if err != nil {
		t.Fatal(err)
	}
	return head.Head
}

func TestUpdateUsesDeltaManifestAndProfileTimeout(t *testing.T) {
	profile := deltaProfile()
	profile.OpenWiki.TimeoutMinutes = 35
	project, store, service, compiler, baseline := seedBaseline(t, profile, map[string]string{
		"cmd/server/main.go":      "package main\n",
		"cmd/server/unchanged.go": "package main\n",
		"Server/Program.cs":       "class Program {}\n",
	})
	commitChanged(t, project, map[string]string{
		"cmd/server/changed.go":  "package main\n",
		"cmd/server/changed2.go": "package main\n",
	})

	result, err := service.CheckAndEnrich(context.Background(), InitializeRequest{
		ProjectRequest: ProjectRequest{ProjectID: "p1", ProjectRoot: project},
	})
	if err != nil {
		t.Fatal(err)
	}
	if compiler.updateCalls != 1 || result.Proposal == nil {
		t.Fatalf("updateCalls=%d result=%#v", compiler.updateCalls, result)
	}
	manifest := map[string]bool{}
	for _, path := range compiler.lastInput.ScanManifest {
		manifest[path] = true
	}
	if !manifest["cmd/server/changed.go"] || !manifest["cmd/server/changed2.go"] {
		t.Fatalf("changed files missing from delta manifest: %#v", compiler.lastInput.ScanManifest)
	}
	if manifest["cmd/server/unchanged.go"] || manifest["Server/Program.cs"] {
		t.Fatalf("unchanged files entered delta manifest: %#v", compiler.lastInput.ScanManifest)
	}
	if len(compiler.lastInput.ScanManifest) > profile.Scan.Limits.MaxFilesPerUpdate {
		t.Fatalf("delta manifest exceeded update limit: %#v", compiler.lastInput.ScanManifest)
	}
	if compiler.lastInput.Timeout != 35*time.Minute {
		t.Fatalf("compile timeout = %s", compiler.lastInput.Timeout)
	}
	var metadata struct {
		ManifestMode  string `json:"manifestMode"`
		ChangedFiles  int    `json:"changedFiles"`
		DeltaFiles    int    `json:"deltaFiles"`
		BaseRevision  string `json:"baseRevision"`
		ManifestFiles int    `json:"manifestFiles"`
	}
	if err := json.Unmarshal(result.Run.Metadata, &metadata); err != nil {
		t.Fatal(err)
	}
	if metadata.ManifestMode != "delta" || metadata.ChangedFiles != 2 || metadata.DeltaFiles < 2 || metadata.BaseRevision != baseline {
		t.Fatalf("metadata = %#v", metadata)
	}
	state, err := store.LoadState("p1")
	if err != nil {
		t.Fatal(err)
	}
	if state.ConsecutiveFailures != 0 || state.NextRetryAt != "" || state.Status != StatusProposalPending {
		t.Fatalf("state = %#v", state)
	}
}

func TestUpdateFailsFastWhenDeltaExceedsLimit(t *testing.T) {
	profile := deltaProfile()
	profile.Scan.Limits.MaxFilesPerUpdate = 2
	project, store, service, compiler, _ := seedBaseline(t, profile, map[string]string{
		"cmd/server/main.go": "package main\n",
	})
	commitChanged(t, project, map[string]string{
		"cmd/server/a.go": "package main\n",
		"cmd/server/b.go": "package main\n",
		"cmd/server/c.go": "package main\n",
	})

	_, err := service.CheckAndEnrich(context.Background(), InitializeRequest{
		ProjectRequest: ProjectRequest{ProjectID: "p1", ProjectRoot: project},
	})
	if err == nil || !strings.Contains(err.Error(), "maxFilesPerUpdate") {
		t.Fatalf("err = %v", err)
	}
	if compiler.updateCalls != 0 {
		t.Fatalf("compiler should not run for an oversized delta, calls=%d", compiler.updateCalls)
	}
	state, loadErr := store.LoadState("p1")
	if loadErr != nil {
		t.Fatal(loadErr)
	}
	if state.ConsecutiveFailures != 1 || state.NextRetryAt == "" || state.Status != StatusFailed {
		t.Fatalf("state = %#v", state)
	}
	if !shouldSkipScheduledSync(state, time.Now()) {
		t.Fatal("next retry in the future should skip automatic scheduling")
	}
}

func TestCompileFailuresBackOffThenBlockAndSuccessResets(t *testing.T) {
	profile := deltaProfile()
	project, store, service, compiler, _ := seedBaseline(t, profile, map[string]string{
		"cmd/server/main.go": "package main\n",
	})
	commitChanged(t, project, map[string]string{
		"cmd/server/changed.go": "package main\n",
	})
	compiler.err = errors.New("openwiki unavailable")
	request := InitializeRequest{ProjectRequest: ProjectRequest{ProjectID: "p1", ProjectRoot: project}}

	for attempt := 1; attempt <= MaxConsecutiveSyncFailures; attempt++ {
		if _, err := service.CheckAndEnrich(context.Background(), request); err == nil {
			t.Fatalf("attempt %d should fail", attempt)
		}
		state, err := store.LoadState("p1")
		if err != nil {
			t.Fatal(err)
		}
		if state.ConsecutiveFailures != attempt {
			t.Fatalf("attempt %d failures = %d", attempt, state.ConsecutiveFailures)
		}
	}
	state, err := store.LoadState("p1")
	if err != nil {
		t.Fatal(err)
	}
	if state.Status != StatusBlocked || state.NextRetryAt != "" {
		t.Fatalf("blocked state = %#v", state)
	}
	if !shouldSkipScheduledSync(state, time.Now()) {
		t.Fatal("blocked state must skip automatic scheduling")
	}

	compiler.err = nil
	if _, err := service.CheckAndEnrich(context.Background(), request); err != nil {
		t.Fatal(err)
	}
	state, err = store.LoadState("p1")
	if err != nil {
		t.Fatal(err)
	}
	if state.ConsecutiveFailures != 0 || state.NextRetryAt != "" || state.Status != StatusProposalPending {
		t.Fatalf("reset state = %#v", state)
	}
}

func TestShouldSkipScheduledSync(t *testing.T) {
	now := time.Now()
	future := now.Add(time.Hour).Format(time.RFC3339)
	past := now.Add(-time.Hour).Format(time.RFC3339)
	cases := []struct {
		name  string
		state KnowledgeSyncState
		want  bool
	}{
		{"healthy", KnowledgeSyncState{}, false},
		{"blocked-status", KnowledgeSyncState{Status: StatusBlocked}, true},
		{"failure-threshold", KnowledgeSyncState{ConsecutiveFailures: MaxConsecutiveSyncFailures}, true},
		{"future-retry", KnowledgeSyncState{NextRetryAt: future}, true},
		{"past-retry", KnowledgeSyncState{NextRetryAt: past}, false},
		{"invalid-retry", KnowledgeSyncState{NextRetryAt: "not-a-time"}, false},
	}
	for _, tc := range cases {
		if got := shouldSkipScheduledSync(tc.state, now); got != tc.want {
			t.Errorf("%s: got %v want %v", tc.name, got, tc.want)
		}
	}
}

func TestCompileInputUsesProfileTimeoutForInitialize(t *testing.T) {
	profile := DefaultProfile()
	profile.OpenWiki.TimeoutMinutes = 45
	project, _, service, compiler, _ := seedBaseline(t, profile, map[string]string{
		"cmd/server/main.go": "package main\n",
	})
	if _, err := service.InitializePreview(context.Background(), InitializeRequest{
		ProjectRequest: ProjectRequest{ProjectID: "p1", ProjectRoot: project},
	}); err != nil {
		t.Fatal(err)
	}
	if compiler.lastInput.Timeout != 45*time.Minute {
		t.Fatalf("initialize timeout = %s", compiler.lastInput.Timeout)
	}
}

func TestDeltaWithDeletedIncludedFileFailsFast(t *testing.T) {
	profile := deltaProfile()
	project, _, service, compiler, _ := seedBaseline(t, profile, map[string]string{
		"cmd/server/deleted.go": "package main\n",
	})
	if err := os.Remove(filepath.Join(project, "cmd/server/deleted.go")); err != nil {
		t.Fatal(err)
	}
	runGit(t, project, "add", "-A")
	runGit(t, project, "commit", "-m", "delete")

	_, err := service.CheckAndEnrich(context.Background(), InitializeRequest{
		ProjectRequest: ProjectRequest{ProjectID: "p1", ProjectRoot: project},
	})
	if err == nil || !strings.Contains(err.Error(), "deleted or renamed") {
		t.Fatalf("err = %v", err)
	}
	if compiler.updateCalls != 0 {
		t.Fatalf("compiler should not run for deleted included files, calls=%d", compiler.updateCalls)
	}
}

var _ wikicompiler.Provider = (*fakeCompiler)(nil)
