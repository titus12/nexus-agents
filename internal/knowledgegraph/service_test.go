package knowledgegraph

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
	"time"
)

type flakyGraphProvider struct {
	*FakeProvider
	failures int
	calls    int
}

func (p *flakyGraphProvider) SyncSource(_ context.Context, source GraphSource) (GraphSyncResult, error) {
	p.calls++
	if p.calls <= p.failures {
		return GraphSyncResult{}, errors.New("temporary GBrain failure")
	}
	p.SyncedSources = append(p.SyncedSources, source)
	return GraphSyncResult{SourceID: source.ID, ProviderSourceID: source.ProviderSourceID}, nil
}

func TestServiceSyncIsIncrementalAndReconcilesDeletes(t *testing.T) {
	projectRoot := t.TempDir()
	store := NewGraphStateStore(t.TempDir())
	provider := &FakeProvider{}
	exportCall := 0
	service := NewService(ServiceOptions{
		Provider: provider,
		Store:    store,
		ExportRoot: func(string) string {
			return filepath.Join(t.TempDir(), "source")
		},
		Exporter: func(request ExportRequest) (SourceExport, error) {
			exportCall++
			documents := []GraphDocument{
				{ID: "source:a", Slug: "a", Path: "features/a.md", Content: "a\n", Hash: "sha256:a"},
				{ID: "source:b", Slug: "b", Path: "features/b.md", Content: "b\n", Hash: "sha256:b"},
			}
			if exportCall >= 3 {
				documents = []GraphDocument{
					{ID: "source:a", Slug: "a", Path: "features/a.md", Content: "a2\n", Hash: "sha256:a2"},
					{ID: "source:c", Slug: "c", Path: "features/c.md", Content: "c\n", Hash: "sha256:c"},
				}
			}
			return SourceExport{
				Root: request.ExportRoot,
				Manifest: SourceManifest{
					SourceHash: "sha256:source", DocumentCount: len(documents),
				},
				Documents: documents,
			}, nil
		},
		Logf: func(string, ...any) {},
	})
	request := SyncRequest{
		ProjectID: "sample", ProjectRoot: projectRoot, SourceID: "project:sample",
		Branch: "main", Revision: "rev-1",
	}

	first, err := service.SyncNow(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	if first.Created != 2 || first.Updated != 0 || len(provider.SyncedSources) != 1 {
		t.Fatalf("first = %#v sources=%#v", first, provider.SyncedSources)
	}
	second, err := service.SyncNow(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	if second.Unchanged != 2 || len(provider.SyncedSources) != 2 || len(provider.SyncedSources[1].Documents) != 0 {
		t.Fatalf("second = %#v sources=%#v", second, provider.SyncedSources)
	}
	third, err := service.SyncNow(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	if third.Created != 1 || third.Updated != 1 || third.Deleted != 1 {
		t.Fatalf("third = %#v", third)
	}
	if len(provider.SyncedSources) != 3 {
		t.Fatalf("sources=%#v", provider.SyncedSources)
	}
	last := provider.SyncedSources[2]
	if len(last.Documents) != 2 || len(last.DeleteSlugs) != 1 || last.DeleteSlugs[0] != "b" {
		t.Fatalf("last source = %#v", last)
	}
	state, err := service.State("sample")
	if err != nil {
		t.Fatal(err)
	}
	if state.Status != SyncStatusReady || state.Documents != 2 || state.DocumentHashes["a"] != "sha256:a2" {
		t.Fatalf("state = %#v", state)
	}
	request.SourceID = "project:renamed-sample"
	fourth, err := service.SyncNow(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	if fourth.Created != 2 || len(provider.RemovedSourceIDs) != 1 || provider.RemovedSourceIDs[0] != "project:sample" {
		t.Fatalf("fourth = %#v removed=%#v", fourth, provider.RemovedSourceIDs)
	}
}

func TestServiceFailureDegradesDerivedStateOnly(t *testing.T) {
	projectRoot := t.TempDir()
	provider := &FakeProvider{SyncError: errors.New("gbrain unavailable")}
	service := NewService(ServiceOptions{
		Provider: provider,
		Store:    NewGraphStateStore(t.TempDir()),
		Exporter: func(request ExportRequest) (SourceExport, error) {
			return SourceExport{
				Root:      request.ExportRoot,
				Manifest:  SourceManifest{SourceHash: "sha256:source", DocumentCount: 1},
				Documents: []GraphDocument{{Slug: "project", Content: "approved\n", Hash: "sha256:a"}},
			}, nil
		},
		Logf: func(string, ...any) {},
	})
	_, err := service.SyncNow(context.Background(), SyncRequest{
		ProjectID: "sample", ProjectRoot: projectRoot, SourceID: "project:sample",
	})
	if err == nil {
		t.Fatal("expected provider failure")
	}
	state, loadErr := service.State("sample")
	if loadErr != nil {
		t.Fatal(loadErr)
	}
	if state.Status != SyncStatusDegraded || state.LastError == "" {
		t.Fatalf("state = %#v", state)
	}
}

func TestServiceQueueRetriesAndEventuallyBecomesReady(t *testing.T) {
	projectRoot := t.TempDir()
	provider := &flakyGraphProvider{FakeProvider: &FakeProvider{}, failures: 1}
	service := NewService(ServiceOptions{
		Provider: provider,
		Store:    NewGraphStateStore(t.TempDir()),
		Exporter: func(request ExportRequest) (SourceExport, error) {
			return SourceExport{
				Root:      request.ExportRoot,
				Manifest:  SourceManifest{SourceHash: "sha256:source", DocumentCount: 1},
				Documents: []GraphDocument{{Slug: "project", Content: "approved\n", Hash: "sha256:a"}},
			}, nil
		},
		Logf: func(string, ...any) {},
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := service.Start(ctx); err != nil {
		t.Fatal(err)
	}
	defer service.Stop(context.Background())
	if err := service.QueueSync(SyncRequest{
		ProjectID: "sample", ProjectRoot: projectRoot, SourceID: "project:sample",
		Revision: "rev-1", RetryDelay: 10 * time.Millisecond, MaxRetries: 2,
	}); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		state, err := service.State("sample")
		if err != nil {
			t.Fatal(err)
		}
		if state.Status == SyncStatusReady {
			if provider.calls != 2 {
				t.Fatalf("provider calls = %d", provider.calls)
			}
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	state, _ := service.State("sample")
	t.Fatalf("sync did not recover: state=%#v calls=%d", state, provider.calls)
}
