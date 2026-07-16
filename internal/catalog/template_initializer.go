package catalog

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

const (
	TemplateProjectTypeGeneral = "general"
	TemplateProjectTypeGo      = "go"
	TemplateProjectTypeUnity   = "unity"
)

type TemplateInitializationInput struct {
	TargetPath  string `json:"targetPath"`
	ProjectType string `json:"projectType"`
}

type TemplateInitializationWrite struct {
	RelativePath string `json:"relativePath"`
	SourcePath   string `json:"sourcePath,omitempty"`
	Action       string `json:"action"`
	Reason       string `json:"reason,omitempty"`
}

type TemplateInitializationSummary struct {
	Create    int `json:"create"`
	Unchanged int `json:"unchanged"`
	Conflict  int `json:"conflict"`
	Protected int `json:"protected"`
}

type TemplateInitializationPreview struct {
	PlanID      string                        `json:"planId,omitempty"`
	TargetPath  string                        `json:"targetPath"`
	ProjectType string                        `json:"projectType"`
	Summary     TemplateInitializationSummary `json:"summary"`
	Writes      []TemplateInitializationWrite `json:"writes"`
}

type TemplateInitializationResult struct {
	TargetPath  string                        `json:"targetPath"`
	ProjectType string                        `json:"projectType"`
	Summary     TemplateInitializationSummary `json:"summary"`
	Writes      []TemplateInitializationWrite `json:"writes"`
}

func PreviewTemplateInitialization(input TemplateInitializationInput) (TemplateInitializationPreview, error) {
	targetPath, projectType, err := validateTemplateInitializationInput(input)
	if err != nil {
		return TemplateInitializationPreview{}, err
	}

	templateRoot := resolveDataPath("templates")
	sourceFiles, err := initializationSourceFiles(templateRoot, projectType)
	if err != nil {
		return TemplateInitializationPreview{}, err
	}

	preview := TemplateInitializationPreview{
		TargetPath:  targetPath,
		ProjectType: projectType,
		Writes:      make([]TemplateInitializationWrite, 0, len(sourceFiles)+1),
	}
	for _, sourcePath := range sourceFiles {
		relativePath, err := filepath.Rel(templateRoot, sourcePath)
		if err != nil {
			return TemplateInitializationPreview{}, fmt.Errorf("derive template relative path: %w", err)
		}
		relativePath = filepath.ToSlash(relativePath)
		if isProtectedTemplateInitializationPath(relativePath) {
			preview.Writes = append(preview.Writes, TemplateInitializationWrite{
				RelativePath: relativePath,
				SourcePath:   filepath.ToSlash(sourcePath),
				Action:       "protected",
				Reason:       "KnowledgeBase/project is never initialized or overwritten.",
			})
			preview.Summary.Protected++
			continue
		}
		target, err := safeTemplateInitializationTarget(targetPath, relativePath)
		if err != nil {
			return TemplateInitializationPreview{}, err
		}
		action, reason, err := templateInitializationAction(sourcePath, target)
		if err != nil {
			return TemplateInitializationPreview{}, err
		}
		preview.Writes = append(preview.Writes, TemplateInitializationWrite{
			RelativePath: relativePath,
			SourcePath:   filepath.ToSlash(sourcePath),
			Action:       action,
			Reason:       reason,
		})
		switch action {
		case "create":
			preview.Summary.Create++
		case "unchanged":
			preview.Summary.Unchanged++
		case "conflict":
			preview.Summary.Conflict++
		}
	}
	preview.Writes = append(preview.Writes, TemplateInitializationWrite{
		RelativePath: "KnowledgeBase/project/",
		Action:       "protected",
		Reason:       "KnowledgeBase/project is never initialized or overwritten.",
	})
	preview.Summary.Protected++
	sort.Slice(preview.Writes, func(i, j int) bool {
		return preview.Writes[i].RelativePath < preview.Writes[j].RelativePath
	})
	return preview, nil
}

func ApplyTemplateInitialization(input TemplateInitializationInput) (TemplateInitializationResult, error) {
	preview, err := PreviewTemplateInitialization(input)
	if err != nil {
		return TemplateInitializationResult{}, err
	}
	if err := os.MkdirAll(preview.TargetPath, 0o755); err != nil {
		return TemplateInitializationResult{}, fmt.Errorf("create target root: %w", err)
	}
	result := TemplateInitializationResult{
		TargetPath:  preview.TargetPath,
		ProjectType: preview.ProjectType,
		Writes:      make([]TemplateInitializationWrite, 0, len(preview.Writes)),
	}
	for _, write := range preview.Writes {
		switch write.Action {
		case "protected", "unchanged", "conflict":
			result.Writes = append(result.Writes, write)
		case "create":
			target, err := safeTemplateInitializationTarget(preview.TargetPath, write.RelativePath)
			if err != nil {
				return TemplateInitializationResult{}, err
			}
			action, reason, err := templateInitializationAction(filepath.FromSlash(write.SourcePath), target)
			if err != nil {
				return TemplateInitializationResult{}, err
			}
			if action != "create" {
				write.Action = action
				write.Reason = reason
				result.Writes = append(result.Writes, write)
				continue
			}
			data, err := os.ReadFile(filepath.FromSlash(write.SourcePath))
			if err != nil {
				return TemplateInitializationResult{}, fmt.Errorf("read template source %s: %w", write.SourcePath, err)
			}
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return TemplateInitializationResult{}, fmt.Errorf("create target directory for %s: %w", write.RelativePath, err)
			}
			if err := os.WriteFile(target, data, 0o644); err != nil {
				return TemplateInitializationResult{}, fmt.Errorf("write template target %s: %w", write.RelativePath, err)
			}
			result.Writes = append(result.Writes, write)
		default:
			return TemplateInitializationResult{}, fmt.Errorf("unsupported initialization action %q", write.Action)
		}
	}
	for _, write := range result.Writes {
		switch write.Action {
		case "create":
			result.Summary.Create++
		case "unchanged":
			result.Summary.Unchanged++
		case "conflict":
			result.Summary.Conflict++
		case "protected":
			result.Summary.Protected++
		}
	}
	return result, nil
}

func validateTemplateInitializationInput(input TemplateInitializationInput) (string, string, error) {
	targetPath := strings.TrimSpace(input.TargetPath)
	if targetPath == "" {
		return "", "", fmt.Errorf("target path is empty")
	}
	absolutePath, err := filepath.Abs(targetPath)
	if err != nil {
		return "", "", fmt.Errorf("resolve target path: %w", err)
	}
	stat, err := os.Stat(absolutePath)
	if err != nil && !os.IsNotExist(err) {
		return "", "", fmt.Errorf("stat target path: %w", err)
	}
	if err == nil && !stat.IsDir() {
		return "", "", fmt.Errorf("target path %s is not a directory", absolutePath)
	}
	projectType := strings.ToLower(strings.TrimSpace(input.ProjectType))
	switch projectType {
	case TemplateProjectTypeGeneral, TemplateProjectTypeGo, TemplateProjectTypeUnity:
		return absolutePath, projectType, nil
	default:
		return "", "", fmt.Errorf("unsupported project type %q", input.ProjectType)
	}
}

func initializationSourceFiles(templateRoot string, projectType string) ([]string, error) {
	files := []string{}
	err := filepath.Walk(templateRoot, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info == nil || info.IsDir() {
			return nil
		}
		relativePath, err := filepath.Rel(templateRoot, path)
		if err != nil {
			return err
		}
		if includeTemplateInitializationPath(filepath.ToSlash(relativePath), projectType) && !isProtectedTemplateInitializationPath(filepath.ToSlash(relativePath)) {
			files = append(files, path)
		}
		return nil
	})
	if err != nil {
		return nil, fmt.Errorf("read template sources: %w", err)
	}
	sort.Strings(files)
	return files, nil
}

func includeTemplateInitializationPath(relativePath string, projectType string) bool {
	if relativePath == "AGENTS.md" ||
		strings.HasPrefix(relativePath, "KnowledgeBase/") ||
		strings.HasPrefix(relativePath, ".agents/skills/kb-") ||
		strings.HasPrefix(relativePath, ".agents/skills/nexus-") ||
		strings.HasPrefix(relativePath, ".claude/rules/01-communication.md") ||
		strings.HasPrefix(relativePath, ".claude/rules/knowledge-retrieval.md") {
		return true
	}
	if isSharedWorkflowInitializationPath(relativePath) {
		return true
	}
	switch projectType {
	case TemplateProjectTypeGo:
		return strings.HasPrefix(relativePath, ".claude/agents/go-") ||
			strings.HasPrefix(relativePath, ".codex/agents/go-") ||
			strings.HasPrefix(relativePath, ".claude/rules/go-") ||
			strings.HasPrefix(relativePath, ".claude/skills/go-") ||
			strings.HasPrefix(relativePath, ".claude/skills/review-feedback/") ||
			strings.HasPrefix(relativePath, ".agents/skills/wf-go-") ||
			strings.HasPrefix(relativePath, ".claude/commands/wf-go-") ||
			strings.HasPrefix(relativePath, ".claude/workflows/go-")
	case TemplateProjectTypeUnity:
		return strings.HasPrefix(relativePath, ".claude/agents/unity-") ||
			strings.HasPrefix(relativePath, ".codex/agents/unity-") ||
			strings.HasPrefix(relativePath, ".claude/rules/unity-") ||
			strings.HasPrefix(relativePath, ".claude/rules/uiarchitect/") ||
			strings.HasPrefix(relativePath, ".claude/skills/unity-") ||
			strings.HasPrefix(relativePath, ".claude/skills/review-feedback/") ||
			strings.HasPrefix(relativePath, ".agents/skills/wf-unity-") ||
			strings.HasPrefix(relativePath, ".claude/commands/wf-unity-") ||
			strings.HasPrefix(relativePath, ".claude/workflows/wf-unity-") ||
			strings.HasPrefix(relativePath, ".claude/workflows/unity-ui-quick.")
	default:
		return false
	}
}

func isSharedWorkflowInitializationPath(relativePath string) bool {
	return strings.HasPrefix(relativePath, ".agents/skills/wf-commit/") ||
		strings.HasPrefix(relativePath, ".agents/skills/wf-design/") ||
		strings.HasPrefix(relativePath, ".agents/skills/wf-research/") ||
		strings.HasPrefix(relativePath, ".agents/skills/wf-subagents/") ||
		strings.HasPrefix(relativePath, ".claude/commands/wf-commit.") ||
		strings.HasPrefix(relativePath, ".claude/commands/wf-design.") ||
		strings.HasPrefix(relativePath, ".claude/commands/wf-research.") ||
		strings.HasPrefix(relativePath, ".claude/commands/wf-subagents.") ||
		strings.HasPrefix(relativePath, ".claude/workflows/commit-gate.") ||
		strings.HasPrefix(relativePath, ".claude/workflows/design.") ||
		strings.HasPrefix(relativePath, ".claude/workflows/research.") ||
		strings.HasPrefix(relativePath, ".claude/workflows/subagent-driven-development.")
}

func isProtectedTemplateInitializationPath(relativePath string) bool {
	return relativePath == "KnowledgeBase/project" || strings.HasPrefix(relativePath, "KnowledgeBase/project/")
}

func safeTemplateInitializationTarget(targetRoot string, relativePath string) (string, error) {
	if filepath.IsAbs(relativePath) || isProtectedTemplateInitializationPath(filepath.ToSlash(relativePath)) {
		return "", fmt.Errorf("refusing protected or absolute initialization path %s", relativePath)
	}
	target := filepath.Join(targetRoot, filepath.FromSlash(relativePath))
	relative, err := filepath.Rel(targetRoot, target)
	if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("initialization path escapes target root: %s", relativePath)
	}
	return target, nil
}

func templateInitializationAction(sourcePath string, targetPath string) (string, string, error) {
	sourceData, err := os.ReadFile(sourcePath)
	if err != nil {
		return "", "", fmt.Errorf("read template source %s: %w", sourcePath, err)
	}
	targetData, err := os.ReadFile(targetPath)
	if os.IsNotExist(err) {
		return "create", "", nil
	}
	if err != nil {
		return "", "", fmt.Errorf("read target file %s: %w", targetPath, err)
	}
	if bytes.Equal(sourceData, targetData) {
		return "unchanged", "Target content already matches the template.", nil
	}
	return "conflict", "Target file exists with different content; it will not be overwritten.", nil
}
