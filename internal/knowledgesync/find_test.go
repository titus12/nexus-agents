package knowledgesync

import (
	"context"
	"path/filepath"
	"strings"
	"testing"

	"nexus-agents/internal/knowledgegraph"
)

func TestFindKnowledgeUsesGBrainAcrossProjectScopeAndLoadsApprovedMarkdown(t *testing.T) {
	root := t.TempDir()
	exportRoot := func(projectID string) string { return filepath.Join(root, "exports", projectID) }
	projectA1 := t.TempDir()
	projectA2 := t.TempDir()
	for _, item := range []struct {
		id      string
		root    string
		content string
	}{
		{id: "a1", root: projectA1, content: "A1 owns the guild HTTP entrypoint."},
		{id: "a2", root: projectA2, content: "A2 owns the guild member workflow."},
	} {
		writeRepoFile(t, item.root, "KnowledgeBase/index.md", "# KnowledgeBase\n\n[Project](project/index.md)\n")
		writeRepoFile(t, item.root, "KnowledgeBase/log.md", "# Log\n\nInitial knowledge log entry.\n")
		writeRepoFile(t, item.root, "KnowledgeBase/project/index.md", "# "+strings.ToUpper(item.id)+"\n\n"+item.content+"\n")
		if err := SaveProfile(item.root, DefaultProfile()); err != nil {
			t.Fatal(err)
		}
		if _, err := knowledgegraph.ExportApprovedKnowledge(knowledgegraph.ExportRequest{
			ProjectID: item.id, ProjectRoot: item.root,
			SourceID: "project:" + item.id, ProviderSourceID: "project-" + item.id,
			Revision: "rev-" + item.id, ExportRoot: exportRoot(item.id),
			IncludeDomains: true, IncludeFeatures: true,
		}); err != nil {
			t.Fatal(err)
		}
	}

	provider := &knowledgegraph.FakeProvider{SearchResult: knowledgegraph.GraphSearchResult{
		Hits: []knowledgegraph.GraphSearchHit{
			{SourceID: "project:a1", Path: "project", Title: "A1", Snippet: "guild HTTP entrypoint", Score: 0.9},
			{SourceID: "project:a2", Path: "project", Title: "A2", Snippet: "guild member workflow", Score: 0.8},
		},
	}}
	graph := knowledgegraph.NewService(knowledgegraph.ServiceOptions{
		Provider: provider, ExportRoot: exportRoot, Logf: func(string, ...any) {},
	})
	service := NewService(ServiceOptions{KnowledgeGraph: graph})
	result, err := service.FindKnowledge(
		context.Background(),
		ProjectRequest{ProjectID: "a1", ProjectRoot: projectA1},
		[]ProjectRequest{
			{ProjectID: "a1", ProjectRoot: projectA1},
			{ProjectID: "a2", ProjectRoot: projectA2},
		},
		"guild member workflow",
		FindOptions{Mode: "context", MaxTokens: 6000, Scope: "group"},
	)
	if err != nil {
		t.Fatal(err)
	}
	if result.Engine != "gbrain" || result.Degraded || result.Scope != "group" || len(result.Sources) != 2 {
		t.Fatalf("result = %#v", result)
	}
	for _, expected := range []string{
		"A1 owns the guild HTTP entrypoint.",
		"A2 owns the guild member workflow.",
		"Revision: `rev-a1`",
		"Revision: `rev-a2`",
	} {
		if !strings.Contains(result.LoadedKnowledgeMarkdown, expected) {
			t.Fatalf("context missing %q:\n%s", expected, result.LoadedKnowledgeMarkdown)
		}
	}
	if len(provider.SearchQueries) != 1 || len(provider.SearchQueries[0].SourceIDs) != 2 {
		t.Fatalf("search queries = %#v", provider.SearchQueries)
	}
}

func TestFindKnowledgeFallsBackToExistingFTS5Contract(t *testing.T) {
	project := t.TempDir()
	writeRepoFile(t, project, "KnowledgeBase/project/index.md", "# Fallback Project\n\nGuild fallback knowledge.\n")
	service := NewService(ServiceOptions{KnowledgeGraph: &fakeGraphCoordinator{}})
	result, err := service.FindKnowledge(
		context.Background(),
		ProjectRequest{ProjectID: "sample", ProjectRoot: project},
		nil,
		"guild fallback",
		FindOptions{Mode: "context", MaxTokens: 6000},
	)
	if err != nil {
		t.Fatal(err)
	}
	if result.Engine != "fts5" || !result.Degraded || result.FallbackReason == "" {
		t.Fatalf("result = %#v", result)
	}
	if !strings.Contains(result.LoadedKnowledgeMarkdown, "Loaded Knowledge") {
		t.Fatalf("fallback context = %q", result.LoadedKnowledgeMarkdown)
	}
}
