package catalog

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

type templateSyncCounts struct {
	overwritten int
	created     int
	skipped     int
}

func (s *Store) SyncProjectTemplates(projectID string) (ProjectTemplateSyncResult, bool, error) {
	s.mu.RLock()
	project, ok := s.projectByIDLocked(projectID)
	s.mu.RUnlock()
	if !ok {
		return ProjectTemplateSyncResult{}, false, nil
	}

	projectRoot := projectLocalRoot(project)
	if projectRoot == "" {
		return ProjectTemplateSyncResult{}, true, fmt.Errorf("project %s has no local path", projectID)
	}
	if stat, err := os.Stat(projectRoot); err != nil {
		return ProjectTemplateSyncResult{}, true, err
	} else if !stat.IsDir() {
		return ProjectTemplateSyncResult{}, true, fmt.Errorf("project root %s is not a directory", projectRoot)
	}

	templateRoot, err := nexusTemplatesRoot()
	if err != nil {
		return ProjectTemplateSyncResult{}, true, err
	}
	counts, err := synchronizeTemplateTree(templateRoot, projectRoot)
	if err != nil {
		return ProjectTemplateSyncResult{}, true, err
	}

	rescanned, copies, ok, err := s.RescanProject(projectID)
	if err != nil {
		return ProjectTemplateSyncResult{}, true, err
	}
	if !ok {
		return ProjectTemplateSyncResult{}, false, nil
	}
	return ProjectTemplateSyncResult{
		Project:     rescanned,
		Copies:      copies,
		Overwritten: counts.overwritten,
		Created:     counts.created,
		Skipped:     counts.skipped,
	}, true, nil
}

func projectLocalRoot(project Project) string {
	if localPath := strings.TrimSpace(project.LocalPath); localPath != "" {
		return localPath
	}
	return strings.TrimSpace(project.Path)
}

func nexusTemplatesRoot() (string, error) {
	if configuredRoot := strings.TrimSpace(os.Getenv("NEXUS_TEMPLATES_ROOT")); configuredRoot != "" {
		stat, err := os.Stat(configuredRoot)
		if err != nil {
			return "", err
		}
		if !stat.IsDir() {
			return "", fmt.Errorf("configured Nexus templates path %s is not a directory", configuredRoot)
		}
		return configuredRoot, nil
	}

	current, err := os.Getwd()
	if err != nil {
		return "", err
	}
	for {
		candidate := filepath.Join(current, "templates")
		if stat, err := os.Stat(candidate); err == nil && stat.IsDir() {
			return candidate, nil
		}
		parent := filepath.Dir(current)
		if parent == current {
			return "", fmt.Errorf("could not locate Nexus Agents templates directory from %s", current)
		}
		current = parent
	}
}

func synchronizeTemplateTree(templateRoot string, projectRoot string) (templateSyncCounts, error) {
	var counts templateSyncCounts
	err := filepath.WalkDir(templateRoot, func(templatePath string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			return nil
		}
		if !entry.Type().IsRegular() {
			return nil
		}

		relativePath, err := filepath.Rel(templateRoot, templatePath)
		if err != nil {
			return err
		}
		relativePath = filepath.Clean(relativePath)
		if isProtectedProjectKnowledgePath(relativePath) {
			counts.skipped++
			return nil
		}
		destinationPath, err := safeProjectDestination(projectRoot, relativePath)
		if err != nil {
			return err
		}

		_, err = os.Stat(destinationPath)
		exists := err == nil
		if err != nil && !os.IsNotExist(err) {
			return err
		}
		content, err := os.ReadFile(templatePath)
		if err != nil {
			return err
		}
		if err := os.MkdirAll(filepath.Dir(destinationPath), 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(destinationPath, content, 0o644); err != nil {
			return err
		}
		if exists {
			counts.overwritten++
		} else {
			counts.created++
		}
		return nil
	})
	return counts, err
}

func isProtectedProjectKnowledgePath(relativePath string) bool {
	normalized := filepath.ToSlash(filepath.Clean(relativePath))
	return normalized == "KnowledgeBase/Setting.yaml" ||
		normalized == "KnowledgeBase/project" ||
		strings.HasPrefix(normalized, "KnowledgeBase/project/")
}

func safeProjectDestination(projectRoot string, relativePath string) (string, error) {
	if relativePath == "." || filepath.IsAbs(relativePath) || strings.HasPrefix(relativePath, ".."+string(filepath.Separator)) || relativePath == ".." {
		return "", fmt.Errorf("template path %q escapes project root", relativePath)
	}
	destinationPath := filepath.Join(projectRoot, relativePath)
	resolvedRelative, err := filepath.Rel(projectRoot, destinationPath)
	if err != nil {
		return "", err
	}
	if resolvedRelative == ".." || strings.HasPrefix(resolvedRelative, ".."+string(filepath.Separator)) || filepath.IsAbs(resolvedRelative) {
		return "", fmt.Errorf("template path %q escapes project root", relativePath)
	}
	return destinationPath, nil
}
