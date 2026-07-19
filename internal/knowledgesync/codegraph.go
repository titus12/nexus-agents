package knowledgesync

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

type CodeGraphCommandRunner interface {
	Run(ctx context.Context, args ...string) ([]byte, error)
}

type ExecCodeGraphRunner struct{}

func (ExecCodeGraphRunner) Run(ctx context.Context, args ...string) ([]byte, error) {
	command := exec.CommandContext(ctx, "codegraph", args...)
	var stdout, stderr bytes.Buffer
	command.Stdout = &stdout
	command.Stderr = &stderr
	if err := command.Run(); err != nil {
		return stdout.Bytes(), fmt.Errorf("codegraph %s: %w: %s", strings.Join(args, " "), err, boundedText(stderr.String(), 2000))
	}
	return stdout.Bytes(), nil
}

type CodeGraphCLI struct {
	Runner  CodeGraphCommandRunner
	Timeout time.Duration
}

func NewCodeGraphCLI() CodeGraphClient {
	if _, err := exec.LookPath("codegraph"); err != nil {
		return nil
	}
	return &CodeGraphCLI{Runner: ExecCodeGraphRunner{}, Timeout: 30 * time.Second}
}

func (c *CodeGraphCLI) Summary(projectRoot string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), c.timeout())
	defer cancel()
	data, err := c.runner().Run(ctx, "status", projectRoot, "--json")
	if err != nil {
		return "", err
	}
	if !json.Valid(data) {
		return "", fmt.Errorf("codegraph status returned invalid JSON")
	}
	return boundedText(string(data), 12000), nil
}

func (c *CodeGraphCLI) Impact(projectRoot string, changedPaths []string, depth int) (CodeGraphImpact, error) {
	if len(changedPaths) == 0 {
		return CodeGraphImpact{}, nil
	}
	if len(changedPaths) > 100 {
		changedPaths = changedPaths[:100]
	}
	if depth < 1 {
		depth = 1
	}
	ctx, cancel := context.WithTimeout(context.Background(), c.timeout())
	defer cancel()
	args := []string{"affected"}
	args = append(args, changedPaths...)
	args = append(args, "-p", projectRoot, "-d", strconv.Itoa(depth), "-j")
	data, err := c.runner().Run(ctx, args...)
	if err != nil {
		return CodeGraphImpact{}, err
	}
	var response struct {
		ChangedFiles  []string `json:"changedFiles"`
		AffectedTests []string `json:"affectedTests"`
	}
	if err := json.Unmarshal(data, &response); err != nil {
		return CodeGraphImpact{}, fmt.Errorf("decode codegraph affected response: %w", err)
	}
	return CodeGraphImpact{Tests: uniqueSorted(response.AffectedTests)}, nil
}

func (c *CodeGraphCLI) timeout() time.Duration {
	if c.Timeout <= 0 {
		return 30 * time.Second
	}
	return c.Timeout
}

func (c *CodeGraphCLI) runner() CodeGraphCommandRunner {
	if c.Runner == nil {
		return ExecCodeGraphRunner{}
	}
	return c.Runner
}
