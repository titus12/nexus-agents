package knowledgesync

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"nexus-agents/internal/knowledgegraph/gbrain"

	"gopkg.in/yaml.v3"
)

const (
	ProfileVersion               = 1
	ProfileRelativePath          = "KnowledgeBase/Setting.yaml"
	DefaultKnowledgeRoot         = "KnowledgeBase/project"
	TestedOpenWikiVersion        = "0.2.0"
	TestedKnowledgeGraphProvider = "gbrain"
	TestedGBrainVersion          = gbrain.TestedVersion
)

var ErrProfileNotFound = errors.New("knowledge sync profile not found")

type Profile struct {
	Version        int                    `json:"version" yaml:"version"`
	Knowledge      KnowledgeSettings      `json:"knowledge" yaml:"knowledge"`
	Discovery      DiscoverySettings      `json:"discovery" yaml:"discovery"`
	Scan           ScanSettings           `json:"scan" yaml:"scan"`
	Instructions   InstructionSettings    `json:"instructions" yaml:"instructions"`
	CodeGraph      CodeGraphSettings      `json:"codeGraph" yaml:"codeGraph"`
	OpenWiki       OpenWikiSettings       `json:"openWiki" yaml:"openWiki"`
	KnowledgeGraph KnowledgeGraphSettings `json:"knowledgeGraph" yaml:"knowledgeGraph"`
	Schedule       ScheduleSettings       `json:"schedule" yaml:"schedule"`
	Ownership      OwnershipSettings      `json:"ownership" yaml:"ownership"`
}

type KnowledgeSettings struct {
	Root          string `json:"root" yaml:"root"`
	Language      string `json:"language" yaml:"language"`
	UpdateMode    string `json:"updateMode" yaml:"updateMode"`
	DefaultBranch string `json:"defaultBranch" yaml:"defaultBranch"`
}

type DiscoverySettings struct {
	Strategy              string `json:"strategy" yaml:"strategy"`
	GeneratedFromRevision string `json:"generatedFromRevision" yaml:"generatedFromRevision"`
	GeneratedAt           string `json:"generatedAt" yaml:"generatedAt"`
	Reviewed              bool   `json:"reviewed" yaml:"reviewed"`
	ReviewedBy            string `json:"reviewedBy" yaml:"reviewedBy"`
}

type ScanSettings struct {
	Source string     `json:"source" yaml:"source"`
	Rules  []ScanRule `json:"rules" yaml:"rules"`
	Limits ScanLimits `json:"limits" yaml:"limits"`
}

type ScanRule struct {
	Pattern    string  `json:"pattern" yaml:"pattern"`
	Action     string  `json:"action" yaml:"action"`
	Category   string  `json:"category,omitempty" yaml:"category,omitempty"`
	Priority   string  `json:"priority,omitempty" yaml:"priority,omitempty"`
	Reason     string  `json:"reason,omitempty" yaml:"reason,omitempty"`
	Confidence float64 `json:"confidence,omitempty" yaml:"confidence,omitempty"`
	Hard       bool    `json:"hard,omitempty" yaml:"-"`
}

type ScanLimits struct {
	MaxFileSizeKB  int `json:"maxFileSizeKB" yaml:"maxFileSizeKB"`
	MaxFilesPerRun int `json:"maxFilesPerRun" yaml:"maxFilesPerRun"`
}

type InstructionSettings struct {
	RequiredTopics []string `json:"requiredTopics" yaml:"requiredTopics"`
	Additional     string   `json:"additional" yaml:"additional"`
}

type CodeGraphSettings struct {
	Enabled             bool `json:"enabled" yaml:"enabled"`
	ImpactDepth         int  `json:"impactDepth" yaml:"impactDepth"`
	IncludeCallers      bool `json:"includeCallers" yaml:"includeCallers"`
	IncludeCallees      bool `json:"includeCallees" yaml:"includeCallees"`
	IncludeRelatedTests bool `json:"includeRelatedTests" yaml:"includeRelatedTests"`
}

type OpenWikiSettings struct {
	Enabled bool   `json:"enabled" yaml:"enabled"`
	Version string `json:"version" yaml:"version"`
}

type KnowledgeGraphSettings struct {
	Enabled   bool                            `json:"enabled" yaml:"enabled"`
	Provider  string                          `json:"provider" yaml:"provider"`
	Version   string                          `json:"version" yaml:"version"`
	Brain     string                          `json:"brain" yaml:"brain"`
	SourceID  string                          `json:"sourceId" yaml:"sourceId"`
	Engine    string                          `json:"engine" yaml:"engine"`
	Transport string                          `json:"transport" yaml:"transport"`
	Sync      KnowledgeGraphSyncSettings      `json:"sync" yaml:"sync"`
	Export    KnowledgeGraphExportSettings    `json:"export" yaml:"export"`
	Query     KnowledgeGraphQuerySettings     `json:"query" yaml:"query"`
	Synthesis KnowledgeGraphSynthesisSettings `json:"synthesis" yaml:"synthesis"`
	Gaps      KnowledgeGraphGapSettings       `json:"gaps" yaml:"gaps"`
}

type KnowledgeGraphSyncSettings struct {
	OnProposalApplied    bool `json:"onProposalApplied" yaml:"onProposalApplied"`
	CommittedChangesOnly bool `json:"committedChangesOnly" yaml:"committedChangesOnly"`
	RetryMinutes         int  `json:"retryMinutes" yaml:"retryMinutes"`
	MaxRetries           int  `json:"maxRetries" yaml:"maxRetries"`
}

type KnowledgeGraphExportSettings struct {
	IncludeDomains          bool `json:"includeDomains" yaml:"includeDomains"`
	IncludeFeatures         bool `json:"includeFeatures" yaml:"includeFeatures"`
	IncludeCodeFacts        bool `json:"includeCodeFacts" yaml:"includeCodeFacts"`
	IncludeExternalEvidence bool `json:"includeExternalEvidence" yaml:"includeExternalEvidence"`
}

type KnowledgeGraphQuerySettings struct {
	TimeoutSeconds int  `json:"timeoutSeconds" yaml:"timeoutSeconds"`
	MaxResults     int  `json:"maxResults" yaml:"maxResults"`
	MaxGraphDepth  int  `json:"maxGraphDepth" yaml:"maxGraphDepth"`
	ShadowEnabled  bool `json:"shadowEnabled" yaml:"shadowEnabled"`
}

type KnowledgeGraphSynthesisSettings struct {
	Enabled   bool `json:"enabled" yaml:"enabled"`
	Automatic bool `json:"automatic" yaml:"automatic"`
}

type KnowledgeGraphGapSettings struct {
	Enabled        bool `json:"enabled" yaml:"enabled"`
	CreateProposal bool `json:"createProposal" yaml:"createProposal"`
}

type ScheduleSettings struct {
	Enabled              bool `json:"enabled" yaml:"enabled"`
	IntervalMinutes      int  `json:"intervalMinutes" yaml:"intervalMinutes"`
	CommittedChangesOnly bool `json:"committedChangesOnly" yaml:"committedChangesOnly"`
}

type OwnershipSettings struct {
	Owners          []string `json:"owners" yaml:"owners"`
	RequireApproval bool     `json:"requireApproval" yaml:"requireApproval"`
}

func DefaultProfile() Profile {
	return Profile{
		Version: ProfileVersion,
		Knowledge: KnowledgeSettings{
			Root:          DefaultKnowledgeRoot,
			Language:      "zh-CN",
			UpdateMode:    "proposal",
			DefaultBranch: "develop",
		},
		Discovery: DiscoverySettings{Strategy: "ai-assisted"},
		Scan: ScanSettings{
			Source: "git-tracked",
			Rules:  []ScanRule{},
			Limits: ScanLimits{MaxFileSizeKB: 512, MaxFilesPerRun: 3000},
		},
		Instructions: InstructionSettings{
			RequiredTopics: []string{"service-responsibility", "integration-boundaries", "data-ownership", "verification-paths"},
		},
		CodeGraph: CodeGraphSettings{
			Enabled: true, ImpactDepth: 2, IncludeCallers: true, IncludeCallees: true, IncludeRelatedTests: true,
		},
		OpenWiki: OpenWikiSettings{Enabled: true, Version: TestedOpenWikiVersion},
		KnowledgeGraph: KnowledgeGraphSettings{
			Enabled:   true,
			Provider:  TestedKnowledgeGraphProvider,
			Version:   TestedGBrainVersion,
			Brain:     "nexus-development",
			SourceID:  "project:auto",
			Engine:    "pglite",
			Transport: "stdio",
			Sync: KnowledgeGraphSyncSettings{
				OnProposalApplied: true, CommittedChangesOnly: true, RetryMinutes: 5, MaxRetries: 5,
			},
			Export: KnowledgeGraphExportSettings{
				IncludeDomains: true, IncludeFeatures: true,
			},
			Query: KnowledgeGraphQuerySettings{
				TimeoutSeconds: 10, MaxResults: 20, MaxGraphDepth: 3, ShadowEnabled: true,
			},
			Gaps: KnowledgeGraphGapSettings{CreateProposal: true},
		},
		Schedule:  ScheduleSettings{IntervalMinutes: 30, CommittedChangesOnly: true},
		Ownership: OwnershipSettings{Owners: []string{}, RequireApproval: true},
	}
}

func LoadProfile(projectRoot string) (Profile, error) {
	data, err := os.ReadFile(filepath.Join(projectRoot, filepath.FromSlash(ProfileRelativePath)))
	if os.IsNotExist(err) {
		return Profile{}, ErrProfileNotFound
	}
	if err != nil {
		return Profile{}, err
	}
	return ParseProfile(data)
}

func ParseProfile(data []byte) (Profile, error) {
	decoder := yaml.NewDecoder(bytes.NewReader(data))
	decoder.KnownFields(true)
	var profile Profile
	if err := decoder.Decode(&profile); err != nil {
		return Profile{}, fmt.Errorf("parse %s: %w", ProfileRelativePath, err)
	}
	normalizeProfile(&profile)
	if err := ValidateProfile(profile); err != nil {
		return Profile{}, err
	}
	return profile, nil
}

func SerializeProfile(profile Profile) ([]byte, error) {
	normalizeProfile(&profile)
	if err := ValidateProfile(profile); err != nil {
		return nil, err
	}
	var buffer bytes.Buffer
	encoder := yaml.NewEncoder(&buffer)
	encoder.SetIndent(2)
	if err := encoder.Encode(profile); err != nil {
		return nil, err
	}
	_ = encoder.Close()
	return buffer.Bytes(), nil
}

func SaveProfile(projectRoot string, profile Profile) error {
	data, err := SerializeProfile(profile)
	if err != nil {
		return err
	}
	target, err := SafeProjectPath(projectRoot, ProfileRelativePath)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return err
	}
	return atomicWriteFile(target, data, 0o644)
}

func ValidateProfile(profile Profile) error {
	if profile.Version != ProfileVersion {
		return fmt.Errorf("unsupported profile version %d", profile.Version)
	}
	if profile.Knowledge.Root != DefaultKnowledgeRoot {
		return fmt.Errorf("knowledge.root must be %s in the first release", DefaultKnowledgeRoot)
	}
	if profile.Knowledge.UpdateMode != "proposal" {
		return fmt.Errorf("knowledge.updateMode must be proposal")
	}
	if profile.Scan.Source != "git-tracked" {
		return fmt.Errorf("scan.source must be git-tracked")
	}
	if profile.Scan.Limits.MaxFileSizeKB < 1 || profile.Scan.Limits.MaxFileSizeKB > 10240 {
		return fmt.Errorf("scan.limits.maxFileSizeKB must be between 1 and 10240")
	}
	if profile.Scan.Limits.MaxFilesPerRun < 1 || profile.Scan.Limits.MaxFilesPerRun > 50000 {
		return fmt.Errorf("scan.limits.maxFilesPerRun must be between 1 and 50000")
	}
	if profile.Schedule.IntervalMinutes < 1 || profile.Schedule.IntervalMinutes > 10080 {
		return fmt.Errorf("schedule.intervalMinutes must be between 1 and 10080")
	}
	if !profile.Schedule.CommittedChangesOnly {
		return fmt.Errorf("schedule.committedChangesOnly must be true in the first release")
	}
	if profile.OpenWiki.Enabled && strings.TrimSpace(profile.OpenWiki.Version) == "" {
		return fmt.Errorf("openWiki.version is required when OpenWiki is enabled")
	}
	if profile.CodeGraph.ImpactDepth < 0 || profile.CodeGraph.ImpactDepth > 5 {
		return fmt.Errorf("codeGraph.impactDepth must be between 0 and 5")
	}
	if profile.KnowledgeGraph.Enabled {
		if profile.KnowledgeGraph.Provider != TestedKnowledgeGraphProvider {
			return fmt.Errorf("knowledgeGraph.provider must be %s in the PGLite release", TestedKnowledgeGraphProvider)
		}
		if profile.KnowledgeGraph.Version != TestedGBrainVersion {
			return fmt.Errorf("knowledgeGraph.version must be the tested GBrain version %s", TestedGBrainVersion)
		}
		if profile.KnowledgeGraph.Brain == "" {
			return fmt.Errorf("knowledgeGraph.brain is required")
		}
		if profile.KnowledgeGraph.SourceID == "" {
			return fmt.Errorf("knowledgeGraph.sourceId is required")
		}
		if profile.KnowledgeGraph.Engine != "pglite" {
			return fmt.Errorf("knowledgeGraph.engine must be pglite in the local release")
		}
		if profile.KnowledgeGraph.Transport != "stdio" {
			return fmt.Errorf("knowledgeGraph.transport must be stdio in the local release")
		}
		if !profile.KnowledgeGraph.Sync.CommittedChangesOnly {
			return fmt.Errorf("knowledgeGraph.sync.committedChangesOnly must be true")
		}
		if profile.KnowledgeGraph.Sync.RetryMinutes < 1 || profile.KnowledgeGraph.Sync.RetryMinutes > 10080 {
			return fmt.Errorf("knowledgeGraph.sync.retryMinutes must be between 1 and 10080")
		}
		if profile.KnowledgeGraph.Sync.MaxRetries < 0 || profile.KnowledgeGraph.Sync.MaxRetries > 100 {
			return fmt.Errorf("knowledgeGraph.sync.maxRetries must be between 0 and 100")
		}
		if profile.KnowledgeGraph.Query.TimeoutSeconds < 1 || profile.KnowledgeGraph.Query.TimeoutSeconds > 300 {
			return fmt.Errorf("knowledgeGraph.query.timeoutSeconds must be between 1 and 300")
		}
		if profile.KnowledgeGraph.Query.MaxResults < 1 || profile.KnowledgeGraph.Query.MaxResults > 500 {
			return fmt.Errorf("knowledgeGraph.query.maxResults must be between 1 and 500")
		}
		if profile.KnowledgeGraph.Query.MaxGraphDepth < 1 || profile.KnowledgeGraph.Query.MaxGraphDepth > 10 {
			return fmt.Errorf("knowledgeGraph.query.maxGraphDepth must be between 1 and 10")
		}
		if profile.KnowledgeGraph.Synthesis.Automatic && !profile.KnowledgeGraph.Synthesis.Enabled {
			return fmt.Errorf("knowledgeGraph.synthesis.automatic requires synthesis.enabled")
		}
	}
	if len(profile.Scan.Rules) > 500 {
		return fmt.Errorf("scan.rules exceeds the 500 rule limit")
	}
	for index, rule := range profile.Scan.Rules {
		if rule.Action != "include" && rule.Action != "exclude" {
			return fmt.Errorf("scan.rules[%d].action must be include or exclude", index)
		}
		if err := validateRelativePattern(rule.Pattern); err != nil {
			return fmt.Errorf("scan.rules[%d].pattern: %w", index, err)
		}
	}
	if len(profile.Instructions.Additional) > 20000 {
		return fmt.Errorf("instructions.additional exceeds 20000 characters")
	}
	return nil
}

func normalizeProfile(profile *Profile) {
	profile.Knowledge.Root = normalizeRelativePath(profile.Knowledge.Root)
	profile.Knowledge.Language = strings.TrimSpace(profile.Knowledge.Language)
	profile.Knowledge.UpdateMode = strings.ToLower(strings.TrimSpace(profile.Knowledge.UpdateMode))
	profile.Knowledge.DefaultBranch = strings.TrimSpace(profile.Knowledge.DefaultBranch)
	profile.Discovery.Strategy = strings.TrimSpace(profile.Discovery.Strategy)
	profile.Scan.Source = strings.ToLower(strings.TrimSpace(profile.Scan.Source))
	for index := range profile.Scan.Rules {
		profile.Scan.Rules[index].Pattern = normalizeRelativePath(profile.Scan.Rules[index].Pattern)
		profile.Scan.Rules[index].Action = strings.ToLower(strings.TrimSpace(profile.Scan.Rules[index].Action))
		profile.Scan.Rules[index].Category = strings.ToLower(strings.TrimSpace(profile.Scan.Rules[index].Category))
		profile.Scan.Rules[index].Priority = strings.ToLower(strings.TrimSpace(profile.Scan.Rules[index].Priority))
		profile.Scan.Rules[index].Reason = strings.TrimSpace(profile.Scan.Rules[index].Reason)
	}
	if profile.Scan.Rules == nil {
		profile.Scan.Rules = []ScanRule{}
	}
	profile.Instructions.RequiredTopics = uniqueSorted(profile.Instructions.RequiredTopics)
	profile.Instructions.Additional = strings.TrimSpace(profile.Instructions.Additional)
	profile.OpenWiki.Version = strings.TrimPrefix(strings.TrimSpace(profile.OpenWiki.Version), "v")
	profile.KnowledgeGraph.Provider = strings.ToLower(strings.TrimSpace(profile.KnowledgeGraph.Provider))
	profile.KnowledgeGraph.Version = strings.TrimPrefix(strings.TrimSpace(profile.KnowledgeGraph.Version), "v")
	profile.KnowledgeGraph.Brain = strings.TrimSpace(profile.KnowledgeGraph.Brain)
	profile.KnowledgeGraph.SourceID = strings.TrimSpace(profile.KnowledgeGraph.SourceID)
	profile.KnowledgeGraph.Engine = strings.ToLower(strings.TrimSpace(profile.KnowledgeGraph.Engine))
	profile.KnowledgeGraph.Transport = strings.ToLower(strings.TrimSpace(profile.KnowledgeGraph.Transport))
	profile.Ownership.Owners = uniqueSorted(profile.Ownership.Owners)
	if profile.Ownership.Owners == nil {
		profile.Ownership.Owners = []string{}
	}
}

func EffectiveRules(profile Profile) []ScanRule {
	rules := append([]ScanRule(nil), profile.Scan.Rules...)
	for _, pattern := range HardExclusionPatterns() {
		rules = append(rules, ScanRule{
			Pattern: pattern, Action: "exclude", Category: "safety", Priority: "critical",
			Reason: "Nexus hard safety exclusion.", Confidence: 1, Hard: true,
		})
	}
	return rules
}

func HardExclusionPatterns() []string {
	return []string{
		".git/**", ".env", ".env.*", "**/*.pem", "**/*.key", "**/credentials*",
		"**/secrets/**", "**/node_modules/**", "**/vendor/**", "**/dist/**",
		"**/build/**", "**/target/**", "**/bin/**", "**/obj/**", "**/.cache/**",
		"**/coverage/**",
	}
}

func IsHardExcluded(relative string) bool {
	relative = normalizeRelativePath(relative)
	lower := strings.ToLower(relative)
	base := strings.ToLower(path.Base(relative))
	if lower == ".git" || strings.HasPrefix(lower, ".git/") ||
		lower == ".env" || strings.HasPrefix(base, ".env.") ||
		strings.HasSuffix(lower, ".pem") || strings.HasSuffix(lower, ".key") ||
		strings.Contains(base, "credential") ||
		strings.Contains(lower, "/secrets/") || strings.HasPrefix(lower, "secrets/") {
		return true
	}
	for _, segment := range []string{"node_modules", "vendor", "dist", "build", "target", "bin", "obj", ".cache", "coverage"} {
		if lower == segment || strings.HasPrefix(lower, segment+"/") || strings.Contains(lower, "/"+segment+"/") {
			return true
		}
	}
	return false
}

func IncludedPaths(profile Profile, candidates []string) []string {
	rules := EffectiveRules(profile)
	hasInclude := false
	for _, rule := range profile.Scan.Rules {
		if rule.Action == "include" {
			hasInclude = true
			break
		}
	}
	var included []string
	for _, candidate := range candidates {
		candidate = normalizeRelativePath(candidate)
		if candidate == "" || IsHardExcluded(candidate) {
			continue
		}
		include := !hasInclude
		excluded := false
		for _, rule := range rules {
			if !matchScanPattern(rule.Pattern, candidate) {
				continue
			}
			if rule.Action == "exclude" {
				excluded = true
			} else if !rule.Hard {
				include = true
			}
		}
		if include && !excluded {
			included = append(included, candidate)
		}
	}
	sort.Strings(included)
	return included
}

func matchScanPattern(patternValue, candidate string) bool {
	patternValue = normalizeRelativePath(patternValue)
	candidate = normalizeRelativePath(candidate)
	var expression strings.Builder
	expression.WriteString("^")
	for index := 0; index < len(patternValue); {
		switch {
		case index+1 < len(patternValue) && patternValue[index:index+2] == "**":
			expression.WriteString(".*")
			index += 2
		case patternValue[index] == '*':
			expression.WriteString("[^/]*")
			index++
		case patternValue[index] == '?':
			expression.WriteString("[^/]")
			index++
		default:
			expression.WriteString(regexp.QuoteMeta(string(patternValue[index])))
			index++
		}
	}
	expression.WriteString("$")
	matched, err := regexp.MatchString(expression.String(), candidate)
	return err == nil && matched
}

func ProfileHash(profile Profile) (string, error) {
	data, err := SerializeProfile(profile)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(data)
	return "sha256:" + hex.EncodeToString(sum[:]), nil
}

func SafeProjectPath(projectRoot, relative string) (string, error) {
	if strings.TrimSpace(projectRoot) == "" {
		return "", fmt.Errorf("project root is empty")
	}
	if err := validateRelativePattern(relative); err != nil {
		return "", err
	}
	absoluteRoot, err := filepath.Abs(projectRoot)
	if err != nil {
		return "", err
	}
	if resolvedRoot, resolveErr := filepath.EvalSymlinks(absoluteRoot); resolveErr == nil {
		absoluteRoot = resolvedRoot
	}
	target := filepath.Join(absoluteRoot, filepath.FromSlash(normalizeRelativePath(relative)))
	resolved, err := filepath.Rel(absoluteRoot, target)
	if err != nil || resolved == ".." || strings.HasPrefix(resolved, ".."+string(filepath.Separator)) || filepath.IsAbs(resolved) {
		return "", fmt.Errorf("path escapes project root: %s", relative)
	}
	current := absoluteRoot
	for _, component := range strings.Split(filepath.Clean(resolved), string(filepath.Separator)) {
		if component == "." || component == "" {
			continue
		}
		current = filepath.Join(current, component)
		info, statErr := os.Lstat(current)
		if os.IsNotExist(statErr) {
			break
		}
		if statErr != nil {
			return "", statErr
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return "", fmt.Errorf("path contains a symbolic link: %s", relative)
		}
	}
	return target, nil
}

func validateRelativePattern(value string) error {
	value = strings.TrimSpace(strings.ReplaceAll(value, "\\", "/"))
	if value == "" {
		return fmt.Errorf("path is empty")
	}
	if filepath.IsAbs(value) || strings.HasPrefix(value, "/") {
		return fmt.Errorf("absolute paths are not allowed")
	}
	clean := path.Clean(value)
	if clean == ".." || strings.HasPrefix(clean, "../") {
		return fmt.Errorf("path traversal is not allowed")
	}
	return nil
}

func normalizeRelativePath(value string) string {
	value = strings.TrimSpace(strings.ReplaceAll(value, "\\", "/"))
	value = strings.TrimPrefix(value, "./")
	if value == "" {
		return ""
	}
	return path.Clean(value)
}

func uniqueSorted(values []string) []string {
	seen := map[string]bool{}
	out := make([]string, 0, len(values))
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" || seen[value] {
			continue
		}
		seen[value] = true
		out = append(out, value)
	}
	sort.Strings(out)
	return out
}
