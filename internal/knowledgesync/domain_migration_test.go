package knowledgesync

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestBuildDomainMigrationBundleReorganizesApprovedFlatKnowledge(t *testing.T) {
	root := t.TempDir()
	writeMigrationDoc(t, root, "quickstart.md", "Project", "Nexus", "runtime platform overview", "# Nexus\n\nStart the service and use the project console.")
	writeMigrationDoc(t, root, "architecture.md", "Reference", "Architecture", "runtime architecture", "# Runtime architecture\n\nThe HTTP server composes catalog and routing services.")
	writeMigrationDoc(t, root, "api-routing.md", "Routing", "API Routing", "model routing", "# API Routing\n\nThe Codex proxy selects providers and records telemetry.")
	writeMigrationDoc(t, root, "templates.md", "Template", "Templates", "template management", "# Templates\n\nTemplates initialize agents, skills, rules, and workflows.")
	writeMigrationDoc(t, root, "workflow-knowledgebase.md", "Workflow", "Workflow and KB", "workflow and knowledge", "# Workflow and KB\n\n## Workflow-run to evaluation lifecycle\n\nWorkflow runs become evaluations.\n\n## Project KnowledgeBase\n\nKnowledge retrieval loads Domain context.\n\n## Change guidance\n\nRun tests.")

	files, err := BuildDomainMigrationBundle(root)
	if err != nil {
		t.Fatal(err)
	}
	for _, expected := range []string{
		"KnowledgeBase/project/index.md",
		"KnowledgeBase/project/domains/runtime-platform/index.md",
		"KnowledgeBase/project/domains/model-routing/index.md",
		"KnowledgeBase/project/domains/template-management/index.md",
		"KnowledgeBase/project/domains/workflow-evaluation/index.md",
		"KnowledgeBase/project/domains/knowledgebase/index.md",
	} {
		if _, ok := files[expected]; !ok {
			t.Fatalf("missing %s: %#v", expected, files)
		}
	}
	for relative := range files {
		if strings.HasPrefix(relative, "KnowledgeBase/project/") &&
			!strings.HasPrefix(relative, "KnowledgeBase/project/domains/") &&
			relative != "KnowledgeBase/project/index.md" {
			t.Fatalf("migration emitted flat project page %s", relative)
		}
	}
	index := string(files["KnowledgeBase/project/index.md"])
	if !strings.Contains(index, "domains/model-routing/index.md") || !strings.Contains(index, "domains/knowledgebase/index.md") {
		t.Fatalf("project Domain resolver is incomplete:\n%s", index)
	}
}

func TestBuildDomainMigrationBundleExpandsApprovedDomainIndexes(t *testing.T) {
	root := t.TempDir()
	domainDir := filepath.Join(root, "KnowledgeBase", "project", "domains", "model-routing")
	if err := os.MkdirAll(domainDir, 0o755); err != nil {
		t.Fatal(err)
	}
	content := `---
type: Domain
title: Model Routing
description: Routes model requests.
resource: KnowledgeBase/project/domains/model-routing/index.md
tags: [model-routing, domain]
timestamp: 2026-07-19T00:00:00Z
managedBy: openwiki
sourceRevision: abc123
generatedBy: nexus-domain-migration-v1
---
# Model Routing

Routes model requests.

## HTTP entrypoints

The handler is in ` + "`internal/httpapi/server.go`" + `.

## Provider selection

The route table is in ` + "`internal/codexrouter/router.go`" + `.
`
	if err := os.WriteFile(filepath.Join(domainDir, "index.md"), []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}

	files, err := BuildDomainMigrationBundle(root)
	if err != nil {
		t.Fatal(err)
	}
	for _, expected := range []string{
		"KnowledgeBase/project/domains/model-routing/index.md",
		"KnowledgeBase/project/domains/model-routing/http-entrypoints.md",
		"KnowledgeBase/project/domains/model-routing/provider-selection.md",
	} {
		if _, exists := files[expected]; !exists {
			t.Fatalf("missing expanded Domain file %s", expected)
		}
	}
	index := string(files["KnowledgeBase/project/domains/model-routing/index.md"])
	if !strings.Contains(index, "routing:") || !strings.Contains(index, "模型路由") || !strings.Contains(index, "http-entrypoints.md") {
		t.Fatalf("expanded Domain index is missing routing or capability links:\n%s", index)
	}
}

func writeMigrationDoc(t *testing.T, root, name, kind, title, description, body string) {
	t.Helper()
	target := filepath.Join(root, "KnowledgeBase", "project", name)
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		t.Fatal(err)
	}
	content := "---\n" +
		"type: " + kind + "\n" +
		"title: " + title + "\n" +
		"description: " + description + "\n" +
		"resource: KnowledgeBase/project/" + name + "\n" +
		"tags: [test]\n" +
		"timestamp: 2026-07-19T00:00:00Z\n" +
		"managedBy: openwiki\n" +
		"sourceRevision: abc123\n" +
		"---\n" + body + "\n"
	if err := os.WriteFile(target, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}
