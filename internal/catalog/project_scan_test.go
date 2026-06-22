package catalog

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestHydrateTemplateItemFromFilesPrefersCodexTomlModel(t *testing.T) {
	root := t.TempDir()
	claudeRel := "templates/agents/claude/go-worker.md"
	codexRel := "templates/agents/codex/go-worker.toml"
	writeTestFile(t, root, claudeRel, "worker markdown")
	writeTestFile(t, root, codexRel, "name = \"worker\"\nmodel = \"deepseek-v4-pro\"\nmodel_reasoning_effort = \"high\"\n")
	claudePath := filepath.Join(root, filepath.FromSlash(claudeRel))
	codexPath := filepath.Join(root, filepath.FromSlash(codexRel))

	item := TemplateItem{
		ID:          "worker",
		Kind:        "agent",
		ModelTier:   "gpt-5.4",
		SourcePaths: []string{claudePath, codexPath},
	}

	hydrated := hydrateTemplateItemFromFiles(item)
	if hydrated.ModelTier != "deepseek-v4-pro" {
		t.Fatalf("expected model tier from codex toml, got %q", hydrated.ModelTier)
	}
	if !strings.Contains(hydrated.CodexProjection, "model = \"deepseek-v4-pro\"") {
		t.Fatalf("expected codex projection to load template file, got %q", hydrated.CodexProjection)
	}
}

func TestScanProjectConfigSetReadsProjectFiles(t *testing.T) {
	root := t.TempDir()
	templateRoot := t.TempDir()

	writeTestFile(t, root, ".claude/agents/worker.md", "worker markdown")
	writeTestFile(t, root, ".codex/agents/worker.toml", "worker toml")
	writeTestFile(t, root, ".claude/rules/go-00-routing.md", "routing markdown")
	writeTestFile(t, root, ".claude/skills/testing.md", "testing markdown")
	writeTestFile(t, root, ".claude/workflows/go-feature-development.md", "workflow markdown")
	writeTestFile(t, root, ".claude/workflows/go-feature-development.graph.json", `{"id":"go-feature-development","name":"feature graph","nodes":[],"edges":[]}`)
	writeTestFile(t, templateRoot, "agents/claude/go-worker.md", "worker markdown")
	writeTestFile(t, templateRoot, "agents/codex/go-worker.toml", "worker toml")
	writeTestFile(t, templateRoot, "rules/go-00-routing.md", "routing markdown")
	writeTestFile(t, templateRoot, "skills/go-testing.md", "testing markdown")
	writeTestFile(t, templateRoot, "workflows/go-feature-development.md", "workflow markdown")
	writeTestFile(t, templateRoot, "workflows/go-feature-development.graph.json", `{"id":"go-feature-development","name":"feature graph","nodes":[],"edges":[]}`)

	library := TemplateLibrary{
		Agents: []TemplateItem{{
			ID:          "worker",
			Kind:        "agent",
			Name:        "worker",
			Version:     1,
			SourcePaths: []string{filepath.Join(templateRoot, "agents/claude/go-worker.md"), filepath.Join(templateRoot, "agents/codex/go-worker.toml")},
		}},
		Rules: []TemplateItem{{
			ID:      "00-routing",
			Kind:    "rule",
			Name:    "00-routing",
			Version: 1,
			Entry:   filepath.Join(templateRoot, "rules/go-00-routing.md"),
		}},
		Skills: []TemplateItem{{
			ID:      "testing",
			Kind:    "skill",
			Name:    "testing",
			Version: 1,
			Entry:   filepath.Join(templateRoot, "skills/go-testing.md"),
		}},
		Workflows: []TemplateItem{{
			ID:          "feature-development",
			Kind:        "workflow",
			Name:        "feature-development",
			Version:     1,
			Entry:       filepath.Join(templateRoot, "workflows/go-feature-development.md"),
			Files:       []string{filepath.Join(templateRoot, "workflows/go-feature-development.md"), filepath.Join(templateRoot, "workflows/go-feature-development.graph.json")},
			SourcePaths: []string{filepath.Join(templateRoot, "workflows/go-feature-development.md"), filepath.Join(templateRoot, "rules/go-00-routing.md")},
		}},
	}

	copies, err := ScanProjectConfigSet(root, library)
	if err != nil {
		t.Fatalf("scan project config set: %v", err)
	}

	agent := assertProjectCopy(t, copies, "agent", "worker", "synced", ".claude/agents/worker.md")
	if agent.Content != "worker markdown" || len(agent.SourcePaths) != 2 {
		t.Fatalf("expected agent project content and source paths, got %#v", agent)
	}
	rule := assertProjectCopy(t, copies, "rule", "00-routing", "synced", ".claude/rules/go-00-routing.md")
	if rule.Content != "routing markdown" || len(rule.SourcePaths) != 1 {
		t.Fatalf("expected rule project content and source paths, got %#v", rule)
	}
	skill := assertProjectCopy(t, copies, "skill", "testing", "synced", ".claude/skills/testing.md")
	if skill.Content != "testing markdown" || len(skill.SourcePaths) != 1 {
		t.Fatalf("expected skill project content and source paths, got %#v", skill)
	}
	workflow := assertProjectCopy(t, copies, "workflow", "feature-development", "synced", ".claude/workflows/go-feature-development.md")
	if workflow.Content != "workflow markdown" || len(workflow.SourcePaths) != 2 {
		t.Fatalf("expected workflow project content and source paths, got %#v", workflow)
	}
}

func TestScanProjectConfigSetReadsWorkflowFilesAndNexusGraphs(t *testing.T) {
	root := t.TempDir()
	templateRoot := t.TempDir()

	writeTestFile(t, root, ".claude/workflows/go-feature-development.md", "workflow markdown")
	writeTestFile(t, root, ".claude/workflows/go-feature-development.graph.json", `{"id":"go-feature-development","name":"feature graph","nodes":[{"id":"start","type":"input","category":"event","label":"start","agent":"-","detail":"begin","x":10,"y":20}],"edges":[]}`)
	writeTestFile(t, templateRoot, "workflows/go-feature-development.md", "workflow markdown")
	writeTestFile(t, templateRoot, "workflows/go-feature-development.graph.json", `{"id":"go-feature-development","name":"feature graph","nodes":[{"id":"start","type":"input","category":"event","label":"start","agent":"-","detail":"begin","x":10,"y":20}],"edges":[]}`)

	library := TemplateLibrary{
		Workflows: []TemplateItem{{
			ID:      "feature-development",
			Kind:    "workflow",
			Name:    "feature-development",
			Version: 1,
			Entry:   filepath.Join(templateRoot, "workflows/go-feature-development.md"),
			Files:   []string{filepath.Join(templateRoot, "workflows/go-feature-development.md"), filepath.Join(templateRoot, "workflows/go-feature-development.graph.json")},
		}},
	}

	copies, err := ScanProjectConfigSet(root, library)
	if err != nil {
		t.Fatalf("scan project config set: %v", err)
	}

	copy := assertProjectCopy(t, copies, "workflow", "feature-development", "synced", ".claude/workflows/go-feature-development.md")
	if copy.ID != "proj_workflow_project_go_feature_development" {
		t.Fatalf("expected workflow copy id to preserve go filename identity, got %q", copy.ID)
	}
	if copy.Origin == nil || copy.Origin.TemplateID != "feature-development" {
		t.Fatalf("expected workflow copy origin to point at logical template, got %#v", copy.Origin)
	}
}

func TestScanProjectConfigSetDoesNotExpandWorkflowsFromRoutingRule(t *testing.T) {
	root := t.TempDir()
	templateRoot := t.TempDir()

	writeTestFile(t, root, ".claude/rules/go-00-routing.md", "routing markdown")
	writeTestFile(t, templateRoot, "workflows/go-feature-development.md", "workflow markdown")
	writeTestFile(t, templateRoot, "workflows/go-feature-development.graph.json", `{"id":"go-feature-development","name":"feature graph","nodes":[],"edges":[]}`)

	copies, err := ScanProjectConfigSet(root, TemplateLibrary{
		Workflows: []TemplateItem{{
			ID:      "feature-development",
			Kind:    "workflow",
			Name:    "feature-development",
			Version: 1,
			Entry:   filepath.Join(templateRoot, "workflows/go-feature-development.md"),
			Files:   []string{filepath.Join(templateRoot, "workflows/go-feature-development.md"), filepath.Join(templateRoot, "workflows/go-feature-development.graph.json")},
		}},
	})
	if err != nil {
		t.Fatalf("scan project config set: %v", err)
	}
	for _, copy := range copies {
		if copy.Kind == "workflow" {
			t.Fatalf("expected routing rule not to create virtual workflow copies, got %#v", copies)
		}
	}
}

func TestScanProjectConfigSetPrefersPrefixedTemplateCopyOverRuntimeEntry(t *testing.T) {
	root := t.TempDir()
	templateRoot := t.TempDir()

	writeTestFile(t, root, ".claude/agents/worker.md", "legacy runtime worker")
	writeTestFile(t, root, ".claude/agents/go-worker.md", "worker markdown")
	writeTestFile(t, root, ".codex/agents/worker.toml", "legacy runtime worker toml")
	writeTestFile(t, root, ".codex/agents/go-worker.toml", "worker toml")
	writeTestFile(t, templateRoot, "agents/claude/go-worker.md", "worker markdown")
	writeTestFile(t, templateRoot, "agents/codex/go-worker.toml", "worker toml")

	library := TemplateLibrary{
		Agents: []TemplateItem{{
			ID:          "worker",
			Kind:        "agent",
			Name:        "worker",
			Version:     1,
			SourcePaths: []string{filepath.Join(templateRoot, "agents/claude/go-worker.md"), filepath.Join(templateRoot, "agents/codex/go-worker.toml")},
		}},
	}

	copies, err := ScanProjectConfigSet(root, library)
	if err != nil {
		t.Fatalf("scan project config set: %v", err)
	}
	if len(copies) != 1 {
		t.Fatalf("expected one sync copy for worker, got %#v", copies)
	}
	copy := assertProjectCopy(t, copies, "agent", "worker", "synced", ".claude/agents/go-worker.md")
	if copy.ID != "proj_agent_project_go_worker" {
		t.Fatalf("expected prefixed template filename identity, got %q", copy.ID)
	}
}

func TestScanProjectConfigSetDetectsProjectModifiedContent(t *testing.T) {
	root := t.TempDir()
	templateRoot := t.TempDir()

	writeTestFile(t, root, ".claude/rules/go-00-routing.md", "project routing")
	writeTestFile(t, templateRoot, "rules/go-00-routing.md", "template routing")

	library := TemplateLibrary{
		Rules: []TemplateItem{{
			ID:      "00-routing",
			Kind:    "rule",
			Name:    "00-routing",
			Version: 1,
			Entry:   filepath.Join(templateRoot, "rules/go-00-routing.md"),
		}},
	}

	copies, err := ScanProjectConfigSet(root, library)
	if err != nil {
		t.Fatalf("scan project config set: %v", err)
	}

	copy := assertProjectCopy(t, copies, "rule", "00-routing", "project_modified", ".claude/rules/go-00-routing.md")
	if copy.Origin == nil || copy.Origin.BaseHash == "" {
		t.Fatalf("expected origin hash for modified copy, got %#v", copy)
	}
	if !strings.Contains(copy.Diff, "Project file differs from template") {
		t.Fatalf("expected diff to explain project modification, got %q", copy.Diff)
	}
}

func TestStoreProjectWorkflowGraphPersistsToNexusFile(t *testing.T) {
	root := t.TempDir()
	writeTestFile(t, root, ".claude/workflows/go-feature-development.md", "workflow markdown")
	writeTestFile(t, root, ".claude/workflows/go-feature-development.graph.json", `{"id":"go-feature-development","name":"feature graph","nodes":[{"id":"start","type":"input","category":"event","label":"start","agent":"-","detail":"begin","x":10,"y":20}],"edges":[]}`)

	store := NewStoreFromData(
		BootstrapData{
			Projects: []Project{{ID: "sample", Name: "sample", Path: root}},
			ProjectConfigSets: map[string][]ProjectCopy{
				"sample": {{
					ID:           "proj_workflow_sample_go_feature_development",
					Kind:         "workflow",
					Name:         "feature-development",
					LocalVersion: 1,
					SyncMode:     "manual",
					Status:       "synced",
					Path:         ".claude/workflows/go-feature-development.md",
					Origin:       &Origin{TemplateID: "feature-development", BaseVersion: 1, BaseHash: "sha256:test"},
				}},
			},
		},
		nil,
		map[string]WorkflowGraph{
			"feature-development": {
				ID:    "feature-development",
				Name:  "template graph",
				Nodes: []WorkflowNode{{ID: "template", Type: "input", Category: "event", Label: "template", Agent: "-", Detail: "template", X: 1, Y: 2}},
			},
		},
	)

	graph, ok := store.ProjectWorkflowByCopyID("sample", "proj_workflow_sample_go_feature_development")
	if !ok {
		t.Fatal("expected project workflow graph")
	}
	if graph.ID != "go-feature-development" || graph.Nodes[0].X != 10 {
		t.Fatalf("expected graph to load from workflow graph json, got %#v", graph)
	}

	graph.Nodes[0].X = 320
	graph.Nodes = append(graph.Nodes, WorkflowNode{ID: "review", Type: "agent", Category: "action", Label: "review", Agent: "worker", Detail: "review", X: 540, Y: 20})
	graph.Edges = append(graph.Edges, WorkflowEdge{From: "start", To: "review", Label: "next"})
	updated, ok, err := store.UpdateProjectWorkflowGraph("sample", "proj_workflow_sample_go_feature_development", graph)
	if err != nil {
		t.Fatalf("update project workflow graph: %v", err)
	}
	if !ok {
		t.Fatal("expected project workflow graph update to find copy")
	}
	if updated.Nodes[0].X != 320 || len(updated.Edges) != 1 {
		t.Fatalf("unexpected updated graph: %#v", updated)
	}

	reloaded, ok := store.ProjectWorkflowByCopyID("sample", "proj_workflow_sample_go_feature_development")
	if !ok || reloaded.Nodes[0].X != 320 || len(reloaded.Edges) != 1 {
		t.Fatalf("expected saved graph to reload from workflow graph json, got ok=%v graph=%#v", ok, reloaded)
	}
}

func TestStoreCreateAndDeleteProjectWorkflowWritesProjectFiles(t *testing.T) {
	root := t.TempDir()
	store := NewStoreFromData(
		BootstrapData{
			Projects:          []Project{{ID: "sample", Name: "sample", Path: root}},
			ProjectConfigSets: map[string][]ProjectCopy{"sample": {}},
		},
		nil,
		nil,
	)

	copy, graph, ok, err := store.CreateProjectWorkflow("sample", WorkflowInput{Name: "Release Guard", Summary: "Project release checklist.", Trigger: "manual"})
	if err != nil {
		t.Fatalf("create project workflow: %v", err)
	}
	if !ok {
		t.Fatal("expected project workflow create to find project")
	}
	if copy.Kind != "workflow" || copy.Origin != nil || copy.Status != "detached" {
		t.Fatalf("unexpected created project workflow copy: %#v", copy)
	}
	if graph.ID != "release-guard" || len(graph.Nodes) == 0 {
		t.Fatalf("unexpected created project workflow graph: %#v", graph)
	}
	if _, err := os.Stat(filepath.Join(root, ".claude", "workflows", "release-guard.md")); err != nil {
		t.Fatalf("expected project workflow markdown file: %v", err)
	}
	if _, err := os.Stat(filepath.Join(root, ".claude", "workflows", "release-guard.graph.json")); err != nil {
		t.Fatalf("expected project workflow graph file: %v", err)
	}

	ok, err = store.DeleteProjectWorkflow("sample", copy.ID)
	if err != nil {
		t.Fatalf("delete project workflow: %v", err)
	}
	if !ok {
		t.Fatal("expected project workflow delete to find copy")
	}
	if _, err := os.Stat(filepath.Join(root, ".claude", "workflows", "release-guard.md")); !os.IsNotExist(err) {
		t.Fatalf("expected project workflow markdown to be removed, err=%v", err)
	}
	if _, err := os.Stat(filepath.Join(root, ".claude", "workflows", "release-guard.graph.json")); !os.IsNotExist(err) {
		t.Fatalf("expected project workflow graph to be removed, err=%v", err)
	}
}

func TestImportProjectWritesUserHomeNexusIndex(t *testing.T) {
	root := t.TempDir()
	home := t.TempDir()
	t.Setenv("USERPROFILE", home)
	t.Setenv("HOME", home)
	writeTestFile(t, root, ".claude/rules/01-communication.md", "communication")

	store := NewStoreFromData(BootstrapData{
		TemplateLibrary: TemplateLibrary{
			Rules: []TemplateItem{{
				ID:      "01-communication",
				Kind:    "rule",
				Name:    "01-communication",
				Version: 1,
				Content: "communication",
			}},
		},
		ProjectConfigSets: map[string][]ProjectCopy{},
	}, nil, nil)

	project, err := store.ImportProject(ProjectInput{Name: "btd-game-server", Path: root})
	if err != nil {
		t.Fatalf("import project: %v", err)
	}

	nexusPath := filepath.Join(home, ".nexus")
	data, err := os.ReadFile(nexusPath)
	if err != nil {
		t.Fatalf("expected user home .nexus file: %v", err)
	}
	content := string(data)
	for _, token := range []string{`"version": 1`, `"projectId": "btd-game-server"`, `"projectName": "btd-game-server"`, `"path": "`} {
		if !strings.Contains(content, token) {
			t.Fatalf("expected .nexus metadata token %s, got %s", token, content)
		}
	}
	if project.LocalConfigPath != "" || project.LocalConfigIgnored || project.LocalPath == "" || project.RepoKey == "" {
		t.Fatalf("expected project local import fields, got %#v", project)
	}

	if _, err := store.ImportProject(ProjectInput{Name: "btd-game-server", Path: root}); err != nil {
		t.Fatalf("repeat import project: %v", err)
	}

	ok, err := store.DeleteProject(project.ID)
	if err != nil {
		t.Fatalf("delete project: %v", err)
	}
	if !ok {
		t.Fatal("expected delete project to remove imported project")
	}
	if _, err := os.Stat(nexusPath); !os.IsNotExist(err) {
		t.Fatalf("expected delete project to remove user home .nexus file when last project is removed, err=%v", err)
	}
}

func TestImportProjectMigratesLegacyNexusWorkflowDirectory(t *testing.T) {
	root := t.TempDir()
	writeTestFile(t, root, ".claude/workflows/go-feature-development.md", "workflow markdown")
	writeTestFile(t, root, ".nexus/workflows/go-feature-development.json", `{"id":"go-feature-development","name":"feature graph","nodes":[],"edges":[]}`)

	store := NewStoreFromData(BootstrapData{ProjectConfigSets: map[string][]ProjectCopy{}}, nil, nil)

	if _, err := store.ImportProject(ProjectInput{Name: "btd-game-server", Path: root}); err != nil {
		t.Fatalf("import project: %v", err)
	}

	if _, err := os.Stat(filepath.Join(root, ".nexus")); !os.IsNotExist(err) {
		t.Fatalf("expected legacy .nexus path to be removed after migration, err=%v", err)
	}
	if _, err := os.Stat(filepath.Join(root, ".claude", "workflows", "go-feature-development.graph.json")); err != nil {
		t.Fatalf("expected migrated workflow graph: %v", err)
	}
	if _, err := os.Stat(filepath.Join(root, ".nexus", "workflows", "go-feature-development.json")); !os.IsNotExist(err) {
		t.Fatalf("expected legacy workflow graph to be removed, err=%v", err)
	}
}

func TestStoreRescanProjectRefreshesUserHomeNexusIndex(t *testing.T) {
	root := t.TempDir()
	home := t.TempDir()
	t.Setenv("USERPROFILE", home)
	t.Setenv("HOME", home)
	writeTestFile(t, root, ".claude/rules/01-communication.md", "communication")

	store := NewStoreFromData(BootstrapData{
		TemplateLibrary: TemplateLibrary{
			Rules: []TemplateItem{{
				ID:      "01-communication",
				Kind:    "rule",
				Name:    "01-communication",
				Version: 1,
				Content: "communication",
			}},
			Skills: []TemplateItem{{
				ID:      "testing",
				Kind:    "skill",
				Name:    "testing",
				Version: 1,
				Entry:   filepath.Join(root, "template-testing.md"),
			}},
		},
		ProjectConfigSets: map[string][]ProjectCopy{},
	}, nil, nil)

	project, err := store.ImportProject(ProjectInput{Name: "btd-game-server", Path: root})
	if err != nil {
		t.Fatalf("import project: %v", err)
	}
	if project.ConfigSummary.Skills != 0 {
		t.Fatalf("expected no skills before rescan, got %#v", project.ConfigSummary)
	}

	writeTestFile(t, root, ".claude/skills/testing.md", "project testing skill")
	rescanned, copies, ok, err := store.RescanProject(project.ID)
	if err != nil {
		t.Fatalf("rescan project: %v", err)
	}
	if !ok {
		t.Fatal("expected project to rescan")
	}
	if rescanned.ConfigSummary.Skills != 1 {
		t.Fatalf("expected rescan summary to include skill, got %#v", rescanned.ConfigSummary)
	}
	assertProjectCopy(t, copies, "skill", "testing", "project_modified", ".claude/skills/testing.md")
	metadata, ok := readUserProjectIndex()
	if !ok || len(metadata.Projects) != 1 || metadata.Projects[0].LastScannedAt == "" {
		t.Fatalf("expected rescan to refresh user .nexus metadata, got %#v ok=%v", metadata, ok)
	}
}

func TestSyncProjectCopyWritesTemplateContentToProject(t *testing.T) {
	root := t.TempDir()
	templateRoot := t.TempDir()
	home := t.TempDir()
	t.Setenv("USERPROFILE", home)
	t.Setenv("HOME", home)

	templateSkill := filepath.Join(templateRoot, "skills", "testing.md")
	writeTestFile(t, templateRoot, "skills/testing.md", "template testing skill\n")
	writeTestFile(t, root, ".claude/skills/testing.md", "project testing skill\n")

	store := NewStoreFromData(BootstrapData{
		TemplateLibrary: TemplateLibrary{
			Skills: []TemplateItem{{
				ID:      "testing",
				Kind:    "skill",
				Name:    "testing",
				Version: 1,
				Entry:   templateSkill,
			}},
		},
		ProjectConfigSets: map[string][]ProjectCopy{},
	}, nil, nil)

	project, err := store.ImportProject(ProjectInput{Name: "btd-game-server", Path: root})
	if err != nil {
		t.Fatalf("import project: %v", err)
	}
	copy := assertProjectCopy(t, store.data.ProjectConfigSets[project.ID], "skill", "testing", "project_modified", ".claude/skills/testing.md")

	synced, ok, err := store.SyncProjectCopy(project.ID, copy.ID)
	if err != nil {
		t.Fatalf("sync project copy: %v", err)
	}
	if !ok {
		t.Fatal("expected sync project copy to find copy")
	}
	if synced.Status != "synced" {
		t.Fatalf("expected synced copy, got %#v", synced)
	}
	data, err := os.ReadFile(filepath.Join(root, ".claude", "skills", "testing.md"))
	if err != nil {
		t.Fatalf("read synced skill: %v", err)
	}
	if string(data) != "template testing skill\n" {
		t.Fatalf("expected project skill to be overwritten from template, got %q", string(data))
	}

	_, copies, ok, err := store.RescanProject(project.ID)
	if err != nil {
		t.Fatalf("rescan project: %v", err)
	}
	if !ok {
		t.Fatal("expected rescan project to find project")
	}
	assertProjectCopy(t, copies, "skill", "testing", "synced", ".claude/skills/testing.md")
}

func TestSyncProjectWorkflowCopyWritesMarkdownAndGraph(t *testing.T) {
	root := t.TempDir()
	templateRoot := t.TempDir()
	home := t.TempDir()
	t.Setenv("USERPROFILE", home)
	t.Setenv("HOME", home)

	templateMarkdown := filepath.Join(templateRoot, "workflows", "feature.md")
	templateGraph := filepath.Join(templateRoot, "workflows", "feature.graph.json")
	writeTestFile(t, templateRoot, "workflows/feature.md", "# template workflow\n")
	writeTestFile(t, templateRoot, "workflows/feature.graph.json", `{"id":"feature","name":"template","nodes":[],"edges":[]}`+"\n")
	writeTestFile(t, root, ".claude/workflows/feature.md", "# project workflow\n")
	writeTestFile(t, root, ".claude/workflows/feature.graph.json", `{"id":"feature","name":"project","nodes":[],"edges":[]}`+"\n")

	store := NewStoreFromData(BootstrapData{
		TemplateLibrary: TemplateLibrary{
			Workflows: []TemplateItem{{
				ID:      "feature",
				Kind:    "workflow",
				Name:    "feature",
				Version: 1,
				Entry:   templateMarkdown,
				Files:   []string{templateMarkdown, templateGraph},
			}},
		},
		ProjectConfigSets: map[string][]ProjectCopy{},
	}, nil, nil)

	project, err := store.ImportProject(ProjectInput{Name: "btd-game-server", Path: root})
	if err != nil {
		t.Fatalf("import project: %v", err)
	}
	copy := assertProjectCopy(t, store.data.ProjectConfigSets[project.ID], "workflow", "feature", "project_modified", ".claude/workflows/feature.md")

	if _, ok, err := store.SyncProjectCopy(project.ID, copy.ID); err != nil {
		t.Fatalf("sync project workflow: %v", err)
	} else if !ok {
		t.Fatal("expected sync project workflow to find copy")
	}

	markdown, err := os.ReadFile(filepath.Join(root, ".claude", "workflows", "feature.md"))
	if err != nil {
		t.Fatalf("read workflow markdown: %v", err)
	}
	if string(markdown) != "# template workflow\n" {
		t.Fatalf("expected workflow markdown from template, got %q", string(markdown))
	}
	graph, err := os.ReadFile(filepath.Join(root, ".claude", "workflows", "feature.graph.json"))
	if err != nil {
		t.Fatalf("read workflow graph: %v", err)
	}
	if string(graph) != `{"id":"feature","name":"template","nodes":[],"edges":[]}`+"\n" {
		t.Fatalf("expected workflow graph from template, got %q", string(graph))
	}
}

func TestAddProjectCopyFromWorkflowTemplateWritesMarkdownAndGraph(t *testing.T) {
	root := t.TempDir()
	templateRoot := t.TempDir()
	home := t.TempDir()
	t.Setenv("USERPROFILE", home)
	t.Setenv("HOME", home)

	templateMarkdown := filepath.Join(templateRoot, "workflows", "go-bugfix.md")
	templateGraph := filepath.Join(templateRoot, "workflows", "go-bugfix.graph.json")
	writeTestFile(t, templateRoot, "workflows/go-bugfix.md", "# go-bugfix\n\nTrigger: `--bug`\n")
	writeTestFile(t, templateRoot, "workflows/go-bugfix.graph.json", `{"id":"bugfix","name":"bugfix","nodes":[],"edges":[]}`+"\n")

	store := NewStoreFromData(BootstrapData{
		TemplateLibrary: TemplateLibrary{
			Workflows: []TemplateItem{{
				ID:      "bugfix",
				Kind:    "workflow",
				Name:    "bugfix",
				Version: 1,
				Entry:   templateMarkdown,
				Files:   []string{templateMarkdown, templateGraph},
			}},
		},
		ProjectConfigSets: map[string][]ProjectCopy{},
	}, nil, nil)

	project, err := store.ImportProject(ProjectInput{Name: "btd-game-server", Path: root})
	if err != nil {
		t.Fatalf("import project: %v", err)
	}
	copy, ok, err := store.AddProjectCopyFromTemplate(project.ID, "workflow", "bugfix")
	if err != nil {
		t.Fatalf("add project copy from template: %v", err)
	}
	if !ok {
		t.Fatal("expected add project copy from template to find project and template")
	}
	if copy.Kind != "workflow" || copy.Status != "synced" || copy.Origin == nil || copy.Origin.TemplateID != "bugfix" {
		t.Fatalf("unexpected workflow copy: %#v", copy)
	}
	assertProjectCopy(t, store.data.ProjectConfigSets[project.ID], "workflow", "bugfix", "synced", ".claude/workflows/go-bugfix.md")
	if data, err := os.ReadFile(filepath.Join(root, ".claude", "workflows", "go-bugfix.md")); err != nil || string(data) != "# go-bugfix\n\nTrigger: `--bug`\n" {
		t.Fatalf("expected workflow markdown from template, data=%q err=%v", string(data), err)
	}
	if data, err := os.ReadFile(filepath.Join(root, ".claude", "workflows", "go-bugfix.graph.json")); err != nil || string(data) != `{"id":"bugfix","name":"bugfix","nodes":[],"edges":[]}`+"\n" {
		t.Fatalf("expected workflow graph from template, data=%q err=%v", string(data), err)
	}
}

func TestAddProjectCopyFromWorkflowTemplateWritesCodexWorkflowSkillFolder(t *testing.T) {
	root := t.TempDir()
	templateRoot := t.TempDir()
	home := t.TempDir()
	t.Setenv("USERPROFILE", home)
	t.Setenv("HOME", home)

	templateSkill := filepath.Join(templateRoot, "skills", "codex", "wf-go-feat", "SKILL.md")
	templateMetadata := filepath.Join(templateRoot, "skills", "codex", "wf-go-feat", "agents", "openai.yaml")
	templateWorkflow := filepath.Join(templateRoot, "workflows", "go-feature-development.md")
	templateGraph := filepath.Join(templateRoot, "workflows", "go-feature-development.graph.json")
	writeTestFile(t, templateRoot, "skills/codex/wf-go-feat/SKILL.md", "---\nname: wf-go-feat\ndescription: Go feature workflow.\n---\n\n# wf-go-feat\n")
	writeTestFile(t, templateRoot, "skills/codex/wf-go-feat/agents/openai.yaml", "interface:\n  display_name: \"WF Go Feature\"\n")
	writeTestFile(t, templateRoot, "workflows/go-feature-development.md", "# go-feature-development\n")
	writeTestFile(t, templateRoot, "workflows/go-feature-development.graph.json", `{"id":"feature-development","name":"feature","nodes":[],"edges":[]}`+"\n")

	store := NewStoreFromData(BootstrapData{
		TemplateLibrary: TemplateLibrary{
			Workflows: []TemplateItem{{
				ID:      "feature-development",
				Kind:    "workflow",
				Name:    "feature-development",
				Version: 1,
				Entry:   templateWorkflow,
				Files:   []string{templateWorkflow, templateGraph},
			}},
			Skills: []TemplateItem{{
				ID:      "wf-go-feat",
				Kind:    "skill",
				Name:    "wf-go-feat",
				Version: 1,
				Entry:   templateSkill,
				Files:   []string{templateSkill, templateMetadata},
			}},
		},
		ProjectConfigSets: map[string][]ProjectCopy{},
	}, nil, nil)

	project, err := store.ImportProject(ProjectInput{Name: "btd-game-server", Path: root})
	if err != nil {
		t.Fatalf("import project: %v", err)
	}
	copy, ok, err := store.AddProjectCopyFromTemplate(project.ID, "workflow", "feature-development")
	if err != nil {
		t.Fatalf("add project workflow copy from template: %v", err)
	}
	if !ok {
		t.Fatal("expected add project copy from template to find project and template")
	}
	if copy.Kind != "workflow" || copy.Status != "synced" || copy.Origin == nil || copy.Origin.TemplateID != "feature-development" {
		t.Fatalf("unexpected workflow copy: %#v", copy)
	}
	assertProjectCopy(t, store.data.ProjectConfigSets[project.ID], "workflow", "feature-development", "synced", ".claude/workflows/go-feature-development.md")
	for _, projectCopy := range store.data.ProjectConfigSets[project.ID] {
		if projectCopy.Kind == "skill" && projectCopy.Name == "wf-go-feat" {
			t.Fatalf("workflow skill should be hidden from project config copies: %#v", projectCopy)
		}
	}
	if data, err := os.ReadFile(filepath.Join(root, ".agents", "skills", "wf-go-feat", "SKILL.md")); err != nil || string(data) != "---\nname: wf-go-feat\ndescription: Go feature workflow.\n---\n\n# wf-go-feat\n" {
		t.Fatalf("expected codex skill markdown from template, data=%q err=%v", string(data), err)
	}
	if data, err := os.ReadFile(filepath.Join(root, ".agents", "skills", "wf-go-feat", "agents", "openai.yaml")); err != nil || string(data) != "interface:\n  display_name: \"WF Go Feature\"\n" {
		t.Fatalf("expected codex skill metadata from template, data=%q err=%v", string(data), err)
	}
}

func TestBootstrapFiltersWorkflowSkillTemplates(t *testing.T) {
	store := NewStoreFromData(BootstrapData{
		TemplateLibrary: TemplateLibrary{
			Skills: []TemplateItem{
				{ID: "go-testing", Kind: "skill", Name: "go-testing", Version: 1, Entry: "templates/skills/go-testing.md", Files: []string{"templates/skills/go-testing.md"}},
				{ID: "wf-go-feat", Kind: "skill", Name: "wf-go-feat", Version: 1, Entry: "templates/skills/codex/wf-go-feat/SKILL.md", Files: []string{"templates/skills/codex/wf-go-feat/SKILL.md"}},
			},
		},
		ProjectConfigSets: map[string][]ProjectCopy{
			"sample": {
				{Kind: "skill", Name: "go-testing", Origin: &Origin{TemplateID: "go-testing"}},
				{Kind: "skill", Name: "wf-go-feat", Origin: &Origin{TemplateID: "wf-go-feat"}},
				{Kind: "workflow", Name: "feature-development", Origin: &Origin{TemplateID: "feature-development"}},
			},
		},
		Projects: []Project{{ID: "sample", Name: "Sample"}},
	}, nil, nil)

	data := store.Bootstrap()
	if len(data.TemplateLibrary.Skills) != 1 || data.TemplateLibrary.Skills[0].ID != "go-testing" {
		t.Fatalf("expected bootstrap to hide wf skill templates, got %#v", data.TemplateLibrary.Skills)
	}
	copies := data.ProjectConfigSets["sample"]
	for _, copy := range copies {
		if copy.Kind == "skill" && strings.HasPrefix(copy.Name, "wf-") {
			t.Fatalf("expected bootstrap to hide wf skill project copies, got %#v", copies)
		}
	}
	if len(copies) != 2 {
		t.Fatalf("expected public project copies to keep normal skill and workflow only, got %#v", copies)
	}
}

func TestTemplateHashPrefersDeclaredFilesOverSourcePaths(t *testing.T) {
	root := t.TempDir()
	templateMarkdown := filepath.Join(root, "workflow.md")
	templateGraph := filepath.Join(root, "workflow.graph.json")
	routingSource := filepath.Join(root, "go-00-routing.md")
	writeTestFile(t, root, "workflow.md", "workflow")
	writeTestFile(t, root, "workflow.graph.json", `{"id":"workflow","nodes":[],"edges":[]}`)
	writeTestFile(t, root, "go-00-routing.md", "routing source")

	withRoutingSource := templateContentHash(TemplateItem{
		ID:          "workflow",
		Kind:        "workflow",
		Entry:       templateMarkdown,
		Files:       []string{templateMarkdown, templateGraph},
		SourcePaths: []string{templateMarkdown, templateGraph, routingSource},
	})
	withoutRoutingSource := templateContentHash(TemplateItem{
		ID:    "workflow",
		Kind:  "workflow",
		Entry: templateMarkdown,
		Files: []string{templateMarkdown, templateGraph},
	})

	if withRoutingSource != withoutRoutingSource {
		t.Fatalf("expected source-only routing references not to affect hash, got %s and %s", withRoutingSource, withoutRoutingSource)
	}
}

func TestNewStoreRestoresProjectsFromUserHomeNexusIndex(t *testing.T) {
	root := t.TempDir()
	home := t.TempDir()
	t.Setenv("USERPROFILE", home)
	t.Setenv("HOME", home)
	writeTestFile(t, root, ".claude/rules/01-communication.md", "communication")
	writeTestFile(t, home, ".nexus", `{
  "version": 1,
  "projects": [
    {
      "projectId": "btd-game-server",
      "projectName": "btd-game-server",
      "path": "`+filepath.ToSlash(root)+`",
      "repoKey": "github.com/example/btd-game-server",
      "importedAt": "2026-06-20T10:00:00Z",
      "lastScannedAt": "2026-06-20T10:30:00Z"
    }
  ]
}`)

	store := NewStore()
	bootstrap := store.Bootstrap()
	if len(bootstrap.Projects) != 1 {
		t.Fatalf("expected one restored project, got %#v", bootstrap.Projects)
	}
	project := bootstrap.Projects[0]
	if project.ID != "btd-game-server" || project.Name != "btd-game-server" || filepath.Clean(project.Path) != filepath.Clean(root) {
		t.Fatalf("expected restored project identity and path, got %#v", project)
	}
	if project.LocalConfigPath != "" || project.LocalConfigIgnored {
		t.Fatalf("expected no project root .nexus config fields, got %#v", project)
	}
}

func TestImportProjectRefreshesConfigSetWhenUserIndexAlreadyContainsProject(t *testing.T) {
	root := t.TempDir()
	home := t.TempDir()
	t.Setenv("USERPROFILE", home)
	t.Setenv("HOME", home)
	writeTestFile(t, root, ".claude/agents/go-worker.md", "worker markdown")
	writeTestFile(t, root, ".codex/agents/go-worker.toml", "name = \"worker\"")
	writeTestFile(t, root, ".claude/rules/go-00-routing.md", "routing markdown")
	writeTestFile(t, home, ".nexus", `{
  "version": 1,
  "projects": [
    {
      "projectId": "btd-game-server",
      "projectName": "btd-game-server",
      "path": "`+filepath.ToSlash(root)+`",
      "repoKey": "local:btd-game-server",
      "importedAt": "2026-06-20T10:00:00Z",
      "lastScannedAt": "2026-06-20T10:30:00Z"
    }
  ]
}`)

	store := NewStore()
	project, err := store.ImportProject(ProjectInput{Name: "btd-game-server", Path: root})
	if err != nil {
		t.Fatalf("import project: %v", err)
	}
	copies, ok := store.ProjectConfigSet(project.ID, "")
	if !ok {
		t.Fatal("expected imported project config set")
	}
	if len(copies) == 0 {
		t.Fatalf("expected imported project config copies to be preserved, got %#v", copies)
	}
}

func TestReadProjectWorkflowGraphAcceptsUTF8BOM(t *testing.T) {
	root := t.TempDir()
	graphPath := filepath.Join(root, ".claude", "workflows", "go-feature-development.graph.json")
	if err := os.MkdirAll(filepath.Dir(graphPath), 0o755); err != nil {
		t.Fatalf("create graph dir: %v", err)
	}
	data := append([]byte{0xef, 0xbb, 0xbf}, []byte(`{"id":"go-feature-development","name":"feature graph","nodes":[],"edges":[]}`)...)
	if err := os.WriteFile(graphPath, data, 0o644); err != nil {
		t.Fatalf("write bom graph: %v", err)
	}

	graph, ok := readProjectWorkflowGraph(root, ProjectCopy{
		Kind: "workflow",
		Name: "feature graph",
		Path: ".claude/workflows/go-feature-development.md",
	})
	if !ok {
		t.Fatal("expected BOM-prefixed graph JSON to be readable")
	}
	if graph.ID != "go-feature-development" {
		t.Fatalf("expected graph id, got %#v", graph)
	}
}

func writeTestFile(t *testing.T, root string, relative string, content string) {
	t.Helper()
	path := filepath.Join(root, filepath.FromSlash(relative))
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("create test dir: %v", err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("write test file %s: %v", relative, err)
	}
}

func assertProjectCopy(t *testing.T, copies []ProjectCopy, kind string, name string, status string, pathSuffix string) ProjectCopy {
	t.Helper()
	for _, copy := range copies {
		if copy.Kind == kind && copy.Name == name {
			if copy.Status != status {
				t.Fatalf("expected %s/%s status %s, got %#v", kind, name, status, copy)
			}
			if !strings.HasSuffix(filepath.ToSlash(copy.Path), filepath.ToSlash(pathSuffix)) {
				t.Fatalf("expected %s/%s path suffix %s, got %q", kind, name, pathSuffix, copy.Path)
			}
			if copy.SyncMode != "manual" || copy.LocalVersion != 1 {
				t.Fatalf("expected manual v1 copy for %s/%s, got %#v", kind, name, copy)
			}
			return copy
		}
	}
	t.Fatalf("expected %s/%s copy in %#v", kind, name, copies)
	return ProjectCopy{}
}
