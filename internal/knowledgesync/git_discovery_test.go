package knowledgesync

import (
	"context"
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
