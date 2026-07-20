package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"nexus-agents/internal/catalog"
	"nexus-agents/internal/knowledgebase"
	"nexus-agents/internal/knowledgegraph"
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
	graphProvider := &knowledgegraph.FakeProvider{
		HealthResult: knowledgegraph.GraphHealth{Provider: "gbrain", Status: knowledgegraph.GraphStatusReady},
	}
	graphService := knowledgegraph.NewService(knowledgegraph.ServiceOptions{
		Provider: graphProvider,
		Store:    knowledgegraph.NewGraphStateStore(t.TempDir()),
		ExportRoot: func(string) string {
			return filepath.Join(t.TempDir(), "source")
		},
		Exporter: func(request knowledgegraph.ExportRequest) (knowledgegraph.SourceExport, error) {
			return knowledgegraph.SourceExport{
				Root: request.ExportRoot,
				Manifest: knowledgegraph.SourceManifest{
					SourceHash: "sha256:source", DocumentCount: 1,
				},
				Documents: []knowledgegraph.GraphDocument{{
					Slug: "features/service", Content: "# Service\n", Hash: "sha256:service",
				}},
			}, nil
		},
		Logf: func(string, ...any) {},
	})
	service := knowledgesync.NewService(knowledgesync.ServiceOptions{
		Store: NewKnowledgeTestStateStore(t), Compiler: httpKnowledgeCompiler{},
		KnowledgeGraph: graphService,
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
	graphStatusResponse := requestJSON(t, server, http.MethodGet, "/api/projects/sample/knowledge/graph/status", "")
	if graphStatusResponse.Code != http.StatusOK {
		t.Fatalf("graph status endpoint = %d, body=%s", graphStatusResponse.Code, graphStatusResponse.Body.String())
	}
	var graphState knowledgegraph.SyncState
	if err := json.Unmarshal(graphStatusResponse.Body.Bytes(), &graphState); err != nil {
		t.Fatal(err)
	}
	if graphState.Status != knowledgegraph.SyncStatusPending {
		t.Fatalf("graph state after apply = %#v", graphState)
	}
	graphSyncResponse := requestJSON(t, server, http.MethodPost, "/api/projects/sample/knowledge/graph/sync", "")
	if graphSyncResponse.Code != http.StatusOK {
		t.Fatalf("graph sync endpoint = %d, body=%s", graphSyncResponse.Code, graphSyncResponse.Body.String())
	}
	graphStatusResponse = requestJSON(t, server, http.MethodGet, "/api/projects/sample/knowledge/graph/status", "")
	if err := json.Unmarshal(graphStatusResponse.Body.Bytes(), &graphState); err != nil {
		t.Fatal(err)
	}
	if graphState.Status != knowledgegraph.SyncStatusReady || graphState.Documents != 1 {
		t.Fatalf("graph state after sync = %#v", graphState)
	}
	graphProvider.SearchResult = knowledgegraph.GraphSearchResult{Hits: []knowledgegraph.GraphSearchHit{{
		SourceID: "project:sample", Path: "documents/service", Title: "Service", Score: 0.95,
	}}}
	shadowResponse := requestJSON(
		t,
		server,
		http.MethodPost,
		"/api/projects/sample/knowledge/graph/shadow-search",
		`{"query":"service responsibility","expectedPaths":["KnowledgeBase/project/service.md"]}`,
	)
	if shadowResponse.Code != http.StatusOK {
		t.Fatalf("shadow search endpoint = %d, body=%s", shadowResponse.Code, shadowResponse.Body.String())
	}
	var shadowBody struct {
		Shadow knowledgegraph.ShadowSearchRun `json:"shadow"`
	}
	if err := json.Unmarshal(shadowResponse.Body.Bytes(), &shadowBody); err != nil {
		t.Fatal(err)
	}
	if shadowBody.Shadow.Status != "ready" || shadowBody.Shadow.GBrain.Paths[0] != "documents/service" ||
		shadowBody.Shadow.Comparison.ExpectedDocumentCoverage != 1 {
		t.Fatalf("shadow response = %#v", shadowBody)
	}
	shadowRunsResponse := requestJSON(t, server, http.MethodGet, "/api/projects/sample/knowledge/graph/shadow-runs?limit=10", "")
	if shadowRunsResponse.Code != http.StatusOK {
		t.Fatalf("shadow runs endpoint = %d, body=%s", shadowRunsResponse.Code, shadowRunsResponse.Body.String())
	}
	var shadowRuns []knowledgegraph.ShadowSearchRun
	if err := json.Unmarshal(shadowRunsResponse.Body.Bytes(), &shadowRuns); err != nil {
		t.Fatal(err)
	}
	if len(shadowRuns) != 1 || shadowRuns[0].ID != shadowBody.Shadow.ID {
		t.Fatalf("shadow runs = %#v", shadowRuns)
	}
	shadowSummaryResponse := requestJSON(t, server, http.MethodGet, "/api/projects/sample/knowledge/graph/shadow-summary", "")
	if shadowSummaryResponse.Code != http.StatusOK {
		t.Fatalf("shadow summary endpoint = %d, body=%s", shadowSummaryResponse.Code, shadowSummaryResponse.Body.String())
	}
	var shadowSummary knowledgegraph.ShadowSearchSummary
	if err := json.Unmarshal(shadowSummaryResponse.Body.Bytes(), &shadowSummary); err != nil {
		t.Fatal(err)
	}
	if shadowSummary.Runs != 1 || shadowSummary.Succeeded != 1 || shadowSummary.AverageExpectedCoverage != 1 {
		t.Fatalf("shadow summary = %#v", shadowSummary)
	}
	statusResponse := requestJSON(t, server, http.MethodGet, "/api/projects/sample/knowledge/sync-status", "")
	if statusResponse.Code != http.StatusOK {
		t.Fatalf("status endpoint = %d, body=%s", statusResponse.Code, statusResponse.Body.String())
	}
}

func TestProjectGroupShadowSearchUsesEachProjectSourceOnce(t *testing.T) {
	rootA1 := t.TempDir()
	rootA2 := t.TempDir()
	for _, root := range []string{rootA1, rootA2} {
		if err := knowledgesync.SaveProfile(root, knowledgesync.DefaultProfile()); err != nil {
			t.Fatal(err)
		}
		writeHTTPTestFile(t, root, "KnowledgeBase/project/index.md", httpOKFDoc(
			"Project", "Project", "KnowledgeBase/project/index.md",
			"# Project\n\n[Guild](./domains/guild/index.md)",
		))
		writeHTTPTestFile(t, root, "KnowledgeBase/project/domains/guild/index.md", httpOKFDoc(
			"Domain", "Guild", "KnowledgeBase/project/domains/guild/index.md",
			"# Guild\n\n[Member](member.md)",
		))
		writeHTTPTestFile(t, root, "KnowledgeBase/project/domains/guild/member.md", httpOKFDoc(
			"Guide", "Guild Member", "KnowledgeBase/project/domains/guild/member.md",
			"# Guild Member\n\nGuild member lifecycle.",
		))
	}
	store := catalog.NewStoreFromData(catalog.BootstrapData{
		Projects: []catalog.Project{
			{ID: "a1", Name: "A1", Path: rootA1, LocalPath: rootA1},
			{ID: "a2", Name: "A2", Path: rootA2, LocalPath: rootA2},
		},
		ProjectGroups: []catalog.ProjectGroup{{
			ID: "group-a", Name: "Project A", ProjectIDs: []string{"a1", "a2"},
		}},
		ProjectConfigSets: map[string][]catalog.ProjectCopy{"a1": {}, "a2": {}},
	}, nil, nil)
	graphProvider := &knowledgegraph.FakeProvider{
		HealthResult: knowledgegraph.GraphHealth{Provider: "gbrain", Status: knowledgegraph.GraphStatusReady},
		SearchResult: knowledgegraph.GraphSearchResult{Hits: []knowledgegraph.GraphSearchHit{
			{SourceID: "project:a1", Path: "features/guild/member", Score: 1},
			{SourceID: "project:a2", Path: "features/guild/member", Score: 1},
		}},
	}
	graphService := knowledgegraph.NewService(knowledgegraph.ServiceOptions{
		Provider: graphProvider, Store: knowledgegraph.NewGraphStateStore(t.TempDir()),
		Logf: func(string, ...any) {},
	})
	service := knowledgesync.NewService(knowledgesync.ServiceOptions{
		Store: NewKnowledgeTestStateStore(t), KnowledgeGraph: graphService,
	})
	server := NewServerWithStoreAndKnowledgeSync(store, service)
	response := requestJSON(
		t,
		server,
		http.MethodPost,
		"/api/projects/a1/knowledge/graph/shadow-search",
		`{"query":"guild member"}`,
	)
	if response.Code != http.StatusOK {
		t.Fatalf("group shadow status=%d body=%s", response.Code, response.Body.String())
	}
	var body struct {
		Shadow knowledgegraph.ShadowSearchRun `json:"shadow"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body.Shadow.Scope != "group" || body.Shadow.GroupID != "group-a" || len(body.Shadow.SourceIDs) != 2 {
		t.Fatalf("shadow = %#v", body.Shadow)
	}
	if len(graphProvider.SearchQueries) != 1 || len(graphProvider.SearchQueries[0].SourceIDs) != 2 {
		t.Fatalf("queries = %#v", graphProvider.SearchQueries)
	}
	if len(body.Shadow.Comparison.DuplicateDocuments) != 0 {
		t.Fatalf("shared slug across sources was treated as duplicate: %#v", body.Shadow.Comparison)
	}
}

func TestKnowledgeRetrieveUsesGBrainProjectGroupContextPackAndWorkflowContract(t *testing.T) {
	rootA1 := t.TempDir()
	rootA2 := t.TempDir()
	exportBase := t.TempDir()
	exportRoot := func(projectID string) string { return filepath.Join(exportBase, projectID) }
	for _, item := range []struct {
		id      string
		root    string
		content string
	}{
		{id: "a1", root: rootA1, content: "A1 owns the guild HTTP entrypoint."},
		{id: "a2", root: rootA2, content: "A2 owns the guild member workflow."},
	} {
		writeHTTPTestFile(t, item.root, "KnowledgeBase/index.md", "# KnowledgeBase\n\n[Project](project/index.md)\n")
		writeHTTPTestFile(t, item.root, "KnowledgeBase/log.md", "# Log\n\nInitial knowledge log entry.\n")
		writeHTTPTestFile(t, item.root, "KnowledgeBase/project/index.md", "# "+strings.ToUpper(item.id)+"\n\n"+item.content+"\n")
		if err := knowledgesync.SaveProfile(item.root, knowledgesync.DefaultProfile()); err != nil {
			t.Fatal(err)
		}
		if _, err := knowledgegraph.ExportApprovedKnowledge(knowledgegraph.ExportRequest{
			ProjectID: item.id, ProjectRoot: item.root,
			SourceID: "project:" + item.id, ProviderSourceID: "project-" + item.id,
			Revision: "rev-" + item.id, ExportRoot: exportRoot(item.id),
			IncludeDomains: true, IncludeFeatures: true,
		}); err != nil {
			t.Fatal(err)
		}
	}
	store := catalog.NewStoreFromData(catalog.BootstrapData{
		Projects: []catalog.Project{
			{ID: "a1", Name: "A1", Path: rootA1, LocalPath: rootA1},
			{ID: "a2", Name: "A2", Path: rootA2, LocalPath: rootA2},
		},
		ProjectGroups: []catalog.ProjectGroup{{
			ID: "group-a", Name: "Project A", ProjectIDs: []string{"a1", "a2"},
		}},
		ProjectConfigSets: map[string][]catalog.ProjectCopy{"a1": {}, "a2": {}},
	}, nil, nil)
	provider := &knowledgegraph.FakeProvider{
		HealthResult: knowledgegraph.GraphHealth{Provider: "gbrain", Status: knowledgegraph.GraphStatusReady},
		SearchResult: knowledgegraph.GraphSearchResult{Hits: []knowledgegraph.GraphSearchHit{
			{SourceID: "project:a1", Path: "project", Title: "A1", Score: 0.9},
			{SourceID: "project:a2", Path: "project", Title: "A2", Score: 0.8},
		}},
	}
	graph := knowledgegraph.NewService(knowledgegraph.ServiceOptions{
		Provider: provider, ExportRoot: exportRoot,
		Store: knowledgegraph.NewGraphStateStore(t.TempDir()), Logf: func(string, ...any) {},
	})
	service := knowledgesync.NewService(knowledgesync.ServiceOptions{
		Store: NewKnowledgeTestStateStore(t), KnowledgeGraph: graph,
	})
	server := NewServerWithStoreAndKnowledgeSync(store, service)

	response := requestJSON(
		t,
		server,
		http.MethodGet,
		"/api/projects/a1/knowledge/retrieve?q=guild%20workflow&mode=context&maxTokens=6000",
		"",
	)
	if response.Code != http.StatusOK {
		t.Fatalf("retrieve status=%d body=%s", response.Code, response.Body.String())
	}
	var retrieval knowledgebase.RetrievalResult
	if err := json.Unmarshal(response.Body.Bytes(), &retrieval); err != nil {
		t.Fatal(err)
	}
	if retrieval.Engine != "gbrain" || retrieval.Scope != "group" || retrieval.Degraded ||
		len(retrieval.ProjectIDs) != 2 || len(retrieval.Sources) != 2 {
		t.Fatalf("retrieval = %#v", retrieval)
	}
	for _, expected := range []string{
		"A1 owns the guild HTTP entrypoint.",
		"A2 owns the guild member workflow.",
	} {
		if !strings.Contains(retrieval.LoadedKnowledgeMarkdown, expected) {
			t.Fatalf("context missing %q:\n%s", expected, retrieval.LoadedKnowledgeMarkdown)
		}
	}

	startRequest := httptest.NewRequest(http.MethodPost, "/api/workflow-runs/start", strings.NewReader(`{
	  "projectId":"a1",
	  "workflowType":"feature-development",
	  "taskTitle":"guild workflow"
	}`))
	startRequest.Header.Set("Content-Type", "application/json")
	startRequest.Header.Set("Session-Id", "sess-knowledge-find")
	startResponse := httptest.NewRecorder()
	server.ServeHTTP(startResponse, startRequest)
	if startResponse.Code != http.StatusCreated {
		t.Fatalf("workflow start=%d body=%s", startResponse.Code, startResponse.Body.String())
	}
	var run struct {
		Context map[string]any `json:"context"`
	}
	if err := json.Unmarshal(startResponse.Body.Bytes(), &run); err != nil {
		t.Fatal(err)
	}
	knowledge, ok := run.Context["knowledgeRetrieval"].(map[string]any)
	if !ok || knowledge["engine"] != "gbrain" || knowledge["scope"] != "group" {
		t.Fatalf("workflow knowledge = %#v", run.Context["knowledgeRetrieval"])
	}
	if len(provider.SearchQueries) != 2 {
		t.Fatalf("search queries = %#v", provider.SearchQueries)
	}
}

func TestShadowSearchProjectsInfersGroupsWithoutQueryParameters(t *testing.T) {
	store := catalog.NewStoreFromData(catalog.BootstrapData{
		Projects: []catalog.Project{
			{ID: "a1", Name: "A1"},
			{ID: "a2", Name: "A2"},
			{ID: "b1", Name: "B1"},
			{ID: "common", Name: "Common"},
			{ID: "standalone", Name: "Standalone"},
		},
		ProjectGroups: []catalog.ProjectGroup{
			{ID: "group-a", Name: "Project A", ProjectIDs: []string{"a1", "a2", "common"}},
			{ID: "group-b", Name: "Project B", ProjectIDs: []string{"b1", "common"}},
		},
		ProjectConfigSets: map[string][]catalog.ProjectCopy{},
	}, nil, nil)
	server := &Server{store: store}

	projects, scope, groupID, err := server.shadowSearchProjects("a1", "", "")
	if err != nil {
		t.Fatal(err)
	}
	if scope != "group" || groupID != "group-a" || projectIDs(projects) != "a1,a2,common" {
		t.Fatalf("single group resolution: scope=%q group=%q projects=%q", scope, groupID, projectIDs(projects))
	}

	projects, scope, groupID, err = server.shadowSearchProjects("common", "", "")
	if err != nil {
		t.Fatal(err)
	}
	if scope != "group" || groupID != "" || projectIDs(projects) != "common,a1,a2,b1" {
		t.Fatalf("multi-group resolution: scope=%q group=%q projects=%q", scope, groupID, projectIDs(projects))
	}

	projects, scope, groupID, err = server.shadowSearchProjects("standalone", "", "")
	if err != nil {
		t.Fatal(err)
	}
	if scope != "project" || groupID != "" || projectIDs(projects) != "standalone" {
		t.Fatalf("standalone resolution: scope=%q group=%q projects=%q", scope, groupID, projectIDs(projects))
	}
}

func projectIDs(projects []catalog.Project) string {
	ids := make([]string, 0, len(projects))
	for _, project := range projects {
		ids = append(ids, project.ID)
	}
	return strings.Join(ids, ",")
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
