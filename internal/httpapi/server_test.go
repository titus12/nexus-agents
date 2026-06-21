package httpapi

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"nexus-agents/internal/catalog"
	"nexus-agents/internal/codexrouter"
)

func getJSON(t *testing.T, server http.Handler, path string, target any) {
	t.Helper()

	request := httptest.NewRequest(http.MethodGet, path, nil)
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("GET %s: expected status 200, got %d", path, response.Code)
	}
	if err := json.NewDecoder(response.Body).Decode(target); err != nil {
		t.Fatalf("GET %s: decode response: %v", path, err)
	}
}

func requestJSON(t *testing.T, server http.Handler, method string, path string, body string) *httptest.ResponseRecorder {
	t.Helper()

	request := httptest.NewRequest(method, path, bytes.NewBufferString(body))
	if body != "" {
		request.Header.Set("Content-Type", "application/json")
	}
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	return response
}

func decodeJSON(t *testing.T, response *httptest.ResponseRecorder, target any) {
	t.Helper()
	if err := json.NewDecoder(response.Body).Decode(target); err != nil {
		t.Fatalf("decode response: %v", err)
	}
}

func TestHealthEndpoint(t *testing.T) {
	server := NewServer()
	request := httptest.NewRequest(http.MethodGet, "/api/health", nil)
	response := httptest.NewRecorder()

	server.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", response.Code)
	}

	var body map[string]string
	if err := json.NewDecoder(response.Body).Decode(&body); err != nil {
		t.Fatalf("decode health response: %v", err)
	}

	if body["service"] != "nexus-agents" {
		t.Fatalf("expected service nexus-agents, got %q", body["service"])
	}
	if body["status"] != "ok" {
		t.Fatalf("expected status ok, got %q", body["status"])
	}
}

func TestWebUIRootServesVueIndex(t *testing.T) {
	server := NewServer()
	response := requestJSON(t, server, http.MethodGet, "/", "")

	if response.Code != http.StatusOK {
		t.Fatalf("expected web root status 200, got %d", response.Code)
	}
	contentType := response.Header().Get("Content-Type")
	if !strings.Contains(contentType, "text/html") {
		t.Fatalf("expected html content type, got %q", contentType)
	}
	body := response.Body.String()
	for _, token := range []string{`<div id="app"></div>`, `/assets/`} {
		if !strings.Contains(body, token) {
			t.Fatalf("expected web index to contain %q, got %s", token, body)
		}
	}
}

func TestWebUIAssetServesFromVueDist(t *testing.T) {
	server := NewServer()
	indexResponse := requestJSON(t, server, http.MethodGet, "/", "")
	assetPath := firstAssetPath(t, indexResponse.Body.String())

	assetResponse := requestJSON(t, server, http.MethodGet, assetPath, "")

	if assetResponse.Code != http.StatusOK {
		t.Fatalf("expected asset %s status 200, got %d", assetPath, assetResponse.Code)
	}
	if assetResponse.Body.Len() == 0 {
		t.Fatalf("expected asset %s body", assetPath)
	}
	if strings.Contains(assetResponse.Header().Get("Content-Type"), "text/html") {
		t.Fatalf("expected asset %s to not be served as html", assetPath)
	}
}

func TestWebUISPAFallbackServesIndex(t *testing.T) {
	server := NewServer()
	response := requestJSON(t, server, http.MethodGet, "/projects/btd-game-server/workflows", "")

	if response.Code != http.StatusOK {
		t.Fatalf("expected SPA fallback status 200, got %d", response.Code)
	}
	if !strings.Contains(response.Body.String(), `<div id="app"></div>`) {
		t.Fatalf("expected SPA fallback to return index.html, got %s", response.Body.String())
	}
}

func firstAssetPath(t *testing.T, indexHTML string) string {
	t.Helper()
	matches := regexp.MustCompile(`(?:src|href)="(/assets/[^"]+)"`).FindStringSubmatch(indexHTML)
	if len(matches) < 2 {
		t.Fatalf("expected index html to reference an embedded asset, got %s", indexHTML)
	}
	return matches[1]
}

func TestBootstrapEndpoint(t *testing.T) {
	server := NewServer()
	var body struct {
		TemplateLibrary struct {
			Agents    []struct{ ID string } `json:"agents"`
			Rules     []struct{ ID string } `json:"rules"`
			Skills    []struct{ ID string } `json:"skills"`
			Workflows []struct{ ID string } `json:"workflows"`
		} `json:"templateLibrary"`
		Projects          []struct{ ID string }            `json:"projects"`
		ProjectConfigSets map[string][]catalog.ProjectCopy `json:"projectConfigSets"`
	}
	getJSON(t, server, "/api/bootstrap", &body)

	if len(body.TemplateLibrary.Agents) == 0 || len(body.TemplateLibrary.Rules) == 0 || len(body.TemplateLibrary.Skills) == 0 || len(body.TemplateLibrary.Workflows) == 0 {
		t.Fatalf("expected template library to be populated, got %#v", body.TemplateLibrary)
	}
	if len(body.Projects) != 0 {
		t.Fatalf("expected no default projects, got %#v", body.Projects)
	}
	if len(body.ProjectConfigSets) != 0 {
		t.Fatalf("expected no default project config sets, got %#v", body.ProjectConfigSets)
	}
}

func TestProjectEndpoints(t *testing.T) {
	server := NewServer()

	var projects []struct {
		ID string `json:"id"`
	}
	getJSON(t, server, "/api/projects", &projects)
	if len(projects) != 0 {
		t.Fatalf("expected no default projects, got %#v", projects)
	}

	missing := requestJSON(t, server, http.MethodGet, "/api/projects/missing", "")
	if missing.Code != http.StatusNotFound {
		t.Fatalf("expected missing project status 404, got %d", missing.Code)
	}
}

func TestProjectImportAndDeleteEndpoints(t *testing.T) {
	server := NewServer()
	root := t.TempDir()

	importResponse := requestJSON(t, server, http.MethodPost, "/api/projects/import", `{"name":"imported-game","path":"`+strings.ReplaceAll(root, `\`, `\\`)+`"}`)
	if importResponse.Code != http.StatusCreated {
		t.Fatalf("expected import status 201, got %d", importResponse.Code)
	}

	var imported struct {
		ID                 string `json:"id"`
		Name               string `json:"name"`
		Path               string `json:"path"`
		Status             string `json:"status"`
		RepoKey            string `json:"repoKey"`
		LocalPath          string `json:"localPath"`
		LocalConfigPath    string `json:"localConfigPath"`
		LocalConfigIgnored bool   `json:"localConfigIgnored"`
	}
	decodeJSON(t, importResponse, &imported)
	if imported.ID != "imported-game" || imported.Status != "draft" {
		t.Fatalf("unexpected imported project: %#v", imported)
	}
	if imported.RepoKey == "" || imported.LocalPath == "" || imported.LocalConfigPath != ".nexus" || !imported.LocalConfigIgnored {
		t.Fatalf("expected local import metadata fields, got %#v", imported)
	}
	if _, err := os.Stat(filepath.Join(root, ".nexus")); err != nil {
		t.Fatalf("expected imported project to contain .nexus file: %v", err)
	}

	var projects []struct {
		ID string `json:"id"`
	}
	getJSON(t, server, "/api/projects", &projects)
	found := false
	for _, project := range projects {
		found = found || project.ID == imported.ID
	}
	if !found {
		t.Fatalf("expected imported project in project list, got %#v", projects)
	}

	deleteResponse := requestJSON(t, server, http.MethodDelete, "/api/projects/imported-game", "")
	if deleteResponse.Code != http.StatusNoContent {
		t.Fatalf("expected delete status 204, got %d", deleteResponse.Code)
	}
	if _, err := os.Stat(filepath.Join(root, ".nexus")); !os.IsNotExist(err) {
		t.Fatalf("expected delete endpoint to remove root .nexus file, err=%v", err)
	}
	gitignore, err := os.ReadFile(filepath.Join(root, ".gitignore"))
	if err != nil {
		t.Fatalf("expected .gitignore after delete: %v", err)
	}
	if count := strings.Count(string(gitignore), ".nexus"); count != 1 {
		t.Fatalf("expected delete endpoint to keep one .nexus ignore rule, got %d in %q", count, string(gitignore))
	}

	missingResponse := requestJSON(t, server, http.MethodGet, "/api/projects/imported-game", "")
	if missingResponse.Code != http.StatusNotFound {
		t.Fatalf("expected deleted project to return 404, got %d", missingResponse.Code)
	}
}

func TestTemplateEndpoints(t *testing.T) {
	server := NewServer()

	for _, path := range []string{"/api/templates/agents", "/api/templates/rules", "/api/templates/skills", "/api/templates/workflows"} {
		var items []struct {
			ID      string   `json:"id"`
			Kind    string   `json:"kind"`
			Version int      `json:"version"`
			Files   []string `json:"files"`
		}
		getJSON(t, server, path, &items)
		if len(items) == 0 {
			t.Fatalf("expected template items for %s", path)
		}
		if items[0].ID == "" || items[0].Kind == "" || items[0].Version == 0 {
			t.Fatalf("expected template identity fields for %s, got %#v", path, items[0])
		}
	}
}

func TestTemplateEndpointsExposePlanMetadata(t *testing.T) {
	server := NewServer()

	var agents []struct {
		ID            string   `json:"id"`
		ModelTier     string   `json:"modelTier"`
		RulesCount    int      `json:"rulesCount"`
		SkillsCount   int      `json:"skillsCount"`
		RelatedRules  []string `json:"relatedRules"`
		RelatedSkills []string `json:"relatedSkills"`
		Tools         []string `json:"tools"`
		MCP           []string `json:"mcp"`
		Content       string   `json:"content"`
	}
	getJSON(t, server, "/api/templates/agents", &agents)
	if agents[0].ModelTier == "" || agents[0].RulesCount == 0 || agents[0].SkillsCount == 0 {
		t.Fatalf("expected agent card metadata, got %#v", agents[0])
	}
	if len(agents[0].RelatedRules) == 0 || len(agents[0].RelatedSkills) == 0 || len(agents[0].Tools) == 0 || len(agents[0].MCP) == 0 {
		t.Fatalf("expected agent drawer relationships and tools, got %#v", agents[0])
	}
	if agents[0].Content == "" {
		t.Fatal("expected agent concrete content")
	}

	var rules []struct {
		ID          string   `json:"id"`
		Source      string   `json:"source"`
		Content     string   `json:"content"`
		SourcePaths []string `json:"sourcePaths"`
	}
	getJSON(t, server, "/api/templates/rules", &rules)
	if rules[0].Source == "" || rules[0].Content == "" || len(rules[0].SourcePaths) == 0 {
		t.Fatalf("expected rule source and content metadata, got %#v", rules[0])
	}

	var skills []struct {
		ID               string   `json:"id"`
		ApplicableAgents []string `json:"applicableAgents"`
		Content          string   `json:"content"`
		SourcePaths      []string `json:"sourcePaths"`
	}
	getJSON(t, server, "/api/templates/skills", &skills)
	if len(skills[0].ApplicableAgents) == 0 || skills[0].Content == "" || len(skills[0].SourcePaths) == 0 {
		t.Fatalf("expected skill applicable agents and package content, got %#v", skills[0])
	}
}

func TestBtdGameServerTemplateInventory(t *testing.T) {
	server := NewServer()

	var agents []struct {
		ID              string   `json:"id"`
		ModelTier       string   `json:"modelTier"`
		SourcePaths     []string `json:"sourcePaths"`
		CodexProjection string   `json:"codexProjection"`
		ClaudeSource    string   `json:"claudeSource"`
	}
	getJSON(t, server, "/api/templates/agents", &agents)
	assertTemplateIDs(t, "agents", agents, []string{
		"debugger", "gatekeeper", "hephaestus", "librarian", "oracle", "prometheus",
		"quick", "reviewer-logic", "reviewer-perf", "reviewer-security", "sisyphus", "worker",
	})
	for _, agent := range agents {
		if agent.ModelTier == "" || agent.CodexProjection == "" || agent.ClaudeSource == "" {
			t.Fatalf("expected btd agent model and source metadata, got %#v", agent)
		}
		if !containsString(agent.SourcePaths, "templates/agents/claude/go-"+agent.ID+".md") {
			t.Fatalf("expected copied agent markdown template for %s, got %#v", agent.ID, agent.SourcePaths)
		}
		if !containsString(agent.SourcePaths, "templates/agents/codex/go-"+agent.ID+".toml") {
			t.Fatalf("expected copied agent toml template for %s, got %#v", agent.ID, agent.SourcePaths)
		}
	}

	var rules []struct {
		ID          string   `json:"id"`
		SourcePaths []string `json:"sourcePaths"`
		Content     string   `json:"content"`
	}
	getJSON(t, server, "/api/templates/rules", &rules)
	assertTemplateIDs(t, "rules", rules, []string{"00-routing", "01-communication", "02-safety", "03-project-model"})
	for _, rule := range rules {
		if rule.Content == "" || !hasTemplateMarkdownPath(rule.SourcePaths, "templates/rules/") {
			t.Fatalf("expected copied btd rule content and template path, got %#v", rule)
		}
	}

	var skills []struct {
		ID          string   `json:"id"`
		SourcePaths []string `json:"sourcePaths"`
		Content     string   `json:"content"`
	}
	getJSON(t, server, "/api/templates/skills", &skills)
	assertTemplateIDs(t, "skills", skills, []string{
		"coding-rules", "cross-client", "cross-config", "cross-gate", "cross-social", "dev-workflow",
		"high-risk-api", "pmconf-pattern", "quest-system", "review-feedback", "skill-standard", "testing",
	})
	for _, skill := range skills {
		if skill.Content == "" || !hasTemplateMarkdownPath(skill.SourcePaths, "templates/skills/") {
			t.Fatalf("expected copied btd skill content and template path, got %#v", skill)
		}
	}

	var workflows []struct {
		ID          string   `json:"id"`
		Entry       string   `json:"entry"`
		Source      string   `json:"source"`
		SourcePaths []string `json:"sourcePaths"`
		Content     string   `json:"content"`
	}
	getJSON(t, server, "/api/templates/workflows", &workflows)
	assertTemplateIDs(t, "workflows", workflows, []string{
		"feature-development", "modify-existing", "bugfix", "code-review", "design",
		"research", "commit-gate", "refactor", "lark-integration",
	})
	for _, workflow := range workflows {
		if workflow.Entry == "" || !strings.HasPrefix(workflow.Entry, "templates/workflows/") || !strings.HasSuffix(workflow.Entry, ".md") {
			t.Fatalf("expected workflow entry to be copied markdown under templates/workflows, got %#v", workflow)
		}
		if workflow.Source != "Expanded workflow markdown" || workflow.Content == "" || !containsString(workflow.SourcePaths, "templates/rules/go-00-routing.md") {
			t.Fatalf("expected btd workflow content and routing lineage, got %#v", workflow)
		}
		if !containsString(workflow.SourcePaths, workflow.Entry) {
			t.Fatalf("expected workflow source paths to include copied markdown entry, got %#v", workflow)
		}
	}
}

func TestTemplateCrudEndpoints(t *testing.T) {
	server := NewServer()

	createResponse := requestJSON(t, server, http.MethodPost, "/api/templates/rules", `{"name":"review-risk","summary":"Check high risk changes before merge.","entry":".claude/rules/review-risk.md","files":[".claude/rules/review-risk.md"]}`)
	if createResponse.Code != http.StatusCreated {
		t.Fatalf("expected create status 201, got %d", createResponse.Code)
	}

	var created struct {
		ID      string `json:"id"`
		Kind    string `json:"kind"`
		Version int    `json:"version"`
		Summary string `json:"summary"`
	}
	decodeJSON(t, createResponse, &created)
	if created.ID == "" || created.Kind != "rule" || created.Version != 1 {
		t.Fatalf("unexpected created template: %#v", created)
	}

	updateResponse := requestJSON(t, server, http.MethodPut, "/api/templates/rules/"+created.ID, `{"summary":"Updated review checklist."}`)
	if updateResponse.Code != http.StatusOK {
		t.Fatalf("expected update status 200, got %d", updateResponse.Code)
	}

	var updated struct {
		ID      string `json:"id"`
		Version int    `json:"version"`
		Summary string `json:"summary"`
	}
	decodeJSON(t, updateResponse, &updated)
	if updated.Version != 2 || updated.Summary != "Updated review checklist." {
		t.Fatalf("unexpected updated template: %#v", updated)
	}

	deleteResponse := requestJSON(t, server, http.MethodDelete, "/api/templates/rules/"+created.ID, "")
	if deleteResponse.Code != http.StatusNoContent {
		t.Fatalf("expected delete status 204, got %d", deleteResponse.Code)
	}
}

func TestProjectCopySyncAndDetachEndpoints(t *testing.T) {
	store := catalog.NewStoreFromData(catalog.BootstrapData{
		Projects:          []catalog.Project{{ID: "sample", Name: "sample", Path: "D:/sample"}},
		ProjectConfigSets: map[string][]catalog.ProjectCopy{"sample": {{ID: "copy-worker", Kind: "agent", Name: "worker", LocalVersion: 1, SyncMode: "manual", Status: "template_updated", Diff: "changed", Origin: &catalog.Origin{TemplateID: "worker", BaseVersion: 1, BaseHash: "sha256:test"}}}},
	}, nil, nil)
	server := NewServerWithStore(store)

	syncResponse := requestJSON(t, server, http.MethodPost, "/api/projects/sample/config/copy-worker/sync", "")
	if syncResponse.Code != http.StatusOK {
		t.Fatalf("expected sync status 200, got %d", syncResponse.Code)
	}

	var synced struct {
		Status string `json:"status"`
		Diff   string `json:"diff"`
		Origin *struct {
			TemplateID string `json:"templateId"`
		} `json:"origin"`
	}
	decodeJSON(t, syncResponse, &synced)
	if synced.Status != "synced" || synced.Origin == nil || synced.Diff == "" {
		t.Fatalf("unexpected synced copy: %#v", synced)
	}

	detachResponse := requestJSON(t, server, http.MethodPost, "/api/projects/sample/config/copy-worker/detach", "")
	if detachResponse.Code != http.StatusOK {
		t.Fatalf("expected detach status 200, got %d", detachResponse.Code)
	}

	var detached struct {
		Status string      `json:"status"`
		Origin interface{} `json:"origin"`
	}
	decodeJSON(t, detachResponse, &detached)
	if detached.Status != "detached" || detached.Origin != nil {
		t.Fatalf("unexpected detached copy: %#v", detached)
	}
}

func TestProjectSyncPreviewEndpoint(t *testing.T) {
	store := catalog.NewStoreFromData(catalog.BootstrapData{
		Projects:          []catalog.Project{{ID: "sample", Name: "sample", Path: "D:/sample"}},
		ProjectConfigSets: map[string][]catalog.ProjectCopy{"sample": {{ID: "copy-worker", Kind: "agent", Name: "worker", LocalVersion: 1, SyncMode: "manual", Status: "template_updated", Diff: "changed", Origin: &catalog.Origin{TemplateID: "worker", BaseVersion: 1, BaseHash: "sha256:test"}}}},
	}, nil, nil)
	server := NewServerWithStore(store)

	var preview []struct {
		ID           string `json:"id"`
		Kind         string `json:"kind"`
		Status       string `json:"status"`
		LocalVersion int    `json:"localVersion"`
		Origin       *struct {
			TemplateID  string `json:"templateId"`
			BaseVersion int    `json:"baseVersion"`
			BaseHash    string `json:"baseHash"`
		} `json:"origin"`
		Diff string `json:"diff"`
	}
	getJSON(t, server, "/api/projects/sample/sync-preview", &preview)
	if len(preview) != 1 || preview[0].LocalVersion == 0 || preview[0].Diff == "" || preview[0].Origin == nil || !strings.HasPrefix(preview[0].Origin.BaseHash, "sha256:") {
		t.Fatalf("expected sync preview metadata, got %#v", preview)
	}
}

func TestProjectRescanEndpoint(t *testing.T) {
	root := t.TempDir()
	writeHTTPTestFile(t, root, ".claude/rules/01-communication.md", "communication")
	store := catalog.NewStoreFromData(catalog.BootstrapData{
		TemplateLibrary: catalog.TemplateLibrary{
			Rules: []catalog.TemplateItem{{
				ID:      "01-communication",
				Kind:    "rule",
				Name:    "01-communication",
				Version: 1,
				Content: "communication",
			}},
			Skills: []catalog.TemplateItem{{
				ID:      "testing",
				Kind:    "skill",
				Name:    "testing",
				Version: 1,
				Content: "template testing skill",
			}},
		},
		ProjectConfigSets: map[string][]catalog.ProjectCopy{},
	}, nil, nil)
	project, err := store.ImportProject(catalog.ProjectInput{Name: "sample", Path: root})
	if err != nil {
		t.Fatalf("import project: %v", err)
	}
	writeHTTPTestFile(t, root, ".claude/skills/testing.md", "project testing skill")

	response := requestJSON(t, NewServerWithStore(store), http.MethodPost, "/api/projects/"+project.ID+"/rescan", "")
	if response.Code != http.StatusOK {
		t.Fatalf("expected rescan status 200, got %d", response.Code)
	}
	var body struct {
		Project struct {
			ID            string `json:"id"`
			ConfigSummary struct {
				Skills int `json:"skills"`
			} `json:"configSummary"`
		} `json:"project"`
		Copies []struct {
			Kind string `json:"kind"`
			Name string `json:"name"`
		} `json:"copies"`
	}
	decodeJSON(t, response, &body)
	if body.Project.ID != project.ID || body.Project.ConfigSummary.Skills != 1 {
		t.Fatalf("expected rescanned project summary, got %#v", body.Project)
	}
	foundSkill := false
	for _, copy := range body.Copies {
		foundSkill = foundSkill || (copy.Kind == "skill" && copy.Name == "testing")
	}
	if !foundSkill {
		t.Fatalf("expected rescanned copies to include skill, got %#v", body.Copies)
	}
}

func TestWorkflowEndpoints(t *testing.T) {
	server := NewServer()

	var workflows []struct {
		ID        string `json:"id"`
		NodeCount int    `json:"nodeCount"`
		EdgeCount int    `json:"edgeCount"`
	}
	getJSON(t, server, "/api/workflows", &workflows)
	if len(workflows) == 0 {
		t.Fatal("expected workflows")
	}
	if workflows[0].NodeCount == 0 || workflows[0].EdgeCount == 0 {
		t.Fatalf("expected workflow graph counts, got %#v", workflows[0])
	}

	var graph struct {
		ID    string `json:"id"`
		Nodes []struct {
			ID       string `json:"id"`
			Type     string `json:"type"`
			Category string `json:"category"`
		} `json:"nodes"`
		Edges []struct {
			From string `json:"from"`
			To   string `json:"to"`
		} `json:"edges"`
	}
	getJSON(t, server, "/api/workflows/code-review", &graph)
	if len(graph.Nodes) == 0 || len(graph.Edges) == 0 {
		t.Fatalf("expected workflow graph, got %#v", graph)
	}
	if graph.Nodes[0].Category == "" {
		t.Fatalf("expected node category, got %#v", graph.Nodes[0])
	}
}

func TestWorkflowCrudEndpoints(t *testing.T) {
	server := NewServer()

	createResponse := requestJSON(t, server, http.MethodPost, "/api/workflows", `{"name":"release guard","summary":"Review release checklist.","trigger":"manual"}`)
	if createResponse.Code != http.StatusCreated {
		t.Fatalf("expected create workflow status 201, got %d", createResponse.Code)
	}

	var created struct {
		ID        string `json:"id"`
		Name      string `json:"name"`
		NodeCount int    `json:"nodeCount"`
	}
	decodeJSON(t, createResponse, &created)
	if created.ID == "" || created.Name != "release guard" || created.NodeCount == 0 {
		t.Fatalf("unexpected created workflow: %#v", created)
	}

	updateResponse := requestJSON(t, server, http.MethodPut, "/api/workflows/"+created.ID, `{"summary":"Updated release checklist.","status":"ready"}`)
	if updateResponse.Code != http.StatusOK {
		t.Fatalf("expected update workflow status 200, got %d", updateResponse.Code)
	}

	var updated struct {
		ID      string `json:"id"`
		Status  string `json:"status"`
		Summary string `json:"summary"`
	}
	decodeJSON(t, updateResponse, &updated)
	if updated.Status != "ready" || updated.Summary != "Updated release checklist." {
		t.Fatalf("unexpected updated workflow: %#v", updated)
	}

	deleteResponse := requestJSON(t, server, http.MethodDelete, "/api/workflows/"+created.ID, "")
	if deleteResponse.Code != http.StatusNoContent {
		t.Fatalf("expected delete workflow status 204, got %d", deleteResponse.Code)
	}
}

func TestWorkflowGraphUpdateEndpoint(t *testing.T) {
	server := NewServer()

	graphBody := `{
		"id":"code-review",
		"name":"--rev 代码审核",
		"nodes":[
			{"id":"trigger","type":"input","category":"event","label":"--rev","agent":"-","detail":"start","x":80,"y":120},
			{"id":"review","type":"agent","category":"action","label":"reviewer","agent":"reviewer-logic","detail":"review diff","x":360,"y":120}
		],
		"edges":[{"from":"trigger","to":"review","label":"diff"}]
	}`

	updateResponse := requestJSON(t, server, http.MethodPut, "/api/workflows/code-review/graph", graphBody)
	if updateResponse.Code != http.StatusOK {
		t.Fatalf("expected update graph status 200, got %d", updateResponse.Code)
	}

	var updated struct {
		ID    string `json:"id"`
		Nodes []struct {
			ID string `json:"id"`
			X  int    `json:"x"`
		} `json:"nodes"`
		Edges []struct {
			From string `json:"from"`
			To   string `json:"to"`
		} `json:"edges"`
	}
	decodeJSON(t, updateResponse, &updated)
	if updated.ID != "code-review" || len(updated.Nodes) != 2 || updated.Nodes[1].X != 360 || len(updated.Edges) != 1 {
		t.Fatalf("unexpected updated graph: %#v", updated)
	}

	var summary []struct {
		ID        string `json:"id"`
		NodeCount int    `json:"nodeCount"`
		EdgeCount int    `json:"edgeCount"`
	}
	getJSON(t, server, "/api/workflows", &summary)
	for _, workflow := range summary {
		if workflow.ID == "code-review" {
			if workflow.NodeCount != 2 || workflow.EdgeCount != 1 {
				t.Fatalf("expected graph counts to update summary, got %#v", workflow)
			}
			return
		}
	}
	t.Fatalf("expected code-review workflow in summary, got %#v", summary)
}

func TestProjectWorkflowGraphEndpointPersistsProjectGraph(t *testing.T) {
	root := t.TempDir()
	writeHTTPTestFile(t, root, ".claude/workflows/go-feature-development.md", "workflow markdown")
	writeHTTPTestFile(t, root, ".claude/workflows/go-feature-development.graph.json", `{"id":"go-feature-development","name":"feature graph","nodes":[{"id":"start","type":"input","category":"event","label":"start","agent":"-","detail":"begin","x":10,"y":20}],"edges":[]}`)

	store := catalog.NewStoreFromData(
		catalog.BootstrapData{
			Projects: []catalog.Project{{ID: "sample", Name: "sample", Path: root}},
			ProjectConfigSets: map[string][]catalog.ProjectCopy{
				"sample": {{
					ID:           "proj_workflow_sample_go_feature_development",
					Kind:         "workflow",
					Name:         "feature-development",
					LocalVersion: 1,
					SyncMode:     "manual",
					Status:       "synced",
					Path:         ".claude/workflows/go-feature-development.md",
					Origin:       &catalog.Origin{TemplateID: "feature-development", BaseVersion: 1, BaseHash: "sha256:test"},
				}},
			},
		},
		nil,
		map[string]catalog.WorkflowGraph{
			"feature-development": {
				ID:    "feature-development",
				Name:  "template graph",
				Nodes: []catalog.WorkflowNode{{ID: "template", Type: "input", Category: "event", Label: "template", Agent: "-", Detail: "template", X: 1, Y: 2}},
			},
		},
	)
	server := NewServerWithStore(store)

	var loaded struct {
		ID    string `json:"id"`
		Nodes []struct {
			ID string `json:"id"`
			X  int    `json:"x"`
		} `json:"nodes"`
	}
	getJSON(t, server, "/api/projects/sample/config/proj_workflow_sample_go_feature_development/graph", &loaded)
	if loaded.ID != "go-feature-development" || loaded.Nodes[0].X != 10 {
		t.Fatalf("expected project graph loaded from workflow graph file, got %#v", loaded)
	}

	updateResponse := requestJSON(t, server, http.MethodPut, "/api/projects/sample/config/proj_workflow_sample_go_feature_development/graph", `{
		"id":"go-feature-development",
		"name":"feature graph",
		"nodes":[
			{"id":"start","type":"input","category":"event","label":"start","agent":"-","detail":"begin","x":120,"y":20},
			{"id":"worker","type":"agent","category":"action","label":"worker","agent":"worker","detail":"work","x":360,"y":20}
		],
		"edges":[{"from":"start","to":"worker","label":"next"}]
	}`)
	if updateResponse.Code != http.StatusOK {
		t.Fatalf("expected project graph update status 200, got %d", updateResponse.Code)
	}

	var updated struct {
		Nodes []struct {
			ID string `json:"id"`
			X  int    `json:"x"`
		} `json:"nodes"`
		Edges []struct {
			From string `json:"from"`
			To   string `json:"to"`
		} `json:"edges"`
	}
	decodeJSON(t, updateResponse, &updated)
	if len(updated.Nodes) != 2 || updated.Nodes[0].X != 120 || len(updated.Edges) != 1 {
		t.Fatalf("unexpected project graph update response: %#v", updated)
	}

	var reloaded struct {
		Nodes []struct {
			ID string `json:"id"`
			X  int    `json:"x"`
		} `json:"nodes"`
		Edges []struct {
			From string `json:"from"`
			To   string `json:"to"`
		} `json:"edges"`
	}
	getJSON(t, server, "/api/projects/sample/config/proj_workflow_sample_go_feature_development/graph", &reloaded)
	if len(reloaded.Nodes) != 2 || reloaded.Nodes[0].X != 120 || len(reloaded.Edges) != 1 {
		t.Fatalf("expected updated project graph to persist, got %#v", reloaded)
	}
}

func TestProjectWorkflowCreateAndDeleteEndpoints(t *testing.T) {
	root := t.TempDir()
	store := catalog.NewStoreFromData(
		catalog.BootstrapData{
			Projects:          []catalog.Project{{ID: "sample", Name: "sample", Path: root}},
			ProjectConfigSets: map[string][]catalog.ProjectCopy{"sample": {}},
		},
		nil,
		nil,
	)
	server := NewServerWithStore(store)

	createResponse := requestJSON(t, server, http.MethodPost, "/api/projects/sample/config/workflows", `{"name":"Release Guard","summary":"Project release checklist.","trigger":"manual"}`)
	if createResponse.Code != http.StatusCreated {
		t.Fatalf("expected create project workflow status 201, got %d", createResponse.Code)
	}
	var created struct {
		Copy struct {
			ID     string      `json:"id"`
			Kind   string      `json:"kind"`
			Origin interface{} `json:"origin"`
			Path   string      `json:"path"`
		} `json:"copy"`
		Graph struct {
			ID    string        `json:"id"`
			Nodes []interface{} `json:"nodes"`
		} `json:"graph"`
	}
	decodeJSON(t, createResponse, &created)
	if created.Copy.ID == "" || created.Copy.Kind != "workflow" || created.Copy.Origin != nil || created.Graph.ID != "release-guard" {
		t.Fatalf("unexpected created project workflow response: %#v", created)
	}

	deleteResponse := requestJSON(t, server, http.MethodDelete, "/api/projects/sample/config/"+created.Copy.ID, "")
	if deleteResponse.Code != http.StatusNoContent {
		t.Fatalf("expected delete project workflow status 204, got %d", deleteResponse.Code)
	}

	missingGraph := requestJSON(t, server, http.MethodGet, "/api/projects/sample/config/"+created.Copy.ID+"/graph", "")
	if missingGraph.Code != http.StatusNotFound {
		t.Fatalf("expected deleted project workflow graph to be 404, got %d", missingGraph.Code)
	}
}

func TestWorkflowDuplicateEndpoint(t *testing.T) {
	server := NewServer()

	response := requestJSON(t, server, http.MethodPost, "/api/workflows/code-review/duplicate", "")
	if response.Code != http.StatusCreated {
		t.Fatalf("expected duplicate workflow status 201, got %d", response.Code)
	}

	var duplicated struct {
		ID        string `json:"id"`
		Name      string `json:"name"`
		NodeCount int    `json:"nodeCount"`
		EdgeCount int    `json:"edgeCount"`
	}
	decodeJSON(t, response, &duplicated)
	if duplicated.ID == "code-review" || duplicated.NodeCount != 10 || duplicated.EdgeCount != 12 {
		t.Fatalf("unexpected duplicated workflow: %#v", duplicated)
	}
	if duplicated.Name == "" || duplicated.Name == "--rev 代码审核" {
		t.Fatalf("expected duplicate name to identify copy, got %#v", duplicated)
	}

	var graph struct {
		ID    string        `json:"id"`
		Nodes []interface{} `json:"nodes"`
		Edges []interface{} `json:"edges"`
	}
	getJSON(t, server, "/api/workflows/"+duplicated.ID, &graph)
	if len(graph.Nodes) != duplicated.NodeCount || len(graph.Edges) != duplicated.EdgeCount {
		t.Fatalf("expected duplicated graph counts, got workflow %#v graph %#v", duplicated, graph)
	}
}

func writeHTTPTestFile(t *testing.T, root string, relative string, content string) {
	t.Helper()
	filePath := filepath.Join(root, filepath.FromSlash(relative))
	if err := os.MkdirAll(filepath.Dir(filePath), 0o755); err != nil {
		t.Fatalf("create test dir: %v", err)
	}
	if err := os.WriteFile(filePath, []byte(content), 0o644); err != nil {
		t.Fatalf("write test file %s: %v", relative, err)
	}
}

func TestModelRouteEndpoint(t *testing.T) {
	server := NewServer()

	var routes []struct {
		ID       string `json:"id"`
		Client   string `json:"client"`
		Source   string `json:"source"`
		Target   string `json:"target"`
		Provider string `json:"provider"`
		Status   string `json:"status"`
	}
	getJSON(t, server, "/api/model-routes", &routes)

	if len(routes) != 5 {
		t.Fatalf("expected five Codex model routes, got %d routes: %#v", len(routes), routes)
	}

	wantTargets := map[string]string{
		"gpt-5.5":           "gpt-5.5",
		"gpt-5.4":           "gpt-5.4",
		"gpt-5.4-mini":      "gpt-5.4-mini",
		"deepseek-v4-pro":   "deepseek-v4-pro",
		"deepseek-v4-flash": "deepseek-v4-flash",
	}
	for _, route := range routes {
		if route.Client != "Codex Responses" {
			t.Fatalf("expected only Codex Responses routes, got %#v", route)
		}
		wantTarget, ok := wantTargets[route.Source]
		if !ok {
			t.Fatalf("unexpected route source %q in %#v", route.Source, route)
		}
		if route.Target != wantTarget {
			t.Fatalf("expected %s to target itself, got %#v", route.Source, route)
		}
		if strings.HasPrefix(route.Source, "gpt-") && route.Provider != "ChatGPT Subscription" {
			t.Fatalf("expected GPT route to use ChatGPT Subscription provider, got %#v", route)
		}
		if strings.HasPrefix(route.Source, "deepseek-") && route.Provider != "Winky DeepSeek" {
			t.Fatalf("expected DeepSeek route to use Winky DeepSeek provider, got %#v", route)
		}
		delete(wantTargets, route.Source)
	}
	if len(wantTargets) != 0 {
		t.Fatalf("missing model routes: %#v", wantTargets)
	}
}

func TestInfrastructureEndpoint(t *testing.T) {
	server := NewServerWithInfrastructureService(catalog.NewInfrastructureService(catalog.InfrastructureServiceOptions{}))

	var items []struct {
		ID             string   `json:"id"`
		Name           string   `json:"name"`
		Kind           string   `json:"kind"`
		Status         string   `json:"status"`
		GitHubURL      string   `json:"githubUrl"`
		InstallCommand string   `json:"installCommand"`
		CommonCommands []string `json:"commonCommands"`
	}
	getJSON(t, server, "/api/infrastructure", &items)

	if len(items) != 2 {
		t.Fatalf("expected exactly 2 infrastructure items, got %#v", items)
	}
	if items[0].ID != "rtk" || items[0].GitHubURL != "https://github.com/rtk-ai/rtk" {
		t.Fatalf("expected first infrastructure item to be RTK with GitHub URL, got %#v", items[0])
	}
	if items[1].ID != "codegraph" || items[1].GitHubURL != "https://github.com/colbymchenry/codegraph" {
		t.Fatalf("expected second infrastructure item to be Codegraph with GitHub URL, got %#v", items[1])
	}
	for _, item := range items {
		if item.Name == "" || item.Kind == "" || item.Status == "" || item.InstallCommand == "" || len(item.CommonCommands) == 0 {
			t.Fatalf("expected complete infrastructure card and drawer metadata, got %#v", item)
		}
	}
}

func TestInfrastructureCatalogExposesCuratedThirdPartyCandidates(t *testing.T) {
	server := NewServerWithInfrastructureService(catalog.NewInfrastructureService(catalog.InfrastructureServiceOptions{}))

	var items []struct {
		ID             string `json:"id"`
		Name           string `json:"name"`
		Source         string `json:"source"`
		Installable    bool   `json:"installable"`
		GitHubURL      string `json:"githubUrl"`
		InstallCommand string `json:"installCommand"`
	}
	getJSON(t, server, "/api/infrastructure/catalog", &items)

	if len(items) == 0 {
		t.Fatal("expected curated third-party infrastructure catalog items")
	}
	foundRepomix := false
	for _, item := range items {
		if item.ID == "repomix" {
			foundRepomix = true
			if item.Source != "third_party" || !item.Installable {
				t.Fatalf("expected repomix to be installable third-party infra, got %#v", item)
			}
			if item.GitHubURL != "https://github.com/yamadashy/repomix" || item.InstallCommand != "npm install -g repomix" {
				t.Fatalf("expected repomix install metadata, got %#v", item)
			}
		}
		if item.ID == "rtk" || item.ID == "codegraph" {
			t.Fatalf("catalog should contain optional third-party candidates only, got %#v", item)
		}
	}
	if !foundRepomix {
		t.Fatalf("expected repomix in third-party catalog, got %#v", items)
	}
}

func TestInfrastructureInstallUsesAllowlistedThirdPartyCommand(t *testing.T) {
	var calls []string
	runner := catalog.InfrastructureCommandRunnerFunc(func(name string, args ...string) (string, error) {
		call := strings.TrimSpace(name + " " + strings.Join(args, " "))
		calls = append(calls, call)
		switch call {
		case "npm install -g repomix":
			return "added repomix\n", nil
		case "repomix --version":
			return "repomix 1.2.3\n", nil
		default:
			t.Fatalf("unexpected command: %s", call)
			return "", nil
		}
	})
	server := NewServerWithInfrastructureService(catalog.NewInfrastructureService(catalog.InfrastructureServiceOptions{Runner: runner}))

	response := requestJSON(t, server, http.MethodPost, "/api/infrastructure/repomix/install", "")
	if response.Code != http.StatusOK {
		t.Fatalf("expected repomix install status 200, got %d", response.Code)
	}
	var installed struct {
		ID          string `json:"id"`
		Source      string `json:"source"`
		Status      string `json:"status"`
		Version     string `json:"version"`
		Output      string `json:"output"`
		Installable bool   `json:"installable"`
	}
	decodeJSON(t, response, &installed)
	if installed.ID != "repomix" || installed.Source != "third_party" || installed.Status != "ready" || installed.Version != "1.2.3" || !installed.Installable {
		t.Fatalf("unexpected installed repomix response: %#v", installed)
	}
	if !strings.Contains(installed.Output, "added repomix") {
		t.Fatalf("expected install output to be preserved, got %#v", installed)
	}
	wantCalls := []string{
		"npm install -g repomix",
		"repomix --version",
	}
	if strings.Join(calls, "|") != strings.Join(wantCalls, "|") {
		t.Fatalf("expected allowlisted install calls %#v, got %#v", wantCalls, calls)
	}
}

func TestInfrastructureCheckAndCodegraphUpdateEndpoints(t *testing.T) {
	var calls []string
	runner := catalog.InfrastructureCommandRunnerFunc(func(name string, args ...string) (string, error) {
		call := strings.TrimSpace(name + " " + strings.Join(args, " "))
		calls = append(calls, call)
		switch call {
		case "codegraph --version":
			return "1.0.1\n", nil
		case "codegraph upgrade --check":
			return "Update available: 1.0.2\n", nil
		case "codegraph upgrade":
			return "Updated CodeGraph to 1.0.2\n", nil
		default:
			t.Fatalf("unexpected command: %s", call)
			return "", nil
		}
	})
	server := NewServerWithInfrastructureService(catalog.NewInfrastructureService(catalog.InfrastructureServiceOptions{Runner: runner}))

	checkResponse := requestJSON(t, server, http.MethodPost, "/api/infrastructure/codegraph/check", "")
	if checkResponse.Code != http.StatusOK {
		t.Fatalf("expected codegraph check status 200, got %d", checkResponse.Code)
	}
	var checked struct {
		ID      string `json:"id"`
		Version string `json:"version"`
		Output  string `json:"output"`
	}
	decodeJSON(t, checkResponse, &checked)
	if checked.ID != "codegraph" || checked.Version != "1.0.1" || !strings.Contains(checked.Output, "Update available") {
		t.Fatalf("unexpected codegraph check result: %#v", checked)
	}

	updateResponse := requestJSON(t, server, http.MethodPost, "/api/infrastructure/codegraph/update", "")
	if updateResponse.Code != http.StatusOK {
		t.Fatalf("expected codegraph update status 200, got %d", updateResponse.Code)
	}
	var updated struct {
		ID      string `json:"id"`
		Version string `json:"version"`
		Output  string `json:"output"`
	}
	decodeJSON(t, updateResponse, &updated)
	if updated.ID != "codegraph" || updated.Version != "1.0.1" || !strings.Contains(updated.Output, "Updated CodeGraph") {
		t.Fatalf("unexpected codegraph update result: %#v", updated)
	}
	wantCalls := []string{
		"codegraph --version",
		"codegraph upgrade --check",
		"codegraph upgrade",
		"codegraph --version",
	}
	if strings.Join(calls, "|") != strings.Join(wantCalls, "|") {
		t.Fatalf("expected allowlisted codegraph calls %#v, got %#v", wantCalls, calls)
	}
}

func TestInfrastructureRTKUpdateUsesDedicatedUpdater(t *testing.T) {
	rtkUpdater := catalog.InfrastructureUpdaterFunc(func() (string, error) {
		return "Downloaded GitHub release asset and replaced rtk.exe\nrtk 0.43.0", nil
	})
	server := NewServerWithInfrastructureService(catalog.NewInfrastructureService(catalog.InfrastructureServiceOptions{RTKUpdater: rtkUpdater}))

	response := requestJSON(t, server, http.MethodPost, "/api/infrastructure/rtk/update", "")
	if response.Code != http.StatusOK {
		t.Fatalf("expected rtk update status 200, got %d", response.Code)
	}
	var body struct {
		ID      string `json:"id"`
		Version string `json:"version"`
		Output  string `json:"output"`
	}
	decodeJSON(t, response, &body)
	if body.ID != "rtk" || body.Version != "0.43.0" || !strings.Contains(body.Output, "GitHub release asset") {
		t.Fatalf("unexpected rtk update response: %#v", body)
	}
}

func TestLocalDirectoriesEndpointListsOnlyDirectories(t *testing.T) {
	root := t.TempDir()
	writeHTTPTestFile(t, root, "btd-game-server/.keep", "")
	writeHTTPTestFile(t, root, "nexus-agents/.keep", "")
	writeHTTPTestFile(t, root, "notes.txt", "not a directory")

	server := NewServer()
	var body struct {
		Path    string `json:"path"`
		Parent  string `json:"parent"`
		Entries []struct {
			Name string `json:"name"`
			Path string `json:"path"`
		} `json:"entries"`
	}
	getJSON(t, server, "/api/local-directories?path="+url.QueryEscape(root), &body)

	if body.Path != root {
		t.Fatalf("expected current path %q, got %#v", root, body)
	}
	if body.Parent == "" {
		t.Fatalf("expected parent path, got %#v", body)
	}
	names := []string{}
	for _, entry := range body.Entries {
		names = append(names, entry.Name)
		if entry.Path == "" {
			t.Fatalf("expected entry path, got %#v", entry)
		}
	}
	if !containsString(names, "btd-game-server") || !containsString(names, "nexus-agents") {
		t.Fatalf("expected child directories, got %#v", names)
	}
	if containsString(names, "notes.txt") {
		t.Fatalf("expected files to be hidden, got %#v", names)
	}
}

func TestLocalDirectoriesEndpointWithoutPathListsChooserRoots(t *testing.T) {
	server := NewServer()
	var body struct {
		Path   string `json:"path"`
		Parent string `json:"parent"`
		Roots  []struct {
			Name string `json:"name"`
			Path string `json:"path"`
		} `json:"roots"`
		Shortcuts []struct {
			Name string `json:"name"`
			Path string `json:"path"`
		} `json:"shortcuts"`
		Entries []struct {
			Name string `json:"name"`
			Path string `json:"path"`
		} `json:"entries"`
	}
	getJSON(t, server, "/api/local-directories", &body)

	if body.Path != "" || body.Parent != "" {
		t.Fatalf("expected root chooser response without selected path, got %#v", body)
	}
	if len(body.Roots) == 0 {
		t.Fatalf("expected at least one chooser root entry, got %#v", body)
	}
	if len(body.Shortcuts) == 0 {
		t.Fatalf("expected chooser shortcuts for Explorer-style navigation, got %#v", body)
	}
	for _, entry := range body.Roots {
		if entry.Name == "" || entry.Path == "" {
			t.Fatalf("expected chooser root entries to include name and path, got %#v", body)
		}
		if strings.Contains(strings.ToLower(entry.Path), `workspace\src`) {
			t.Fatalf("expected root chooser not to default to workspace src, got %#v", body)
		}
	}
}

func TestLocalDirectoriesEndpointIncludesExplorerNavigationMetadata(t *testing.T) {
	root := t.TempDir()
	writeHTTPTestFile(t, root, "project-a/.keep", "")

	server := NewServer()
	var body struct {
		Path  string `json:"path"`
		Roots []struct {
			Name string `json:"name"`
			Path string `json:"path"`
		} `json:"roots"`
		Shortcuts []struct {
			Name string `json:"name"`
			Path string `json:"path"`
		} `json:"shortcuts"`
		Entries []struct {
			Name string `json:"name"`
			Path string `json:"path"`
		} `json:"entries"`
	}
	getJSON(t, server, "/api/local-directories?path="+url.QueryEscape(root), &body)

	if body.Path != root {
		t.Fatalf("expected current path %q, got %#v", root, body)
	}
	if len(body.Roots) == 0 {
		t.Fatalf("expected roots on directory browsing response, got %#v", body)
	}
	if len(body.Shortcuts) == 0 {
		t.Fatalf("expected shortcuts on directory browsing response, got %#v", body)
	}
}

func TestLocalDirectoryPickerEndpointReturnsNativeSelection(t *testing.T) {
	root := t.TempDir()
	server := NewServerWithLocalDirectoryPicker(LocalDirectoryPickerFunc(func() (string, bool, error) {
		return root, true, nil
	}))

	response := requestJSON(t, server, http.MethodPost, "/api/local-directory-picker", "")
	if response.Code != http.StatusOK {
		t.Fatalf("expected native picker status 200, got %d", response.Code)
	}
	var body struct {
		Path     string `json:"path"`
		Selected bool   `json:"selected"`
	}
	decodeJSON(t, response, &body)
	if body.Path != root || !body.Selected {
		t.Fatalf("expected selected native folder path, got %#v", body)
	}
}

func TestLocalDirectoryPickerEndpointNoSelection(t *testing.T) {
	server := NewServerWithLocalDirectoryPicker(LocalDirectoryPickerFunc(func() (string, bool, error) {
		return "", false, nil
	}))

	response := requestJSON(t, server, http.MethodPost, "/api/local-directory-picker", "")
	if response.Code != http.StatusNoContent {
		t.Fatalf("expected canceled native picker status 204, got %d", response.Code)
	}
}

func TestDefaultLocalDirectoryPickerHasShortTimeout(t *testing.T) {
	if defaultLocalDirectoryPickerTimeout <= 0 || defaultLocalDirectoryPickerTimeout > 5*time.Second {
		t.Fatalf("expected native picker timeout to be short enough for Web fallback, got %s", defaultLocalDirectoryPickerTimeout)
	}
}

func TestModelRouteResolveEndpoint(t *testing.T) {
	server := NewServer()

	tests := []struct {
		name            string
		path            string
		wantProvider    string
		wantTarget      string
		wantPassthrough bool
	}{
		{
			name:            "codex gpt-5.5 passthrough",
			path:            "/api/model-routes/resolve?client=codex&model=gpt-5.5",
			wantProvider:    "ChatGPT Subscription",
			wantTarget:      "gpt-5.5",
			wantPassthrough: true,
		},
		{
			name:            "codex gpt-5.4-mini subscription passthrough",
			path:            "/api/model-routes/resolve?client=codex&model=gpt-5.4-mini",
			wantProvider:    "ChatGPT Subscription",
			wantTarget:      "gpt-5.4-mini",
			wantPassthrough: true,
		},
		{
			name:         "codex deepseek flash to winky",
			path:         "/api/model-routes/resolve?client=codex&model=deepseek-v4-flash",
			wantProvider: "Winky DeepSeek",
			wantTarget:   "deepseek-v4-flash",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var body struct {
				Provider    string `json:"provider"`
				TargetModel string `json:"targetModel"`
				Passthrough bool   `json:"passthrough"`
			}
			getJSON(t, server, tt.path, &body)
			if body.Provider != tt.wantProvider {
				t.Fatalf("expected provider %q, got %q", tt.wantProvider, body.Provider)
			}
			if body.TargetModel != tt.wantTarget {
				t.Fatalf("expected target model %q, got %q", tt.wantTarget, body.TargetModel)
			}
			if body.Passthrough != tt.wantPassthrough {
				t.Fatalf("expected passthrough %v, got %v", tt.wantPassthrough, body.Passthrough)
			}
		})
	}
}

func TestModelRouteResolveMissReturnsNotFound(t *testing.T) {
	server := NewServer()
	response := requestJSON(t, server, http.MethodGet, "/api/model-routes/resolve?client=codex&model=unknown-model", "")
	if response.Code != http.StatusNotFound {
		t.Fatalf("expected unknown route to return 404, got %d", response.Code)
	}
}

func TestCodexRouterCatalogAndModelsEndpoints(t *testing.T) {
	server := NewServer()

	var catalogBody struct {
		Models []struct {
			Slug                  string `json:"slug"`
			DisplayName           string `json:"display_name"`
			ApplyPatchToolType    string `json:"apply_patch_tool_type"`
			DefaultReasoningLevel string `json:"default_reasoning_level"`
		} `json:"models"`
	}
	getJSON(t, server, "/proxy/codex/model-catalog.json", &catalogBody)
	if len(catalogBody.Models) == 0 {
		t.Fatal("expected Codex model catalog entries")
	}
	foundGPT := false
	foundDeepSeek := false
	for _, model := range catalogBody.Models {
		if model.Slug == "gpt-5.5" {
			foundGPT = model.DisplayName != "" && model.ApplyPatchToolType == "freeform"
		}
		// Hybrid mode: DeepSeek is exposed under a built-in GPT slug so the
		// Codex picker renders it; the DisplayName carries the real model name.
		if model.Slug == "deepseek-v4-pro" {
			foundDeepSeek = model.DisplayName == "DeepSeek V4 Pro" && model.DefaultReasoningLevel == "high"
		}
	}
	if !foundGPT || !foundDeepSeek {
		t.Fatalf("expected catalog to include GPT subscription and DeepSeek models, got %#v", catalogBody.Models)
	}

	var modelsBody struct {
		Object string `json:"object"`
		Data   []struct {
			ID      string `json:"id"`
			Object  string `json:"object"`
			OwnedBy string `json:"owned_by"`
		} `json:"data"`
	}
	getJSON(t, server, "/proxy/codex/v1/models", &modelsBody)
	foundModelGPT := false
	foundModelDeepSeek := false
	for _, model := range modelsBody.Data {
		foundModelGPT = foundModelGPT || model.ID == "gpt-5.5"
		foundModelDeepSeek = foundModelDeepSeek || model.ID == "deepseek-v4-flash"
	}
	if modelsBody.Object != "list" || !foundModelGPT || !foundModelDeepSeek {
		t.Fatalf("expected OpenAI-compatible model list with GPT and DeepSeek models, got %#v", modelsBody)
	}
}

func TestCodexRouterResponsesConvertsDeepSeekChatCompletion(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" {
			t.Fatalf("expected chat completions upstream path, got %s", r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer provider-key" {
			t.Fatalf("expected provider authorization header, got %q", r.Header.Get("Authorization"))
		}
		var body struct {
			Model    string `json:"model"`
			Stream   bool   `json:"stream"`
			Messages []struct {
				Role    string `json:"role"`
				Content string `json:"content"`
			} `json:"messages"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatalf("decode upstream request: %v", err)
		}
		if body.Model != "deepseek-v4-pro" || body.Stream {
			t.Fatalf("unexpected upstream body: %#v", body)
		}
		if len(body.Messages) != 2 || body.Messages[0].Role != "system" || body.Messages[1].Content != "hello" {
			t.Fatalf("expected system instructions and user input messages, got %#v", body.Messages)
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"id":     "chatcmpl_test",
			"object": "chat.completion",
			"choices": []map[string]any{{
				"message": map[string]any{
					"role":    "assistant",
					"content": "hello from deepseek",
				},
			}},
			"usage": map[string]any{"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
		})
	}))
	defer upstream.Close()

	server := NewServerWithCodexRouter(codexrouter.NewService(codexrouter.Config{
		DefaultModel: "deepseek-v4-pro",
		Routes: []codexrouter.Route{{
			ID:          "deepseek-v4-pro",
			DisplayName: "DeepSeek V4 Pro",
			API:         "chat_completions",
			BaseURL:     upstream.URL + "/v1",
			Model:       "deepseek-v4-pro",
			Provider:    "Winky DeepSeek",
			AuthMode:    "api_key",
			APIKey:      "provider-key",
		}},
	}))

	response := requestJSON(t, server, http.MethodPost, "/proxy/codex/v1/responses", `{"model":"deepseek-v4-pro","instructions":"be brief","input":"hello","stream":true}`)
	if response.Code != http.StatusOK {
		t.Fatalf("expected responses status 200, got %d body=%s", response.Code, response.Body.String())
	}
	if !strings.Contains(response.Header().Get("Content-Type"), "text/event-stream") {
		t.Fatalf("expected event-stream response, got %q", response.Header().Get("Content-Type"))
	}
	body := response.Body.String()
	if !strings.Contains(body, "response.completed") || !strings.Contains(body, "hello from deepseek") || !strings.Contains(body, `"total_tokens":7`) {
		t.Fatalf("expected SSE response event with converted text and usage, got %s", body)
	}
}

func TestCodexRouterResponsesPassesGPTSubscriptionBearer(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/backend-api/codex/responses" {
			t.Fatalf("expected Codex backend responses path, got %s", r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer codex-token" {
			t.Fatalf("expected Codex bearer passthrough, got %q", r.Header.Get("Authorization"))
		}
		var body struct {
			Model string `json:"model"`
			Input string `json:"input"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatalf("decode upstream request: %v", err)
		}
		if body.Model != "gpt-5.5" || body.Input != "hello gpt" {
			t.Fatalf("unexpected passthrough body: %#v", body)
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"id":          "resp_subscription",
			"object":      "response",
			"status":      "completed",
			"model":       "gpt-5.5",
			"output_text": "hello from subscription",
		})
	}))
	defer upstream.Close()

	server := NewServerWithCodexRouter(codexrouter.NewService(codexrouter.Config{
		DefaultModel: "gpt-5.5",
		Routes: []codexrouter.Route{{
			ID:          "gpt-5.5",
			DisplayName: "GPT-5.5",
			API:         "responses",
			BaseURL:     upstream.URL + "/backend-api/codex",
			Model:       "gpt-5.5",
			Provider:    "Codex subscription",
			AuthMode:    "codex_openai",
		}},
	}))

	request := httptest.NewRequest(http.MethodPost, "/proxy/codex/v1/responses", strings.NewReader(`{"model":"gpt-5.5","input":"hello gpt"}`))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer codex-token")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("expected passthrough status 200, got %d body=%s", response.Code, response.Body.String())
	}
	if !strings.Contains(response.Body.String(), "hello from subscription") {
		t.Fatalf("expected passthrough body, got %s", response.Body.String())
	}
}

func assertTemplateIDs[T any](t *testing.T, label string, items []T, want []string) {
	t.Helper()
	if len(items) != len(want) {
		t.Fatalf("expected %d %s templates, got %d", len(want), label, len(items))
	}
	found := make(map[string]bool, len(items))
	for _, item := range items {
		data, err := json.Marshal(item)
		if err != nil {
			t.Fatalf("marshal %s template item: %v", label, err)
		}
		var body struct {
			ID string `json:"id"`
		}
		if err := json.Unmarshal(data, &body); err != nil {
			t.Fatalf("unmarshal %s template item id: %v", label, err)
		}
		found[body.ID] = true
	}
	for _, id := range want {
		if !found[id] {
			t.Fatalf("expected %s template %s, got %#v", label, id, found)
		}
	}
}

func containsString(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

func hasTemplateMarkdownPath(values []string, prefix string) bool {
	for _, value := range values {
		if strings.HasPrefix(value, prefix) && strings.HasSuffix(value, ".md") {
			return true
		}
	}
	return false
}
