package catalog

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

func ScanProjectConfigSet(projectRoot string, library TemplateLibrary) ([]ProjectCopy, error) {
	return scanProjectConfigSetForProject("project", projectRoot, library)
}

func scanProjectConfigSetForProject(projectToken string, projectRoot string, library TemplateLibrary) ([]ProjectCopy, error) {
	if projectRoot == "" {
		return nil, fmt.Errorf("project root is empty")
	}
	stat, err := os.Stat(projectRoot)
	if err != nil {
		return nil, err
	}
	if !stat.IsDir() {
		return nil, fmt.Errorf("project root %s is not a directory", projectRoot)
	}

	var copies []ProjectCopy
	copies = append(copies, scanAgentCopies(projectToken, projectRoot, library.Agents)...)
	copies = append(copies, scanMarkdownCopies(projectToken, projectRoot, "rule", ".claude/rules", library.Rules)...)
	copies = append(copies, scanMarkdownCopies(projectToken, projectRoot, "skill", ".claude/skills", library.Skills)...)
	copies = append(copies, scanWorkflowCopies(projectToken, projectRoot, library.Workflows)...)

	sort.SliceStable(copies, func(left, right int) bool {
		if copies[left].Kind != copies[right].Kind {
			return copies[left].Kind < copies[right].Kind
		}
		return copies[left].Name < copies[right].Name
	})
	return copies, nil
}

func scanAgentCopies(projectToken string, projectRoot string, templates []TemplateItem) []ProjectCopy {
	claudeFiles := markdownFilesByID(filepath.Join(projectRoot, ".claude", "agents"))
	codexFiles := filesByID(filepath.Join(projectRoot, ".codex", "agents"), ".toml")
	ids := make(map[string]bool, len(claudeFiles)+len(codexFiles))
	for id := range claudeFiles {
		ids[id] = true
	}
	for id := range codexFiles {
		ids[id] = true
	}

	templateByID := templateItemsByID(templates)
	candidates := make(map[string]projectCopyCandidate, len(ids))
	for _, id := range sortedKeys(ids) {
		paths := []string{}
		if path := claudeFiles[id]; path != "" {
			paths = append(paths, path)
		}
		if path := codexFiles[id]; path != "" {
			paths = append(paths, path)
		}
		template, ok := templateByID[id]
		copy := projectCopyFromScan(projectToken, "agent", id, displayProjectPath(projectRoot, paths[0]), paths, template, ok)
		addProjectCopyCandidate(candidates, id, template, ok, copy)
	}
	return projectCopiesFromCandidates(candidates)
}

func scanMarkdownCopies(projectToken string, projectRoot string, kind string, relativeDir string, templates []TemplateItem) []ProjectCopy {
	files := markdownFilesByID(filepath.Join(projectRoot, filepath.FromSlash(relativeDir)))
	templateByID := templateItemsByID(templates)
	candidates := make(map[string]projectCopyCandidate, len(files))
	for _, id := range sortedKeys(files) {
		projectPath := files[id]
		template, ok := templateByID[id]
		copy := projectCopyFromScan(projectToken, kind, id, displayProjectPath(projectRoot, projectPath), []string{projectPath}, template, ok)
		addProjectCopyCandidate(candidates, id, template, ok, copy)
	}
	return projectCopiesFromCandidates(candidates)
}

func scanWorkflowCopies(projectToken string, projectRoot string, workflows []TemplateItem) []ProjectCopy {
	workflowFiles := markdownFilesByID(filepath.Join(projectRoot, ".claude", "workflows"))
	graphFiles := workflowGraphFilesByID(filepath.Join(projectRoot, ".claude", "workflows"))
	ids := make(map[string]bool, len(workflowFiles)+len(graphFiles))
	for id := range workflowFiles {
		ids[id] = true
	}
	for id := range graphFiles {
		ids[id] = true
	}
	if len(ids) > 0 {
		templateByID := templateItemsByID(workflows)
		candidates := make(map[string]projectCopyCandidate, len(ids))
		for _, id := range sortedKeys(ids) {
			paths := []string{}
			displayPath := ""
			if path := workflowFiles[id]; path != "" {
				paths = append(paths, path)
				displayPath = displayProjectPath(projectRoot, path)
			}
			if path := graphFiles[id]; path != "" {
				paths = append(paths, path)
			}
			if displayPath == "" {
				displayPath = displayProjectPath(projectRoot, graphFiles[id])
			}
			template, ok := templateByID[id]
			copy := projectCopyFromScan(projectToken, "workflow", id, displayPath, paths, template, ok)
			addProjectCopyCandidate(candidates, id, template, ok, copy)
		}
		return projectCopiesFromCandidates(candidates)
	}

	routingPath := filepath.Join(projectRoot, ".claude", "rules", "go-00-routing.md")
	if _, err := os.Stat(routingPath); err != nil {
		return nil
	}

	copies := make([]ProjectCopy, 0, len(workflows))
	for _, workflow := range workflows {
		displayPath := displayProjectPath(projectRoot, routingPath) + "#" + workflow.ID
		copies = append(copies, projectCopyFromScan(projectToken, "workflow", workflow.ID, displayPath, []string{routingPath}, workflowTemplateFromRouting(workflow), true))
	}
	return copies
}

type projectCopyCandidate struct {
	copy ProjectCopy
	rank int
}

func addProjectCopyCandidate(candidates map[string]projectCopyCandidate, id string, template TemplateItem, hasTemplate bool, copy ProjectCopy) {
	key := copy.Kind + ":detached:" + id
	if hasTemplate {
		key = copy.Kind + ":template:" + template.ID
	}
	candidate := projectCopyCandidate{
		copy: copy,
		rank: projectCopyCandidateRank(id, template, hasTemplate),
	}
	if existing, ok := candidates[key]; !ok || candidate.rank < existing.rank {
		candidates[key] = candidate
	}
}

func projectCopiesFromCandidates(candidates map[string]projectCopyCandidate) []ProjectCopy {
	copies := make([]ProjectCopy, 0, len(candidates))
	for _, candidate := range candidates {
		copies = append(copies, candidate.copy)
	}
	return copies
}

func projectCopyCandidateRank(id string, template TemplateItem, hasTemplate bool) int {
	if !hasTemplate {
		return 3
	}
	if templateItemHasFileStem(template, id) {
		return 0
	}
	if id == template.ID {
		return 1
	}
	return 2
}

func templateItemHasFileStem(item TemplateItem, id string) bool {
	for _, path := range templateFileCandidates(item) {
		base := filepath.Base(filepath.FromSlash(path))
		if ext := filepath.Ext(base); ext != "" {
			base = strings.TrimSuffix(base, ext)
		}
		if base == id {
			return true
		}
	}
	return false
}

func projectCopyFromScan(projectToken string, kind string, id string, displayPath string, projectFiles []string, template TemplateItem, hasTemplate bool) ProjectCopy {
	copy := ProjectCopy{
		ID:           fmt.Sprintf("proj_%s_%s_%s", kind, projectToken, strings.ReplaceAll(slugify(id), "-", "_")),
		Kind:         kind,
		Name:         id,
		LocalVersion: 1,
		SyncMode:     "manual",
		Status:       "detached",
		Path:         displayPath,
		Diff:         "Project file has no matching Template Library item.",
	}
	if template.Name != "" {
		copy.Name = template.Name
	}
	if !hasTemplate {
		return copy
	}

	templateHash := templateContentHash(template)
	projectHash := filesContentHash(projectFiles)
	copy.Origin = &Origin{
		TemplateID:  template.ID,
		BaseVersion: template.Version,
		BaseHash:    templateHash,
	}
	if templateHash != "" && projectHash == templateHash {
		copy.Status = "synced"
		copy.Diff = "Project file matches Template Library content hash."
		return copy
	}

	copy.Status = "project_modified"
	copy.Diff = fmt.Sprintf("Project file differs from template. Manual sync should show a diff before overwriting.\nprojectHash: %s\ntemplateHash: %s", projectHash, templateHash)
	return copy
}

func summarizeProjectCopies(copies []ProjectCopy) ConfigSummary {
	var summary ConfigSummary
	for _, copy := range copies {
		switch copy.Kind {
		case "agent":
			summary.Agents++
		case "rule":
			summary.Rules++
		case "skill":
			summary.Skills++
		case "workflow":
			summary.Workflows++
		}
	}
	return summary
}

func hydrateTemplateLibraryFromFiles(library TemplateLibrary) TemplateLibrary {
	library.Agents = hydrateTemplateItemsFromFiles(library.Agents)
	library.Rules = hydrateTemplateItemsFromFiles(library.Rules)
	library.Skills = hydrateTemplateItemsFromFiles(library.Skills)
	library.Workflows = hydrateTemplateItemsFromFiles(library.Workflows)
	return library
}

func hydrateTemplateItemsFromFiles(items []TemplateItem) []TemplateItem {
	hydrated := make([]TemplateItem, len(items))
	for index, item := range items {
		hydrated[index] = hydrateTemplateItemFromFiles(item)
	}
	return hydrated
}

func hydrateTemplateItemFromFiles(item TemplateItem) TemplateItem {
	if item.Kind == "agent" {
		if content := readFirstExistingText(pathsMatching(item, "templates/agents/claude/")); content != "" {
			item.Content = content
		}
		if projection := readFirstExistingText(pathsMatching(item, "templates/agents/codex/")); projection != "" {
			item.CodexProjection = projection
			if model := parseCodexAgentModel(projection); model != "" {
				item.ModelTier = model
			}
		}
		return item
	}

	if content := readFirstExistingText(templateFileCandidates(item)); content != "" {
		item.Content = content
	}
	return item
}

func templateItemsByID(items []TemplateItem) map[string]TemplateItem {
	mapped := make(map[string]TemplateItem, len(items))
	for _, item := range items {
		for _, id := range templateItemLookupIDs(item) {
			mapped[id] = item
		}
	}
	return mapped
}

func templateItemLookupIDs(item TemplateItem) []string {
	ids := []string{item.ID, item.Slug}
	for _, path := range templateFileCandidates(item) {
		base := filepath.Base(filepath.FromSlash(path))
		if ext := filepath.Ext(base); ext != "" {
			base = strings.TrimSuffix(base, ext)
		}
		ids = append(ids, base)
	}
	return uniqueStrings(ids)
}

func templateContentHash(item TemplateItem) string {
	if files := existingFiles(templateFileCandidates(item)); len(files) > 0 {
		return filesContentHash(files)
	}
	if item.Content != "" {
		return textContentHash(item.Content)
	}
	return contentHash(item.ID, item.Version)
}

func filesContentHash(paths []string) string {
	hash := sha256.New()
	found := false
	sorted := append([]string(nil), paths...)
	sort.Strings(sorted)
	for _, path := range sorted {
		data, err := os.ReadFile(resolveDataPath(path))
		if err != nil {
			continue
		}
		found = true
		hash.Write(data)
		hash.Write([]byte{0})
	}
	if !found {
		return ""
	}
	return "sha256:" + hex.EncodeToString(hash.Sum(nil))
}

func textContentHash(content string) string {
	sum := sha256.Sum256([]byte(content))
	return "sha256:" + hex.EncodeToString(sum[:])
}

func templateFileCandidates(item TemplateItem) []string {
	candidates := []string{}
	if item.Entry != "" {
		candidates = append(candidates, item.Entry)
	}
	candidates = append(candidates, item.Files...)
	if len(candidates) == 0 {
		candidates = append(candidates, item.SourcePaths...)
	}
	return uniqueStrings(candidates)
}

func pathsMatching(item TemplateItem, needle string) []string {
	matches := []string{}
	for _, path := range templateFileCandidates(item) {
		if strings.Contains(filepath.ToSlash(path), needle) {
			matches = append(matches, path)
		}
	}
	return matches
}

func workflowTemplateFromRouting(item TemplateItem) TemplateItem {
	routingPaths := []string{}
	for _, path := range templateFileCandidates(item) {
		slashed := filepath.ToSlash(path)
		if strings.Contains(slashed, "rules/") && strings.Contains(slashed, "00-routing") {
			routingPaths = append(routingPaths, path)
		}
	}
	if len(routingPaths) == 0 {
		return item
	}
	item.Entry = ""
	item.Files = nil
	item.SourcePaths = routingPaths
	item.Content = ""
	return item
}

func readFirstExistingText(paths []string) string {
	for _, path := range paths {
		data, err := os.ReadFile(resolveDataPath(path))
		if err == nil {
			return string(data)
		}
	}
	return ""
}

func parseCodexAgentModel(projection string) string {
	for _, line := range strings.Split(projection, "\n") {
		trimmed := strings.TrimSpace(line)
		if !strings.HasPrefix(trimmed, "model") || strings.HasPrefix(trimmed, "model_reasoning_effort") {
			continue
		}
		key, value, ok := strings.Cut(trimmed, "=")
		if !ok || strings.TrimSpace(key) != "model" {
			continue
		}
		model := strings.TrimSpace(value)
		model = strings.Trim(model, `"`)
		if model != "" {
			return model
		}
	}
	return ""
}

func existingFiles(paths []string) []string {
	existing := []string{}
	for _, path := range paths {
		resolved := resolveDataPath(path)
		if stat, err := os.Stat(resolved); err == nil && !stat.IsDir() {
			existing = append(existing, resolved)
		}
	}
	sort.Strings(existing)
	return existing
}

func resolveDataPath(value string) string {
	if value == "" || filepath.IsAbs(value) {
		return value
	}
	if _, err := os.Stat(value); err == nil {
		return value
	}
	wd, err := os.Getwd()
	if err != nil {
		return value
	}
	for {
		if _, err := os.Stat(filepath.Join(wd, "go.mod")); err == nil {
			return filepath.Join(wd, value)
		}
		parent := filepath.Dir(wd)
		if parent == wd {
			break
		}
		wd = parent
	}
	return value
}

func markdownFilesByID(dir string) map[string]string {
	return filesByID(dir, ".md")
}

func workflowGraphFilesByID(dir string) map[string]string {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return map[string]string{}
	}
	files := make(map[string]string)
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		name := entry.Name()
		if !strings.HasSuffix(strings.ToLower(name), ".graph.json") {
			continue
		}
		id := strings.TrimSuffix(name, ".graph.json")
		files[id] = filepath.Join(dir, name)
	}
	return files
}

func filesByID(dir string, suffix string) map[string]string {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return map[string]string{}
	}
	files := make(map[string]string)
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		name := entry.Name()
		if !strings.HasSuffix(strings.ToLower(name), suffix) {
			continue
		}
		id := strings.TrimSuffix(name, filepath.Ext(name))
		files[id] = filepath.Join(dir, name)
	}
	return files
}

func sortedKeys[T any](values map[string]T) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func uniqueStrings(values []string) []string {
	seen := make(map[string]bool, len(values))
	unique := make([]string, 0, len(values))
	for _, value := range values {
		if value == "" || seen[value] {
			continue
		}
		seen[value] = true
		unique = append(unique, value)
	}
	return unique
}

func displayProjectPath(projectRoot string, absolutePath string) string {
	relative, err := filepath.Rel(projectRoot, absolutePath)
	if err != nil {
		return filepath.ToSlash(absolutePath)
	}
	return filepath.ToSlash(relative)
}

func latestProjectConfigStamp(projectRoot string) string {
	var latest int64
	for _, relativeDir := range []string{".claude/agents", ".codex/agents", ".claude/rules", ".claude/skills"} {
		files := filesByID(filepath.Join(projectRoot, filepath.FromSlash(relativeDir)), "")
		for _, path := range files {
			stat, err := os.Stat(path)
			if err == nil && stat.ModTime().Unix() > latest {
				latest = stat.ModTime().Unix()
			}
		}
	}
	if latest == 0 {
		return nowStamp()
	}
	return time.Unix(latest, 0).Format("2006-01-02 15:04")
}
