package wikicompiler

import (
	"context"
	"errors"
	"nexus-agents/internal/knowledgebase"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type fakeCommandRunner struct {
	version   CommandResult
	run       CommandResult
	runErr    error
	onRun     func(directory string)
	onCommand func(args []string)
}

func (f fakeCommandRunner) Run(ctx context.Context, directory string, environment map[string]string, name string, args ...string) (CommandResult, error) {
	if len(args) == 1 && args[0] == "--help" {
		return f.version, nil
	}
	if f.onCommand != nil {
		f.onCommand(append([]string(nil), args...))
	}
	if f.onRun != nil {
		f.onRun(directory)
	}
	return f.run, f.runErr
}

func TestBuildInstructionsUsesConfiguredLanguageAndRequiresBilingualAliases(t *testing.T) {
	instructions := BuildInstructions(CompileInput{Revision: "abc123", Language: "zh-CN"})
	for _, expected := range []string{
		"Write titles, descriptions, and explanatory prose in `zh-CN`",
		"both Chinese and English routing aliases",
		"`routing.aliases.zh` and `routing.aliases.en`",
	} {
		if !strings.Contains(instructions, expected) {
			t.Fatalf("instructions are missing %q:\n%s", expected, instructions)
		}
	}
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
				authPath := filepath.Join(directory, "openwiki", "domain", "auth.md")
				authData, err := os.ReadFile(authPath)
				if err != nil {
					t.Fatal(err)
				}
				writeFile(t, directory, "openwiki/domain/auth.md", strings.Replace(
					string(authData),
					"tags: [auth]\n",
					"tags: [auth]\nrouting:\n  aliases:\n    zh: [\u8ba4\u8bc1]\n    en: [authentication]\n",
					1,
				))
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
				writeFile(t, directory, "openwiki/domains/model-routing/index.md", "---\ntype: Documentation Index\ntitle: Model Routing\ndescription: Files and subdirectories in Model Routing.\n---\n")
				if runCalls == 1 {
					return
				}
				writeFile(t, directory, "openwiki/domains/model-routing/domain.md", "---\ntype: Domain\ntitle: Model Routing\ndescription: Routes Codex model traffic.\nresource: internal/codexrouter/router.go\ntags: [routing]\ntimestamp: 2026-07-19T00:00:00Z\n---\n# Model Routing\n\nModel routing owns provider selection, protocol conversion, session telemetry, and the `/proxy/codex` entrypoint. Source facts come from `internal/codexrouter/router.go`, `convert.go`, and `internal/httpapi/server.go`. Changes require router and HTTP API tests, and related workflow evaluation behavior must be reviewed.\n")
				domainPath := filepath.Join(directory, "openwiki", "domains", "model-routing", "domain.md")
				domainData, err := os.ReadFile(domainPath)
				if err != nil {
					t.Fatal(err)
				}
				writeFile(t, directory, "openwiki/domains/model-routing/domain.md", strings.Replace(
					string(domainData),
					"timestamp: 2026-07-19T00:00:00Z\n",
					"timestamp: 2026-07-19T00:00:00Z\nrouting:\n  aliases:\n    zh: [\u6a21\u578b\u8def\u7531]\n    en: [model routing]\n",
					1,
				))
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

func TestOpenWikiRepairsMissingBilingualDomainAliases(t *testing.T) {
	project := initRepository(t)
	writeFile(t, project, "README.md", "# Repo\n")
	runGit(t, project, "add", ".")
	runGit(t, project, "commit", "-m", "initial")
	runCalls := 0
	var repairPrompt string
	openWiki := &OpenWiki{
		Version: "0.2.0", Timeout: 2 * time.Second, Workspaces: &WorkspaceManager{},
		Runner: fakeCommandRunner{
			version: CommandResult{Stdout: "openwiki v0.2.0"},
			onCommand: func(args []string) {
				if len(args) > 0 {
					repairPrompt = args[len(args)-1]
				}
			},
			onRun: func(directory string) {
				runCalls++
				writeFile(t, directory, "openwiki/index.md", "# Domains\n")
				aliases := "    en: [identity admission]\n"
				if runCalls > 1 {
					aliases = "    zh: [\u8eab\u4efd\u8ba4\u8bc1, \u73a9\u5bb6\u51c6\u5165]\n    en: [identity admission]\n"
				}
				body := strings.Repeat("This capability owns platform identity validation, account lifecycle, player admission, server selection, token creation, access control, integration boundaries, and focused verification paths. ", 3)
				writeFile(t, directory, "openwiki/domains/identity-admission/domain.md", "---\ntype: Domain\ntitle: Identity and player admission\ndescription: Owns player identity and admission.\nresource: internal/auth/service.go\ntags: [identity]\ntimestamp: 2026-07-24T00:00:00+08:00\nrouting:\n  aliases:\n"+aliases+"---\n# Identity and player admission\n\n"+body+"\n")
			},
		},
	}
	bundle, err := openWiki.Initialize(context.Background(), CompileInput{
		ProjectID: "p", RunID: "alias-repair", ProjectRoot: project, Revision: "HEAD",
		DataRoot: t.TempDir(), KnowledgeRoot: "KnowledgeBase/project", Language: "zh-CN",
	})
	if err != nil {
		t.Fatal(err)
	}
	if runCalls != 2 {
		t.Fatalf("expected one generation and one alias repair call, got %d", runCalls)
	}
	if !strings.Contains(repairPrompt, "routing.aliases.zh") || !strings.Contains(repairPrompt, "domains/identity-admission/domain.md") {
		t.Fatalf("repair prompt did not request bilingual aliases:\n%s", repairPrompt)
	}
	fm, _, ok := knowledgebase.ParseFrontmatter(string(bundle.Files["KnowledgeBase/project/domains/identity-admission/index.md"]))
	if !ok || len(fm.Routing.Aliases.ZH) == 0 || len(fm.Routing.Aliases.EN) == 0 {
		t.Fatalf("bilingual aliases were not repaired: %#v", fm.Routing.Aliases)
	}
	if len(bundle.Warnings) != 0 {
		t.Fatalf("successful alias repair should not create proposal warnings: %#v", bundle.Warnings)
	}
}

func TestOpenWikiPromotesDomainDocumentAndIgnoresGeneratedDirectoryIndex(t *testing.T) {
	root := t.TempDir()
	writeFile(t, root, "openwiki/index.md", "---\ntype: Documentation Index\ntitle: OpenWiki\ndescription: Files and subdirectories in OpenWiki.\n---\n# Directories\n")
	writeFile(t, root, "openwiki/domains/auth/index.md", "---\ntype: Documentation Index\ntitle: Auth\ndescription: Files and subdirectories in Auth.\n---\n")
	writeFile(t, root, "openwiki/domains/auth/domain.md", "---\ntype: Domain\ntitle: Authentication\ndescription: Owns request identity and authorization boundaries.\nresource: internal/auth/service.go\ntags: [auth]\n---\n# Authentication\n\nAuthentication owns request identity validation, authorization boundaries, token parsing, middleware integration, and failure behavior. The implementation starts in `internal/auth/service.go`, coordinates with session management, and is verified by focused unit tests plus HTTP integration tests.\n")
	files, _, err := ReadAndNormalizeOpenWiki(root, "KnowledgeBase/project")
	if err != nil {
		t.Fatal(err)
	}
	domainPath := "KnowledgeBase/project/domains/auth/index.md"
	domain := string(files[domainPath])
	if !strings.Contains(domain, "# Authentication") || strings.Contains(strings.ToLower(domain), "files and subdirectories") {
		t.Fatalf("Domain document was not promoted over generated index:\n%s", domain)
	}
	if _, exists := files["KnowledgeBase/project/domains/auth/domain.md"]; exists {
		t.Fatalf("Domain source file should be promoted to %s", domainPath)
	}
}

func TestOpenWikiAddsDeterministicDomainRoutingAliases(t *testing.T) {
	root := t.TempDir()
	writeFile(t, root, "openwiki/index.md", "# Domains\n")
	writeFile(t, root, "openwiki/domains/event-system/domain.md", "---\ntype: Domain\ntitle: 事件系统 EventCenter\ndescription: Owns in-process event dispatch.\nresource: Packages/com.dotdot.event-center/Runtime/EventCenter.cs\ntags: [events, unity]\ntimestamp: 2026-07-24T00:00:00+08:00\n---\n# 事件系统\n\nEventCenter owns synchronous event dispatch, listener registration, diagnostics, and related verification guidance.\n")
	files, _, err := ReadAndNormalizeOpenWiki(root, "KnowledgeBase/project")
	if err != nil {
		t.Fatal(err)
	}
	domainPath := "KnowledgeBase/project/domains/event-system/index.md"
	domain := string(files[domainPath])
	fm, _, ok := knowledgebase.ParseFrontmatter(domain)
	if !ok {
		t.Fatalf("normalized Domain is missing frontmatter:\n%s", domain)
	}
	if len(fm.Routing.Aliases.ZH) == 0 || fm.Routing.Aliases.ZH[0] != "事件系统 EventCenter" {
		t.Fatalf("unexpected zh routing aliases: %#v\n%s", fm.Routing.Aliases.ZH, domain)
	}
	if !containsStringValue(fm.Routing.Aliases.EN, "event system") || !containsStringValue(fm.Routing.Aliases.EN, "EventCenter") {
		t.Fatalf("unexpected en routing aliases: %#v\n%s", fm.Routing.Aliases.EN, domain)
	}
	for relative, data := range files {
		writeFile(t, root, relative, string(data))
	}
	writeFile(t, root, "KnowledgeBase/index.md", "# KnowledgeBase\n\n[Project](project/index.md)\n")
	writeFile(t, root, "KnowledgeBase/log.md", "# Log\n\nGenerated for validation.\n")
	report, err := knowledgebase.Validate(root)
	if err != nil {
		t.Fatal(err)
	}
	for _, issue := range report.Issues {
		if issue.Code == "missing_domain_aliases" || issue.Code == "missing_bilingual_aliases" {
			t.Fatalf("normalized Domain should satisfy routing alias validation: %#v\n%s", issue, domain)
		}
	}
}

func TestOpenWikiDoesNotReportSafeNormalizationAsWarnings(t *testing.T) {
	root := t.TempDir()
	writeFile(t, root, "openwiki/index.md", "---\ntype: Documentation Index\ntitle: OpenWiki\n---\n# Domains\n")
	writeFile(t, root, "openwiki/domains/event-system/domain.md", "---\ntype: Domain\ntitle: \u4e8b\u4ef6\u7cfb\u7edf EventCenter\ndescription: Owns in-process event dispatch.\nresource: internal/eventcenter/service.go\ntags: [events]\n---\n# Event System\n\n"+strings.Repeat("EventCenter owns listener registration, synchronous dispatch, diagnostics, integration boundaries, compatibility cautions, and focused verification paths. ", 3)+"\n")
	files, warnings, err := ReadAndNormalizeOpenWiki(root, "KnowledgeBase/project")
	if err != nil {
		t.Fatal(err)
	}
	if len(warnings) != 0 {
		t.Fatalf("safe timestamp/frontmatter normalization should not create proposal warnings: %#v", warnings)
	}
	domain := string(files["KnowledgeBase/project/domains/event-system/index.md"])
	if !strings.Contains(domain, "timestamp:") {
		t.Fatalf("missing timestamp was not normalized:\n%s", domain)
	}
	if strings.HasPrefix(string(files["KnowledgeBase/project/index.md"]), "---") {
		t.Fatalf("reserved index frontmatter was not removed:\n%s", files["KnowledgeBase/project/index.md"])
	}
}

func TestOpenWikiPreservesExplicitDomainRoutingAliases(t *testing.T) {
	root := t.TempDir()
	writeFile(t, root, "openwiki/index.md", "# Domains\n")
	writeFile(t, root, "openwiki/domains/event-system/domain.md", "---\ntype: Domain\ntitle: 事件系统\ndescription: Owns in-process event dispatch.\nresource: Packages/com.dotdot.event-center/Runtime/EventCenter.cs\ntags: [events, unity]\ntimestamp: 2026-07-24T00:00:00+08:00\nrouting:\n  aliases:\n    zh: [事件总线]\n    en: [EventCenter]\n---\n# 事件系统\n\nEventCenter owns synchronous event dispatch, listener registration, diagnostics, and related verification guidance.\n")
	files, _, err := ReadAndNormalizeOpenWiki(root, "KnowledgeBase/project")
	if err != nil {
		t.Fatal(err)
	}
	domain := string(files["KnowledgeBase/project/domains/event-system/index.md"])
	if strings.Count(domain, "\nrouting:\n") != 1 {
		t.Fatalf("explicit routing metadata was duplicated:\n%s", domain)
	}
	fm, _, ok := knowledgebase.ParseFrontmatter(domain)
	if !ok || len(fm.Routing.Aliases.ZH) != 1 || fm.Routing.Aliases.ZH[0] != "事件总线" ||
		len(fm.Routing.Aliases.EN) != 1 || fm.Routing.Aliases.EN[0] != "EventCenter" {
		t.Fatalf("explicit aliases were not preserved: %#v\n%s", fm.Routing.Aliases, domain)
	}
}

func TestOpenWikiReplacesEmptyDomainRoutingAliasesWithoutDroppingKeywords(t *testing.T) {
	root := t.TempDir()
	writeFile(t, root, "openwiki/index.md", "# Domains\n")
	writeFile(t, root, "openwiki/domains/event-system/domain.md", "---\ntype: Domain\ntitle: 事件系统 EventCenter\ndescription: Owns in-process event dispatch.\nresource: Packages/com.dotdot.event-center/Runtime/EventCenter.cs\ntags: [events, unity]\ntimestamp: 2026-07-24T00:00:00+08:00\nrouting:\n  aliases:\n    zh: []\n    en: []\n  keywords:\n    en: [dispatch]\n---\n# 事件系统\n\nEventCenter owns synchronous event dispatch, listener registration, diagnostics, and related verification guidance.\n")
	files, _, err := ReadAndNormalizeOpenWiki(root, "KnowledgeBase/project")
	if err != nil {
		t.Fatal(err)
	}
	domain := string(files["KnowledgeBase/project/domains/event-system/index.md"])
	fm, _, ok := knowledgebase.ParseFrontmatter(domain)
	if !ok || len(fm.Routing.Aliases.ZH) == 0 || len(fm.Routing.Aliases.EN) == 0 {
		t.Fatalf("empty aliases were not replaced: %#v\n%s", fm.Routing.Aliases, domain)
	}
	if len(fm.Routing.Keywords.EN) != 1 || fm.Routing.Keywords.EN[0] != "dispatch" {
		t.Fatalf("routing keywords were not preserved: %#v\n%s", fm.Routing.Keywords, domain)
	}
}

func TestOpenWikiUsesInitForInitializeAndUpdateForUpdate(t *testing.T) {
	for _, test := range []struct {
		name     string
		update   bool
		expected string
	}{
		{name: "initialize", expected: "--init"},
		{name: "update", update: true, expected: "--update"},
	} {
		t.Run(test.name, func(t *testing.T) {
			project := initRepository(t)
			writeFile(t, project, "README.md", "# Repo\n")
			runGit(t, project, "add", ".")
			runGit(t, project, "commit", "-m", "initial")
			var commandArgs []string
			openWiki := &OpenWiki{
				Version: "0.2.0", Timeout: time.Second, Workspaces: &WorkspaceManager{},
				Runner: fakeCommandRunner{
					version: CommandResult{Stdout: "openwiki v0.2.0"},
					onCommand: func(args []string) {
						commandArgs = args
					},
					onRun: func(directory string) {
						writeFile(t, directory, "openwiki/index.md", "# Domains\n")
					},
				},
			}
			input := CompileInput{
				ProjectID: "p", RunID: test.name, ProjectRoot: project, Revision: "HEAD",
				DataRoot: t.TempDir(), KnowledgeRoot: "KnowledgeBase/project",
			}
			var err error
			if test.update {
				_, err = openWiki.Update(context.Background(), input)
			} else {
				_, err = openWiki.Initialize(context.Background(), input)
			}
			if err != nil {
				t.Fatal(err)
			}
			if !containsArgument(commandArgs, test.expected) {
				t.Fatalf("command args = %v, expected %s", commandArgs, test.expected)
			}
			unexpected := "--update"
			if test.update {
				unexpected = "--init"
			}
			if containsArgument(commandArgs, unexpected) {
				t.Fatalf("command args = %v, did not expect %s", commandArgs, unexpected)
			}
		})
	}
}

func TestOpenWikiRedactsSecretsOnFailure(t *testing.T) {
	t.Setenv("OPENWIKI_TEST_TOKEN", "inherited-secret-value")
	project := initRepository(t)
	writeFile(t, project, "README.md", "# Repo\n")
	runGit(t, project, "add", ".")
	runGit(t, project, "commit", "-m", "initial")
	openWiki := &OpenWiki{
		Version: "0.2.0", Workspaces: &WorkspaceManager{},
		Runner: fakeCommandRunner{
			version: CommandResult{Stdout: "0.2.0"},
			run:     CommandResult{Stdout: "provider rejected inherited-secret-value", Stderr: "bad token secret-value"},
			runErr:  errors.New("exit 1"),
		},
	}
	_, err := openWiki.Initialize(context.Background(), CompileInput{
		ProjectID: "p", RunID: "r", ProjectRoot: project, Revision: "HEAD",
		DataRoot: t.TempDir(), Environment: map[string]string{"OPENAI_API_KEY": "secret-value"},
	})
	if err == nil || strings.Contains(err.Error(), "secret-value") || strings.Contains(err.Error(), "inherited-secret-value") {
		t.Fatalf("error was not safely redacted: %v", err)
	}
	if strings.Contains(err.Error(), "timed out") {
		t.Fatalf("ordinary compiler failure was incorrectly reported as a timeout: %v", err)
	}
	if !strings.Contains(err.Error(), "provider rejected [REDACTED]") {
		t.Fatalf("stdout failure details were not preserved: %v", err)
	}
}

func containsArgument(args []string, expected string) bool {
	for _, arg := range args {
		if arg == expected {
			return true
		}
	}
	return false
}

func containsStringValue(values []string, expected string) bool {
	for _, value := range values {
		if value == expected {
			return true
		}
	}
	return false
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
