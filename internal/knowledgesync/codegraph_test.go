package knowledgesync

import (
	"context"
	"strings"
	"testing"
)

type fakeCodeGraphRunner struct {
	calls [][]string
}

func (f *fakeCodeGraphRunner) Run(ctx context.Context, args ...string) ([]byte, error) {
	f.calls = append(f.calls, append([]string(nil), args...))
	if len(args) > 0 && args[0] == "status" {
		return []byte(`{"initialized":true,"fileCount":10,"nodeCount":100}`), nil
	}
	return []byte(`{"changedFiles":["internal/service.go"],"affectedTests":["internal/service_test.go"]}`), nil
}

func TestCodeGraphCLIUsesStatusAndAffectedJSON(t *testing.T) {
	runner := &fakeCodeGraphRunner{}
	client := &CodeGraphCLI{Runner: runner}
	summary, err := client.Summary("D:/repo")
	if err != nil || !strings.Contains(summary, `"initialized":true`) {
		t.Fatalf("summary = %q, err=%v", summary, err)
	}
	impact, err := client.Impact("D:/repo", []string{"internal/service.go"}, 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(impact.Tests) != 1 || impact.Tests[0] != "internal/service_test.go" {
		t.Fatalf("impact = %#v", impact)
	}
	if len(runner.calls) != 2 || runner.calls[1][0] != "affected" {
		t.Fatalf("calls = %#v", runner.calls)
	}
}
