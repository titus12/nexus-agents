package knowledgesync

import (
	"fmt"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"nexus-agents/internal/knowledgebase"
)

type domainRoutingProfile struct {
	AliasesZH  []string
	AliasesEN  []string
	KeywordsZH []string
	KeywordsEN []string
}

var domainRoutingProfiles = map[string]domainRoutingProfile{
	"runtime-platform": {
		AliasesZH:  []string{"运行平台", "控制台", "项目管理"},
		AliasesEN:  []string{"runtime platform", "control plane", "nexus platform"},
		KeywordsZH: []string{"启动", "服务装配", "运行时", "状态存储"},
		KeywordsEN: []string{"startup", "composition", "runtime", "state storage"},
	},
	"model-routing": {
		AliasesZH:  []string{"模型路由", "模型代理", "Codex 路由"},
		AliasesEN:  []string{"model routing", "model proxy", "Codex routing"},
		KeywordsZH: []string{"HTTP 入口", "协议转换", "供应商", "调用链"},
		KeywordsEN: []string{"HTTP entrypoint", "protocol conversion", "provider", "call chain"},
	},
	"template-management": {
		AliasesZH:  []string{"模板管理", "Agent 模板", "工作流模板"},
		AliasesEN:  []string{"template management", "agent templates", "workflow templates"},
		KeywordsZH: []string{"初始化", "同步", "模板布局", "冲突保护"},
		KeywordsEN: []string{"initialization", "synchronization", "template layout", "conflict protection"},
	},
	"workflow-evaluation": {
		AliasesZH:  []string{"工作流评估", "任务运行", "评估闭环"},
		AliasesEN:  []string{"workflow evaluation", "task run", "evaluation loop"},
		KeywordsZH: []string{"会话归属", "遥测", "学习案例", "运行生命周期"},
		KeywordsEN: []string{"session attribution", "telemetry", "learning case", "run lifecycle"},
	},
	"knowledgebase": {
		AliasesZH:  []string{"知识库", "知识同步", "知识检索"},
		AliasesEN:  []string{"knowledge base", "knowledge sync", "knowledge retrieval"},
		KeywordsZH: []string{"仓库扫描", "Proposal", "上下文包", "增量维护"},
		KeywordsEN: []string{"repository scan", "proposal", "context pack", "incremental maintenance"},
	},
}

type domainDetailSection struct {
	Title string
	Body  string
}

var inlineCodePathPattern = regexp.MustCompile("`([^`]*(?:/|\\\\)[^`]*)`")

// BuildDomainDetailBundle expands approved Domain overviews into navigable,
// feature-level documents without invoking a model. It is intentionally fast
// and proposal-only; the approved source text remains the source of truth.
func BuildDomainDetailBundle(projectRoot string) (map[string][]byte, error) {
	domainsRoot := filepath.Join(projectRoot, filepath.FromSlash(DefaultKnowledgeRoot), "domains")
	entries, err := os.ReadDir(domainsRoot)
	if err != nil {
		return nil, err
	}
	files := map[string][]byte{}
	var domainSlugs []string
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		slug := entry.Name()
		indexPath := filepath.Join(domainsRoot, slug, "index.md")
		data, err := os.ReadFile(indexPath)
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return nil, err
		}
		fm, body, ok := knowledgebase.ParseFrontmatter(string(data))
		if !ok || !strings.EqualFold(fm.Type, "Domain") {
			continue
		}
		domainSlugs = append(domainSlugs, slug)
		if fm.GeneratedBy == domainMigrationVersion {
			if err := preserveDomainMarkdown(files, domainsRoot, slug); err != nil {
				return nil, err
			}
			continue
		}
		preamble, details, retained := splitDomainDetails(body)
		details = expandDenseDomainDetails(slug, details)
		if len(details) == 0 {
			return nil, fmt.Errorf("Domain %s has no feature sections to expand", slug)
		}

		var capabilityLinks []string
		usedSlugs := map[string]int{}
		for _, detail := range details {
			detailSlug := migrationSlug(detail.Title)
			if detailSlug == "" {
				detailSlug = "capability"
			}
			usedSlugs[detailSlug]++
			if usedSlugs[detailSlug] > 1 {
				detailSlug = fmt.Sprintf("%s-%d", detailSlug, usedSlugs[detailSlug])
			}
			relative := path.Join(DefaultKnowledgeRoot, "domains", slug, detailSlug+".md")
			description := markdownSummary(detail.Body)
			capabilityLinks = append(capabilityLinks, "- ["+detail.Title+"]("+detailSlug+".md) - "+description)
			files[relative] = []byte(renderDomainFeature(fm, slug, detailSlug, detail, description))
		}
		files[path.Join(DefaultKnowledgeRoot, "domains", slug, "index.md")] = []byte(
			renderDomainOverview(fm, slug, preamble, capabilityLinks, retained),
		)

		// Preserve already approved detail documents on repeated expansion runs.
		if err := preserveDomainMarkdown(files, domainsRoot, slug); err != nil {
			return nil, err
		}
	}
	if len(domainSlugs) == 0 {
		return nil, fmt.Errorf("no approved Domain index pages are available for detail expansion")
	}
	sort.Strings(domainSlugs)
	var projectIndex strings.Builder
	projectIndex.WriteString("# Project Domains\n\n")
	for _, slug := range domainSlugs {
		fm, _, _ := knowledgebase.ParseFrontmatter(string(files[path.Join(DefaultKnowledgeRoot, "domains", slug, "index.md")]))
		projectIndex.WriteString("- [" + fm.Title + "](domains/" + slug + "/index.md) - " + fm.Description + "\n")
	}
	files[path.Join(DefaultKnowledgeRoot, "index.md")] = []byte(projectIndex.String())
	return files, nil
}

func preserveDomainMarkdown(files map[string][]byte, domainsRoot, slug string) error {
	domainDir := filepath.Join(domainsRoot, slug)
	children, err := os.ReadDir(domainDir)
	if err != nil {
		return err
	}
	for _, child := range children {
		if child.IsDir() || strings.ToLower(filepath.Ext(child.Name())) != ".md" {
			continue
		}
		relative := path.Join(DefaultKnowledgeRoot, "domains", slug, child.Name())
		if _, generated := files[relative]; generated {
			continue
		}
		existing, readErr := os.ReadFile(filepath.Join(domainDir, child.Name()))
		if readErr != nil {
			return readErr
		}
		files[relative] = existing
	}
	return nil
}

func expandDenseDomainDetails(slug string, details []domainDetailSection) []domainDetailSection {
	if slug != "knowledgebase" || len(details) != 1 || !strings.EqualFold(details[0].Title, "Project KnowledgeBase") {
		return details
	}
	paragraphs := strings.Split(strings.ReplaceAll(details[0].Body, "\r\n", "\n"), "\n\n")
	titles := []string{
		"Repository scanning and indexing",
		"Domain routing and context retrieval",
		"AI workflow context loading",
		"Knowledge APIs validation and maintenance",
	}
	if len(paragraphs) != len(titles) {
		return details
	}
	expanded := make([]domainDetailSection, 0, len(titles))
	for index, title := range titles {
		body := strings.TrimSpace(paragraphs[index])
		if body == "" {
			return details
		}
		expanded = append(expanded, domainDetailSection{Title: title, Body: body})
	}
	return expanded
}

func splitDomainDetails(body string) (string, []domainDetailSection, string) {
	lines := strings.Split(strings.ReplaceAll(strings.TrimSpace(body), "\r\n", "\n"), "\n")
	if len(lines) > 0 && strings.HasPrefix(strings.TrimSpace(lines[0]), "# ") {
		lines = lines[1:]
	}
	var preamble []string
	var retained []string
	var details []domainDetailSection
	var current *domainDetailSection
	flush := func() {
		if current == nil {
			return
		}
		current.Body = strings.TrimSpace(current.Body)
		switch strings.ToLower(strings.TrimSpace(current.Title)) {
		case "related domains", "verification":
			retained = append(retained, "## "+current.Title+"\n\n"+current.Body)
		default:
			if current.Body != "" {
				details = append(details, *current)
			}
		}
		current = nil
	}
	for _, line := range lines {
		if strings.HasPrefix(line, "## ") {
			flush()
			current = &domainDetailSection{Title: strings.TrimSpace(strings.TrimPrefix(line, "## "))}
			continue
		}
		if current == nil {
			preamble = append(preamble, line)
			continue
		}
		if strings.HasPrefix(line, "### ") {
			line = "## " + strings.TrimSpace(strings.TrimPrefix(line, "### "))
		} else if strings.HasPrefix(line, "#### ") {
			line = "### " + strings.TrimSpace(strings.TrimPrefix(line, "#### "))
		}
		current.Body += line + "\n"
	}
	flush()
	return strings.TrimSpace(strings.Join(preamble, "\n")), details, strings.TrimSpace(strings.Join(retained, "\n\n"))
}

func renderDomainOverview(fm knowledgebase.Frontmatter, slug, preamble string, capabilityLinks []string, retained string) string {
	resource := path.Join(DefaultKnowledgeRoot, "domains", slug, "index.md")
	var body strings.Builder
	body.WriteString(renderDomainFrontmatter(fm, slug, resource))
	body.WriteString("# " + fm.Title + "\n\n")
	if preamble != "" {
		body.WriteString(strings.TrimSpace(preamble) + "\n\n")
	}
	body.WriteString("## Capabilities\n\n")
	body.WriteString(strings.Join(capabilityLinks, "\n") + "\n\n")
	if retained != "" {
		body.WriteString(retained + "\n")
	}
	return body.String()
}

func renderDomainFrontmatter(fm knowledgebase.Frontmatter, slug, resource string) string {
	routing := domainRoutingProfiles[slug]
	sourceRevision := strings.TrimSpace(fm.SourceRevision)
	if sourceRevision == "" {
		sourceRevision = "unknown"
	}
	return "---\n" +
		"type: Domain\n" +
		"title: " + fm.Title + "\n" +
		"description: " + fm.Description + "\n" +
		"resource: " + resource + "\n" +
		"tags: [" + strings.Join(uniqueMigrationStrings(append(fm.Tags, "domain")), ", ") + "]\n" +
		"timestamp: " + time.Now().Format(time.RFC3339) + "\n" +
		"managedBy: openwiki\n" +
		"sourceRevision: " + sourceRevision + "\n" +
		"generatedBy: " + domainMigrationVersion + "\n" +
		"routing:\n" +
		"  aliases:\n" +
		"    zh: [" + strings.Join(routing.AliasesZH, ", ") + "]\n" +
		"    en: [" + strings.Join(routing.AliasesEN, ", ") + "]\n" +
		"  keywords:\n" +
		"    zh: [" + strings.Join(routing.KeywordsZH, ", ") + "]\n" +
		"    en: [" + strings.Join(routing.KeywordsEN, ", ") + "]\n" +
		"---\n"
}

func renderDomainFeature(fm knowledgebase.Frontmatter, domainSlug, detailSlug string, detail domainDetailSection, description string) string {
	resource := path.Join(DefaultKnowledgeRoot, "domains", domainSlug, detailSlug+".md")
	sourcePaths := extractSourcePaths(detail.Body)
	sourceRevision := strings.TrimSpace(fm.SourceRevision)
	if sourceRevision == "" {
		sourceRevision = "unknown"
	}
	var frontmatter strings.Builder
	frontmatter.WriteString("---\n")
	frontmatter.WriteString("type: Guide\n")
	frontmatter.WriteString("title: " + fm.Title + " - " + detail.Title + "\n")
	frontmatter.WriteString("description: " + description + "\n")
	frontmatter.WriteString("resource: " + resource + "\n")
	frontmatter.WriteString("tags: [" + domainSlug + ", feature]\n")
	if len(sourcePaths) > 0 {
		frontmatter.WriteString("sourcePaths: [" + strings.Join(sourcePaths, ", ") + "]\n")
	}
	frontmatter.WriteString("timestamp: " + time.Now().Format(time.RFC3339) + "\n")
	frontmatter.WriteString("managedBy: openwiki\n")
	frontmatter.WriteString("sourceRevision: " + sourceRevision + "\n")
	frontmatter.WriteString("generatedBy: " + domainMigrationVersion + "\n")
	frontmatter.WriteString("---\n")
	return frontmatter.String() +
		"# " + detail.Title + "\n\n" +
		strings.TrimSpace(detail.Body) + "\n\n" +
		"## Domain\n\n- [" + fm.Title + "](index.md)\n"
}

func markdownSummary(body string) string {
	for _, line := range strings.Split(body, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, "```") || strings.HasPrefix(line, "- ") || strings.HasPrefix(line, "|") {
			continue
		}
		line = strings.ReplaceAll(line, "`", "")
		if len([]rune(line)) > 180 {
			line = string([]rune(line)[:180]) + "..."
		}
		return line
	}
	return "Detailed capability, implementation boundaries, relationships, and verification guidance."
}

func extractSourcePaths(body string) []string {
	var paths []string
	for _, match := range inlineCodePathPattern.FindAllStringSubmatch(body, -1) {
		value := filepath.ToSlash(strings.TrimSpace(match[1]))
		if strings.Contains(value, " ") ||
			strings.HasPrefix(value, "http") ||
			strings.HasPrefix(value, "/") ||
			strings.ContainsAny(value, "*{}<>") ||
			!strings.Contains(value, "/") {
			continue
		}
		paths = append(paths, value)
	}
	return uniqueMigrationStrings(paths)
}
