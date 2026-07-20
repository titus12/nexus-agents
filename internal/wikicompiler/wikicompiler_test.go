package wikicompiler

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type fakeCommandRunner struct {
	version CommandResult
	run     CommandResult
	runErr  error
	onRun   func(directory string)
}

func (f fakeCommandRunner) Run(ctx context.Context, directory string, environment map[string]string, name string, args ...string) (CommandResult, error) {
	if len(args) == 1 && args[0] == "--help" {
		return f.version, nil
	}
	if f.onRun != nil {
		f.onRun(directory)
	}
	return f.run, f.runErr
}

func TestOpenWikiRunsInSnapshotAndNormalizesOnlyOpenWiki(t *testing.T) {
	project := initRepository(t)
	writeFile(t, project, "README.md", "# Repo\n")
	runGit(t, project, "add", ".")
	runGit(t, project, "commit", "-m", "initial")
	openWiki := &OpenWiki{
		Version: "0.2.0", Timeout: time.Second, Workspaces: &WorkspaceManager{},
		Runner: fakeCommandRunner{
			version: CommandResult{Stdout: "openwiki v0.2.0"},
			onRun: func(directory string) {
				writeFile(t, directory, "AGENTS.md", "side effect")
				writeFile(t, directory, "openwiki/index.md", "---\ntype: Documentation Index\ntitle: OpenWiki\n---\n# Index\n")
				writeFile(t, directory, "openwiki/domain/auth.md", "---\ntype: Architecture Overview\ntitle: Auth\ndescription: auth\nresource: internal/auth/service.go\ntags: [auth]\n---\n# Auth\n\nUseful auth knowledge from `openwiki/INSTRUCTIONS.md`. The authentication capability owns request identity validation, token parsing, authorization boundaries, and failure responses. Its implementation starts in `internal/auth/service.go`, is called by the HTTP middleware, and must not expose credentials in logs. Changes require focused unit tests, HTTP integration tests, and verification of the related session-management capability. Related Domains should be linked explicitly when their contracts depend on authenticated identity.\n")
			},
		},
	}
	dataRoot := t.TempDir()
	bundle, err := openWiki.Initialize(context.Background(), CompileInput{
		ProjectID: "p", RunID: "r", ProjectRoot: project, Revision: "HEAD",
		DataRoot: dataRoot, KnowledgeRoot: "KnowledgeBase/project",
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := bundle.Files["KnowledgeBase/project/index.md"]; !ok {
		t.Fatalf("paths = %#v", bundle.Paths)
	}
	if strings.HasPrefix(string(bundle.Files["KnowledgeBase/project/index.md"]), "---") {
		t.Fatal("reserved index frontmatter was not removed")
	}
	if !strings.Contains(string(bundle.Files["KnowledgeBase/project/index.md"]), "(domains/auth/index.md)") {
		t.Fatalf("project index did not become a domain resolver:\n%s", bundle.Files["KnowledgeBase/project/index.md"])
	}
	if _, ok := bundle.Files["AGENTS.md"]; ok {
		t.Fatal("OpenWiki side effect escaped output boundary")
	}
	if bundle.WorkspaceRoot != "" {
		t.Fatalf("workspace should not be retained by default: %s", bundle.WorkspaceRoot)
	}
	if _, err := os.Stat(filepath.Join(dataRoot, "p", "workspaces", "r")); !os.IsNotExist(err) {
		t.Fatalf("workspace was not cleaned after compilation: %v", err)
	}
	AnnotateProvenance(bundle.Files, "abc123", "0.2.0")
	auth := string(bundle.Files["KnowledgeBase/project/domains/auth/index.md"])
	for _, expected := range []string{
		"type: Domain",
		"resource: KnowledgeBase/project/domains/auth/index.md",
		"sourcePaths: [internal/auth/service.go]",
		"timestamp:",
		"managedBy: openwiki",
		"sourceRevision: abc123",
	} {
		if !strings.Contains(auth, expected) {
			t.Fatalf("generated knowledge is missing %q:\n%s", expected, auth)
		}
	}
	if strings.Contains(auth, "resource: internal/auth/service.go") {
		t.Fatalf("generated provenance was not added:\n%s", auth)
	}
	if strings.Contains(auth, "INSTRUCTIONS.md") || !strings.Contains(auth, "Nexus-selected committed source snapshot") {
		t.Fatalf("run-control document reference escaped normalization:\n%s", auth)
	}
}

func TestOpenWikiRewritesFlatDomainLinks(t *testing.T) {
	root := t.TempDir()
	openWikiRoot := filepath.Join(root, "openwiki")
	writeFile(t, root, "openwiki/index.md", "# Project\n\n[Routing](api-routing.md)\n")
	writeFile(t, root, "openwiki/domains/index.md", "# Directories\n\n- [Routing](api-routing/)\n")
	writeFile(t, root, "openwiki/api-routing.md", "---\ntype: Routing\ntitle: Model Routing\ndescription: Routes models.\nresource: internal/router.go\ntags: [routing]\n---\n# Routing\n\nSee [Workflows](workflow.md).\n")
	writeFile(t, root, "openwiki/workflow.md", "---\ntype: Workflow\ntitle: Workflow Evaluation\ndescription: Evaluates workflows.\nresource: internal/workflow.go\ntags: [workflow]\n---\n# Workflow\n\nSee [Routing](api-routing.md).\n")
	if _, err := os.Stat(openWikiRoot); err != nil {
		t.Fatal(err)
	}
	files, _, err := ReadAndNormalizeOpenWiki(root, "KnowledgeBase/project")
	if err != nil {
		t.Fatal(err)
	}
	routingPath := "KnowledgeBase/project/domains/api-routing/index.md"
	workflowPath := "KnowledgeBase/project/domains/workflow/index.md"
	if _, ok := files[routingPath]; !ok {
		t.Fatalf("missing routing domain: %#v", files)
	}
	if _, ok := files[workflowPath]; !ok {
		t.Fatalf("missing workflow domain: %#v", files)
	}
	if _, ok := files["KnowledgeBase/project/domains/index/index.md"]; ok {
		t.Fatal("OpenWiki domains/index.md must not become a fake Domain")
	}
	if !strings.Contains(string(files[routingPath]), "../workflow/index.md") {
		t.Fatalf("flat cross-domain link was not rewritten:\n%s", files[routingPath])
	}
}

func TestOpenWikiRepairsThinDomainPages(t *testing.T) {
	project := initRepository(t)
	writeFile(t, project, "README.md", "# Repo\n")
	runGit(t, project, "add", ".")
	runGit(t, project, "commit", "-m", "initial")
	runCalls := 0
	openWiki := &OpenWiki{
		Version: "0.2.0", Timeout: 2 * time.Second, Workspaces: &WorkspaceManager{},
		Runner: fakeCommandRunner{
			version: CommandResult{Stdout: "openwiki v0.2.0"},
			onRun: func(directory string) {
				runCalls++
				writeFile(t, directory, "openwiki/index.md", "# Domains\n")
				if runCalls == 1 {
					writeFile(t, directory, "openwiki/domains/model-routing/index.md", "---\ntype: Domain\ntitle: Model Routing\ndescription: Files and subdirectories in Model Routing.\nresource: openwiki/domains/model-routing/index.md\ntags: [routing]\ntimestamp: 2026-07-19T00:00:00Z\n---\n")
					return
				}
				writeFile(t, directory, "openwiki/domains/model-routing/index.md", "---\ntype: Domain\ntitle: Model Routing\ndescription: Routes Codex model traffic.\nresource: openwiki/domains/model-routing/index.md\ntags: [routing]\ntimestamp: 2026-07-19T00:00:00Z\n---\n# Model Routing\n\nModel routing owns provider selection, protocol conversion, session telemetry, and the `/proxy/codex` entrypoint. Source facts come from `internal/codexrouter/router.go`, `convert.go`, and `internal/httpapi/server.go`. Changes require router and HTTP API tests, and related workflow evaluation behavior must be reviewed.\n")
			},
		},
	}
	bundle, err := openWiki.Initialize(context.Background(), CompileInput{
		ProjectID: "p", RunID: "domain-repair", ProjectRoot: project, Revision: "HEAD",
		DataRoot: t.TempDir(), KnowledgeRoot: "KnowledgeBase/project",
	})
	if err != nil {
		t.Fatal(err)
	}
	if runCalls != 2 {
		t.Fatalf("expected one generation and one repair call, got %d", runCalls)
	}
	domain := string(bundle.Files["KnowledgeBase/project/domains/model-routing/index.md"])
	if strings.Contains(strings.ToLower(domain), "files and subdirectories") || len(domain) < 500 {
		t.Fatalf("thin Domain page was not repaired:\n%s", domain)
	}
}

func TestOpenWikiRedactsSecretsOnFailure(t *testing.T) {
	project := initRepository(t)
	writeFile(t, project, "README.md", "# Repo\n")
	runGit(t, project, "add", ".")
	runGit(t, project, "commit", "-m", "initial")
	openWiki := &OpenWiki{
		Version: "0.2.0", Workspaces: &WorkspaceManager{},
		Runner: fakeCommandRunner{
			version: CommandResult{Stdout: "0.2.0"},
			run:     CommandResult{Stderr: "bad token secret-value"},
			runErr:  errors.New("exit 1"),
		},
	}
	_, err := openWiki.Initialize(context.Background(), CompileInput{
		ProjectID: "p", RunID: "r", ProjectRoot: project, Revision: "HEAD",
		DataRoot: t.TempDir(), Environment: map[string]string{"OPENAI_API_KEY": "secret-value"},
	})
	if err == nil || strings.Contains(err.Error(), "secret-value") {
		t.Fatalf("error was not safely redacted: %v", err)
	}
	if strings.Contains(err.Error(), "timed out") {
		t.Fatalf("ordinary compiler failure was incorrectly reported as a timeout: %v", err)
	}
}

func initRepository(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	runGit(t, root, "init")
	runGit(t, root, "config", "user.email", "nexus@example.test")
	runGit(t, root, "config", "user.name", "Nexus Test")
	return root
}

func writeFile(t *testing.T, root, relative, content string) {
	t.Helper()
	target := filepath.Join(root, filepath.FromSlash(relative))
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(target, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func runGit(t *testing.T, root string, args ...string) {
	t.Helper()
	command := exec.Command("git", append([]string{"-C", root}, args...)...)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, output)
	}
}
