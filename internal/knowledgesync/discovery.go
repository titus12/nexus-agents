package knowledgesync

import (
	"context"
	"encoding/json"
	"path"
	"sort"
	"strings"
	"time"
)

type RepositoryInventory struct {
	Revision       string          `json:"revision"`
	Branch         string          `json:"branch"`
	TrackedFiles   int             `json:"trackedFiles"`
	Files          []InventoryFile `json:"files"`
	DirectoryStats []DirectoryStat `json:"directoryStats"`
	Manifests      []string        `json:"manifests"`
	Readmes        []string        `json:"readmes"`
	ExistingKB     []string        `json:"existingKnowledge"`
	Extensions     map[string]int  `json:"extensions"`
	CodeGraph      string          `json:"codeGraphSummary,omitempty"`
	Truncated      bool            `json:"truncated"`
}

type InventoryFile struct {
	Path     string `json:"path"`
	Category string `json:"category"`
	Size     int64  `json:"size,omitempty"`
}

type DirectoryStat struct {
	Path       string         `json:"path"`
	FileCount  int            `json:"fileCount"`
	Extensions map[string]int `json:"extensions"`
}

type ScanPolicyProposal struct {
	Revision       string              `json:"revision"`
	Rules          []ScanRule          `json:"rules"`
	RequiredTopics []string            `json:"requiredTopics"`
	Uncertain      []UncertainPath     `json:"uncertain"`
	Warnings       []string            `json:"warnings"`
	AIRefined      bool                `json:"aiRefined"`
	Inventory      RepositoryInventory `json:"inventory"`
}

type UncertainPath struct {
	Path       string  `json:"path"`
	Reason     string  `json:"reason"`
	Confidence float64 `json:"confidence"`
}

type DiscoveryClient interface {
	ProposeScanPolicy(ctx context.Context, inventory RepositoryInventory) ([]byte, error)
}

func BuildInventory(ctx context.Context, projectRoot string, runner GitRunner, codeGraph CodeGraphClient, limits ScanLimits) (RepositoryInventory, error) {
	if limits.MaxFilesPerRun <= 0 {
		limits.MaxFilesPerRun = DefaultProfile().Scan.Limits.MaxFilesPerRun
	}
	state, err := ReadGitState(ctx, runner, projectRoot)
	if err != nil {
		return RepositoryInventory{}, err
	}
	files, err := ListCommittedFiles(ctx, runner, projectRoot, state.Head, limits.MaxFilesPerRun+1)
	if err != nil {
		return RepositoryInventory{}, err
	}
	inventory := RepositoryInventory{
		Revision: state.Head, Branch: state.Branch, Extensions: map[string]int{},
		Files: []InventoryFile{}, DirectoryStats: []DirectoryStat{}, Manifests: []string{},
		Readmes: []string{}, ExistingKB: []string{},
	}
	if len(files) > limits.MaxFilesPerRun {
		inventory.Truncated = true
		files = files[:limits.MaxFilesPerRun]
	}
	inventory.TrackedFiles = len(files)
	directories := map[string]*DirectoryStat{}
	for _, committedFile := range files {
		relative := committedFile.Path
		if IsHardExcluded(relative) {
			continue
		}
		if isLikelyBinaryPath(relative) {
			continue
		}
		size := committedFile.Size
		if size > int64(limits.MaxFileSizeKB)*1024 {
			continue
		}
		ext := strings.ToLower(path.Ext(relative))
		category := inventoryCategory(relative)
		inventory.Files = append(inventory.Files, InventoryFile{Path: relative, Category: category, Size: size})
		inventory.Extensions[ext]++
		dir := path.Dir(relative)
		if dir == "." {
			dir = ""
		}
		stat := directories[dir]
		if stat == nil {
			stat = &DirectoryStat{Path: dir, Extensions: map[string]int{}}
			directories[dir] = stat
		}
		stat.FileCount++
		stat.Extensions[ext]++
		base := strings.ToLower(path.Base(relative))
		if isManifest(base, ext) {
			inventory.Manifests = append(inventory.Manifests, relative)
		}
		if strings.HasPrefix(base, "readme") {
			inventory.Readmes = append(inventory.Readmes, relative)
		}
		if strings.HasPrefix(strings.ToLower(relative), "knowledgebase/") {
			inventory.ExistingKB = append(inventory.ExistingKB, relative)
		}
	}
	for _, stat := range directories {
		inventory.DirectoryStats = append(inventory.DirectoryStats, *stat)
	}
	sort.Slice(inventory.Files, func(i, j int) bool { return inventory.Files[i].Path < inventory.Files[j].Path })
	sort.Slice(inventory.DirectoryStats, func(i, j int) bool { return inventory.DirectoryStats[i].Path < inventory.DirectoryStats[j].Path })
	sort.Strings(inventory.Manifests)
	sort.Strings(inventory.Readmes)
	sort.Strings(inventory.ExistingKB)
	if codeGraph != nil {
		if summary, err := codeGraph.Summary(projectRoot); err == nil {
			inventory.CodeGraph = boundedText(summary, 12000)
		}
	}
	return inventory, nil
}

func DiscoverPolicy(ctx context.Context, projectRoot string, runner GitRunner, client DiscoveryClient, codeGraph CodeGraphClient) (ScanPolicyProposal, error) {
	profile := DefaultProfile()
	inventory, err := BuildInventory(ctx, projectRoot, runner, codeGraph, profile.Scan.Limits)
	if err != nil {
		return ScanPolicyProposal{}, err
	}
	fallback := deterministicPolicy(inventory)
	fallback.Inventory = inventory
	if client == nil {
		fallback.Warnings = append(fallback.Warnings, "AI refinement was skipped; deterministic repository discovery was used.")
		return fallback, nil
	}
	data, err := client.ProposeScanPolicy(ctx, inventory)
	if err != nil {
		fallback.Warnings = append(fallback.Warnings, "AI refinement failed: "+boundedText(err.Error(), 500))
		return fallback, nil
	}
	var proposal ScanPolicyProposal
	decoder := json.NewDecoder(strings.NewReader(string(data)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&proposal); err != nil {
		fallback.Warnings = append(fallback.Warnings, "AI refinement returned invalid JSON; deterministic discovery was used.")
		return fallback, nil
	}
	proposal.Revision = inventory.Revision
	proposal.Inventory = inventory
	proposal.AIRefined = true
	proposal.RequiredTopics = uniqueSorted(proposal.RequiredTopics)
	for index := range proposal.Rules {
		proposal.Rules[index].Pattern = normalizeRelativePath(proposal.Rules[index].Pattern)
	}
	testProfile := DefaultProfile()
	testProfile.Scan.Rules = proposal.Rules
	if err := ValidateProfile(testProfile); err != nil {
		fallback.Warnings = append(fallback.Warnings, "AI refinement violated scan-policy safety rules: "+err.Error())
		return fallback, nil
	}
	return proposal, nil
}

func deterministicPolicy(inventory RepositoryInventory) ScanPolicyProposal {
	directories := map[string]int{}
	rootFiles := map[string]bool{}
	specialFiles := map[string]bool{}
	for _, manifest := range inventory.Manifests {
		specialFiles[manifest] = true
	}
	for _, readme := range inventory.Readmes {
		specialFiles[readme] = true
	}
	for _, file := range inventory.Files {
		relative := normalizeRelativePath(file.Path)
		if relative == "" {
			continue
		}
		top := strings.Split(relative, "/")[0]
		if !IsHardExcluded(top) && top != "KnowledgeBase" {
			if strings.Contains(relative, "/") {
				directories[top]++
			} else {
				rootFiles[relative] = true
			}
		}
	}
	var rules []ScanRule
	for _, directory := range sortedMapKeys(directories) {
		category := "code"
		reason := "Git-tracked repository content discovered by Nexus."
		lower := strings.ToLower(directory)
		if lower == "docs" || strings.Contains(lower, "doc") {
			category = "docs"
			reason = "Repository-owned documentation."
		}
		rules = append(rules, ScanRule{Pattern: directory + "/**", Action: "include", Category: category, Priority: "normal", Reason: reason, Confidence: 0.75})
	}
	for _, file := range sortedMapKeys(rootFiles) {
		if specialFiles[file] {
			continue
		}
		category := "code"
		reason := "Git-tracked repository content discovered by Nexus."
		lower := strings.ToLower(file)
		if lower == "readme.md" || strings.Contains(lower, "doc") || strings.HasPrefix(lower, "change") {
			category = "docs"
			reason = "Repository-owned documentation."
		}
		rules = append(rules, ScanRule{Pattern: file, Action: "include", Category: category, Priority: "normal", Reason: reason, Confidence: 0.75})
	}
	for _, manifest := range inventory.Manifests {
		rules = append(rules, ScanRule{Pattern: manifest, Action: "include", Category: "manifest", Priority: "high", Reason: "Project manifest or contract.", Confidence: 0.95})
	}
	for _, readme := range inventory.Readmes {
		rules = append(rules, ScanRule{Pattern: readme, Action: "include", Category: "docs", Priority: "high", Reason: "Repository README.", Confidence: 0.95})
	}
	return ScanPolicyProposal{
		Revision:       inventory.Revision,
		Rules:          rules,
		RequiredTopics: DefaultProfile().Instructions.RequiredTopics,
		Uncertain:      []UncertainPath{},
		Warnings:       []string{},
	}
}

func ProfileFromDiscovery(proposal ScanPolicyProposal, reviewer string) Profile {
	profile := DefaultProfile()
	profile.Discovery.GeneratedFromRevision = proposal.Revision
	profile.Discovery.GeneratedAt = time.Now().Format(time.RFC3339)
	profile.Discovery.Reviewed = strings.TrimSpace(reviewer) != ""
	profile.Discovery.ReviewedBy = strings.TrimSpace(reviewer)
	profile.Scan.Rules = append([]ScanRule(nil), proposal.Rules...)
	if len(proposal.RequiredTopics) > 0 {
		profile.Instructions.RequiredTopics = proposal.RequiredTopics
	}
	return profile
}

func inventoryCategory(relative string) string {
	lower := strings.ToLower(relative)
	if strings.HasPrefix(lower, "knowledgebase/") {
		return "knowledge"
	}
	if isContractOrDoc(lower) {
		return "docs_contract"
	}
	if isTestPath(lower) {
		return "tests"
	}
	if isGeneratedOrDependency(lower) {
		return "generated_dependency"
	}
	return "code_config"
}

func isManifest(base, ext string) bool {
	switch base {
	case "go.mod", "package.json", "pyproject.toml", "pom.xml", "cargo.toml", "build.gradle", "settings.gradle":
		return true
	}
	return ext == ".csproj" || ext == ".sln"
}

func isLikelyBinaryPath(relative string) bool {
	switch strings.ToLower(path.Ext(relative)) {
	case ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff",
		".pdf", ".zip", ".gz", ".tgz", ".7z", ".rar", ".tar",
		".exe", ".dll", ".so", ".dylib", ".class", ".jar", ".war",
		".mp3", ".wav", ".ogg", ".mp4", ".mov", ".avi", ".mkv",
		".woff", ".woff2", ".ttf", ".otf", ".bin":
		return true
	default:
		return false
	}
}

func sortedMapKeys[T any](values map[string]T) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		if key != "" {
			keys = append(keys, key)
		}
	}
	sort.Strings(keys)
	return keys
}

func boundedText(value string, max int) string {
	value = strings.TrimSpace(value)
	if max <= 0 || len(value) <= max {
		return value
	}
	return value[:max] + "…"
}
