package gbrain

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"nexus-agents/internal/knowledgegraph"
)

func TestProviderSyncSourceUsesPutAndDeletePages(t *testing.T) {
	options := helperProcessOptions(t, false)
	recordPath := filepath.Join(options.HomeDir, "sync-commands.log")
	options.Environment["NEXUS_GBRAIN_HELPER_RECORD"] = recordPath
	provider := NewProvider(options)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := provider.Start(ctx); err != nil {
		t.Fatal(err)
	}
	result, err := provider.SyncSource(context.Background(), knowledgegraph.GraphSource{
		ID: "project:sample", ProviderSourceID: "project-sample",
		ProjectID: "sample", Root: t.TempDir(), Revision: "rev-1",
		Documents: []knowledgegraph.GraphDocument{
			{Slug: "project", Content: "# Project\n"},
			{Slug: "features/runtime/start", Content: "# Start\n"},
		},
		DeleteSlugs: []string{"features/runtime/old"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if result.ProviderSourceID != "project-sample" || result.Documents != 2 || result.Deleted != 0 {
		t.Fatalf("result = %#v", result)
	}
	_, err = provider.SyncSource(context.Background(), knowledgegraph.GraphSource{
		ID: "project:sample", ProviderSourceID: "project-sample",
		ProjectID: "sample", Root: t.TempDir(), Revision: "rev-1",
		AllDocuments: []knowledgegraph.GraphDocument{
			{Slug: "project", Content: "# Project\n"},
			{Slug: "features/runtime/start", Content: "# Start\n"},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	record, err := os.ReadFile(recordPath)
	if err != nil {
		t.Fatal(err)
	}
	if count := strings.Count(string(record), "tool put_page"); count != 2 {
		t.Fatalf("repeated sync wrote duplicate pages, put_page count=%d:\n%s", count, record)
	}
	if err := os.Remove(options.Environment["NEXUS_GBRAIN_HELPER_SOURCES"]); err != nil {
		t.Fatal(err)
	}
	_, err = provider.SyncSource(context.Background(), knowledgegraph.GraphSource{
		ID: "project:sample", ProviderSourceID: "project-sample",
		ProjectID: "sample", Root: t.TempDir(), Revision: "rev-1",
		AllDocuments: []knowledgegraph.GraphDocument{
			{Slug: "project", Content: "# Project\n"},
			{Slug: "features/runtime/start", Content: "# Start\n"},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	record, err = os.ReadFile(recordPath)
	if err != nil {
		t.Fatal(err)
	}
	if count := strings.Count(string(record), "tool put_page"); count != 4 {
		t.Fatalf("recreated source did not rebuild all pages, put_page count=%d:\n%s", count, record)
	}
	health := provider.Snapshot()
	if health.Status != knowledgegraph.GraphStatusReady || health.ProcessID == 0 {
		t.Fatalf("health = %#v", health)
	}
	stopCtx, stopCancel := context.WithTimeout(context.Background(), time.Second)
	defer stopCancel()
	if err := provider.Stop(stopCtx); err != nil {
		t.Fatal(err)
	}
}
