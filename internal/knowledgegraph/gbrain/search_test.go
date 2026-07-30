package gbrain

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

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

func TestProviderSearchReturnsWhenDeadlineExpiresWaitingForAnotherSearch(t *testing.T) {
	options := helperProcessOptions(t, false)
	startedPath := filepath.Join(options.HomeDir, "search-started")
	releasePath := filepath.Join(options.HomeDir, "search-release")
	options.Environment["NEXUS_GBRAIN_HELPER_SEARCH_STARTED"] = startedPath
	options.Environment["NEXUS_GBRAIN_HELPER_SEARCH_RELEASE"] = releasePath
	provider := NewProvider(options)
	if err := provider.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	defer func() {
		_ = os.WriteFile(releasePath, []byte("release\n"), 0o644)
		_ = provider.Stop(context.Background())
	}()

	firstDone := make(chan error, 1)
	go func() {
		_, err := provider.Search(context.Background(), knowledgegraph.GraphSearchQuery{
			Query: "first query", SourceIDs: []string{"project:sample"},
		})
		firstDone <- err
	}()
	waitForFile(t, startedPath)

	deadlineContext, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	secondDone := make(chan error, 1)
	go func() {
		_, err := provider.Search(deadlineContext, knowledgegraph.GraphSearchQuery{
			Query: "second query", SourceIDs: []string{"project:sample"},
		})
		secondDone <- err
	}()

	select {
	case err := <-secondDone:
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatalf("second search error = %v, want context deadline exceeded", err)
		}
	case <-time.After(250 * time.Millisecond):
		t.Fatal("second search did not return after its deadline")
	}

	if err := os.WriteFile(releasePath, []byte("release\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := <-firstDone; err != nil {
		t.Fatal(err)
	}
}

func waitForFile(t *testing.T, path string) {
	t.Helper()
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		if _, err := os.Stat(path); err == nil {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", path)
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
