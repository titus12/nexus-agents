package knowledgesync

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestInventoryUsesTrackedFilesAndClassifiesChanges(t *testing.T) {
	root := initTestRepository(t)
	writeRepoFile(t, root, "go.mod", "module example\n\ngo 1.26\n")
	writeRepoFile(t, root, "README.md", "# Example\n")
	writeRepoFile(t, root, "internal/http/router.go", "package http\n")
	writeRepoFile(t, root, "internal/http/router_test.go", "package http\n")
	writeRepoFile(t, root, ".env", "SECRET=x\n")
	runGit(t, root, "add", "go.mod", "README.md", "internal/http/router.go", "internal/http/router_test.go", ".env")
	runGit(t, root, "commit", "-m", "initial")
	if err := os.Remove(filepath.Join(root, "README.md")); err != nil {
		t.Fatal(err)
	}

	inventory, err := BuildInventory(context.Background(), root, ExecGitRunner{}, nil, ScanLimits{MaxFileSizeKB: 512, MaxFilesPerRun: 100})
	if err != nil {
		t.Fatal(err)
	}
	for _, file := range inventory.Files {
		if file.Path == ".env" {
			t.Fatal("sensitive tracked file entered inventory")
		}
	}
	if len(inventory.Manifests) != 1 || len(inventory.Readmes) != 1 {
		t.Fatalf("inventory manifests/readmes = %#v / %#v", inventory.Manifests, inventory.Readmes)
	}
	classified := ClassifyChanges([]GitChange{{Status: "M", Path: "internal/http/router.go"}})
	if classified.Class != ChangeStableCode {
		t.Fatalf("class = %s", classified.Class)
	}
}

func TestDiscoveryFallsBackWithoutAI(t *testing.T) {
	root := initTestRepository(t)
	writeRepoFile(t, root, "cmd/server/main.go", "package main\n")
	runGit(t, root, "add", ".")
	runGit(t, root, "commit", "-m", "initial")
	proposal, err := DiscoverPolicy(context.Background(), root, ExecGitRunner{}, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if proposal.AIRefined || len(proposal.Rules) == 0 || len(proposal.Warnings) == 0 {
		t.Fatalf("unexpected fallback proposal: %#v", proposal)
	}
}

func TestDeterministicPolicyIncludesRootFilesAndDirectoryDescendants(t *testing.T) {
	inventory := RepositoryInventory{
		Files: []InventoryFile{
			{Path: "application.go", Category: "code"},
			{Path: "application_test.go", Category: "test"},
			{Path: "internal/http/router.go", Category: "code"},
		},
		Manifests: []string{"go.mod"},
		Readmes:   []string{"README.md"},
	}
	proposal := deterministicPolicy(inventory)
	profile := DefaultProfile()
	profile.Scan.Rules = proposal.Rules

	candidates := []string{
		"application.go",
		"application_test.go",
		"internal/http/router.go",
		"go.mod",
		"README.md",
	}
	included := IncludedPaths(profile, candidates)
	if len(included) != len(candidates) {
		t.Fatalf("included paths = %#v, want %#v", included, candidates)
	}

	rules := map[string]ScanRule{}
	for _, rule := range proposal.Rules {
		rules[rule.Pattern] = rule
	}
	if _, ok := rules["application.go"]; !ok {
		t.Fatalf("expected exact root-file rule, got %#v", proposal.Rules)
	}
	if _, ok := rules["application.go/**"]; ok {
		t.Fatalf("root files must not use recursive directory rule: %#v", proposal.Rules)
	}
	if _, ok := rules["internal/**"]; !ok {
		t.Fatalf("expected recursive directory rule, got %#v", proposal.Rules)
	}
}

func TestDiscoverPolicyRetainsDeterministicCoverageAfterAIRefinement(t *testing.T) {
	root := initTestRepository(t)
	writeRepoFile(t, root, "application.go", "package sandwich\n")
	writeRepoFile(t, root, "pkg/actor/runtime.go", "package actor\n")
	writeRepoFile(t, root, "pkg/actor/gen/generated.go", "package gen\n")
	runGit(t, root, "add", ".")
	runGit(t, root, "commit", "-m", "initial")

	client := staticDiscoveryClient{response: []byte(`{
		"revision":"ignored",
		"rules":[
			{"pattern":"*.go","action":"include","category":"code","priority":"high","reason":"Go source","confidence":0.9},
			{"pattern":"**/gen/**","action":"exclude","category":"generated_artifact","priority":"high","reason":"Generated code","confidence":0.95}
		],
		"requiredTopics":["runtime"],
		"uncertain":[],
		"warnings":[]
	}`)}

	proposal, err := DiscoverPolicy(context.Background(), root, ExecGitRunner{}, client, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !proposal.AIRefined {
		t.Fatalf("expected AI-refined proposal, got %#v", proposal)
	}
	profile := ProfileFromDiscovery(proposal, "reviewer")
	candidates := []string{"application.go", "pkg/actor/runtime.go", "pkg/actor/gen/generated.go"}
	included := IncludedPaths(profile, candidates)
	if !containsDiscoveryPath(included, "application.go") || !containsDiscoveryPath(included, "pkg/actor/runtime.go") {
		t.Fatalf("AI refinement narrowed deterministic source coverage: %#v", included)
	}
	if containsDiscoveryPath(included, "pkg/actor/gen/generated.go") {
		t.Fatalf("AI explicit generated-code exclusion was not preserved: %#v", included)
	}
}

type staticDiscoveryClient struct {
	response []byte
	err      error
}

func (c staticDiscoveryClient) ProposeScanPolicy(context.Context, RepositoryInventory) ([]byte, error) {
	if c.err != nil {
		return nil, c.err
	}
	if len(c.response) == 0 {
		return nil, errors.New("missing discovery response")
	}
	return c.response, nil
}

func containsDiscoveryPath(paths []string, target string) bool {
	for _, path := range paths {
		if path == target {
			return true
		}
	}
	return false
}

func initTestRepository(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	runGit(t, root, "init")
	runGit(t, root, "config", "user.email", "nexus@example.test")
	runGit(t, root, "config", "user.name", "Nexus Test")
	return root
}

func writeRepoFile(t *testing.T, root, relative, content string) {
	t.Helper()
	target := filepath.Join(root, filepath.FromSlash(relative))
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(target, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func runGit(t *testing.T, root string, args ...string) string {
	t.Helper()
	command := exec.Command("git", append([]string{"-C", root}, args...)...)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, output)
	}
	return string(output)
}
