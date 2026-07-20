package knowledgegraph

import (
	"context"
	"errors"
	"testing"
)

func TestNoopProviderIsExplicitlyDisabled(t *testing.T) {
	provider := NoopProvider{}
	health, err := provider.Health(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if health.Status != GraphStatusDisabled {
		t.Fatalf("status = %q", health.Status)
	}
	if _, err := provider.Search(context.Background(), GraphSearchQuery{Query: "guild"}); !errors.Is(err, ErrProviderDisabled) {
		t.Fatalf("search error = %v", err)
	}
}

func TestFakeProviderRecordsDeterministicCalls(t *testing.T) {
	provider := &FakeProvider{
		SearchResult: GraphSearchResult{Hits: []GraphSearchHit{{ID: "doc:1", Score: 1}}},
	}
	result, err := provider.Search(context.Background(), GraphSearchQuery{Query: "model routing", Limit: 5})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Hits) != 1 || len(provider.SearchQueries) != 1 {
		t.Fatalf("result = %#v, queries = %#v", result, provider.SearchQueries)
	}
}
