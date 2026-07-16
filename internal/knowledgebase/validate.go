package knowledgebase

import (
	"path"
	"regexp"
	"sort"
	"strings"
	"time"
)

const largeDocumentBytes int64 = 120000
const minUsefulBodyChars = 20

var validFrontmatterTypes = map[string]bool{
	"Index": true, "Log": true, "Project": true, "Routing": true,
	"Domain": true, "Guide": true, "Workflow": true, "Schema": true,
	"Template": true, "Decision": true, "Reference": true, "CodingRules": true, "Rules": true, "Checklist": true,
}

var placeholderPattern = regexp.MustCompile(`(?i)\b(TBD|TODO|FIXME|placeholder|coming soon)\b|待补充|占位|稍后补充`)
var mojibakePattern = regexp.MustCompile(`(�|Ã.|Â.|â€.|鈥|閳|鐞|閹|婢|鍔|绋|寮圭|闈㈡|鎸夐|缃戠|鍗忚|鎴樻|鐜╂)`)

func Validate(projectRoot string) (ValidationReport, error) {
	bundle, err := ScanBundle(projectRoot)
	if err != nil {
		return ValidationReport{}, err
	}
	issues := validateBundle(bundle)
	summary := summarize(bundle, issues)
	return normalizeValidationReport(ValidationReport{Root: DefaultRoot, Summary: summary, Issues: issues, CheckedAt: nowStamp()}), nil
}

func Maintenance(projectRoot string) (MaintenanceReport, error) {
	report, err := Validate(projectRoot)
	if err != nil {
		return MaintenanceReport{}, err
	}
	bundle, err := ScanBundle(projectRoot)
	if err != nil {
		return MaintenanceReport{}, err
	}
	maintenance := MaintenanceReport{Root: DefaultRoot, Summary: report.Summary, Issues: report.Issues, CheckedAt: nowStamp()}
	for _, doc := range bundle.Documents {
		if doc.SizeBytes > largeDocumentBytes {
			maintenance.LargeDocuments = append(maintenance.LargeDocuments, doc)
			maintenance.SuggestedActions = append(maintenance.SuggestedActions, "Split large document "+doc.Path+" into routing, patterns, anti-patterns, examples, or templates.")
		}
		if isStale(doc.Frontmatter.Timestamp) {
			maintenance.StaleDocuments = append(maintenance.StaleDocuments, doc)
		}
	}
	maintenance.DuplicateRules = detectDuplicateRules(bundle)
	return normalizeMaintenanceReport(maintenance), nil
}

func validateBundle(bundle Bundle) []ValidationIssue {
	var issues []ValidationIssue
	if !bundle.Exists {
		return append(issues, ValidationIssue{Severity: "warning", Code: "missing_bundle", Path: DefaultRoot, Message: "Knowledge base root does not exist."})
	}
	docByPath := map[string]bool{}
	titleByNormalized := map[string][]string{}
	for _, doc := range bundle.Documents {
		docByPath[doc.Path] = true
		if doc.Frontmatter.Title != "" {
			normalizedTitle := strings.ToLower(strings.TrimSpace(doc.Frontmatter.Title))
			titleByNormalized[normalizedTitle] = append(titleByNormalized[normalizedTitle], doc.Path)
		}
	}
	if !docByPath[path.Join(DefaultRoot, "index.md")] {
		issues = append(issues, ValidationIssue{Severity: "error", Code: "missing_index", Path: path.Join(DefaultRoot, "index.md"), Message: "OKF bundle should include index.md."})
	}
	if !docByPath[path.Join(DefaultRoot, "log.md")] {
		issues = append(issues, ValidationIssue{Severity: "warning", Code: "missing_log", Path: path.Join(DefaultRoot, "log.md"), Message: "OKF bundle should include log.md for knowledge changes."})
	}
	for _, doc := range bundle.Documents {
		issues = append(issues, validateDocument(doc, docByPath)...)
		for _, link := range doc.Links {
			resolved := resolveKnowledgeRef(doc.Directory, link.Target)
			if resolved == "" || !strings.HasPrefix(resolved, DefaultRoot+"/") {
				continue
			}
			if !docByPath[resolved] {
				issues = append(issues, ValidationIssue{Severity: "error", Code: "broken_link", Path: doc.Path, Line: link.Line, Message: "Markdown link target does not exist: " + link.Target})
			}
		}
	}
	issues = append(issues, validateDuplicateTitles(titleByNormalized)...)
	issues = append(issues, validateRoutingCoverage(bundle, docByPath)...)
	issues = append(issues, validateDomainRoutingAliases(bundle)...)
	issues = append(issues, validateOrphanDocuments(bundle)...)
	return issues
}

func validateDocument(doc Document, docByPath map[string]bool) []ValidationIssue {
	if doc.Reserved {
		if likelyMojibake(doc.Body) {
			return []ValidationIssue{{Severity: "warning", Code: "mojibake_content", Path: doc.Path, Message: "Document appears to contain mojibake/garbled Chinese text; rewrite the affected frontmatter or body as UTF-8."}}
		}
		return nil
	}
	var issues []ValidationIssue
	fm := doc.Frontmatter
	if fm.Raw == "" {
		return []ValidationIssue{{Severity: "error", Code: "missing_frontmatter", Path: doc.Path, Message: "Document is missing OKF YAML frontmatter."}}
	}
	if fm.Type == "" {
		issues = append(issues, ValidationIssue{Severity: "error", Code: "missing_type", Path: doc.Path, Message: "Frontmatter field type is required."})
	} else if !validFrontmatterTypes[fm.Type] {
		issues = append(issues, ValidationIssue{Severity: "warning", Code: "unknown_type", Path: doc.Path, Message: "Frontmatter type is not one of the recommended OKF document types: " + fm.Type})
	}
	if fm.Title == "" {
		issues = append(issues, ValidationIssue{Severity: "warning", Code: "missing_title", Path: doc.Path, Message: "Frontmatter field title is recommended."})
	}
	if fm.Description == "" {
		issues = append(issues, ValidationIssue{Severity: "warning", Code: "missing_description", Path: doc.Path, Message: "Frontmatter field description is recommended."})
	}
	if fm.Resource == "" {
		issues = append(issues, ValidationIssue{Severity: "warning", Code: "missing_resource", Path: doc.Path, Message: "Frontmatter field resource is recommended."})
	} else if normalizedResourcePath(fm.Resource) != doc.Path {
		issues = append(issues, ValidationIssue{Severity: "warning", Code: "resource_mismatch", Path: doc.Path, Message: "Frontmatter resource should match the document path for deterministic OKF lookup."})
	}
	if fm.Timestamp == "" {
		issues = append(issues, ValidationIssue{Severity: "warning", Code: "missing_timestamp", Path: doc.Path, Message: "Frontmatter field timestamp is recommended."})
	} else if _, err := time.Parse(time.RFC3339, fm.Timestamp); err != nil {
		issues = append(issues, ValidationIssue{Severity: "warning", Code: "invalid_timestamp", Path: doc.Path, Message: "Frontmatter timestamp should be RFC3339 so maintenance can detect stale knowledge."})
	}
	if len(fm.Tags) == 0 {
		issues = append(issues, ValidationIssue{Severity: "warning", Code: "missing_tags", Path: doc.Path, Message: "Frontmatter tags are recommended for routing and knowledge discovery."})
	}
	if len(strings.TrimSpace(doc.Body)) < minUsefulBodyChars {
		issues = append(issues, ValidationIssue{Severity: "warning", Code: "thin_document", Path: doc.Path, Message: "Document body is too small to be useful as workflow knowledge."})
	}
	if placeholderPattern.MatchString(doc.Body) {
		issues = append(issues, ValidationIssue{Severity: "warning", Code: "placeholder_content", Path: doc.Path, Message: "Document still contains placeholder/TODO content."})
	}
	if likelyMojibake(fm.Raw + "\n" + doc.Body) {
		issues = append(issues, ValidationIssue{Severity: "warning", Code: "mojibake_content", Path: doc.Path, Message: "Document appears to contain mojibake/garbled Chinese text; rewrite the affected frontmatter or body as UTF-8."})
	}
	if doc.Name == "routing.md" && !mentionsKnowledgePath(doc.Body) {
		issues = append(issues, ValidationIssue{Severity: "warning", Code: "routing_without_targets", Path: doc.Path, Message: "Routing documents should name the target KnowledgeBase files agents must load."})
	}
	for _, target := range append([]string{}, append(fm.DependsOn, fm.SeeAlso...)...) {
		resolved := resolveKnowledgeRef(doc.Directory, target)
		if resolved != "" && strings.HasPrefix(resolved, DefaultRoot+"/") && !docByPath[resolved] {
			issues = append(issues, ValidationIssue{Severity: "error", Code: "broken_frontmatter_ref", Path: doc.Path, Message: "Frontmatter reference does not exist: " + target})
		}
	}
	return issues
}

func likelyMojibake(text string) bool {
	text = stripMarkdownCode(text)
	matches := mojibakePattern.FindAllString(text, -1)
	if len(matches) >= 2 {
		return true
	}
	return strings.Contains(text, "�")
}

func stripMarkdownCode(text string) string {
	var out []string
	inFence := false
	for _, line := range strings.Split(text, "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "```") {
			inFence = !inFence
			continue
		}
		if inFence {
			continue
		}
		for {
			start := strings.Index(line, "`")
			if start < 0 {
				break
			}
			end := strings.Index(line[start+1:], "`")
			if end < 0 {
				break
			}
			line = line[:start] + line[start+1+end+1:]
		}
		out = append(out, line)
	}
	return strings.Join(out, "\n")
}

func normalizedResourcePath(resource string) string {
	resource = strings.TrimSpace(resource)
	resource = strings.TrimPrefix(resource, "./")
	resource = strings.TrimSuffix(resource, "/")
	resource = path.Clean(strings.ReplaceAll(resource, "\\", "/"))
	return resource
}

func resolveKnowledgeRef(directory, target string) string {
	target = strings.TrimSpace(target)
	if target == "" || strings.HasPrefix(target, "http://") || strings.HasPrefix(target, "https://") || strings.HasPrefix(target, "#") {
		return ""
	}
	target = strings.Split(target, "#")[0]
	target = normalizedResourcePath(target)
	if strings.HasPrefix(target, DefaultRoot+"/") {
		return target
	}
	return path.Clean(path.Join(directory, target))
}

func mentionsKnowledgePath(body string) bool {
	return strings.Contains(body, DefaultRoot+"/") || strings.Contains(body, "./") || strings.Contains(body, "../")
}

func validateDuplicateTitles(titleByNormalized map[string][]string) []ValidationIssue {
	var issues []ValidationIssue
	for title, paths := range titleByNormalized {
		if title == "" || len(paths) < 2 {
			continue
		}
		sort.Strings(paths)
		issues = append(issues, ValidationIssue{Severity: "warning", Code: "duplicate_title", Path: paths[0], Message: "Multiple knowledge documents share the same title: " + strings.Join(paths, ", ")})
	}
	return issues
}

func validateRoutingCoverage(bundle Bundle, docByPath map[string]bool) []ValidationIssue {
	var issues []ValidationIssue
	for _, doc := range bundle.Documents {
		if doc.Name != "README.md" && doc.Name != "index.md" {
			continue
		}
		if !strings.HasPrefix(doc.Path, DefaultRoot+"/domains/") {
			continue
		}
		domainDir := path.Dir(doc.Path)
		routingPath := path.Join(domainDir, "routing.md")
		if !docByPath[routingPath] {
			issues = append(issues, ValidationIssue{Severity: "warning", Code: "missing_domain_routing", Path: routingPath, Message: "Domain knowledge should include routing.md so agents can load only the required files."})
		}
	}
	return issues
}

func validateDomainRoutingAliases(bundle Bundle) []ValidationIssue {
	type aliasOwner struct {
		domain string
		path   string
	}
	var issues []ValidationIssue
	owners := map[string]aliasOwner{}
	broadAliases := map[string]bool{
		"状态": true, "数据": true, "系统": true, "功能": true, "模块": true,
		"state": true, "data": true, "system": true, "feature": true, "module": true,
	}
	for _, doc := range bundle.Documents {
		if !isDomainRoutingDocument(doc.Path) {
			continue
		}
		domain := inferDomainFromPath(doc.Path)
		if domain == "" {
			continue
		}
		aliases := routingAliasValues(doc.Frontmatter.Routing.Aliases)
		if len(aliases) == 0 {
			issues = append(issues, ValidationIssue{Severity: "warning", Code: "missing_domain_aliases", Path: doc.Path, Message: "Domain routing should declare routing.aliases so Agent Knowledge Routing can deterministically match user queries before FTS5/vector fallback."})
			continue
		}
		if !hasChineseAlias(doc.Frontmatter.Routing.Aliases) || !hasEnglishAlias(doc.Frontmatter.Routing.Aliases) {
			issues = append(issues, ValidationIssue{Severity: "warning", Code: "missing_bilingual_aliases", Path: doc.Path, Message: "Domain routing aliases should include at least one Chinese and one English strong alias to reduce maintenance burden and improve deterministic routing."})
		}
		for _, alias := range aliases {
			normalized := normalizeAlias(alias)
			if normalized == "" {
				continue
			}
			if broadAliases[normalized] {
				issues = append(issues, ValidationIssue{Severity: "warning", Code: "broad_domain_alias", Path: doc.Path, Message: "Alias is too broad for deterministic domain routing; move it to routing.keywords or make it more specific: " + alias})
			}
			if owner, ok := owners[normalized]; ok && owner.domain != domain {
				issues = append(issues, ValidationIssue{Severity: "warning", Code: "duplicate_domain_alias", Path: doc.Path, Message: "Alias is already claimed by " + owner.domain + " in " + owner.path + ": " + alias})
				continue
			}
			owners[normalized] = aliasOwner{domain: domain, path: doc.Path}
		}
	}
	return issues
}

func isDomainRoutingDocument(docPath string) bool {
	const prefix = DefaultRoot + "/domains/"
	if !strings.HasPrefix(docPath, prefix) || !strings.HasSuffix(docPath, "/routing.md") {
		return false
	}
	rest := strings.TrimPrefix(docPath, prefix)
	return strings.Count(rest, "/") == 1
}

func routingAliasValues(aliases RoutingAliases) []string {
	values := append([]string{}, aliases.Values...)
	values = append(values, aliases.ZH...)
	values = append(values, aliases.EN...)
	for _, pair := range aliases.Pairs {
		values = append(values, pair.ZH, pair.EN)
	}
	return compactStrings(values)
}

func hasChineseAlias(aliases RoutingAliases) bool {
	for _, alias := range append(append([]string{}, aliases.Values...), aliases.ZH...) {
		if containsCJK(alias) {
			return true
		}
	}
	for _, pair := range aliases.Pairs {
		if containsCJK(pair.ZH) || containsCJK(pair.EN) {
			return true
		}
	}
	return false
}

func hasEnglishAlias(aliases RoutingAliases) bool {
	for _, alias := range append(append([]string{}, aliases.Values...), aliases.EN...) {
		if containsASCIIAlpha(alias) {
			return true
		}
	}
	for _, pair := range aliases.Pairs {
		if containsASCIIAlpha(pair.ZH) || containsASCIIAlpha(pair.EN) {
			return true
		}
	}
	return false
}

func normalizeAlias(alias string) string {
	return strings.ToLower(strings.TrimSpace(alias))
}

func containsCJK(text string) bool {
	for _, r := range text {
		if r >= '\u4e00' && r <= '\u9fff' {
			return true
		}
	}
	return false
}

func containsASCIIAlpha(text string) bool {
	for _, r := range text {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') {
			return true
		}
	}
	return false
}

func compactStrings(values []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value != "" && !seen[value] {
			seen[value] = true
			out = append(out, value)
		}
	}
	return out
}

func validateOrphanDocuments(bundle Bundle) []ValidationIssue {
	if len(bundle.Documents) < 2 {
		return nil
	}
	inbound := map[string]int{}
	for _, doc := range bundle.Documents {
		for _, link := range doc.Links {
			resolved := resolveKnowledgeRef(doc.Directory, link.Target)
			if strings.HasPrefix(resolved, DefaultRoot+"/") {
				inbound[resolved]++
			}
		}
		for _, target := range append([]string{}, append(doc.Frontmatter.DependsOn, doc.Frontmatter.SeeAlso...)...) {
			resolved := resolveKnowledgeRef(doc.Directory, target)
			if strings.HasPrefix(resolved, DefaultRoot+"/") {
				inbound[resolved]++
			}
		}
	}
	var issues []ValidationIssue
	for _, doc := range bundle.Documents {
		if doc.Path == path.Join(DefaultRoot, "index.md") || doc.Path == path.Join(DefaultRoot, "log.md") || doc.Name == "routing.md" {
			continue
		}
		if inbound[doc.Path] == 0 {
			issues = append(issues, ValidationIssue{Severity: "warning", Code: "orphan_document", Path: doc.Path, Message: "Document is not linked from another knowledge file or frontmatter relation; it may be invisible to routing."})
		}
	}
	return issues
}

func summarize(bundle Bundle, issues []ValidationIssue) Summary {
	summary := Summary{Exists: bundle.Exists, Root: DefaultRoot, Documents: len(bundle.Documents)}
	for _, doc := range bundle.Documents {
		if strings.Contains(doc.Path, "/domains/") && (strings.HasSuffix(doc.Path, "/README.md") || strings.HasSuffix(doc.Path, "/index.md")) {
			summary.Domains++
		}
		if strings.Contains(doc.Path, "/workflows/") {
			summary.Workflows++
		}
		if doc.ModifiedAt > summary.LastModified {
			summary.LastModified = doc.ModifiedAt
		}
	}
	for _, issue := range issues {
		summary.Issues++
		if issue.Severity == "error" {
			summary.Errors++
		} else {
			summary.Warnings++
		}
	}
	return summary
}

func isStale(timestamp string) bool {
	if timestamp == "" {
		return false
	}
	parsed, err := time.Parse(time.RFC3339, timestamp)
	if err != nil {
		return false
	}
	return time.Since(parsed) > 90*24*time.Hour
}

func detectDuplicateRules(bundle Bundle) []DuplicateRule {
	seen := map[string][]string{}
	for _, doc := range bundle.Documents {
		for _, line := range strings.Split(doc.Body, "\n") {
			text := strings.TrimSpace(strings.TrimPrefix(line, "-"))
			if len(text) < 40 {
				continue
			}
			lower := strings.ToLower(text)
			if strings.Contains(lower, "must") || strings.Contains(lower, "shall") || strings.Contains(text, "不得") || strings.Contains(text, "必须") || strings.Contains(text, "禁止") {
				seen[text] = append(seen[text], doc.Path)
			}
		}
	}
	var duplicates []DuplicateRule
	for text, paths := range seen {
		if len(paths) > 1 {
			duplicates = append(duplicates, DuplicateRule{Text: text, Paths: paths})
		}
	}
	return duplicates
}
