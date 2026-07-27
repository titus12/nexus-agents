package wikicompiler

import (
	"fmt"
	"nexus-agents/internal/knowledgebase"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode/utf8"
)

var resourceLinePattern = regexp.MustCompile(`(?m)^resource:\s*["']?([^"'\n]+)["']?\s*$`)
var controlDocumentReferencePattern = regexp.MustCompile("(?i)`?(?:openwiki/|KnowledgeBase/project/)INSTRUCTIONS\\.md`?")
var markdownLinkTargetPattern = regexp.MustCompile(`\]\(([^)]+)\)`)

var canonicalOKFTypes = map[string]string{
	"index": "Index", "log": "Log", "project": "Project", "routing": "Routing",
	"domain": "Domain", "guide": "Guide", "workflow": "Workflow", "schema": "Schema",
	"template": "Template", "decision": "Decision", "reference": "Reference",
	"codingrules": "CodingRules", "rules": "Rules", "checklist": "Checklist",
}

func ReadAndNormalizeOpenWiki(repositoryRoot, knowledgeRoot string) (map[string][]byte, []string, error) {
	if strings.TrimSpace(knowledgeRoot) == "" {
		knowledgeRoot = "KnowledgeBase/project"
	}
	openWikiRoot := filepath.Join(repositoryRoot, "openwiki")
	info, err := os.Stat(openWikiRoot)
	if err != nil {
		return nil, nil, fmt.Errorf("OpenWiki output is missing: %w", err)
	}
	if !info.IsDir() {
		return nil, nil, fmt.Errorf("OpenWiki output path is not a directory")
	}
	type openWikiFile struct {
		relative string
		content  string
	}
	var sourceFiles []openWikiFile
	var generatedDomainIndexes []openWikiFile
	err = filepath.WalkDir(openWikiRoot, func(filePath string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			return nil
		}
		relative, err := filepath.Rel(openWikiRoot, filePath)
		if err != nil {
			return err
		}
		relative = filepath.ToSlash(relative)
		if strings.EqualFold(relative, "INSTRUCTIONS.md") ||
			strings.EqualFold(relative, "domains/index.md") ||
			strings.EqualFold(relative, "_plan.md") ||
			strings.ToLower(filepath.Ext(relative)) != ".md" {
			return nil
		}
		data, err := os.ReadFile(filePath)
		if err != nil {
			return err
		}
		if !utf8.Valid(data) {
			return fmt.Errorf("OpenWiki output is not valid UTF-8: %s", relative)
		}
		if isGeneratedDomainDirectoryIndex(relative, string(data)) {
			generatedDomainIndexes = append(generatedDomainIndexes, openWikiFile{relative: relative, content: string(data)})
			return nil
		}
		sourceFiles = append(sourceFiles, openWikiFile{relative: relative, content: string(data)})
		return nil
	})
	if err != nil {
		return nil, nil, err
	}
	if len(sourceFiles) == 0 {
		return nil, nil, fmt.Errorf("OpenWiki produced no Markdown knowledge files")
	}
	destinations := make(map[string]string, len(sourceFiles))
	destinationOwners := map[string]string{}
	for _, source := range sourceFiles {
		destination := domainDestination(knowledgeRoot, source.relative)
		if owner, duplicate := destinationOwners[destination]; duplicate {
			return nil, nil, fmt.Errorf("OpenWiki outputs %s and %s map to the same domain path %s", owner, source.relative, destination)
		}
		destinations[source.relative] = destination
		destinationOwners[destination] = source.relative
	}
	files := map[string][]byte{}
	var warnings []string
	for _, source := range sourceFiles {
		destination := destinations[source.relative]
		normalized, warning := normalizeMarkdown(destination, source.content, knowledgeRoot)
		normalized = rewriteMarkdownLinks(normalized, source.relative, destination, destinations)
		if warning != "" {
			warnings = append(warnings, warning)
		}
		files[destination] = []byte(normalized)
	}
	for _, source := range generatedDomainIndexes {
		destination := domainDestination(knowledgeRoot, source.relative)
		if _, exists := files[destination]; exists {
			continue
		}
		normalized, warning := normalizeMarkdown(destination, source.content, knowledgeRoot)
		if warning != "" {
			warnings = append(warnings, warning)
		}
		files[destination] = []byte(normalized)
	}
	if err := ensureDomainIndexes(files, knowledgeRoot); err != nil {
		return nil, nil, err
	}
	files[path.Join(knowledgeRoot, "index.md")] = []byte(buildProjectDomainIndex(files, knowledgeRoot))
	sort.Strings(warnings)
	return files, warnings, nil
}

func normalizeMarkdown(destination, content, knowledgeRoot string) (string, string) {
	content = strings.ReplaceAll(content, "\r\n", "\n")
	content = strings.TrimPrefix(content, "\ufeff")
	var warnings []string
	if isReservedDestination(destination, knowledgeRoot) {
		if _, body, ok := splitFrontmatter(content); ok {
			content = body
		}
	} else if frontmatter, body, ok := splitFrontmatter(content); ok {
		normalized, frontmatterWarnings := normalizeConceptFrontmatter(destination, frontmatter, knowledgeRoot)
		warnings = append(warnings, frontmatterWarnings...)
		content = "---\n" + normalized + "\n---\n" + strings.TrimLeft(body, "\n")
	}
	content = strings.ReplaceAll(content, "(openwiki/", "("+knowledgeRoot+"/")
	content = strings.ReplaceAll(content, "`openwiki/", "`"+knowledgeRoot+"/")
	content = controlDocumentReferencePattern.ReplaceAllString(content, "the Nexus-selected committed source snapshot")
	if !strings.HasSuffix(content, "\n") {
		content += "\n"
	}
	return content, strings.Join(warnings, "; ")
}

func normalizeConceptFrontmatter(destination, frontmatter, knowledgeRoot string) (string, []string) {
	lines := strings.Split(strings.TrimRight(frontmatter, "\n"), "\n")
	var warnings []string
	typeIndex, resourceIndex := -1, -1
	hasTimestamp, hasSourcePaths, hasTags := false, false, false
	originalResource := ""
	for index, line := range lines {
		key, value, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		switch strings.TrimSpace(key) {
		case "type":
			typeIndex = index
			rawType := strings.Trim(strings.TrimSpace(value), `"'`)
			canonical, valid := canonicalOKFTypes[strings.ToLower(strings.ReplaceAll(rawType, " ", ""))]
			if isDomainIndexDestination(destination, knowledgeRoot) {
				canonical, valid = "Domain", true
			}
			if !valid {
				canonical = "Guide"
				warnings = append(warnings, "Normalized unsupported OKF type on "+destination+" to Guide")
			}
			lines[index] = "type: " + canonical
		case "resource":
			resourceIndex = index
			originalResource = normalizeFrontmatterPath(value)
			lines[index] = "resource: " + destination
		case "timestamp":
			hasTimestamp = strings.TrimSpace(value) != ""
		case "tags":
			hasTags = strings.TrimSpace(value) != "" && strings.TrimSpace(value) != "[]"
		case "sourcePaths", "source_paths":
			hasSourcePaths = true
		}
	}
	if typeIndex < 0 {
		documentType := "Guide"
		if isDomainIndexDestination(destination, knowledgeRoot) {
			documentType = "Domain"
		}
		lines = append(lines, "type: "+documentType)
		warnings = append(warnings, "Added missing OKF type on "+destination)
	}
	if resourceIndex < 0 {
		lines = append(lines, "resource: "+destination)
		warnings = append(warnings, "Added missing OKF resource on "+destination)
	}
	if !hasTimestamp {
		lines = append(lines, "timestamp: "+time.Now().Format(time.RFC3339))
	}
	if !hasTags {
		tag := "knowledge"
		if isDomainIndexDestination(destination, knowledgeRoot) {
			rest := strings.TrimPrefix(destination, path.Join(knowledgeRoot, "domains")+"/")
			tag = strings.Split(rest, "/")[0]
		}
		lines = append(lines, "tags: ["+tag+", domain]")
		warnings = append(warnings, "Added missing OKF tags on "+destination)
	}
	if !hasSourcePaths && isSourceEvidencePath(originalResource, destination, knowledgeRoot) {
		lines = append(lines, "sourcePaths: ["+originalResource+"]")
	}
	lines = ensureDomainRoutingAliases(lines, destination, knowledgeRoot)
	return strings.Join(lines, "\n"), warnings
}

func ensureDomainRoutingAliases(lines []string, destination, knowledgeRoot string) []string {
	if !isDomainIndexDestination(destination, knowledgeRoot) {
		return lines
	}
	routingIndex, aliasesIndex, hasAliases := locateRoutingAliases(lines)
	if hasAliases {
		return lines
	}

	title := frontmatterScalar(strings.Join(lines, "\n"), "title")
	zhAliases := []string{}
	if containsCJKText(title) {
		zhAliases = append(zhAliases, strings.TrimSpace(title))
	}
	domainSlug := strings.TrimSuffix(path.Base(path.Dir(destination)), path.Ext(path.Base(path.Dir(destination))))
	enAliases := uniqueNonEmptyStrings(
		strings.ReplaceAll(domainSlug, "-", " "),
		englishWords(title),
	)
	if len(zhAliases) == 0 && len(enAliases) == 0 {
		return lines
	}

	var aliasLines []string
	if len(zhAliases) > 0 {
		aliasLines = append(aliasLines, "    zh: "+yamlInlineList(zhAliases))
	}
	if len(enAliases) > 0 {
		aliasLines = append(aliasLines, "    en: "+yamlInlineList(enAliases))
	}
	if aliasesIndex >= 0 {
		aliasesEnd := aliasesIndex + 1
		for aliasesEnd < len(lines) {
			if strings.TrimSpace(lines[aliasesEnd]) != "" && leadingSpaceCount(lines[aliasesEnd]) <= 2 {
				break
			}
			aliasesEnd++
		}
		block := append([]string{"  aliases:"}, aliasLines...)
		return replaceLines(lines, aliasesIndex, aliasesEnd, block...)
	}
	if routingIndex >= 0 {
		block := append([]string{"  aliases:"}, aliasLines...)
		return insertLines(lines, routingIndex+1, block...)
	}
	block := append([]string{"routing:", "  aliases:"}, aliasLines...)
	return append(lines, block...)
}

func locateRoutingAliases(lines []string) (routingIndex, aliasesIndex int, hasAliases bool) {
	routingIndex, aliasesIndex = -1, -1
	inRouting := false
	inAliases := false
	for index, line := range lines {
		indent := leadingSpaceCount(line)
		trimmed := strings.TrimSpace(line)
		if trimmed == "" {
			continue
		}
		if indent == 0 {
			key, _, ok := strings.Cut(trimmed, ":")
			inRouting = ok && strings.TrimSpace(key) == "routing"
			inAliases = false
			if inRouting {
				routingIndex = index
			}
			continue
		}
		if !inRouting {
			continue
		}
		if indent == 2 {
			key, value, ok := strings.Cut(trimmed, ":")
			inAliases = ok && strings.TrimSpace(key) == "aliases"
			if !inAliases {
				continue
			}
			aliasesIndex = index
			if inlineListHasValues(value) {
				hasAliases = true
			}
			continue
		}
		if inAliases && indent >= 4 {
			_, value, ok := strings.Cut(strings.TrimPrefix(trimmed, "- "), ":")
			if ok && inlineListHasValues(value) {
				hasAliases = true
			}
		}
	}
	return routingIndex, aliasesIndex, hasAliases
}

func inlineListHasValues(value string) bool {
	value = strings.TrimSpace(value)
	return value != "" && value != "[]"
}

func insertLines(lines []string, index int, values ...string) []string {
	out := make([]string, 0, len(lines)+len(values))
	out = append(out, lines[:index]...)
	out = append(out, values...)
	out = append(out, lines[index:]...)
	return out
}

func replaceLines(lines []string, start, end int, values ...string) []string {
	out := make([]string, 0, len(lines)-(end-start)+len(values))
	out = append(out, lines[:start]...)
	out = append(out, values...)
	out = append(out, lines[end:]...)
	return out
}

func yamlInlineList(values []string) string {
	quoted := make([]string, 0, len(values))
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" {
			continue
		}
		value = strings.NewReplacer(`\`, `\\`, `"`, `\"`, "\r", " ", "\n", " ").Replace(value)
		quoted = append(quoted, `"`+value+`"`)
	}
	return "[" + strings.Join(quoted, ", ") + "]"
}

func uniqueNonEmptyStrings(values ...string) []string {
	seen := map[string]bool{}
	var result []string
	for _, value := range values {
		value = strings.TrimSpace(value)
		key := strings.ToLower(value)
		if value == "" || seen[key] {
			continue
		}
		seen[key] = true
		result = append(result, value)
	}
	return result
}

func englishWords(value string) string {
	var builder strings.Builder
	pendingSpace := false
	for _, r := range value {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || r == '#' || r == '+' || r == '.' {
			if pendingSpace && builder.Len() > 0 {
				builder.WriteByte(' ')
			}
			builder.WriteRune(r)
			pendingSpace = false
			continue
		}
		pendingSpace = builder.Len() > 0
	}
	return strings.TrimSpace(builder.String())
}

func containsCJKText(value string) bool {
	for _, r := range value {
		if r >= '\u4e00' && r <= '\u9fff' {
			return true
		}
	}
	return false
}

func leadingSpaceCount(line string) int {
	count := 0
	for _, r := range line {
		if r != ' ' {
			break
		}
		count++
	}
	return count
}

func domainDestination(knowledgeRoot, relative string) string {
	relative = path.Clean(strings.TrimPrefix(strings.ReplaceAll(relative, "\\", "/"), "./"))
	if strings.EqualFold(relative, "index.md") {
		return path.Join(knowledgeRoot, "index.md")
	}
	if strings.HasPrefix(strings.ToLower(relative), "domains/") {
		rest := strings.TrimPrefix(relative, "domains/")
		parts := strings.Split(rest, "/")
		if len(parts) == 1 {
			slug := sanitizeDomainSlug(strings.TrimSuffix(parts[0], path.Ext(parts[0])))
			return path.Join(knowledgeRoot, "domains", slug, "index.md")
		}
		slug := sanitizeDomainSlug(parts[0])
		tail := path.Join(parts[1:]...)
		if strings.EqualFold(tail, "README.md") || strings.EqualFold(tail, "domain.md") {
			tail = "index.md"
		}
		return path.Join(knowledgeRoot, "domains", slug, tail)
	}
	base := strings.TrimSuffix(path.Base(relative), path.Ext(relative))
	if strings.EqualFold(base, "index") || strings.EqualFold(base, "readme") {
		base = path.Base(path.Dir(relative))
	}
	return path.Join(knowledgeRoot, "domains", sanitizeDomainSlug(base), "index.md")
}

func sanitizeDomainSlug(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	var builder strings.Builder
	lastHyphen := false
	for _, r := range value {
		switch {
		case r >= 'a' && r <= 'z', r >= '0' && r <= '9':
			builder.WriteRune(r)
			lastHyphen = false
		case r == '-' || r == '_' || r == ' ':
			if builder.Len() > 0 && !lastHyphen {
				builder.WriteByte('-')
				lastHyphen = true
			}
		}
	}
	slug := strings.Trim(builder.String(), "-")
	if slug == "" {
		return "domain"
	}
	return slug
}

func isReservedDestination(destination, knowledgeRoot string) bool {
	return destination == path.Join(knowledgeRoot, "index.md") ||
		destination == "KnowledgeBase/index.md" ||
		destination == "KnowledgeBase/log.md"
}

func isDomainIndexDestination(destination, knowledgeRoot string) bool {
	prefix := path.Join(knowledgeRoot, "domains") + "/"
	if !strings.HasPrefix(destination, prefix) || !strings.HasSuffix(destination, "/index.md") {
		return false
	}
	rest := strings.TrimPrefix(destination, prefix)
	return strings.Count(rest, "/") == 1
}

func isGeneratedDomainDirectoryIndex(relative, content string) bool {
	relative = path.Clean(strings.TrimPrefix(strings.ReplaceAll(relative, "\\", "/"), "./"))
	parts := strings.Split(relative, "/")
	if len(parts) != 3 || !strings.EqualFold(parts[0], "domains") || !strings.EqualFold(parts[2], "index.md") {
		return false
	}
	frontmatter, body, hasFrontmatter := splitFrontmatter(strings.ReplaceAll(content, "\r\n", "\n"))
	if !hasFrontmatter {
		return false
	}
	documentType := strings.ToLower(frontmatterScalar(frontmatter, "type"))
	description := strings.ToLower(frontmatterScalar(frontmatter, "description"))
	normalizedBody := strings.ToLower(strings.TrimSpace(body))
	return documentType == "documentation index" ||
		strings.Contains(description, "files and subdirectories") ||
		strings.HasPrefix(normalizedBody, "# directories")
}

func rewriteMarkdownLinks(content, sourceRelative, destination string, destinations map[string]string) string {
	return markdownLinkTargetPattern.ReplaceAllStringFunc(content, func(match string) string {
		submatches := markdownLinkTargetPattern.FindStringSubmatch(match)
		if len(submatches) != 2 {
			return match
		}
		target := strings.TrimSpace(submatches[1])
		if target == "" || strings.HasPrefix(target, "#") || strings.Contains(target, "://") {
			return match
		}
		anchor := ""
		if index := strings.Index(target, "#"); index >= 0 {
			anchor = target[index:]
			target = target[:index]
		}
		resolved := path.Clean(path.Join(path.Dir(sourceRelative), strings.TrimPrefix(target, "./")))
		targetDestination, ok := destinations[resolved]
		if !ok {
			return match
		}
		relativeTarget, err := filepath.Rel(filepath.FromSlash(path.Dir(destination)), filepath.FromSlash(targetDestination))
		if err != nil {
			return match
		}
		return "](" + filepath.ToSlash(relativeTarget) + anchor + ")"
	})
}

func ensureDomainIndexes(files map[string][]byte, knowledgeRoot string) error {
	prefix := path.Join(knowledgeRoot, "domains") + "/"
	domainFiles := map[string][]string{}
	for relative := range files {
		if !strings.HasPrefix(relative, prefix) {
			continue
		}
		rest := strings.TrimPrefix(relative, prefix)
		parts := strings.Split(rest, "/")
		if len(parts) < 2 {
			continue
		}
		domainFiles[parts[0]] = append(domainFiles[parts[0]], relative)
	}
	for slug, paths := range domainFiles {
		indexPath := path.Join(knowledgeRoot, "domains", slug, "index.md")
		if _, exists := files[indexPath]; exists {
			continue
		}
		sort.Strings(paths)
		title := domainTitle(slug)
		var builder strings.Builder
		builder.WriteString("---\n")
		builder.WriteString("type: Domain\n")
		builder.WriteString("title: " + title + "\n")
		builder.WriteString("description: Core project capability for " + title + ".\n")
		builder.WriteString("resource: " + indexPath + "\n")
		builder.WriteString("tags: [" + slug + ", domain]\n")
		builder.WriteString("timestamp: " + time.Now().Format(time.RFC3339) + "\n")
		builder.WriteString("---\n# " + title + "\n\n")
		for _, child := range paths {
			if child == indexPath {
				continue
			}
			builder.WriteString("- [" + documentLabel(child) + "](" + path.Base(child) + ")\n")
		}
		files[indexPath] = []byte(builder.String())
	}
	return nil
}

func buildProjectDomainIndex(files map[string][]byte, knowledgeRoot string) string {
	prefix := path.Join(knowledgeRoot, "domains") + "/"
	type domainEntry struct {
		slug        string
		title       string
		description string
	}
	var domains []domainEntry
	for relative, data := range files {
		if !isDomainIndexDestination(relative, knowledgeRoot) {
			continue
		}
		rest := strings.TrimPrefix(relative, prefix)
		slug := strings.Split(rest, "/")[0]
		frontmatter, _, _ := splitFrontmatter(string(data))
		title := frontmatterScalar(frontmatter, "title")
		if title == "" {
			title = domainTitle(slug)
		}
		description := frontmatterScalar(frontmatter, "description")
		if description == "" {
			description = "Core project capability."
		}
		domains = append(domains, domainEntry{slug: slug, title: title, description: description})
	}
	sort.Slice(domains, func(i, j int) bool { return domains[i].slug < domains[j].slug })
	var builder strings.Builder
	builder.WriteString("# Project Domains\n\n")
	for _, domain := range domains {
		builder.WriteString("- [" + domain.title + "](domains/" + domain.slug + "/index.md) - " + domain.description + "\n")
	}
	return builder.String()
}

func frontmatterScalar(frontmatter, expected string) string {
	for _, line := range strings.Split(frontmatter, "\n") {
		key, value, ok := strings.Cut(line, ":")
		if ok && strings.TrimSpace(key) == expected {
			return strings.Trim(strings.TrimSpace(value), `"'`)
		}
	}
	return ""
}

func domainTitle(slug string) string {
	parts := strings.Fields(strings.ReplaceAll(slug, "-", " "))
	for index := range parts {
		if parts[index] != "" {
			parts[index] = strings.ToUpper(parts[index][:1]) + parts[index][1:]
		}
	}
	return strings.Join(parts, " ")
}

func documentLabel(relative string) string {
	name := strings.TrimSuffix(path.Base(relative), path.Ext(relative))
	return domainTitle(name)
}

func thinDomainPaths(files map[string][]byte, knowledgeRoot string) []string {
	var thin []string
	for relative, data := range files {
		if !isDomainIndexDestination(relative, knowledgeRoot) {
			continue
		}
		_, body, hasFrontmatter := splitFrontmatter(string(data))
		if !hasFrontmatter {
			body = string(data)
		}
		normalized := strings.ToLower(strings.TrimSpace(body))
		bodyRunes := len([]rune(normalized))
		directoryPlaceholder := strings.Contains(normalized, "files and subdirectories") ||
			strings.Contains(normalized, "# directories")
		if bodyRunes < 300 || (bodyRunes < 600 && directoryPlaceholder) {
			thin = append(thin, relative)
		}
	}
	sort.Strings(thin)
	return thin
}

func domainAliasRepairPaths(files map[string][]byte, knowledgeRoot string) []string {
	var paths []string
	for relative, data := range files {
		if !isDomainIndexDestination(relative, knowledgeRoot) {
			continue
		}
		frontmatter, _, ok := knowledgebase.ParseFrontmatter(string(data))
		if !ok || !hasChineseRoutingAlias(frontmatter.Routing.Aliases) || !hasEnglishRoutingAlias(frontmatter.Routing.Aliases) {
			paths = append(paths, relative)
		}
	}
	sort.Strings(paths)
	return paths
}

func hasChineseRoutingAlias(aliases knowledgebase.RoutingAliases) bool {
	for _, alias := range append(append([]string{}, aliases.Values...), aliases.ZH...) {
		if containsCJKText(alias) {
			return true
		}
	}
	for _, pair := range aliases.Pairs {
		if containsCJKText(pair.ZH) || containsCJKText(pair.EN) {
			return true
		}
	}
	return false
}

func hasEnglishRoutingAlias(aliases knowledgebase.RoutingAliases) bool {
	for _, alias := range append(append([]string{}, aliases.Values...), aliases.EN...) {
		if containsASCIIAlphaText(alias) {
			return true
		}
	}
	for _, pair := range aliases.Pairs {
		if containsASCIIAlphaText(pair.ZH) || containsASCIIAlphaText(pair.EN) {
			return true
		}
	}
	return false
}

func containsASCIIAlphaText(value string) bool {
	for _, r := range value {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') {
			return true
		}
	}
	return false
}

func domainRepairPrompt(thinPaths, aliasPaths []string, language string) string {
	var builder strings.Builder
	builder.WriteString("Nexus rejected part of the generated Domain knowledge. OpenWiki owns and overwrites every index.md file, so edit the corresponding `openwiki/domains/<slug>/domain.md` source files, not index.md. Do not answer with a plan only.\n")
	if language = strings.TrimSpace(language); language != "" {
		builder.WriteString("Keep titles, descriptions, and explanatory prose in `" + language + "` except for product names and API identifiers.\n")
	}
	if len(thinPaths) > 0 {
		builder.WriteString("\n## Repair thin Domain pages\n\n")
		builder.WriteString("Each listed page must include responsibility and boundary, important HTTP/code entrypoints, owned state or data, related Domains with explicit Markdown links, source paths, limitations, and verification. Preserve stable facts from approved existing knowledge.\n")
		for _, relative := range thinPaths {
			builder.WriteString("- `" + domainSourcePath(relative) + "`\n")
		}
	}
	if len(aliasPaths) > 0 {
		builder.WriteString("\n## Repair bilingual routing aliases\n\n")
		builder.WriteString("Each listed Domain frontmatter must contain `routing.aliases.zh` and `routing.aliases.en`. Add at least one real Chinese alias containing Chinese characters and one specific English alias. Use stable capability names, product/API names, and common user task terminology. Do not use broad standalone aliases such as system, data, feature, or module; put broad search terms under `routing.keywords` instead. Preserve existing valid aliases and the Domain body.\n")
		for _, relative := range aliasPaths {
			builder.WriteString("- `" + domainSourcePath(relative) + "`\n")
		}
	}
	return builder.String()
}

func domainSourcePath(relative string) string {
	target := strings.TrimPrefix(relative, "KnowledgeBase/project/")
	return strings.TrimSuffix(target, "/index.md") + "/domain.md"
}

func normalizeFrontmatterPath(value string) string {
	value = strings.Trim(strings.TrimSpace(value), `"'`)
	value = strings.TrimPrefix(strings.ReplaceAll(value, "\\", "/"), "./")
	return path.Clean(value)
}

func isSourceEvidencePath(value, destination, knowledgeRoot string) bool {
	if value == "" || value == "." || value == destination {
		return false
	}
	if strings.HasPrefix(value, "openwiki/") {
		return path.Join(knowledgeRoot, strings.TrimPrefix(value, "openwiki/")) != destination
	}
	return !strings.HasPrefix(value, knowledgeRoot+"/")
}

func splitFrontmatter(content string) (string, string, bool) {
	if !strings.HasPrefix(content, "---\n") {
		return "", content, false
	}
	end := strings.Index(content[4:], "\n---")
	if end < 0 {
		return "", content, false
	}
	frontmatter := content[4 : 4+end]
	body := strings.TrimLeft(content[4+end+4:], "\n")
	return frontmatter, body, true
}

func AnnotateProvenance(files map[string][]byte, revision, version string) {
	for relative, data := range files {
		if relative == "KnowledgeBase/index.md" || relative == "KnowledgeBase/log.md" || relative == "KnowledgeBase/project/index.md" {
			continue
		}
		content := strings.ReplaceAll(string(data), "\r\n", "\n")
		frontmatter, body, ok := splitFrontmatter(content)
		if !ok {
			continue
		}
		lines := []string{strings.TrimRight(frontmatter, "\n")}
		if !hasFrontmatterKey(frontmatter, "managedBy", "managed_by") {
			lines = append(lines, "managedBy: openwiki")
		}
		if strings.TrimSpace(revision) != "" && !hasFrontmatterKey(frontmatter, "sourceRevision", "source_revision") {
			lines = append(lines, "sourceRevision: "+strings.TrimSpace(revision))
		}
		if strings.TrimSpace(version) != "" && !hasFrontmatterKey(frontmatter, "generatedBy", "generated_by") {
			lines = append(lines, "generatedBy: openwiki@"+strings.TrimPrefix(strings.TrimSpace(version), "v"))
		}
		files[relative] = []byte("---\n" + strings.Join(lines, "\n") + "\n---\n" + strings.TrimLeft(body, "\n"))
	}
}

func hasFrontmatterKey(frontmatter string, keys ...string) bool {
	for _, line := range strings.Split(frontmatter, "\n") {
		key, _, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		for _, expected := range keys {
			if strings.TrimSpace(key) == expected {
				return true
			}
		}
	}
	return false
}
