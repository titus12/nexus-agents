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

type UserProjectRecord struct {
	ProjectID     string `json:"projectId"`
	ProjectName   string `json:"projectName"`
	Path          string `json:"path"`
	RepoKey       string `json:"repoKey"`
	ImportedAt    string `json:"importedAt"`
	LastScannedAt string `json:"lastScannedAt"`
}

type UserProjectIndex struct {
	Version  int                 `json:"version"`
	Projects []UserProjectRecord `json:"projects"`
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
	return localPath, detectRepoKey(localPath, name), nil
}

func readUserProjectIndex() (UserProjectIndex, bool) {
	var data []byte
	for _, candidate := range []string{userNexusPath(), legacyUserNexusPath()} {
		stat, err := os.Stat(candidate)
		if err != nil || stat.IsDir() {
			continue
		}
		data, err = os.ReadFile(candidate)
		if err == nil {
			break
		}
	}
	if len(data) == 0 {
		return UserProjectIndex{}, false
	}
	var index UserProjectIndex
	if err := json.Unmarshal(stripUTF8BOM(data), &index); err != nil {
		return UserProjectIndex{}, false
	}
	if index.Version == 0 {
		index.Version = 1
	}
	if index.Projects == nil {
		index.Projects = []UserProjectRecord{}
	}
	return index, true
}

func writeUserProjectIndex(index UserProjectIndex) error {
	if index.Version == 0 {
		index.Version = 1
	}
	if len(index.Projects) == 0 {
		if err := os.Remove(userNexusPath()); err != nil && !os.IsNotExist(err) {
			return fmt.Errorf("remove user project index: %w", err)
		}
		_ = os.Remove(userNexusRoot())
		return nil
	}
	data, err := json.MarshalIndent(index, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal user project index: %w", err)
	}
	data = append(data, '\n')
	if err := prepareUserNexusDirectory(); err != nil {
		return err
	}
	if err := os.WriteFile(userNexusPath(), data, 0o644); err != nil {
		return fmt.Errorf("write user project index: %w", err)
	}
	return nil
}

func prepareUserNexusDirectory() error {
	root := userNexusRoot()
	stat, err := os.Stat(root)
	if os.IsNotExist(err) {
		if err := os.MkdirAll(root, 0o755); err != nil {
			return fmt.Errorf("create user .nexus directory: %w", err)
		}
		return nil
	}
	if err != nil {
		return fmt.Errorf("stat user .nexus path: %w", err)
	}
	if stat.IsDir() {
		return nil
	}

	legacyPath := root + ".projects-legacy.json"
	if err := os.Remove(legacyPath); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("remove stale legacy project index: %w", err)
	}
	if err := os.Rename(root, legacyPath); err != nil {
		return fmt.Errorf("preserve legacy user .nexus project index: %w", err)
	}
	if err := os.MkdirAll(root, 0o755); err != nil {
		_ = os.Rename(legacyPath, root)
		return fmt.Errorf("create user .nexus directory: %w", err)
	}
	return nil
}

func upsertUserProjectRecord(record UserProjectRecord) error {
	index, _ := readUserProjectIndex()
	now := time.Now().Format(time.RFC3339)
	if record.ImportedAt == "" {
		record.ImportedAt = now
	}
	if record.LastScannedAt == "" {
		record.LastScannedAt = now
	}
	replaced := false
	for i := range index.Projects {
		if sameProjectPath(index.Projects[i].Path, record.Path) {
			if record.ImportedAt == "" {
				record.ImportedAt = index.Projects[i].ImportedAt
			}
			index.Projects[i] = record
			replaced = true
			break
		}
	}
	if !replaced {
		index.Projects = append(index.Projects, record)
	}
	return writeUserProjectIndex(index)
}

func removeUserProjectRecord(projectPath string) error {
	index, ok := readUserProjectIndex()
	if !ok {
		return nil
	}
	filtered := index.Projects[:0]
	for _, item := range index.Projects {
		if sameProjectPath(item.Path, projectPath) {
			continue
		}
		filtered = append(filtered, item)
	}
	index.Projects = filtered
	return writeUserProjectIndex(index)
}

func restoreProjectsFromUserIndex(library TemplateLibrary) ([]Project, map[string][]ProjectCopy) {
	index, ok := readUserProjectIndex()
	if !ok || len(index.Projects) == 0 {
		return nil, map[string][]ProjectCopy{}
	}
	projects := make([]Project, 0, len(index.Projects))
	configSets := make(map[string][]ProjectCopy, len(index.Projects))
	filtered := make([]UserProjectRecord, 0, len(index.Projects))
	for _, record := range index.Projects {
		localPath := strings.TrimSpace(record.Path)
		if localPath == "" {
			continue
		}
		if stat, err := os.Stat(localPath); err != nil || !stat.IsDir() {
			continue
		}
		copies, err := scanProjectConfigSetForProject(projectTokenFromID(record.ProjectID), localPath, library)
		if err != nil {
			continue
		}
		project := Project{
			ID:            record.ProjectID,
			Name:          record.ProjectName,
			Path:          localPath,
			Status:        "draft",
			UpdatedAt:     latestProjectConfigStamp(localPath),
			ConfigSummary: summarizeProjectCopies(copies),
			RepoKey:       record.RepoKey,
			LocalPath:     localPath,
		}
		projects = append(projects, project)
		configSets[project.ID] = copies
		record.LastScannedAt = time.Now().Format(time.RFC3339)
		filtered = append(filtered, record)
	}
	index.Projects = filtered
	_ = writeUserProjectIndex(index)
	return projects, configSets
}

func userNexusPath() string {
	return filepath.Join(userNexusRoot(), "projects.json")
}

func legacyUserNexusPath() string {
	return userNexusRoot()
}

func userNexusRoot() string {
	home, err := os.UserHomeDir()
	if err != nil || strings.TrimSpace(home) == "" {
		return ".nexus"
	}
	return filepath.Join(home, ".nexus")
}

func sameProjectPath(left string, right string) bool {
	return filepath.Clean(left) == filepath.Clean(right)
}

func projectTokenFromID(projectID string) string {
	if projectID == "" {
		return "project"
	}
	return strings.ReplaceAll(slugify(projectID), "-", "_")
}

func stripUTF8BOM(data []byte) []byte {
	return bytes.TrimPrefix(data, []byte{0xef, 0xbb, 0xbf})
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
	return value
}
