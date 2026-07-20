package knowledgegraph

import "testing"

func TestGraphStateStoreRoundTrip(t *testing.T) {
	store := NewGraphStateStore(t.TempDir())
	state := SyncState{
		ProjectID: "sample", SourceID: "project:sample", ProviderSourceID: "project-sample",
		Status: SyncStatusReady, Documents: 2,
		DocumentHashes: map[string]string{"project": "sha256:a", "domains/runtime": "sha256:b"},
	}
	if err := store.Save(state); err != nil {
		t.Fatal(err)
	}
	loaded, err := store.Load("sample")
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Status != SyncStatusReady || len(loaded.DocumentHashes) != 2 {
		t.Fatalf("state = %#v", loaded)
	}
	run := SyncRun{ID: "run-1", ProjectID: "sample", Status: SyncStatusReady, StartedAt: "2026-07-19T00:00:00Z"}
	if err := store.SaveRun(run); err != nil {
		t.Fatal(err)
	}
	runs, err := store.Runs("sample")
	if err != nil || len(runs) != 1 {
		t.Fatalf("runs = %#v, %v", runs, err)
	}
}
