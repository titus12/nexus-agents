package gbrain

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"nexus-agents/internal/knowledgegraph"
)

func TestProviderSearchUsesScopedGBrainSource(t *testing.T) {
	options := helperProcessOptions(t, false)
	recordPath := filepath.Join(options.HomeDir, "search-commands.log")
	options.Environment["NEXUS_GBRAIN_HELPER_RECORD"] = recordPath
	provider := NewProvider(options)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := provider.Start(ctx); err != nil {
		t.Fatal(err)
	}
	result, err := provider.Search(context.Background(), knowledgegraph.GraphSearchQuery{
		Query: "model routing", SourceIDs: []string{"project:sample"}, Limit: 5,
	})
	if err != nil {
		t.Fatal(err)
	}
	if result.Query != "model routing" || len(result.Hits) != 1 {
		t.Fatalf("result = %#v", result)
	}
	hit := result.Hits[0]
	if hit.SourceID != "project:sample" || hit.Path != "features/model-routing/proxy-model-families" || hit.Score != 0.98 {
		t.Fatalf("hit = %#v", hit)
	}
	data, err := os.ReadFile(recordPath)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(data), "tool search source=project-sample") {
		t.Fatalf("search did not use scoped source:\n%s", data)
	}
}

func TestProviderSearchUsesRRFForMultipleSources(t *testing.T) {
	options := helperProcessOptions(t, false)
	provider := NewProvider(options)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := provider.Start(ctx); err != nil {
		t.Fatal(err)
	}
	result, err := provider.Search(context.Background(), knowledgegraph.GraphSearchQuery{
		Query: "model routing", SourceIDs: []string{"project:a1", "project:a2"}, Limit: 10,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Hits) != 2 {
		t.Fatalf("hits = %#v", result.Hits)
	}
	if result.Hits[0].Score != result.Hits[1].Score ||
		result.Hits[0].SourceID != "project:a1" ||
		result.Hits[1].SourceID != "project:a2" {
		t.Fatalf("RRF hits = %#v", result.Hits)
	}
}
