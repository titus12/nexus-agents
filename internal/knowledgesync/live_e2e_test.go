package knowledgesync

import (
	"context"
	"os"
	"strings"
	"testing"
)

// TestLiveCheckAndEnrichAgainstRealProject drives the full pipeline (inventory,
// chunked Git snapshot, OpenWiki compile) against a real checkout. It is
// skipped unless NEXUS_KNOWLEDGE_LIVE_E2E=1 because the OpenWiki compile makes
// real model calls and can take tens of minutes.
func TestLiveCheckAndEnrichAgainstRealProject(t *testing.T) {
	if os.Getenv("NEXUS_KNOWLEDGE_LIVE_E2E") == "" {
		t.Skip("set NEXUS_KNOWLEDGE_LIVE_E2E=1 to run the live knowledge sync e2e")
	}
	project := os.Getenv("NEXUS_KNOWLEDGE_LIVE_PROJECT")
	if project == "" {
		project = `D:\workspace\src\rtc-integration-unity-client`
	}
	if _, err := LoadProfile(project); err != nil {
		t.Fatalf("project profile: %v", err)
	}
	service := NewService(ServiceOptions{Store: NewStateStore(t.TempDir())})
	result, err := service.CheckAndEnrich(context.Background(), InitializeRequest{
		ProjectRequest: ProjectRequest{ProjectID: "live-e2e", ProjectRoot: project},
	})
	if err != nil {
		t.Fatalf("check-and-enrich failed: %v (run=%#v)", err, result.Run)
	}
	if result.Run.Status != "succeeded" {
		t.Fatalf("run status = %s error=%s", result.Run.Status, result.Run.Error)
	}
	if result.Proposal == nil {
		t.Fatal("expected a proposal")
	}
	var packagesSeen bool
	for _, evidence := range result.Proposal.Evidence {
		for _, path := range evidence.Paths {
			if strings.HasPrefix(path, "Packages/com.centurygame.rtc.sdk/") {
				packagesSeen = true
				break
			}
		}
	}
	if !packagesSeen {
		t.Fatalf("Packages/com.centurygame.rtc.sdk sources missing from the manifest: %#v", result.Proposal.Evidence)
	}
	t.Logf("run metadata=%s warnings=%#v", result.Run.Metadata, result.Run.Warnings)
}
