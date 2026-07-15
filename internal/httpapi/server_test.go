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

func TestEvaluationEndpoints(t *testing.T) {
	evaluationStore, err := catalog.NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.json"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	server := newServerWithOptions(catalog.NewStore(), catalog.NewInfrastructureService(catalog.InfrastructureServiceOptions{}), nil, defaultLocalDirectoryPicker{}, evaluationStore).(*Server)

	createResponse := requestJSON(t, server, http.MethodPost, "/api/task-runs", `{
		"projectId":"sample",
		"workflowTemplateId":"bugfix",
		"workflowCopyId":"proj_workflow_sample_bugfix",
		"workflowType":"bugfix",
		"taskTitle":"fix panic",
		"submittedStatus":"success",
		"durationMs":1200000,
		"context":{"agent":"debugger","model":"gpt-5.4","rules":["bugfix-core"],"tools":["shell"]},
		"metrics":{"testRunCount":2,"retryCount":0},
		"evidence":{"verification":{"hasVerification":true,"passed":true}}
	}`)
	if createResponse.Code != http.StatusCreated {
		t.Fatalf("expected task run create status 201, got %d body=%s", createResponse.Code, createResponse.Body.String())
	}
	var created catalog.TaskRun
	decodeJSON(t, createResponse, &created)
	if created.ID == "" || created.EvaluationStatus != "pending" {
		t.Fatalf("expected pending task run, got %#v", created)
	}

	runResponse := requestJSON(t, server, http.MethodPost, "/api/evaluations/run-pending", "")
	if runResponse.Code != http.StatusOK {
		t.Fatalf("expected run pending status 200, got %d", runResponse.Code)
	}
	var runBody struct {
		Evaluated int `json:"evaluated"`
	}
	decodeJSON(t, runResponse, &runBody)
	if runBody.Evaluated != 1 {
		t.Fatalf("expected one evaluated task run, got %#v", runBody)
	}

	var evaluations []catalog.Evaluation
	getJSON(t, server, "/api/evaluations", &evaluations)
	if len(evaluations) != 1 || evaluations[0].RunID != created.ID || evaluations[0].RubricID != "bugfix-rubric" {
		t.Fatalf("unexpected evaluations: %#v", evaluations)
	}

	score := 80.0
	reviewResponse := requestJSON(t, server, http.MethodPost, "/api/evaluations/"+evaluations[0].ID+"/review", `{"reviewer":"user","overrideStatus":"partial_success","overrideScore":80,"review":{"comment":"needs one more regression case"}}`)
	if reviewResponse.Code != http.StatusCreated {
		t.Fatalf("expected review status 201, got %d body=%s", reviewResponse.Code, reviewResponse.Body.String())
	}
	var review catalog.EvaluationReview
	decodeJSON(t, reviewResponse, &review)
	if review.OverrideScore == nil || *review.OverrideScore != score || review.OverrideStatus != "partial_success" {
		t.Fatalf("unexpected review: %#v", review)
	}

	var summary catalog.EvaluationSummary
	getJSON(t, server, "/api/evaluations/summary", &summary)
	if summary.TotalRuns != 1 || summary.EvaluatedRuns != 1 || len(summary.WorkflowMetrics) != 1 {
		t.Fatalf("unexpected summary: %#v", summary)
	}
	if len(summary.DimensionStats) == 0 {
		t.Fatalf("expected agent/model/rules dimension stats, got %#v", summary)
	}

	rebuildResponse := requestJSON(t, server, http.MethodPost, "/api/learning-cases/rebuild-index", "")
	if rebuildResponse.Code != http.StatusOK {
		t.Fatalf("expected rebuild index status 200, got %d body=%s", rebuildResponse.Code, rebuildResponse.Body.String())
	}
	var cases []catalog.LearningCase
	getJSON(t, server, "/api/learning-cases", &cases)
	if len(cases) != 1 || cases[0].CaseType != "success" {
		t.Fatalf("expected archived success learning case, got %#v", cases)
	}
	var hits []catalog.LearningCaseHit
	getJSON(t, server, "/api/learning-cases/search?q=panic", &hits)
	if len(hits) != 1 || hits[0].Case.ID != cases[0].ID {
		t.Fatalf("expected learning case search hit, got %#v", hits)
	}
}

func TestTaskRunsEndpointAcceptsWorkflowSubmitterPayload(t *testing.T) {
	evaluationStore, err := catalog.NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.json"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	server := newServerWithOptions(catalog.NewStore(), catalog.NewInfrastructureService(catalog.InfrastructureServiceOptions{}), nil, defaultLocalDirectoryPicker{}, evaluationStore).(*Server)

	response := requestJSON(t, server, http.MethodPost, "/api/task-runs", `{
		"projectId":"btd-client",
		"workflowTemplateId":"unity-ui-feature-development",
		"workflowCopyId":"copy-123",
		"workflowType":"ui-feature-development",
		"taskTitle":"Implement shop popup",
		"submittedStatus":"success",
		"context":{"agent":"unity-ui-developer","model":"gpt-5.4","rules":["unity-00-routing"],"skills":["wf-unity-ui-feature"],"tools":["unity-mcp"]},
		"metrics":{"toolCallCount":12,"nodeCount":3},
		"evidence":{"summary":"workflow submitter auto-uploaded result","verification":{"hasVerification":true,"passed":true}}
	}`)
	if response.Code != http.StatusCreated {
		t.Fatalf("expected workflow submitter payload status 201, got %d body=%s", response.Code, response.Body.String())
	}
	var created catalog.TaskRun
	decodeJSON(t, response, &created)
	if created.ProjectID != "btd-client" || created.WorkflowType != "ui-feature-development" || created.WorkflowCopyID != "copy-123" {
		t.Fatalf("unexpected created run: %#v", created)
	}
}

func TestWorkflowRunnerEndpointsStartAndCompleteSubmitTaskRun(t *testing.T) {
	evaluationStore, err := catalog.NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.json"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	server := newServerWithOptions(catalog.NewStore(), catalog.NewInfrastructureService(catalog.InfrastructureServiceOptions{}), nil, defaultLocalDirectoryPicker{}, evaluationStore)

	startReq := httptest.NewRequest(http.MethodPost, "/api/workflow-runs/start", strings.NewReader(`{
		"projectId":"btd-client",
		"workflowTemplateId":"ui-feature-development",
		"workflowType":"ui-feature-development",
		"taskTitle":"Implement popup",
		"context":{"agent":"unity-ui-developer"}
	}`))
	startReq.Header.Set("Content-Type", "application/json")
	startReq.Header.Set("Session-Id", "sess-workflow-1")
	start := httptest.NewRecorder()
	server.ServeHTTP(start, startReq)
	if start.Code != http.StatusCreated {
		t.Fatalf("expected workflow start status 201, got %d body=%s", start.Code, start.Body.String())
	}
	var started struct {
		ID     string `json:"id"`
		Status string `json:"status"`
	}
	decodeJSON(t, start, &started)
	if started.ID == "" || started.Status != "running" {
		t.Fatalf("unexpected started run: %#v", started)
	}

	complete := requestJSON(t, server, http.MethodPost, "/api/workflow-runs/"+started.ID+"/complete", `{
		"submittedStatus":"success",
		"evidence":{"summary":"workflow completed"}
	}`)
	if complete.Code != http.StatusOK {
		t.Fatalf("expected workflow complete status 200, got %d body=%s", complete.Code, complete.Body.String())
	}
	var finished struct {
		Run struct {
			Status    string `json:"status"`
			TaskRunID string `json:"taskRunId"`
		} `json:"run"`
		TaskRun catalog.TaskRun `json:"taskRun"`
	}
	decodeJSON(t, complete, &finished)
	if finished.Run.Status != "completed" || finished.Run.TaskRunID == "" || finished.TaskRun.WorkflowType != "ui-feature-development" {
		t.Fatalf("unexpected workflow completion payload: %#v", finished)
	}
}

func TestEvaluationProjectProposalAndStatisticsEndpoints(t *testing.T) {
	evaluationStore, err := catalog.NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.json"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	server := newServerWithOptions(catalog.NewStore(), catalog.NewInfrastructureService(catalog.InfrastructureServiceOptions{}), nil, defaultLocalDirectoryPicker{}, evaluationStore)

	weakResponse := requestJSON(t, server, http.MethodPost, "/api/task-runs", `{
		"projectId":"sample",
		"workflowTemplateId":"research",
		"workflowType":"research",
		"taskTitle":"research model routing",
		"submittedStatus":"partial_success",
		"context":{"agent":"oracle","model":"deepseek-v4-flash","rules":[]},
		"metrics":{"errorCount":1},
		"evidence":{"contextMissing":true}
	}`)
	if weakResponse.Code != http.StatusCreated {
		t.Fatalf("expected weak task run status 201, got %d body=%s", weakResponse.Code, weakResponse.Body.String())
	}
	if response := requestJSON(t, server, http.MethodPost, "/api/evaluations/run-pending", ""); response.Code != http.StatusOK {
		t.Fatalf("expected run pending status 200, got %d", response.Code)
	}

	var projects catalog.EvaluationProjectsResponse
	getJSON(t, server, "/api/evaluation/projects", &projects)
	if len(projects.Projects) != 1 || projects.Projects[0].ProjectID != "sample" || projects.Projects[0].ProposalCount != 0 {
		t.Fatalf("expected sample project health without auto proposals, got %#v", projects)
	}

	var proposals catalog.EvaluationProposalsResponse
	getJSON(t, server, "/api/evaluation/proposals?projectId=sample&status=pending", &proposals)
	if len(proposals.Items) != 0 {
		t.Fatalf("expected no auto proposals in objective evaluation mode, got %#v", proposals)
	}

	var failed catalog.StatisticsTasksResponse
	getJSON(t, server, "/api/statistics/tasks?view=failed&range=all", &failed)
	if len(failed.Items) != 0 {
		t.Fatalf("partial success should not appear in failed view, got %#v", failed)
	}
	var low catalog.StatisticsTasksResponse
	getJSON(t, server, "/api/statistics/tasks?view=low_scored&range=all", &low)
	if len(low.Items) != 1 || low.Items[0].ProjectID != "sample" || low.Items[0].EvaluationID == "" || low.Items[0].Agent != "oracle" {
		t.Fatalf("expected low scored task view joined with run metadata, got %#v", low)
	}
}

func TestProjectImportAndDeleteEndpoints(t *testing.T) {
	root := t.TempDir()
	home := t.TempDir()
	t.Setenv("USERPROFILE", home)
	t.Setenv("HOME", home)
	server := NewServer()

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
	if imported.RepoKey == "" || imported.LocalPath == "" || imported.LocalConfigPath != "" || imported.LocalConfigIgnored {
		t.Fatalf("expected local import metadata fields, got %#v", imported)
	}
	if _, err := os.Stat(filepath.Join(home, ".nexus")); err != nil {
		t.Fatalf("expected imported project to be indexed in user home .nexus file: %v", err)
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
	if _, err := os.Stat(filepath.Join(home, ".nexus")); !os.IsNotExist(err) {
		t.Fatalf("expected delete endpoint to remove user home .nexus file, err=%v", err)
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
		Name            string   `json:"name"`
		Slug            string   `json:"slug"`
		ModelTier       string   `json:"modelTier"`
		SourcePaths     []string `json:"sourcePaths"`
		CodexProjection string   `json:"codexProjection"`
		ClaudeSource    string   `json:"claudeSource"`
	}
	getJSON(t, server, "/api/templates/agents", &agents)
	assertTemplateIDs(t, "agents", agents, []string{
		"debugger", "gatekeeper", "hephaestus", "librarian", "oracle", "prometheus",
		"quick", "reviewer-logic", "reviewer-perf", "reviewer-security", "sisyphus", "worker",
		"workflow-evaluator", "learning-curator", "model-arbiter",
		"unity-debugger", "unity-bugfix-developer", "unity-bugfix-reviewer", "unity-logic-developer", "unity-logic-reviewer", "unity-ui-developer",
		"unity-asset-safety-evaluator", "unity-regression-evaluator", "unity-workflow-evaluator",
	})
	for _, agent := range agents {
		displayName := "go-" + agent.ID
		if strings.HasPrefix(agent.ID, "unity-") {
			displayName = agent.ID
		}
		if agent.Name != displayName || agent.Slug != displayName {
			t.Fatalf("expected agent template identity to preserve go- filename prefix, got %#v", agent)
		}
		if agent.ModelTier == "" || agent.CodexProjection == "" || agent.ClaudeSource == "" {
			t.Fatalf("expected btd agent model and source metadata, got %#v", agent)
		}
		if !containsString(agent.SourcePaths, "templates/agents/claude/"+displayName+".md") {
			t.Fatalf("expected copied agent markdown template for %s, got %#v", agent.ID, agent.SourcePaths)
		}
		if !containsString(agent.SourcePaths, "templates/agents/codex/"+displayName+".toml") {
			t.Fatalf("expected copied agent toml template for %s, got %#v", agent.ID, agent.SourcePaths)
		}
	}

	var rules []struct {
		ID          string   `json:"id"`
		Name        string   `json:"name"`
		Slug        string   `json:"slug"`
		SourcePaths []string `json:"sourcePaths"`
		Content     string   `json:"content"`
	}
	getJSON(t, server, "/api/templates/rules", &rules)
	assertTemplateIDs(t, "rules", rules, []string{"00-routing", "01-communication", "02-safety", "03-project-model", "unity-00-routing", "unity-01-project-model", "unity-id-bugfix-safety", "unity-id-logic-mod-safety", "unity-id-ui-safety"})
	for _, rule := range rules {
		displayName := strings.TrimSuffix(strings.TrimPrefix(rule.SourcePaths[0], "templates/rules/"), ".md")
		if rule.Name != displayName || rule.Slug != displayName {
			t.Fatalf("expected rule template identity to preserve go- filename prefix, got %#v", rule)
		}
		if rule.Content == "" || !hasTemplateMarkdownPath(rule.SourcePaths, "templates/rules/") {
			t.Fatalf("expected copied btd rule content and template path, got %#v", rule)
		}
	}

	var skills []struct {
		ID          string   `json:"id"`
		Name        string   `json:"name"`
		Slug        string   `json:"slug"`
		SourcePaths []string `json:"sourcePaths"`
		Content     string   `json:"content"`
	}
	getJSON(t, server, "/api/templates/skills", &skills)
	assertTemplateIDs(t, "skills", skills, []string{
		"coding-rules", "cross-client", "cross-config", "cross-gate", "cross-social", "dev-workflow",
		"high-risk-api", "pmconf-pattern", "quest-system", "review-feedback", "skill-standard", "test-first-and-worktree", "testing",
		"unity-mcp-skill", "unity-testing", "unity-asset-safety", "unity-debugger", "unity-bugfix-developer", "unity-bugfix-review", "unity-logic-developer", "unity-logic-review", "unity-ui-developer", "unity-ui-resolver", "csharp-behaviour-tree",
	})
	for _, skill := range skills {
		if strings.HasPrefix(skill.SourcePaths[0], "templates/skills/go-") {
			displayName := "go-" + skill.ID
			if skill.Name != displayName || skill.Slug != displayName {
				t.Fatalf("expected skill template identity to preserve go- filename prefix, got %#v", skill)
			}
		}
		if skill.Content == "" || !hasTemplateMarkdownPath(skill.SourcePaths, "templates/skills/") {
			t.Fatalf("expected copied btd skill content and template path, got %#v", skill)
		}
	}

	var workflows []struct {
		ID          string   `json:"id"`
		Name        string   `json:"name"`
		Slug        string   `json:"slug"`
		Entry       string   `json:"entry"`
		Source      string   `json:"source"`
		SourcePaths []string `json:"sourcePaths"`
		Content     string   `json:"content"`
	}
	getJSON(t, server, "/api/templates/workflows", &workflows)
	assertTemplateIDs(t, "workflows", workflows, []string{
		"feature-development", "bugfix", "code-review", "design",
		"research", "commit-gate", "refactor", "lark-integration", "subagent-driven-development",
		"bug-investigation", "logic-modification", "ui-feature-development",
	})
	for _, workflow := range workflows {
		if strings.HasPrefix(workflow.Entry, "templates/workflows/go-") {
			displayName := "go-" + workflow.ID
			if workflow.Name != displayName || workflow.Slug != displayName {
				t.Fatalf("expected workflow template identity to preserve go- filename prefix, got %#v", workflow)
			}
		}
		if workflow.Entry == "" || !strings.HasPrefix(workflow.Entry, "templates/workflows/") || !strings.HasSuffix(workflow.Entry, ".md") {
			t.Fatalf("expected workflow entry to be copied markdown under templates/workflows, got %#v", workflow)
		}
		routingPath := "templates/rules/go-00-routing.md"
		if strings.HasPrefix(workflow.Entry, "templates/workflows/unity-") {
			routingPath = "templates/rules/unity-00-routing.md"
		}
		if workflow.Source != "Expanded workflow markdown" || workflow.Content == "" || !containsString(workflow.SourcePaths, routingPath) {
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
	root := t.TempDir()
	writeHTTPTestFile(t, root, ".claude/skills/testing.md", "project testing skill")
	store := catalog.NewStoreFromData(catalog.BootstrapData{
		TemplateLibrary: catalog.TemplateLibrary{
			Skills: []catalog.TemplateItem{{ID: "testing", Kind: "skill", Name: "testing", Version: 1, Content: "template testing skill"}},
		},
		Projects:          []catalog.Project{{ID: "sample", Name: "sample", Path: root, LocalPath: root}},
		ProjectConfigSets: map[string][]catalog.ProjectCopy{"sample": {{ID: "copy-testing", Kind: "skill", Name: "testing", Path: ".claude/skills/testing.md", LocalVersion: 1, SyncMode: "manual", Status: "template_updated", Diff: "changed", Origin: &catalog.Origin{TemplateID: "testing", BaseVersion: 1, BaseHash: "sha256:test"}}}},
	}, nil, nil)
	server := NewServerWithStore(store)

	syncResponse := requestJSON(t, server, http.MethodPost, "/api/projects/sample/config/copy-testing/sync", "")
	if syncResponse.Code != http.StatusOK {
		t.Fatalf("expected sync status 200, got %d", syncResponse.Code)
	}
	syncedContent, err := os.ReadFile(filepath.Join(root, ".claude", "skills", "testing.md"))
	if err != nil {
		t.Fatalf("expected synced skill file: %v", err)
	}
	if string(syncedContent) != "template testing skill" {
		t.Fatalf("expected sync endpoint to write project file, got %q", string(syncedContent))
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

	detachResponse := requestJSON(t, server, http.MethodPost, "/api/projects/sample/config/copy-testing/detach", "")
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

func TestProjectCopyFromTemplateEndpoint(t *testing.T) {
	root := t.TempDir()
	templateRoot := t.TempDir()
	templateMarkdown := filepath.Join(templateRoot, "workflows", "go-bugfix.md")
	templateGraph := filepath.Join(templateRoot, "workflows", "go-bugfix.graph.json")
	writeHTTPTestFile(t, templateRoot, "workflows/go-bugfix.md", "# go-bugfix\n")
	writeHTTPTestFile(t, templateRoot, "workflows/go-bugfix.graph.json", `{"id":"bugfix","name":"bugfix","nodes":[],"edges":[]}`+"\n")

	store := catalog.NewStoreFromData(catalog.BootstrapData{
		TemplateLibrary: catalog.TemplateLibrary{
			Workflows: []catalog.TemplateItem{{
				ID:      "bugfix",
				Kind:    "workflow",
				Name:    "bugfix",
				Version: 1,
				Entry:   templateMarkdown,
				Files:   []string{templateMarkdown, templateGraph},
			}},
		},
		Projects:          []catalog.Project{{ID: "sample", Name: "sample", Path: root, LocalPath: root}},
		ProjectConfigSets: map[string][]catalog.ProjectCopy{"sample": {}},
	}, nil, nil)
	server := NewServerWithStore(store)

	response := requestJSON(t, server, http.MethodPost, "/api/projects/sample/config/from-template", `{"kind":"workflow","templateId":"bugfix"}`)
	if response.Code != http.StatusCreated {
		t.Fatalf("expected add from template status 201, got %d", response.Code)
	}
	var copy catalog.ProjectCopy
	decodeJSON(t, response, &copy)
	if copy.Kind != "workflow" || copy.Status != "synced" || copy.Path != ".claude/workflows/go-bugfix.md" {
		t.Fatalf("unexpected project copy from template: %#v", copy)
	}
	if _, err := os.Stat(filepath.Join(root, ".claude", "workflows", "go-bugfix.graph.json")); err != nil {
		t.Fatalf("expected workflow graph to be written: %v", err)
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
		"name":"$wf-go-review 代码审核",
		"nodes":[
			{"id":"trigger","type":"input","category":"event","label":"$wf-go-review","agent":"-","detail":"start","x":80,"y":120},
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
	if duplicated.Name == "" || duplicated.Name == "$wf-go-review 代码审核" {
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

func TestProjectKnowledgeRetrieveEndpoint(t *testing.T) {
	root := t.TempDir()
	writeHTTPTestFile(t, root, "design/KnowledgeBase/index.md", httpOKFDoc("Index", "Root", "design/KnowledgeBase/index.md", "# Root\n\n[Project routing](./project/routing.md)"))
	writeHTTPTestFile(t, root, "design/KnowledgeBase/project/routing.md", httpOKFDoc("Routing", "Project Routing", "design/KnowledgeBase/project/routing.md", "# Routing\n\nUI tasks read `design/KnowledgeBase/domains/ui/routing.md`."))
	writeHTTPTestFile(t, root, "design/KnowledgeBase/domains/ui/routing.md", httpOKFDoc("Routing", "UI Routing", "design/KnowledgeBase/domains/ui/routing.md", "# UI Routing\n\nRequired:\n- design/KnowledgeBase/domains/ui/coding_rules.md"))
	writeHTTPTestFile(t, root, "design/KnowledgeBase/domains/ui/coding_rules.md", httpOKFDoc("CodingRules", "UI Coding Rules", "design/KnowledgeBase/domains/ui/coding_rules.md", "# Coding\n\n## ViewModel\n\n弹窗 UI 使用 ViewModel 管理状态。"))
	store := catalog.NewStoreFromData(catalog.BootstrapData{Projects: []catalog.Project{{ID: "sample", Name: "sample", Path: root}}}, nil, nil)
	server := NewServerWithStore(store)

	response := requestJSON(t, server, http.MethodGet, "/api/projects/sample/knowledge/retrieve?q=UI&mode=routing&maxTokens=1000", "")
	if response.Code != http.StatusOK {
		t.Fatalf("expected retrieve status 200, got %d body=%s", response.Code, response.Body.String())
	}
	var body struct {
		MatchedDomain string `json:"matchedDomain"`
		Required      []struct {
			Path    string   `json:"path"`
			Snippet string   `json:"snippet"`
			Reasons []string `json:"reasons"`
		} `json:"required"`
		TokenBudget struct {
			MaxTokens  int `json:"maxTokens"`
			UsedTokens int `json:"usedTokens"`
		} `json:"tokenBudget"`
		LoadedKnowledgeMarkdown string `json:"loadedKnowledgeMarkdown"`
	}
	decodeJSON(t, response, &body)
	if body.MatchedDomain != "ui" || len(body.Required) == 0 || body.TokenBudget.UsedTokens > body.TokenBudget.MaxTokens || !strings.Contains(body.LoadedKnowledgeMarkdown, "Loaded Knowledge") {
		t.Fatalf("unexpected retrieve response: %#v", body)
	}
}

func httpOKFDoc(kind, title, resource, body string) string {
	return "---\ntype: " + kind + "\ntitle: " + title + "\ndescription: Test.\nresource: " + resource + "\ntags: [test, ui]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n" + body
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

	if len(routes) != 11 {
		t.Fatalf("expected eleven Codex model routes, got %d routes: %#v", len(routes), routes)
	}

	wantTargets := map[string]string{
		"gpt-5.6":           "gpt-5.6",
		"gpt-5.6-terra":     "gpt-5.6-terra",
		"gpt-5.6-luna":      "gpt-5.6-luna",
		"gpt-5.5":           "gpt-5.5",
		"gpt-5.4":           "gpt-5.4",
		"gpt-5.4-mini":      "gpt-5.4-mini",
		"deepseek-v4-pro":   "deepseek-v4-pro",
		"deepseek-v4-flash": "deepseek-v4-flash",
		"glm-5.2":           "glm-5.2",
		"glm-5.1":           "glm-5.1",
		"claude-sonnet-5":   "claude-sonnet-5",
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
		if strings.HasPrefix(route.Source, "glm-") && route.Provider != "Winky GLM" {
			t.Fatalf("expected GLM route to use Winky GLM provider, got %#v", route)
		}
		if strings.HasPrefix(route.Source, "claude-") && route.Provider != "Winky Claude" {
			t.Fatalf("expected Claude route to use Winky Claude provider, got %#v", route)
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
			name:            "codex gpt-5.6 passthrough",
			path:            "/api/model-routes/resolve?client=codex&model=gpt-5.6",
			wantProvider:    "ChatGPT Subscription",
			wantTarget:      "gpt-5.6",
			wantPassthrough: true,
		},
		{
			name:            "codex gpt-5.6 terra passthrough",
			path:            "/api/model-routes/resolve?client=codex&model=gpt-5.6-terra",
			wantProvider:    "ChatGPT Subscription",
			wantTarget:      "gpt-5.6-terra",
			wantPassthrough: true,
		},
		{
			name:            "codex gpt-5.6 luna passthrough",
			path:            "/api/model-routes/resolve?client=codex&model=gpt-5.6-luna",
			wantProvider:    "ChatGPT Subscription",
			wantTarget:      "gpt-5.6-luna",
			wantPassthrough: true,
		},
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
		{
			name:         "codex glm 5.2 to winky",
			path:         "/api/model-routes/resolve?client=codex&model=glm-5.2",
			wantProvider: "Winky GLM",
			wantTarget:   "glm-5.2",
		},
		{
			name:         "codex glm 5.1 to winky",
			path:         "/api/model-routes/resolve?client=codex&model=glm-5.1",
			wantProvider: "Winky GLM",
			wantTarget:   "glm-5.1",
		},
		{
			name:         "codex claude sonnet 5 to winky",
			path:         "/api/model-routes/resolve?client=codex&model=claude-sonnet-5",
			wantProvider: "Winky Claude",
			wantTarget:   "claude-sonnet-5",
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
	foundGLM := false
	foundClaude := false
	for _, model := range catalogBody.Models {
		if model.Slug == "gpt-5.6" {
			foundGPT = model.DisplayName != "" && model.ApplyPatchToolType == "freeform"
		}
		// Hybrid mode: DeepSeek is exposed under a built-in GPT slug so the
		// Codex picker renders it; the DisplayName carries the real model name.
		if model.Slug == "deepseek-v4-pro" {
			foundDeepSeek = model.DisplayName == "DeepSeek V4 Pro" && model.DefaultReasoningLevel == "high"
		}
		if model.Slug == "glm-5.2" {
			foundGLM = model.DisplayName == "GLM-5.2" && model.DefaultReasoningLevel == "none"
		}
		if model.Slug == "claude-sonnet-5" {
			foundClaude = model.DisplayName == "Claude Sonnet 5" && model.DefaultReasoningLevel == "high"
		}
	}
	if !foundGPT || !foundDeepSeek || !foundGLM || !foundClaude {
		t.Fatalf("expected catalog to include GPT subscription, DeepSeek, GLM, and Claude models, got %#v", catalogBody.Models)
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
	foundModelGLM := false
	foundModelClaude := false
	for _, model := range modelsBody.Data {
		foundModelGPT = foundModelGPT || model.ID == "gpt-5.6"
		foundModelDeepSeek = foundModelDeepSeek || model.ID == "deepseek-v4-flash"
		foundModelGLM = foundModelGLM || model.ID == "glm-5.2"
		foundModelClaude = foundModelClaude || model.ID == "claude-sonnet-5"
	}
	if modelsBody.Object != "list" || !foundModelGPT || !foundModelDeepSeek || !foundModelGLM || !foundModelClaude {
		t.Fatalf("expected OpenAI-compatible model list with GPT, DeepSeek, GLM, and Claude models, got %#v", modelsBody)
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

func TestCodexModelProbeRequiresLocalRequest(t *testing.T) {
	server := NewServer()

	remote := httptest.NewRequest(http.MethodPost, "/api/debug/codex-model-probe", strings.NewReader(`{"models":["gpt-5.6-luna"]}`))
	remote.RemoteAddr = "203.0.113.9:12345"
	remote.Header.Set("Content-Type", "application/json")
	remote.Header.Set("Authorization", "Bearer codex-token")
	remoteResponse := httptest.NewRecorder()
	server.ServeHTTP(remoteResponse, remote)
	if remoteResponse.Code != http.StatusForbidden {
		t.Fatalf("expected remote status 403, got %d", remoteResponse.Code)
	}
}

func TestCodexModelProbeEndpointProbesCandidates(t *testing.T) {
	var capturedModels []string
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/backend-api/codex/responses" {
			t.Fatalf("expected Codex backend responses path, got %s", r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer codex-token" {
			t.Fatalf("expected Codex bearer passthrough, got %q", r.Header.Get("Authorization"))
		}
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatalf("decode upstream request: %v", err)
		}
		capturedModels = append(capturedModels, body["model"].(string))
		if body["model"] == "gpt-5.6-luna-preview" {
			writeJSON(w, http.StatusOK, map[string]any{"status": "completed"})
			return
		}
		writeJSON(w, http.StatusNotFound, map[string]any{"detail": "Model not found"})
	}))
	defer upstream.Close()

	server := NewServerWithCodexRouter(codexrouter.NewService(codexrouter.Config{
		DefaultModel: "gpt-5.5",
		Routes: []codexrouter.Route{{
			ID:       "gpt-5.5",
			API:      "responses",
			BaseURL:  upstream.URL + "/backend-api/codex",
			Model:    "gpt-5.5",
			AuthMode: "codex_openai",
		}},
	}))

	request := httptest.NewRequest(http.MethodPost, "/api/debug/codex-model-probe", strings.NewReader(`{"models":["gpt-5.6-luna","gpt-5.6-luna-preview"]}`))
	request.RemoteAddr = "127.0.0.1:12345"
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer codex-token")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("expected probe status 200, got %d body=%s", response.Code, response.Body.String())
	}
	var body struct {
		Results []struct {
			Model      string `json:"model"`
			StatusCode int    `json:"statusCode"`
			Body       string `json:"body"`
		} `json:"results"`
	}
	decodeJSON(t, response, &body)
	if len(body.Results) != 2 || body.Results[0].StatusCode != http.StatusNotFound || body.Results[1].StatusCode != http.StatusOK {
		t.Fatalf("unexpected probe response: %#v", body)
	}
	if strings.Join(capturedModels, ",") != "gpt-5.6-luna,gpt-5.6-luna-preview" {
		t.Fatalf("unexpected probed models: %#v", capturedModels)
	}
}

func TestCodexRouterTokenUsageHeadersAttachToWorkflowCompletion(t *testing.T) {
	evaluationStore, err := catalog.NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.json"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{
			"id":      "chatcmpl_token",
			"choices": []map[string]any{{"message": map[string]any{"role": "assistant", "content": "done"}}},
			"usage": map[string]any{
				"prompt_tokens":         100,
				"completion_tokens":     20,
				"total_tokens":          120,
				"prompt_tokens_details": map[string]any{"cached_tokens": 60},
			},
		})
	}))
	defer upstream.Close()
	router := codexrouter.NewService(codexrouter.Config{
		DefaultModel: "deepseek-v4-pro",
		Routes: []codexrouter.Route{{
			ID:       "deepseek-v4-pro",
			API:      "chat_completions",
			BaseURL:  upstream.URL,
			Model:    "deepseek-v4-pro",
			AuthMode: "api_key",
			APIKey:   "provider-key",
		}},
	})
	server := newServerWithOptions(catalog.NewStore(), catalog.NewInfrastructureService(catalog.InfrastructureServiceOptions{}), router, defaultLocalDirectoryPicker{}, evaluationStore)

	startReq := httptest.NewRequest(http.MethodPost, "/api/workflow-runs/start", strings.NewReader(`{"projectId":"sample","workflowType":"bugfix","workflowTemplateId":"bugfix"}`))
	startReq.Header.Set("Content-Type", "application/json")
	startReq.Header.Set("Session-Id", "sess-token-1")
	start := httptest.NewRecorder()
	server.ServeHTTP(start, startReq)
	if start.Code != http.StatusCreated {
		t.Fatalf("start workflow status=%d body=%s", start.Code, start.Body.String())
	}
	var started struct {
		ID string `json:"id"`
	}
	decodeJSON(t, start, &started)

	request := httptest.NewRequest(http.MethodPost, "/proxy/codex/v1/responses", strings.NewReader(`{"model":"deepseek-v4-pro","input":"hello","stream":false}`))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Session-Id", "sess-token-1")
	request.Header.Set("X-Nexus-Workflow-Role", "worker")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("proxy response status=%d body=%s", response.Code, response.Body.String())
	}

	complete := requestJSON(t, server, http.MethodPost, "/api/workflow-runs/"+started.ID+"/complete", `{"submittedStatus":"success"}`)
	if complete.Code != http.StatusOK {
		t.Fatalf("complete workflow status=%d body=%s", complete.Code, complete.Body.String())
	}
	var finished struct {
		TaskRun catalog.TaskRun `json:"taskRun"`
	}
	decodeJSON(t, complete, &finished)
	usage, ok := finished.TaskRun.Metrics["tokenUsage"].(map[string]any)
	if !ok {
		t.Fatalf("expected token usage metric, got %#v", finished.TaskRun.Metrics)
	}
	if usage["workflowRunId"] != started.ID || int(usage["requestCount"].(float64)) != 1 || int(usage["totalTokens"].(float64)) != 120 {
		t.Fatalf("unexpected token usage metric: %#v", usage)
	}
	routeMetrics, ok := finished.TaskRun.Metrics["routeMetrics"].(map[string]any)
	if !ok {
		t.Fatalf("expected route metrics, got %#v", finished.TaskRun.Metrics)
	}
	if routeMetrics["workflowRunId"] != started.ID || int(routeMetrics["requestCount"].(float64)) != 1 || int(routeMetrics["successCount"].(float64)) != 1 {
		t.Fatalf("unexpected route metrics: %#v", routeMetrics)
	}
	roles, ok := routeMetrics["roles"].(map[string]any)
	if !ok || roles["worker"] == nil {
		t.Fatalf("expected worker role route metrics, got %#v", routeMetrics)
	}
}

func TestTaskRunSubmitEnrichesMetricsFromBoundSession(t *testing.T) {
	evaluationStore, err := catalog.NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.json"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	if err := evaluationStore.RecordTokenUsage(codexrouter.TokenUsageEvent{
		WorkflowRunID: "wf_run_session_submit",
		Role:          "worker",
		Model:         "gpt-test",
		InputTokens:   100,
		OutputTokens:  20,
		TotalTokens:   120,
	}); err != nil {
		t.Fatalf("record token usage: %v", err)
	}
	if err := evaluationStore.RecordWorkflowRouteEvent(codexrouter.WorkflowRouteEvent{
		WorkflowRunID:  "wf_run_session_submit",
		Role:           "worker",
		Model:          "gpt-test",
		StatusCode:     200,
		DurationMS:     1500,
		RequestBytes:   2048,
		ToolCount:      3,
		InputItemCount: 2,
		ToolCallCount:  1,
	}); err != nil {
		t.Fatalf("record route event: %v", err)
	}
	server := newServerWithOptions(catalog.NewStore(), catalog.NewInfrastructureService(catalog.InfrastructureServiceOptions{}), nil, defaultLocalDirectoryPicker{}, evaluationStore).(*Server)
	if err := server.sessionStore.Bind(catalog.ActiveWorkflowSession{
		SessionID:     "sess-submit-1",
		WorkflowRunID: "wf_run_session_submit",
		ProjectID:     "sample",
		WorkflowType:  "bugfix",
		Status:        "active",
	}); err != nil {
		t.Fatalf("bind session: %v", err)
	}

	body := `{
		"projectId":"sample",
		"workflowType":"bugfix",
		"submittedStatus":"success",
		"metrics":{
			"toolCallCount":7,
			"tokenUsage":{"workflowRunId":"client_fake","requestCount":99},
			"routeMetrics":{"workflowRunId":"client_fake","requestCount":99}
		}
	}`
	request := httptest.NewRequest(http.MethodPost, "/api/task-runs", strings.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Session-Id", "sess-submit-1")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	if response.Code != http.StatusCreated {
		t.Fatalf("submit task run status=%d body=%s", response.Code, response.Body.String())
	}
	var run catalog.TaskRun
	decodeJSON(t, response, &run)
	if numberValueForHTTPTest(run.Metrics["toolCallCount"]) != 7 {
		t.Fatalf("expected original tool metric to remain, got %#v", run.Metrics)
	}
	usage, ok := run.Metrics["tokenUsage"].(map[string]any)
	if !ok || usage["workflowRunId"] != "wf_run_session_submit" || int(numberValueForHTTPTest(usage["requestCount"])) != 1 || int(numberValueForHTTPTest(usage["totalTokens"])) != 120 {
		t.Fatalf("expected server token usage, got %#v", run.Metrics["tokenUsage"])
	}
	routeMetrics, ok := run.Metrics["routeMetrics"].(map[string]any)
	if !ok || routeMetrics["workflowRunId"] != "wf_run_session_submit" || int(numberValueForHTTPTest(routeMetrics["requestCount"])) != 1 || int(numberValueForHTTPTest(routeMetrics["toolCallCount"])) != 1 {
		t.Fatalf("expected server route metrics, got %#v", run.Metrics["routeMetrics"])
	}
	if _, ok, err := server.sessionStore.Lookup("sess-submit-1"); err != nil || ok {
		t.Fatalf("expected session to be completed, ok=%v err=%v", ok, err)
	}
}

func TestTaskRunSubmitEnrichesMetricsFromSessionTimeWindow(t *testing.T) {
	evaluationStore, err := catalog.NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.json"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	if err := evaluationStore.RecordTokenUsage(codexrouter.TokenUsageEvent{
		SessionID:    "sess-window-1",
		Role:         "worker",
		Model:        "gpt-window",
		InputTokens:  80,
		OutputTokens: 16,
		TotalTokens:  96,
		CreatedAt:    "2026-07-02T08:00:03Z",
	}); err != nil {
		t.Fatalf("record token usage: %v", err)
	}
	if err := evaluationStore.RecordTokenUsage(codexrouter.TokenUsageEvent{
		SessionID:    "sess-window-1",
		Role:         "worker",
		Model:        "gpt-window",
		InputTokens:  999,
		OutputTokens: 1,
		TotalTokens:  1000,
		CreatedAt:    "2026-07-02T09:00:00Z",
	}); err != nil {
		t.Fatalf("record out-of-window token usage: %v", err)
	}
	if err := evaluationStore.RecordWorkflowRouteEvent(codexrouter.WorkflowRouteEvent{
		SessionID:      "sess-window-1",
		Role:           "worker",
		Model:          "gpt-window",
		StatusCode:     200,
		DurationMS:     2500,
		RequestBytes:   1024,
		ToolCount:      4,
		InputItemCount: 3,
		ToolCallCount:  2,
		CreatedAt:      "2026-07-02T08:00:04Z",
	}); err != nil {
		t.Fatalf("record route event: %v", err)
	}
	server := newServerWithOptions(catalog.NewStore(), catalog.NewInfrastructureService(catalog.InfrastructureServiceOptions{}), nil, defaultLocalDirectoryPicker{}, evaluationStore).(*Server)

	body := `{
		"projectId":"sample",
		"workflowType":"bugfix",
		"submittedStatus":"success",
		"startedAt":"2026-07-02T08:00:00Z",
		"endedAt":"2026-07-02T08:00:10Z",
		"sessionId":"sess-window-1",
		"metrics":{
			"toolCallCount":7,
			"tokenUsage":{"workflowRunId":"client_fake","requestCount":99},
			"routeMetrics":{"workflowRunId":"client_fake","requestCount":99}
		}
	}`
	request := httptest.NewRequest(http.MethodPost, "/api/task-runs", strings.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	if response.Code != http.StatusCreated {
		t.Fatalf("submit task run status=%d body=%s", response.Code, response.Body.String())
	}
	var run catalog.TaskRun
	decodeJSON(t, response, &run)
	usage, ok := run.Metrics["tokenUsage"].(map[string]any)
	if !ok || usage["sessionId"] != "sess-window-1" || int(numberValueForHTTPTest(usage["requestCount"])) != 1 || int(numberValueForHTTPTest(usage["totalTokens"])) != 96 {
		t.Fatalf("expected session-window token usage, got %#v", run.Metrics["tokenUsage"])
	}
	routeMetrics, ok := run.Metrics["routeMetrics"].(map[string]any)
	if !ok || routeMetrics["sessionId"] != "sess-window-1" || int(numberValueForHTTPTest(routeMetrics["requestCount"])) != 1 || int(numberValueForHTTPTest(routeMetrics["toolCallCount"])) != 2 {
		t.Fatalf("expected session-window route metrics, got %#v", run.Metrics["routeMetrics"])
	}
}

func numberValueForHTTPTest(value any) float64 {
	switch typed := value.(type) {
	case int:
		return float64(typed)
	case int64:
		return float64(typed)
	case float64:
		return typed
	case json.Number:
		result, _ := typed.Float64()
		return result
	default:
		return 0
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
