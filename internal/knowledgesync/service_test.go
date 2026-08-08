package knowledgesync

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"nexus-agents/internal/knowledgegraph"
	"nexus-agents/internal/wikicompiler"
)

type fakeCompiler struct {
	initializeCalls int
	updateCalls     int
}

type fakeGraphCoordinator struct {
	queued     []knowledgegraph.SyncRequest
	queueError error
}

func (f *fakeGraphCoordinator) QueueSync(request knowledgegraph.SyncRequest) error {
	f.queued = append(f.queued, request)
	return f.queueError
}

func (f *fakeGraphCoordinator) SyncNow(context.Context, knowledgegraph.SyncRequest) (knowledgegraph.GraphSyncResult, error) {
	return knowledgegraph.GraphSyncResult{}, nil
}

func (f *fakeGraphCoordinator) State(projectID string) (knowledgegraph.SyncState, error) {
	return knowledgegraph.SyncState{ProjectID: projectID, Status: knowledgegraph.SyncStatusReady}, nil
}

func (f *fakeGraphCoordinator) Runs(string) ([]knowledgegraph.SyncRun, error) {
	return []knowledgegraph.SyncRun{}, nil
}

func (f *fakeCompiler) Initialize(ctx context.Context, input wikicompiler.CompileInput) (wikicompiler.GeneratedBundle, error) {
	f.initializeCalls++
	return testGeneratedBundle(), nil
}

func (f *fakeCompiler) Update(ctx context.Context, input wikicompiler.CompileInput) (wikicompiler.GeneratedBundle, error) {
	f.updateCalls++
	return testGeneratedBundle(), nil
}

func testGeneratedBundle() wikicompiler.GeneratedBundle {
	return wikicompiler.GeneratedBundle{
		Version: "0.2.0",
		Files: map[string][]byte{
			"KnowledgeBase/project/index.md":                 []byte("# Project Domains\n\n- [Service](domains/service/index.md)\n"),
			"KnowledgeBase/project/domains/service/index.md": []byte("---\ntype: Domain\ntitle: Service\ndescription: Service responsibility\nresource: KnowledgeBase/project/domains/service/index.md\ntags: [service]\ntimestamp: 2026-07-19T00:00:00Z\n---\n# Service\n\nThe service owns a stable responsibility.\n"),
		},
	}
}

func TestServiceInitializationIsProposalOnlyAndNoChangeCheckSkipsCompiler(t *testing.T) {
	project := initTestRepository(t)
	writeRepoFile(t, project, "cmd/server/main.go", "package main\n")
	runGit(t, project, "add", ".")
	runGit(t, project, "commit", "-m", "initial")
	if err := SaveProfile(project, DefaultProfile()); err != nil {
		t.Fatal(err)
	}
	compiler := &fakeCompiler{}
	graph := &fakeGraphCoordinator{}
	service := NewService(ServiceOptions{
		Store: NewStateStore(t.TempDir()), Compiler: compiler, KnowledgeGraph: graph,
	})
	result, err := service.InitializePreview(context.Background(), InitializeRequest{
		ProjectRequest: ProjectRequest{ProjectID: "p1", ProjectRoot: project},
	})
	if err != nil {
		t.Fatal(err)
	}
	if result.Proposal == nil || compiler.initializeCalls != 1 {
		t.Fatalf("result/compiler = %#v / %#v", result, compiler)
	}
	if result.Run.Stage != StageCompleted || result.Run.Progress != 100 || result.Run.UpdatedAt == "" {
		t.Fatalf("run progress = %#v", result.Run)
	}
	runs, err := service.Runs("p1")
	if err != nil {
		t.Fatal(err)
	}
	if len(runs) != 1 || runs[0].Stage != StageCompleted || runs[0].Status != "succeeded" {
		t.Fatalf("stored run progress = %#v", runs)
	}
	proposalPaths := map[string]bool{}
	for _, change := range result.Proposal.Changes {
		proposalPaths[change.Path] = true
	}
	for _, expected := range []string{"KnowledgeBase/index.md", "KnowledgeBase/log.md", "KnowledgeBase/project/index.md", "KnowledgeBase/project/domains/service/index.md"} {
		if !proposalPaths[expected] {
			t.Fatalf("initial proposal is missing %s: %#v", expected, result.Proposal.Changes)
		}
	}
	if _, err := os.Stat(filepath.Join(project, "KnowledgeBase", "project", "domains", "service", "index.md")); !os.IsNotExist(err) {
		t.Fatal("preview modified the project knowledge base")
	}
	if _, err := service.ApplyProposal(context.Background(), ProjectRequest{ProjectID: "p1", ProjectRoot: project}, result.Proposal.ID, ApplyProposalInput{}); err != nil {
		t.Fatal(err)
	}
	if len(graph.queued) != 1 || graph.queued[0].SourceID != "project:p1" || graph.queued[0].Revision == "" {
		t.Fatalf("graph queue = %#v", graph.queued)
	}
	check, err := service.CheckUpdates(context.Background(), ProjectRequest{ProjectID: "p1", ProjectRoot: project})
	if err != nil {
		t.Fatal(err)
	}
	if check.State.Status != StatusUpToDate || compiler.updateCalls != 0 {
		t.Fatalf("check/compiler = %#v / %#v", check, compiler)
	}
}

func TestProposalApplyDoesNotFailWhenGraphQueueFails(t *testing.T) {
	project := initTestRepository(t)
	writeRepoFile(t, project, "cmd/server/main.go", "package main\n")
	runGit(t, project, "add", ".")
	runGit(t, project, "commit", "-m", "initial")
	if err := SaveProfile(project, DefaultProfile()); err != nil {
		t.Fatal(err)
	}
	graph := &fakeGraphCoordinator{queueError: os.ErrPermission}
	service := NewService(ServiceOptions{
		Store: NewStateStore(t.TempDir()), Compiler: &fakeCompiler{}, KnowledgeGraph: graph,
	})
	result, err := service.InitializePreview(context.Background(), InitializeRequest{
		ProjectRequest: ProjectRequest{ProjectID: "p1", ProjectRoot: project},
	})
	if err != nil {
		t.Fatal(err)
	}
	applied, err := service.ApplyProposal(
		context.Background(),
		ProjectRequest{ProjectID: "p1", ProjectRoot: project},
		result.Proposal.ID,
		ApplyProposalInput{},
	)
	if err != nil {
		t.Fatal(err)
	}
	if applied.Proposal == nil || applied.Proposal.Status != ProposalApplied {
		t.Fatalf("applied = %#v", applied)
	}
}

func TestCheckUpdatesAutomaticallyGeneratesProposalWhenGitBaselineIsMissing(t *testing.T) {
	project := initTestRepository(t)
	writeRepoFile(t, project, "Server/Program.cs", "class Program {}\n")
	runGit(t, project, "add", ".")
	runGit(t, project, "commit", "-m", "initial")
	if err := SaveProfile(project, DefaultProfile()); err != nil {
		t.Fatal(err)
	}
	service := NewService(ServiceOptions{
		Store: NewStateStore(t.TempDir()), Compiler: &fakeCompiler{},
	})

	result, err := service.CheckUpdates(context.Background(), ProjectRequest{
		ProjectID: "p1", ProjectRoot: project,
	})
	if err != nil {
		t.Fatal(err)
	}
	if result.Proposal == nil || result.Run.Kind != "enrich" {
		t.Fatalf("result = %#v", result)
	}
	if result.Message != "Committed changes were detected; a review proposal was generated automatically." {
		t.Fatalf("message = %q", result.Message)
	}
	runs, err := service.Runs("p1")
	if err != nil {
		t.Fatal(err)
	}
	var foundMissingBase bool
	for _, run := range runs {
		if run.Kind == "check" && run.ReasonCode == "git_base_missing" {
			foundMissingBase = true
			break
		}
	}
	if !foundMissingBase {
		t.Fatalf("runs = %#v", runs)
	}
}

func TestCheckUpdatesReplacesInvalidPendingProposal(t *testing.T) {
	project := initTestRepository(t)
	writeRepoFile(t, project, "Server/Program.cs", "class Program {}\n")
	runGit(t, project, "add", ".")
	runGit(t, project, "commit", "-m", "initial")
	if err := SaveProfile(project, DefaultProfile()); err != nil {
		t.Fatal(err)
	}
	gitState, err := ReadGitState(context.Background(), ExecGitRunner{}, project)
	if err != nil {
		t.Fatal(err)
	}
	store := NewStateStore(t.TempDir())
	invalid := KnowledgeProposal{
		ID: "invalid-pending", ProjectID: "p1", ProjectRoot: project,
		Branch: gitState.Branch, TargetRevision: gitState.Head, Status: ProposalPending,
		Validation: ProposalValidation{Valid: false, Errors: []string{"broken link"}},
		CreatedAt:  time.Now().Add(-time.Minute).Format(time.RFC3339),
	}
	if err := store.SaveProposal(invalid); err != nil {
		t.Fatal(err)
	}
	if err := store.SaveState(KnowledgeSyncState{
		ProjectID: "p1", ProjectRoot: project, Branch: gitState.Branch,
		LastProcessedCommit: gitState.Head, PendingProposalID: invalid.ID,
		Status: StatusProposalPending,
	}); err != nil {
		t.Fatal(err)
	}
	service := NewService(ServiceOptions{
		Store: store, Compiler: &fakeCompiler{},
	})

	result, err := service.CheckUpdates(context.Background(), ProjectRequest{
		ProjectID: "p1", ProjectRoot: project,
	})
	if err != nil {
		t.Fatal(err)
	}
	if result.Proposal == nil || result.Run.Kind != "enrich" {
		t.Fatalf("result = %#v", result)
	}
	reloaded, err := store.LoadProposal("p1", invalid.ID)
	if err != nil {
		t.Fatal(err)
	}
	if reloaded.Status != ProposalInvalid {
		t.Fatalf("invalid proposal status = %s", reloaded.Status)
	}
}
