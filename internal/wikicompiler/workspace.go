package wikicompiler

import (
	"archive/tar"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

type WorkspaceInput struct {
	DataRoot    string
	ProjectID   string
	RunID       string
	ProjectRoot string
	Revision    string
	Manifest    []string
}

type Workspace struct {
	Root           string
	RepositoryRoot string
}

type WorkspaceManager struct{}

func (m *WorkspaceManager) Prepare(ctx context.Context, input WorkspaceInput) (Workspace, error) {
	dataRoot, err := filepath.Abs(input.DataRoot)
	if err != nil {
		return Workspace{}, err
	}
	if strings.TrimSpace(input.ProjectID) == "" || strings.TrimSpace(input.RunID) == "" {
		return Workspace{}, fmt.Errorf("workspace project and run ids are required")
	}
	root := filepath.Join(dataRoot, safeWorkspaceID(input.ProjectID), "workspaces", safeWorkspaceID(input.RunID))
	if err := ensureBelow(dataRoot, root); err != nil {
		return Workspace{}, err
	}
	if err := os.RemoveAll(root); err != nil {
		return Workspace{}, err
	}
	repositoryRoot := filepath.Join(root, "repository")
	if err := os.MkdirAll(repositoryRoot, 0o755); err != nil {
		return Workspace{}, err
	}
	archivePath := filepath.Join(root, "repository.tar")
	revision := strings.TrimSpace(input.Revision)
	if revision == "" {
		revision = "HEAD"
	}
	args := []string{"-C", input.ProjectRoot, "archive", "--format=tar", "-o", archivePath, revision}
	if len(input.Manifest) > 0 {
		args = append(args, "--")
		args = append(args, input.Manifest...)
	}
	command := exec.CommandContext(ctx, "git", args...)
	if output, err := command.CombinedOutput(); err != nil {
		return Workspace{}, fmt.Errorf("create isolated Git snapshot: %w: %s", err, bounded(string(output), 4096))
	}
	if err := extractTarSecure(archivePath, repositoryRoot); err != nil {
		return Workspace{}, err
	}
	_ = os.Remove(archivePath)
	return Workspace{Root: root, RepositoryRoot: repositoryRoot}, nil
}

func (m *WorkspaceManager) Remove(workspace Workspace) error {
	if strings.TrimSpace(workspace.Root) == "" {
		return nil
	}
	return os.RemoveAll(workspace.Root)
}

func SeedOpenWiki(repositoryRoot string, existing map[string][]byte) error {
	openWikiRoot := filepath.Join(repositoryRoot, "openwiki")
	for relative, data := range existing {
		normalized := strings.TrimPrefix(strings.ReplaceAll(relative, "\\", "/"), "KnowledgeBase/project/")
		if normalized == relative || normalized == "" || normalized == "." || strings.HasPrefix(normalized, "../") {
			continue
		}
		target := filepath.Join(openWikiRoot, filepath.FromSlash(normalized))
		if err := ensureBelow(openWikiRoot, target); err != nil {
			return err
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(target, data, 0o644); err != nil {
			return err
		}
	}
	return nil
}

func PruneToManifest(repositoryRoot string, manifest []string) error {
	allowed := map[string]bool{}
	for _, relative := range manifest {
		relative = filepath.ToSlash(filepath.Clean(filepath.FromSlash(relative)))
		if relative != "." && !strings.HasPrefix(relative, "../") {
			allowed[relative] = true
		}
	}
	var removals []string
	err := filepath.WalkDir(repositoryRoot, func(filePath string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			return nil
		}
		relative, err := filepath.Rel(repositoryRoot, filePath)
		if err != nil {
			return err
		}
		relative = filepath.ToSlash(relative)
		if !allowed[relative] {
			removals = append(removals, filePath)
		}
		return nil
	})
	if err != nil {
		return err
	}
	for _, target := range removals {
		if err := os.Remove(target); err != nil && !os.IsNotExist(err) {
			return err
		}
	}
	return nil
}

func ProjectExternalSources(repositoryRoot string, sources []ExternalSource) error {
	if len(sources) == 0 {
		return nil
	}
	root := filepath.Join(repositoryRoot, ".nexus-external")
	if err := os.MkdirAll(root, 0o700); err != nil {
		return err
	}
	for index := range sources {
		source := &sources[index]
		if strings.TrimSpace(source.LocalPath) == "" {
			continue
		}
		data, err := os.ReadFile(source.LocalPath)
		if err != nil {
			return err
		}
		name := fmt.Sprintf("%02d-%s.md", index+1, safeWorkspaceID(source.Title))
		target := filepath.Join(root, name)
		if err := os.WriteFile(target, data, 0o600); err != nil {
			return err
		}
		source.LocalPath = filepath.ToSlash(filepath.Join(".nexus-external", name))
	}
	return nil
}

func extractTarSecure(archivePath, destination string) error {
	file, err := os.Open(archivePath)
	if err != nil {
		return err
	}
	defer file.Close()
	reader := tar.NewReader(file)
	for {
		header, err := reader.Next()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		name := filepath.Clean(filepath.FromSlash(header.Name))
		if name == "." || filepath.IsAbs(name) || name == ".." || strings.HasPrefix(name, ".."+string(filepath.Separator)) {
			return fmt.Errorf("Git archive contains unsafe path %q", header.Name)
		}
		target := filepath.Join(destination, name)
		if err := ensureBelow(destination, target); err != nil {
			return err
		}
		switch header.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
		case tar.TypeReg, tar.TypeRegA:
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			file, err := os.OpenFile(target, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, os.FileMode(header.Mode)&0o755)
			if err != nil {
				return err
			}
			if _, err := io.Copy(file, reader); err != nil {
				_ = file.Close()
				return err
			}
			if err := file.Close(); err != nil {
				return err
			}
		case tar.TypeSymlink, tar.TypeLink:
			return fmt.Errorf("Git archive links are not supported in isolated workspaces: %s", header.Name)
		}
	}
}

func ensureBelow(root, target string) error {
	absoluteRoot, err := filepath.Abs(root)
	if err != nil {
		return err
	}
	absoluteTarget, err := filepath.Abs(target)
	if err != nil {
		return err
	}
	relative, err := filepath.Rel(absoluteRoot, absoluteTarget)
	if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) || filepath.IsAbs(relative) {
		return fmt.Errorf("path escapes workspace root: %s", target)
	}
	return nil
}

func safeWorkspaceID(value string) string {
	var builder strings.Builder
	for _, r := range strings.ToLower(strings.TrimSpace(value)) {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || r == '-' || r == '_' {
			builder.WriteRune(r)
		} else {
			builder.WriteByte('_')
		}
	}
	if builder.Len() == 0 {
		return "run"
	}
	return builder.String()
}
