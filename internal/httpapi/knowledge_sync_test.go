package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"os/exec"
	"testing"

	"nexus-agents/internal/catalog"
	"nexus-agents/internal/knowledgesync"
	"nexus-agents/internal/wikicompiler"
)

type httpKnowledgeCompiler struct{}

func (httpKnowledgeCompiler) Initialize(context.Context, wikicompiler.CompileInput) (wikicompiler.GeneratedBundle, error) {
	return httpKnowledgeBundle(), nil
}

func (httpKnowledgeCompiler) Update(context.Context, wikicompiler.CompileInput) (wikicompiler.GeneratedBundle, error) {
	return httpKnowledgeBundle(), nil
}

func httpKnowledgeBundle() wikicompiler.GeneratedBundle {
	return wikicompiler.GeneratedBundle{
		Version: "0.2.0",
		Files: map[string][]byte{
			"KnowledgeBase/project/index.md":   []byte("# Project\n"),
			"KnowledgeBase/project/service.md": []byte("---\ntype: Project\ntitle: Service\ndescription: Service knowledge\nresource: KnowledgeBase/project/service.md\ntags: [service]\ntimestamp: 2026-07-19T00:00:00+08:00\nmanagedBy: openwiki\n---\n# Service\n\nStable service responsibility and verification path.\n"),
		},
	}
}

func TestKnowledgeSyncAPIProposalLifecycle(t *testing.T) {
	root := t.TempDir()
	runHTTPGit(t, root, "init")
	runHTTPGit(t, root, "config", "user.email", "nexus@example.test")
	runHTTPGit(t, root, "config", "user.name", "Nexus Test")
	writeHTTPTestFile(t, root, "cmd/server/main.go", "package main\n")
	runHTTPGit(t, root, "add", ".")
	runHTTPGit(t, root, "commit", "-m", "initial")
	if err := knowledgesync.SaveProfile(root, knowledgesync.DefaultProfile()); err != nil {
		t.Fatal(err)
	}

	store := catalog.NewStoreFromData(catalog.BootstrapData{
		Projects:          []catalog.Project{{ID: "sample", Name: "sample", Path: root, LocalPath: root}},
		ProjectConfigSets: map[string][]catalog.ProjectCopy{"sample": {}},
	}, nil, nil)
	service := knowledgesync.NewService(knowledgesync.ServiceOptions{
		Store:    NewKnowledgeTestStateStore(t),
		Compiler: httpKnowledgeCompiler{},
	})
	server := NewServerWithStoreAndKnowledgeSync(store, service)

	profileResponse := requestJSON(t, server, http.MethodGet, "/api/projects/sample/knowledge/sync-profile", "")
	if profileResponse.Code != http.StatusOK {
		t.Fatalf("profile status = %d, body=%s", profileResponse.Code, profileResponse.Body.String())
	}
	discoveryResponse := requestJSON(t, server, http.MethodPost, "/api/projects/sample/knowledge/discovery-preview", "")
	if discoveryResponse.Code != http.StatusOK {
		t.Fatalf("discovery status = %d, body=%s", discoveryResponse.Code, discoveryResponse.Body.String())
	}

	initializeResponse := requestJSON(t, server, http.MethodPost, "/api/projects/sample/knowledge/initialize-preview", `{}`)
	if initializeResponse.Code != http.StatusOK {
		t.Fatalf("initialize status = %d, body=%s", initializeResponse.Code, initializeResponse.Body.String())
	}
	var result knowledgesync.SyncResult
	if err := json.Unmarshal(initializeResponse.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if result.Proposal == nil || result.Proposal.Status != knowledgesync.ProposalPending {
		t.Fatalf("result = %#v", result)
	}

	applyResponse := requestJSON(
		t,
		server,
		http.MethodPost,
		"/api/projects/sample/knowledge/proposals/"+result.Proposal.ID+"/apply",
		`{"paths":["KnowledgeBase/project/service.md"]}`,
	)
	if applyResponse.Code != http.StatusOK {
		t.Fatalf("apply status = %d, body=%s", applyResponse.Code, applyResponse.Body.String())
	}
	statusResponse := requestJSON(t, server, http.MethodGet, "/api/projects/sample/knowledge/sync-status", "")
	if statusResponse.Code != http.StatusOK {
		t.Fatalf("status endpoint = %d, body=%s", statusResponse.Code, statusResponse.Body.String())
	}
}

func NewKnowledgeTestStateStore(t *testing.T) *knowledgesync.StateStore {
	t.Helper()
	return knowledgesync.NewStateStore(t.TempDir())
}

func runHTTPGit(t *testing.T, root string, args ...string) {
	t.Helper()
	command := exec.Command("git", append([]string{"-C", root}, args...)...)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, output)
	}
}
