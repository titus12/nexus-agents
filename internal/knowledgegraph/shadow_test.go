package knowledgegraph

import (
	"context"
	"errors"
	"testing"
	"time"
)

type timeoutSearchProvider struct {
	*FakeProvider
}

func (p *timeoutSearchProvider) Search(ctx context.Context, query GraphSearchQuery) (GraphSearchResult, error) {
	<-ctx.Done()
	return GraphSearchResult{}, ctx.Err()
}

func TestShadowSearchComparesFTS5AndGBrainWithoutMerging(t *testing.T) {
	store := NewGraphStateStore(t.TempDir())
	provider := &FakeProvider{
		HealthResult: GraphHealth{Provider: "gbrain", Status: GraphStatusReady, RestartCount: 2},
		SearchResult: GraphSearchResult{Hits: []GraphSearchHit{
			{Path: "features/model-routing/proxy-model-families", Score: 0.98},
			{Path: "domains/model-routing", Score: 0.72},
		}},
	}
	service := NewService(ServiceOptions{Provider: provider, Store: store, Logf: func(string, ...any) {}})
	run, err := service.ShadowSearchNow(context.Background(), ShadowSearchRequest{
		ProjectID: "sample", ProjectRoot: t.TempDir(), SourceID: "project:sample",
		Query: "修改模型路由会影响哪些入口", Limit: 10, Timeout: time.Second,
		FTS5: ShadowSearchBaseline{
			MatchedProject: true, MatchedDomain: "model-routing",
			Paths: []string{
				"KnowledgeBase/project/domains/model-routing/index.md",
				"KnowledgeBase/project/domains/model-routing/proxy-model-families.md",
			},
			RequiredPaths: []string{"KnowledgeBase/project/domains/model-routing/proxy-model-families.md"},
			LatencyMS:     12, TokenCount: 420,
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if run.Status != "ready" || !run.Comparison.DomainMatched {
		t.Fatalf("run = %#v", run)
	}
	if len(run.Comparison.PathOverlap) != 2 || len(run.Comparison.RequiredMatchedPaths) != 1 {
		t.Fatalf("comparison = %#v", run.Comparison)
	}
	if run.Comparison.RequiredDocumentPrecision != 0.5 || run.Comparison.ExpectedDocumentCoverage != 1 {
		t.Fatalf("comparison metrics = %#v", run.Comparison)
	}
	if len(provider.SearchQueries) != 1 || provider.SearchQueries[0].SourceIDs[0] != "project:sample" {
		t.Fatalf("queries = %#v", provider.SearchQueries)
	}
	runs, err := service.ShadowSearchRuns("sample", 10)
	if err != nil || len(runs) != 1 || runs[0].ID != run.ID {
		t.Fatalf("runs=%#v err=%v", runs, err)
	}
	summary, err := service.ShadowSearchSummary("sample")
	if err != nil {
		t.Fatal(err)
	}
	if summary.Runs != 1 || summary.Succeeded != 1 || summary.DomainMatchRate != 1 ||
		summary.AverageExpectedCoverage != 1 || summary.GBrainP95LatencyMS < 0 {
		t.Fatalf("summary = %#v", summary)
	}
}

func TestShadowSearchTimeoutIsRecordedAsDegraded(t *testing.T) {
	service := NewService(ServiceOptions{
		Provider: &timeoutSearchProvider{FakeProvider: &FakeProvider{
			HealthResult: GraphHealth{Provider: "gbrain", Status: GraphStatusReady},
		}},
		Store: NewGraphStateStore(t.TempDir()),
		Logf:  func(string, ...any) {},
	})
	run, err := service.ShadowSearchNow(context.Background(), ShadowSearchRequest{
		ProjectID: "sample", SourceID: "project:sample", Query: "timeout",
		Timeout: 10 * time.Millisecond,
		FTS5:    ShadowSearchBaseline{MatchedProject: true, Paths: []string{"KnowledgeBase/project/index.md"}},
	})
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("error = %v", err)
	}
	if run.Status != "degraded" || !run.GBrain.TimedOut || run.GBrain.Error == "" {
		t.Fatalf("run = %#v", run)
	}
	runs, loadErr := service.ShadowSearchRuns("sample", 10)
	if loadErr != nil || len(runs) != 1 || !runs[0].GBrain.TimedOut {
		t.Fatalf("runs=%#v err=%v", runs, loadErr)
	}
}

func TestNormalizeGraphComparablePathUsesExportSlugRules(t *testing.T) {
	cases := map[string]string{
		"KnowledgeBase/project/index.md":                                      "project",
		"KnowledgeBase/project/service.md":                                    "documents/service",
		"KnowledgeBase/project/domains/model-routing/index.md":                "domains/model-routing",
		"KnowledgeBase/project/domains/model-routing/proxy-model-families.md": "features/model-routing/proxy-model-families",
		"KnowledgeBase/project/domains/knowledgebase/context/retrieval.md":    "features/knowledgebase/context/retrieval",
		"features/model-routing/proxy-model-families":                         "features/model-routing/proxy-model-families",
	}
	for input, want := range cases {
		if got := normalizeGraphComparablePath(input); got != want {
			t.Fatalf("normalize %q = %q, want %q", input, got, want)
		}
	}
}

func TestInferGraphDomainUsesAggregateHitScores(t *testing.T) {
	hits := []GraphSearchHit{
		{Path: "features/knowledgebase/repository-scanning-and-indexing", Score: 0.4},
		{Path: "domains/template-management", Score: 0.8},
		{Path: "features/template-management/initialization-versus-synchronization", Score: 0.7},
	}
	if got := inferGraphDomain(hits); got != "template-management" {
		t.Fatalf("domain = %q", got)
	}
}

func TestShadowSearchUsesSourceScopedPathsForMultiSourceComparison(t *testing.T) {
	provider := &FakeProvider{
		HealthResult: GraphHealth{Provider: "gbrain", Status: GraphStatusReady},
		SearchResult: GraphSearchResult{Hits: []GraphSearchHit{
			{SourceID: "project:a1", Path: "features/guild/member", Score: 1},
			{SourceID: "project:a2", Path: "features/guild/member", Score: 1},
		}},
	}
	service := NewService(ServiceOptions{
		Provider: provider, Store: NewGraphStateStore(t.TempDir()), Logf: func(string, ...any) {},
	})
	run, err := service.ShadowSearchNow(context.Background(), ShadowSearchRequest{
		ProjectID: "a1", SourceID: "project:a1",
		SourceIDs: []string{"project:a1", "project:a2"}, Scope: "group", GroupID: "group-a",
		Query: "guild member",
		FTS5: ShadowSearchBaseline{
			Paths: []string{"KnowledgeBase/project/domains/guild/member.md"},
			ScopedPaths: []string{
				"project:a1::KnowledgeBase/project/domains/guild/member.md",
				"project:a2::KnowledgeBase/project/domains/guild/member.md",
			},
			ScopedRequiredPaths: []string{
				"project:a1::KnowledgeBase/project/domains/guild/member.md",
				"project:a2::KnowledgeBase/project/domains/guild/member.md",
			},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if run.Scope != "group" || len(run.SourceIDs) != 2 || len(run.Comparison.PathOverlap) != 2 {
		t.Fatalf("run = %#v", run)
	}
	if len(run.Comparison.DuplicateDocuments) != 0 {
		t.Fatalf("same slug in separate sources must not be a duplicate: %#v", run.Comparison)
	}
}
