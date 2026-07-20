package knowledgesync

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

var ErrGitBaseMissing = errors.New("git base revision is missing")

type GitRunner interface {
	Run(ctx context.Context, projectRoot string, args ...string) ([]byte, error)
}

type ExecGitRunner struct{}

func (ExecGitRunner) Run(ctx context.Context, projectRoot string, args ...string) ([]byte, error) {
	commandArgs := append([]string{"-C", projectRoot}, args...)
	command := exec.CommandContext(ctx, "git", commandArgs...)
	var stdout, stderr bytes.Buffer
	command.Stdout = &stdout
	command.Stderr = &stderr
	err := command.Run()
	if err != nil {
		return stdout.Bytes(), fmt.Errorf("git %s: %w: %s", strings.Join(args, " "), err, boundedText(stderr.String(), 4096))
	}
	return stdout.Bytes(), nil
}

type GitState struct {
	Branch string `json:"branch"`
	Head   string `json:"head"`
}

type GitChange struct {
	Status  string `json:"status"`
	Path    string `json:"path"`
	OldPath string `json:"oldPath,omitempty"`
}

type GitFile struct {
	Path string
	Size int64
}

func ReadGitState(ctx context.Context, runner GitRunner, projectRoot string) (GitState, error) {
	if runner == nil {
		runner = ExecGitRunner{}
	}
	headData, err := runner.Run(ctx, projectRoot, "rev-parse", "HEAD")
	if err != nil {
		return GitState{}, err
	}
	branchData, err := runner.Run(ctx, projectRoot, "branch", "--show-current")
	if err != nil {
		return GitState{}, err
	}
	return GitState{Branch: strings.TrimSpace(string(branchData)), Head: strings.TrimSpace(string(headData))}, nil
}

func ListTrackedFiles(ctx context.Context, runner GitRunner, projectRoot string, limit int) ([]string, error) {
	if runner == nil {
		runner = ExecGitRunner{}
	}
	data, err := runner.Run(ctx, projectRoot, "ls-files", "-z")
	if err != nil {
		return nil, err
	}
	parts := bytes.Split(data, []byte{0})
	files := make([]string, 0, len(parts))
	for _, part := range parts {
		value := normalizeRelativePath(string(part))
		if value == "" || IsHardExcluded(value) {
			continue
		}
		files = append(files, value)
		if limit > 0 && len(files) >= limit {
			break
		}
	}
	return files, nil
}

func ListCommittedFiles(ctx context.Context, runner GitRunner, projectRoot, revision string, limit int) ([]GitFile, error) {
	if runner == nil {
		runner = ExecGitRunner{}
	}
	if strings.TrimSpace(revision) == "" {
		revision = "HEAD"
	}
	data, err := runner.Run(ctx, projectRoot, "ls-tree", "-r", "-l", "-z", revision)
	if err != nil {
		return nil, err
	}
	records := bytes.Split(data, []byte{0})
	files := make([]GitFile, 0, len(records))
	for _, record := range records {
		if len(record) == 0 {
			continue
		}
		metadata, filePath, ok := bytes.Cut(record, []byte{'\t'})
		if !ok {
			continue
		}
		fields := strings.Fields(string(metadata))
		if len(fields) < 4 || fields[1] != "blob" {
			continue
		}
		relative := normalizeRelativePath(string(filePath))
		if relative == "" || IsHardExcluded(relative) {
			continue
		}
		size, parseErr := strconv.ParseInt(fields[3], 10, 64)
		if parseErr != nil {
			continue
		}
		files = append(files, GitFile{Path: relative, Size: size})
		if limit > 0 && len(files) >= limit {
			break
		}
	}
	return files, nil
}

func DiffCommitted(ctx context.Context, runner GitRunner, projectRoot, base, target string) ([]GitChange, error) {
	if strings.TrimSpace(base) == "" {
		return nil, ErrGitBaseMissing
	}
	if runner == nil {
		runner = ExecGitRunner{}
	}
	if target == "" {
		target = "HEAD"
	}
	if _, err := runner.Run(ctx, projectRoot, "merge-base", "--is-ancestor", base, target); err != nil {
		return nil, ErrGitBaseMissing
	}
	data, err := runner.Run(ctx, projectRoot, "diff", "--name-status", "-z", "--find-renames", base+".."+target)
	if err != nil {
		return nil, err
	}
	return parseNameStatusZ(data)
}

func parseNameStatusZ(data []byte) ([]GitChange, error) {
	fields := bytes.Split(data, []byte{0})
	var changes []GitChange
	for index := 0; index < len(fields); {
		if len(fields[index]) == 0 {
			index++
			continue
		}
		status := string(fields[index])
		index++
		if index >= len(fields) {
			return nil, fmt.Errorf("incomplete git name-status output")
		}
		if strings.HasPrefix(status, "R") || strings.HasPrefix(status, "C") {
			if index+1 >= len(fields) {
				return nil, fmt.Errorf("incomplete git rename output")
			}
			oldPath := normalizeRelativePath(string(fields[index]))
			newPath := normalizeRelativePath(string(fields[index+1]))
			index += 2
			changes = append(changes, GitChange{Status: status, OldPath: oldPath, Path: newPath})
			continue
		}
		changes = append(changes, GitChange{Status: status, Path: normalizeRelativePath(string(fields[index]))})
		index++
	}
	return changes, nil
}

func gitContext() (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), 15*time.Second)
}
