package knowledgebase

import (
	"fmt"
	"path"
	"strings"
)

type KnowledgeSection struct {
	ID          string
	Path        string
	Title       string
	Type        string
	Domain      string
	Heading     string
	Tags        []string
	Body        string
	StartLine   int
	EndLine     int
	Tokens      int
	Frontmatter Frontmatter
}

func BuildKnowledgeSections(bundle Bundle) []KnowledgeSection {
	var sections []KnowledgeSection
	for _, doc := range bundle.Documents {
		sections = append(sections, sectionsForDocument(doc)...)
	}
	return sections
}

func sectionsForDocument(doc Document) []KnowledgeSection {
	lines := strings.Split(strings.ReplaceAll(doc.Body, "\r\n", "\n"), "\n")
	type marker struct {
		line int
		text string
	}
	var markers []marker
	for i, line := range lines {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "#") {
			hashes := 0
			for hashes < len(trimmed) && trimmed[hashes] == '#' {
				hashes++
			}
			if hashes > 0 && hashes <= 6 && hashes < len(trimmed) && trimmed[hashes] == ' ' {
				markers = append(markers, marker{line: i, text: strings.TrimSpace(trimmed[hashes:])})
			}
		}
	}
	if len(markers) == 0 {
		body := strings.TrimSpace(doc.Body)
		if body == "" {
			return nil
		}
		return []KnowledgeSection{newKnowledgeSection(doc, 0, len(lines)-1, doc.Frontmatter.Title, body)}
	}
	var sections []KnowledgeSection
	for i, current := range markers {
		end := len(lines) - 1
		if i+1 < len(markers) {
			end = markers[i+1].line - 1
		}
		body := strings.TrimSpace(strings.Join(lines[current.line:end+1], "\n"))
		if body == "" {
			continue
		}
		sections = append(sections, newKnowledgeSection(doc, current.line, end, current.text, body))
	}
	return sections
}

func newKnowledgeSection(doc Document, startLine int, endLine int, heading string, body string) KnowledgeSection {
	heading = strings.TrimSpace(heading)
	if heading == "" {
		heading = doc.Frontmatter.Title
	}
	return KnowledgeSection{
		ID:          fmt.Sprintf("%s#%d", doc.Path, startLine+1),
		Path:        doc.Path,
		Title:       documentTitle(doc),
		Type:        doc.Frontmatter.Type,
		Domain:      inferDomainFromPath(doc.Path),
		Heading:     heading,
		Tags:        append([]string{}, doc.Frontmatter.Tags...),
		Body:        body,
		StartLine:   startLine + 1,
		EndLine:     endLine + 1,
		Tokens:      estimateTokens(body),
		Frontmatter: doc.Frontmatter,
	}
}

func inferDomainFromPath(docPath string) string {
	for _, prefix := range domainPathPrefixes() {
		if !strings.HasPrefix(docPath, prefix) {
			continue
		}
		rest := strings.TrimPrefix(docPath, prefix)
		domain := strings.Split(rest, "/")[0]
		return strings.TrimSpace(domain)
	}
	return ""
}

func domainPathPrefixes() []string {
	return []string{
		DefaultRoot + "/project/domains/",
		DefaultRoot + "/domains/",
	}
}

func domainRoutingPaths(domain string) []string {
	domain = strings.TrimSpace(domain)
	if domain == "" {
		return nil
	}
	paths := make([]string, 0, len(domainPathPrefixes()))
	for _, prefix := range domainPathPrefixes() {
		paths = append(paths, prefix+domain+"/routing.md")
	}
	return paths
}

func domainOverviewPaths(domain string) []string {
	domain = strings.TrimSpace(domain)
	if domain == "" {
		return nil
	}
	paths := make([]string, 0, len(domainPathPrefixes())*2)
	for _, prefix := range domainPathPrefixes() {
		paths = append(paths, prefix+domain+"/index.md", prefix+domain+"/README.md")
	}
	return paths
}

func isDomainOverviewDocument(docPath string) bool {
	for _, prefix := range domainPathPrefixes() {
		if !strings.HasPrefix(docPath, prefix) {
			continue
		}
		rest := strings.TrimPrefix(docPath, prefix)
		if strings.Count(rest, "/") != 1 {
			continue
		}
		name := strings.ToLower(path.Base(rest))
		return name == "index.md" || name == "readme.md"
	}
	return false
}

func estimateTokens(text string) int {
	runes := len([]rune(text))
	if runes == 0 {
		return 0
	}
	tokens := runes / 4
	if tokens < 1 {
		tokens = 1
	}
	return tokens
}

func cleanKnowledgePath(value string) string {
	value = strings.TrimSpace(value)
	value = strings.Trim(value, "`\"'")
	value = strings.TrimPrefix(value, "./")
	value = strings.Split(value, "#")[0]
	value = strings.ReplaceAll(value, "\\", "/")
	value = path.Clean(value)
	if value == "." {
		return ""
	}
	return value
}
