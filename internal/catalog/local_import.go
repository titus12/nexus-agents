package catalog

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type ProjectLocalMetadata struct {
	SchemaVersion int    `json:"schemaVersion"`
	ProjectID     string `json:"projectId"`
	ProjectName   string `json:"projectName"`
	RepoKey       string `json:"repoKey"`
	LocalPath     string `json:"localPath"`
	ImportedAt    string `json:"importedAt"`
	LastScannedAt string `json:"lastScannedAt"`
}

func prepareImportedProject(name string, projectPath string) (string, string, error) {
	if projectPath == "" {
		return "", "", fmt.Errorf("project path is empty")
	}
	localPath, err := filepath.Abs(projectPath)
	if err != nil {
		return "", "", fmt.Errorf("resolve project path: %w", err)
	}
	stat, err := os.Stat(localPath)
	if err != nil {
		return "", "", fmt.Errorf("stat project path: %w", err)
	}
	if !stat.IsDir() {
		return "", "", fmt.Errorf("project path %s is not a directory", localPath)
	}
	if err := migrateLegacyNexusDirectory(localPath); err != nil {
		return "", "", err
	}
	return localPath, detectRepoKey(localPath, name), nil
}

func writeProjectLocalMetadata(projectRoot string, metadata ProjectLocalMetadata) error {
	now := time.Now().Format(time.RFC3339)
	existing, _ := readProjectLocalMetadata(projectRoot)
	if metadata.ImportedAt == "" {
		metadata.ImportedAt = existing.ImportedAt
	}
	if metadata.ImportedAt == "" {
		metadata.ImportedAt = now
	}
	metadata.LastScannedAt = now

	data, err := json.MarshalIndent(metadata, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal .nexus metadata: %w", err)
	}
	data = append(data, '\n')
	if err := os.WriteFile(filepath.Join(projectRoot, ".nexus"), data, 0o644); err != nil {
		return fmt.Errorf("write .nexus metadata: %w", err)
	}
	return nil
}

func readProjectLocalMetadata(projectRoot string) (ProjectLocalMetadata, bool) {
	data, err := os.ReadFile(filepath.Join(projectRoot, ".nexus"))
	if err != nil {
		return ProjectLocalMetadata{}, false
	}
	var metadata ProjectLocalMetadata
	if err := json.Unmarshal(stripUTF8BOM(data), &metadata); err != nil {
		return ProjectLocalMetadata{}, false
	}
	return metadata, true
}

func stripUTF8BOM(data []byte) []byte {
	return bytes.TrimPrefix(data, []byte{0xef, 0xbb, 0xbf})
}

func applyProjectLocalMetadata(project Project) Project {
	if strings.TrimSpace(project.Path) == "" {
		return project
	}
	metadata, hasMetadata := readProjectLocalMetadata(project.Path)
	if hasMetadata {
		if strings.TrimSpace(metadata.RepoKey) != "" {
			project.RepoKey = metadata.RepoKey
		}
		if strings.TrimSpace(metadata.LocalPath) != "" {
			project.LocalPath = metadata.LocalPath
		}
	}
	if stat, err := os.Stat(filepath.Join(project.Path, ".nexus")); err == nil && !stat.IsDir() {
		project.LocalConfigPath = ".nexus"
		project.LocalConfigIgnored = gitIgnoreRulePresent(project.Path, ".nexus")
	}
	return project
}

func removeProjectLocalMetadata(project Project) error {
	projectRoot := strings.TrimSpace(project.LocalPath)
	if projectRoot == "" {
		projectRoot = strings.TrimSpace(project.Path)
	}
	if projectRoot == "" {
		return nil
	}

	nexusPath := filepath.Join(projectRoot, ".nexus")
	stat, err := os.Stat(nexusPath)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("stat .nexus metadata: %w", err)
	}
	if stat.IsDir() {
		return fmt.Errorf(".nexus is a directory; Nexus will not recursively remove it")
	}
	if err := os.Remove(nexusPath); err != nil {
		return fmt.Errorf("remove .nexus metadata: %w", err)
	}
	return nil
}

func gitIgnoreRulePresent(projectRoot string, rule string) bool {
	data, err := os.ReadFile(filepath.Join(projectRoot, ".gitignore"))
	if err != nil {
		return false
	}
	for _, line := range strings.Split(string(data), "\n") {
		if strings.TrimSpace(line) == rule {
			return true
		}
	}
	return false
}

func ensureGitIgnoreRule(projectRoot string, rule string) (bool, error) {
	gitignorePath := filepath.Join(projectRoot, ".gitignore")
	data, err := os.ReadFile(gitignorePath)
	if err != nil && !os.IsNotExist(err) {
		return false, fmt.Errorf("read .gitignore: %w", err)
	}
	content := string(data)
	for _, line := range strings.Split(content, "\n") {
		if strings.TrimSpace(line) == rule {
			return true, nil
		}
	}
	if content != "" && !strings.HasSuffix(content, "\n") {
		content += "\n"
	}
	content += rule + "\n"
	if err := os.WriteFile(gitignorePath, []byte(content), 0o644); err != nil {
		return false, fmt.Errorf("write .gitignore: %w", err)
	}
	return true, nil
}

func migrateLegacyNexusDirectory(projectRoot string) error {
	nexusPath := filepath.Join(projectRoot, ".nexus")
	stat, err := os.Stat(nexusPath)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("stat .nexus: %w", err)
	}
	if !stat.IsDir() {
		return nil
	}

	entries, err := os.ReadDir(nexusPath)
	if err != nil {
		return fmt.Errorf("read legacy .nexus directory: %w", err)
	}
	for _, entry := range entries {
		if entry.Name() != "workflows" || !entry.IsDir() {
			return fmt.Errorf("legacy .nexus directory contains unknown entry %s; move it before importing", entry.Name())
		}
	}

	legacyWorkflowDir := filepath.Join(nexusPath, "workflows")
	if _, err := os.Stat(legacyWorkflowDir); err == nil {
		files, err := os.ReadDir(legacyWorkflowDir)
		if err != nil {
			return fmt.Errorf("read legacy workflow graphs: %w", err)
		}
		targetDir := filepath.Join(projectRoot, ".claude", "workflows")
		if err := os.MkdirAll(targetDir, 0o755); err != nil {
			return fmt.Errorf("create workflow directory: %w", err)
		}
		for _, file := range files {
			if file.IsDir() || !strings.HasSuffix(strings.ToLower(file.Name()), ".json") {
				return fmt.Errorf("legacy .nexus/workflows contains unknown entry %s; move it before importing", file.Name())
			}
			stem := strings.TrimSuffix(file.Name(), filepath.Ext(file.Name()))
			source := filepath.Join(legacyWorkflowDir, file.Name())
			target := filepath.Join(targetDir, stem+".graph.json")
			data, err := os.ReadFile(source)
			if err != nil {
				return fmt.Errorf("read legacy workflow graph %s: %w", file.Name(), err)
			}
			if err := os.WriteFile(target, data, 0o644); err != nil {
				return fmt.Errorf("write migrated workflow graph %s: %w", target, err)
			}
			if err := os.Remove(source); err != nil {
				return fmt.Errorf("remove legacy workflow graph %s: %w", source, err)
			}
		}
		if err := os.Remove(legacyWorkflowDir); err != nil {
			return fmt.Errorf("remove legacy workflow directory: %w", err)
		}
	}
	if err := os.Remove(nexusPath); err != nil {
		return fmt.Errorf("remove legacy .nexus directory: %w", err)
	}
	return nil
}

func detectRepoKey(projectRoot string, fallbackName string) string {
	if remote := readGitOriginURL(projectRoot); remote != "" {
		return normalizeRepoKey(remote)
	}
	if fallbackName == "" {
		fallbackName = baseName(projectRoot)
	}
	return "local:" + slugify(fallbackName)
}

func readGitOriginURL(projectRoot string) string {
	data, err := os.ReadFile(filepath.Join(projectRoot, ".git", "config"))
	if err != nil {
		return ""
	}
	inOrigin := false
	for _, line := range strings.Split(string(data), "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "[") {
			inOrigin = strings.EqualFold(trimmed, `[remote "origin"]`)
			continue
		}
		if inOrigin && strings.HasPrefix(trimmed, "url") {
			parts := strings.SplitN(trimmed, "=", 2)
			if len(parts) == 2 {
				return strings.TrimSpace(parts[1])
			}
		}
	}
	return ""
}

func normalizeRepoKey(remote string) string {
	value := strings.TrimSpace(remote)
	value = strings.TrimSuffix(value, ".git")
	value = strings.TrimPrefix(value, "git@")
	value = strings.ReplaceAll(value, ":", "/")
	value = strings.TrimPrefix(value, "https://")
	value = strings.TrimPrefix(value, "http://")
	return strings.ToLower(value)
}

func projectTokenFromID(projectID string) string {
	if strings.HasSuffix(projectID, "-game-server") {
		return strings.TrimSuffix(projectID, "-game-server")
	}
	return slugify(projectID)
}
