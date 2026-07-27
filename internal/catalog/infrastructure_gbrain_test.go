package catalog

import (
	"fmt"
	"path/filepath"
	"strings"
	"testing"
)

type recordingEnvironmentRunner struct {
	calls       []string
	environment map[string]string
}

type codeGraphSetupRunner struct {
	calls       []string
	initialized bool
}

func (r *codeGraphSetupRunner) Run(name string, args ...string) (string, error) {
	r.calls = append(r.calls, strings.Join(append([]string{name}, args...), " "))
	if name != "codegraph" {
		return "", fmt.Errorf("unexpected command %s", name)
	}
	switch args[0] {
	case "status":
		return fmt.Sprintf(`{"initialized":%t,"version":"1.0.1","projectPath":%q,"indexPath":%q}`,
			r.initialized, args[1], filepath.Join(args[1], ".codegraph")), nil
	case "init":
		r.initialized = true
		return "initialized", nil
	case "sync":
		return "synced", nil
	default:
		return "", fmt.Errorf("unexpected CodeGraph action %s", args[0])
	}
}

func (r *recordingEnvironmentRunner) Run(name string, args ...string) (string, error) {
	r.calls = append(r.calls, strings.Join(append([]string{name}, args...), " "))
	switch name {
	case "bun":
		if len(args) == 1 && args[0] == "--version" {
			return "1.2.20", nil
		}
		return "installed gbrain@0.42.62.0", nil
	case "gbrain":
		return "gbrain 0.42.62.0", nil
	default:
		return "", fmt.Errorf("missing %s", name)
	}
}

func (r *recordingEnvironmentRunner) RunWithEnvironment(environment map[string]string, name string, args ...string) (string, error) {
	r.environment = environment
	return r.Run(name, args...)
}

func TestGBrainInfrastructureUsesPinnedManagedInstall(t *testing.T) {
	runner := &recordingEnvironmentRunner{}
	toolRoot := t.TempDir()
	service := NewInfrastructureService(InfrastructureServiceOptions{Runner: runner, ToolRoot: toolRoot})

	item, ok, err := service.Check("gbrain")
	if err != nil || !ok {
		t.Fatalf("check = %#v, %v, %v", item, ok, err)
	}
	if item.Status != "ready" || item.Version != testedGBrainVersion {
		t.Fatalf("item = %#v", item)
	}
	if _, _, err := service.Install("gbrain"); err != nil {
		t.Fatal(err)
	}
	foundPinnedInstall := false
	for _, call := range runner.calls {
		if call == "bun install --global github:garrytan/gbrain#"+testedGBrainCommit {
			foundPinnedInstall = true
		}
	}
	if !foundPinnedInstall {
		t.Fatalf("calls = %#v", runner.calls)
	}
	if got := runner.environment["BUN_INSTALL_GLOBAL_DIR"]; got != filepath.Join(toolRoot, "gbrain", testedGBrainCommit) {
		t.Fatalf("BUN_INSTALL_GLOBAL_DIR = %q", got)
	}
	if got := runner.environment["BUN_INSTALL_BIN"]; got != filepath.Join(toolRoot, "gbrain", testedGBrainCommit, "bin") {
		t.Fatalf("BUN_INSTALL_BIN = %q", got)
	}
}

func TestGBrainInfrastructureRequiresBun(t *testing.T) {
	service := NewInfrastructureService(InfrastructureServiceOptions{
		Runner: InfrastructureCommandRunnerFunc(func(name string, args ...string) (string, error) {
			if name == "bun" {
				return "", fmt.Errorf("not found")
			}
			return "gbrain 0.42.62.0", nil
		}),
		ToolRoot: t.TempDir(),
	})
	item, ok, err := service.Check("gbrain")
	if err != nil || !ok {
		t.Fatalf("check = %#v, %v, %v", item, ok, err)
	}
	if item.Status != "not_installed" || !strings.Contains(item.Output, "Bun runtime") {
		t.Fatalf("item = %#v", item)
	}
}

func TestManagedInstallEnvironmentReplacesExistingValues(t *testing.T) {
	merged := mergeEnvironment(
		[]string{"PATH=bin", "BUN_INSTALL_BIN=old", "BUN_INSTALL_GLOBAL_DIR=old"},
		map[string]string{"BUN_INSTALL_BIN": "new-bin", "BUN_INSTALL_GLOBAL_DIR": "new-root"},
	)
	joined := strings.Join(merged, "\n")
	if strings.Contains(joined, "BUN_INSTALL_BIN=old") || strings.Contains(joined, "BUN_INSTALL_GLOBAL_DIR=old") {
		t.Fatalf("old values were retained:\n%s", joined)
	}
	if strings.Count(joined, "BUN_INSTALL_BIN=") != 1 || strings.Count(joined, "BUN_INSTALL_GLOBAL_DIR=") != 1 {
		t.Fatalf("managed values were duplicated:\n%s", joined)
	}
}

func TestEnsureCodeGraphInitializesThenSynchronizesExistingIndex(t *testing.T) {
	root := t.TempDir()
	runner := &codeGraphSetupRunner{}
	service := NewInfrastructureService(InfrastructureServiceOptions{Runner: runner})

	first, err := service.EnsureCodeGraph(root)
	if err != nil {
		t.Fatalf("ensure CodeGraph first time: %v", err)
	}
	if !first.Initialized || first.Action != "init" || first.ProjectPath != root {
		t.Fatalf("unexpected first CodeGraph setup: %#v", first)
	}

	second, err := service.EnsureCodeGraph(root)
	if err != nil {
		t.Fatalf("ensure CodeGraph second time: %v", err)
	}
	if !second.Initialized || second.Action != "sync" || second.ProjectPath != root {
		t.Fatalf("unexpected second CodeGraph setup: %#v", second)
	}

	wantCalls := []string{
		"codegraph status " + root + " --json",
		"codegraph init " + root,
		"codegraph status " + root + " --json",
		"codegraph status " + root + " --json",
		"codegraph sync " + root,
		"codegraph status " + root + " --json",
	}
	if strings.Join(runner.calls, "\n") != strings.Join(wantCalls, "\n") {
		t.Fatalf("CodeGraph calls = %#v, want %#v", runner.calls, wantCalls)
	}
}
