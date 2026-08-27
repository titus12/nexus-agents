package wikicompiler

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"sort"
	"strings"
	"time"
)

const DefaultOpenWikiVersion = "0.2.0"

type CommandResult struct {
	Stdout string
	Stderr string
}

type CommandRunner interface {
	Run(ctx context.Context, directory string, environment map[string]string, name string, args ...string) (CommandResult, error)
}

type ExecCommandRunner struct {
	MaxOutput int
}

func (r ExecCommandRunner) Run(ctx context.Context, directory string, environment map[string]string, name string, args ...string) (CommandResult, error) {
	command := exec.CommandContext(ctx, name, args...)
	command.Dir = directory
	command.Env = os.Environ()
	for key, value := range environment {
		command.Env = append(command.Env, key+"="+value)
	}
	limit := r.MaxOutput
	if limit <= 0 {
		limit = 1024 * 1024
	}
	stdout := &limitedBuffer{limit: limit}
	stderr := &limitedBuffer{limit: limit}
	command.Stdout = stdout
	command.Stderr = stderr
	err := command.Run()
	return CommandResult{Stdout: stdout.String(), Stderr: stderr.String()}, err
}

type OpenWiki struct {
	Runner     CommandRunner
	Workspaces *WorkspaceManager
	Version    string
	Timeout    time.Duration
}

func NewOpenWiki() *OpenWiki {
	return &OpenWiki{
		Runner: ExecCommandRunner{}, Workspaces: &WorkspaceManager{},
		Version: DefaultOpenWikiVersion, Timeout: 20 * time.Minute,
	}
}

func (o *OpenWiki) Initialize(ctx context.Context, input CompileInput) (GeneratedBundle, error) {
	return o.compile(ctx, input, false)
}

func (o *OpenWiki) Update(ctx context.Context, input CompileInput) (GeneratedBundle, error) {
	return o.compile(ctx, input, true)
}

func (o *OpenWiki) compile(ctx context.Context, input CompileInput, update bool) (GeneratedBundle, error) {
	if o.Runner == nil {
		o.Runner = ExecCommandRunner{}
	}
	if o.Workspaces == nil {
		o.Workspaces = &WorkspaceManager{}
	}
	expectedVersion := strings.TrimPrefix(strings.TrimSpace(o.Version), "v")
	if expectedVersion == "" {
		expectedVersion = DefaultOpenWikiVersion
	}
	checkCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
	versionResult, err := o.Runner.Run(checkCtx, input.ProjectRoot, nil, "openwiki", "--help")
	cancel()
	if err != nil {
		return GeneratedBundle{}, fmt.Errorf("OpenWiki is unavailable: %w: %s", err, redactOutput(versionResult.Stderr, input.Environment))
	}
	actualVersion := parseVersion(versionResult.Stdout + "\n" + versionResult.Stderr)
	if actualVersion != expectedVersion {
		return GeneratedBundle{}, fmt.Errorf("OpenWiki version mismatch: found %q, require %q", actualVersion, expectedVersion)
	}

	workspace, err := o.Workspaces.Prepare(ctx, WorkspaceInput{
		DataRoot: input.DataRoot, ProjectID: input.ProjectID, RunID: input.RunID,
		ProjectRoot: input.ProjectRoot, Revision: input.Revision, Manifest: input.ScanManifest,
	})
	if err != nil {
		return GeneratedBundle{}, err
	}
	retainWorkspace := strings.EqualFold(strings.TrimSpace(os.Getenv("NEXUS_RETAIN_KNOWLEDGE_WORKSPACES")), "true")
	if !retainWorkspace {
		defer o.Workspaces.Remove(workspace)
	}
	if len(input.ScanManifest) > 0 {
		if err := PruneToManifest(workspace.RepositoryRoot, input.ScanManifest); err != nil {
			return GeneratedBundle{}, err
		}
	}
	if err := ProjectExternalSources(workspace.RepositoryRoot, input.ExternalSources); err != nil {
		return GeneratedBundle{}, err
	}
	if update && len(input.ExistingKnowledge) > 0 {
		if err := SeedOpenWiki(workspace.RepositoryRoot, input.ExistingKnowledge); err != nil {
			return GeneratedBundle{}, err
		}
	}
	instructions := BuildInstructions(input)
	if err := WriteInstructions(workspace.RepositoryRoot, instructions); err != nil {
		return GeneratedBundle{}, err
	}

	timeout := o.Timeout
	if timeout <= 0 {
		timeout = 20 * time.Minute
	}
	runCtx, runCancel := context.WithTimeout(ctx, timeout)
	defer runCancel()
	runMode := "--init"
	if update {
		runMode = "--update"
	}
	result, runErr := o.Runner.Run(runCtx, workspace.RepositoryRoot, input.Environment, "openwiki", "code", runMode, "--print")
	runContextErr := runCtx.Err()
	if runErr != nil {
		if runContextErr == context.DeadlineExceeded {
			return GeneratedBundle{}, fmt.Errorf("OpenWiki timed out after %s", timeout)
		}
		return GeneratedBundle{}, fmt.Errorf("OpenWiki failed: %w: %s", runErr, commandFailureOutput(result, input.Environment))
	}
	files, warnings, err := ReadAndNormalizeOpenWiki(workspace.RepositoryRoot, input.KnowledgeRoot)
	if err != nil {
		return GeneratedBundle{}, err
	}
	thin := thinDomainPaths(files, input.KnowledgeRoot)
	aliasRepair := domainAliasRepairPaths(files, input.KnowledgeRoot)
	if len(thin) > 0 || len(aliasRepair) > 0 {
		repairResult, repairErr := o.Runner.Run(
			runCtx,
			workspace.RepositoryRoot,
			input.Environment,
			"openwiki",
			"code",
			"--update",
			"--print",
			domainRepairPrompt(thin, aliasRepair, input.Language),
		)
		if repairErr != nil {
			if runCtx.Err() == context.DeadlineExceeded {
				return GeneratedBundle{}, fmt.Errorf("OpenWiki timed out after %s while repairing Domain output", timeout)
			}
			return GeneratedBundle{}, fmt.Errorf("OpenWiki Domain repair failed: %w: %s", repairErr, commandFailureOutput(repairResult, input.Environment))
		}
		repairedFiles, repairWarnings, repairReadErr := ReadAndNormalizeOpenWiki(workspace.RepositoryRoot, input.KnowledgeRoot)
		if repairReadErr != nil {
			return GeneratedBundle{}, repairReadErr
		}
		if stillThin := thinDomainPaths(repairedFiles, input.KnowledgeRoot); len(stillThin) > 0 {
			return GeneratedBundle{}, fmt.Errorf("OpenWiki produced thin Domain pages after one repair pass: %s", strings.Join(stillThin, ", "))
		}
		if stillMissingAliases := domainAliasRepairPaths(repairedFiles, input.KnowledgeRoot); len(stillMissingAliases) > 0 {
			return GeneratedBundle{}, fmt.Errorf("OpenWiki produced Domain pages without bilingual routing aliases after one repair pass: %s", strings.Join(stillMissingAliases, ", "))
		}
		files = repairedFiles
		warnings = repairWarnings
		result.Stdout = strings.TrimSpace(result.Stdout + "\n" + repairResult.Stdout)
	}
	paths := make([]string, 0, len(files))
	for relative := range files {
		paths = append(paths, relative)
	}
	sort.Strings(paths)
	workspaceRoot := ""
	if retainWorkspace {
		workspaceRoot = workspace.Root
	}
	return GeneratedBundle{
		Files: files, Paths: paths, Compiler: "openwiki", Version: expectedVersion,
		WorkspaceRoot: workspaceRoot, Output: bounded(result.Stdout, 16000), Warnings: warnings,
	}, nil
}

func parseVersion(output string) string {
	for _, field := range strings.Fields(strings.TrimSpace(output)) {
		field = strings.TrimLeft(field, "vV")
		if field == "" || field[0] < '0' || field[0] > '9' {
			continue
		}
		return strings.TrimRight(field, ",;")
	}
	return ""
}

func redactOutput(output string, environment map[string]string) string {
	return bounded(redactSensitiveOutput(output, environment), 4096)
}

func redactSensitiveOutput(output string, environment map[string]string) string {
	for _, entry := range os.Environ() {
		key, value, ok := strings.Cut(entry, "=")
		if ok {
			output = redactEnvironmentValue(output, key, value)
		}
	}
	for key, value := range environment {
		output = redactEnvironmentValue(output, key, value)
	}
	return strings.TrimSpace(output)
}

func redactEnvironmentValue(output, key, value string) string {
	upper := strings.ToUpper(key)
	if value != "" && (strings.Contains(upper, "KEY") || strings.Contains(upper, "TOKEN") || strings.Contains(upper, "SECRET")) {
		return strings.ReplaceAll(output, value, "[REDACTED]")
	}
	return output
}

func commandFailureOutput(result CommandResult, environment map[string]string) string {
	var parts []string
	if stderr := strings.TrimSpace(result.Stderr); stderr != "" {
		parts = append(parts, "stderr: "+boundedTail(redactSensitiveOutput(stderr, environment), 2048))
	}
	if stdout := strings.TrimSpace(result.Stdout); stdout != "" {
		parts = append(parts, "stdout: "+boundedTail(redactSensitiveOutput(stdout, environment), 2048))
	}
	if len(parts) == 0 {
		return "no command output"
	}
	return strings.Join(parts, "\n")
}

func bounded(value string, max int) string {
	value = strings.TrimSpace(value)
	if max <= 0 || len(value) <= max {
		return value
	}
	return value[:max] + "…"
}

func boundedTail(value string, max int) string {
	value = strings.TrimSpace(value)
	if max <= 0 || len(value) <= max {
		return value
	}
	return "…" + value[len(value)-max:]
}

type limitedBuffer struct {
	buffer bytes.Buffer
	limit  int
}

func (b *limitedBuffer) Write(data []byte) (int, error) {
	original := len(data)
	if b.buffer.Len() < b.limit {
		remaining := b.limit - b.buffer.Len()
		if len(data) > remaining {
			data = data[:remaining]
		}
		_, _ = b.buffer.Write(data)
	}
	return original, nil
}

func (b *limitedBuffer) String() string {
	return b.buffer.String()
}
