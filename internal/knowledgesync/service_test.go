package knowledgesync

import (
	"context"
	"os"
	"path/filepath"
	"testing"

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
