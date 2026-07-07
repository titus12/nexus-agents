package knowledgebase

import (
	"fmt"
	"html"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

const (
	renderKindDirectory = "directory"
	renderKindDocument  = "document"
)

var inlineCodePattern = regexp.MustCompile("`([^`]+)`")
var markdownInlineLinkPattern = regexp.MustCompile(`\[([^\]]+)\]\(([^)]+)\)`)

func BuildRenderTree(projectRoot string) (RenderTree, error) {
	bundle, err := ScanBundle(projectRoot)
	if err != nil {
		return RenderTree{}, err
	}
	root := &renderNodeBuilder{
		node: RenderNode{
			Path:  DefaultRoot,
			Title: path.Base(DefaultRoot),
			Kind:  renderKindDirectory,
		},
		dirs: map[string]*renderNodeBuilder{},
	}
	for _, doc := range bundle.Documents {
		root.addDocument(doc)
	}
	tree := RenderTree{Root: DefaultRoot, Nodes: root.sortedChildren()}
	return normalizeRenderTree(tree), nil
}

type renderNodeBuilder struct {
	node RenderNode
	dirs map[string]*renderNodeBuilder
	docs []RenderNode
}

func (builder *renderNodeBuilder) addDocument(doc Document) {
	rel := strings.TrimPrefix(doc.Path, DefaultRoot+"/")
	parts := strings.Split(rel, "/")
	current := builder
	currentPath := DefaultRoot
	for _, part := range parts[:len(parts)-1] {
		currentPath = path.Join(currentPath, part)
		if current.dirs == nil {
			current.dirs = map[string]*renderNodeBuilder{}
		}
		child := current.dirs[part]
		if child == nil {
			child = &renderNodeBuilder{
				node: RenderNode{
					Path:  currentPath,
					Title: titleFromPath(part),
					Kind:  renderKindDirectory,
				},
				dirs: map[string]*renderNodeBuilder{},
			}
			current.dirs[part] = child
		}
		current = child
	}
	docNode := RenderNode{Path: doc.Path, Title: documentTitle(doc), Type: doc.Frontmatter.Type, Kind: renderKindDocument}
	if doc.Name == "index.md" || doc.Name == "README.md" {
		current.node.IndexDocument = doc.Path
		if current.node.Title == titleFromPath(path.Base(current.node.Path)) || current.node.Title == path.Base(current.node.Path) {
			current.node.Title = docNode.Title
		}
	}
	current.docs = append(current.docs, docNode)
}

func (builder *renderNodeBuilder) sortedChildren() []RenderNode {
	dirNames := make([]string, 0, len(builder.dirs))
	for name := range builder.dirs {
		dirNames = append(dirNames, name)
	}
	sort.Strings(dirNames)
	nodes := make([]RenderNode, 0, len(dirNames)+len(builder.docs))
	for _, name := range dirNames {
		child := builder.dirs[name]
		node := child.node
		node.Children = child.sortedChildren()
		nodes = append(nodes, node)
	}
	sort.SliceStable(builder.docs, func(i, j int) bool {
		return docSortKey(builder.docs[i].Path) < docSortKey(builder.docs[j].Path)
	})
	nodes = append(nodes, builder.docs...)
	return nodes
}

func docSortKey(docPath string) string {
	base := path.Base(docPath)
	switch base {
	case "index.md":
		return "00-" + docPath
	case "README.md":
		return "01-" + docPath
	case "routing.md":
		return "02-" + docPath
	default:
		return "10-" + docPath
	}
}

func documentTitle(doc Document) string {
	if doc.Frontmatter.Title != "" {
		return doc.Frontmatter.Title
	}
	base := strings.TrimSuffix(doc.Name, path.Ext(doc.Name))
	return titleFromPath(base)
}

func titleFromPath(value string) string {
	value = strings.TrimSuffix(value, ".md")
	value = strings.ReplaceAll(value, "_", " ")
	value = strings.ReplaceAll(value, "-", " ")
	if strings.EqualFold(value, "README") {
		return "Overview"
	}
	if strings.EqualFold(value, "index") {
		return "Index"
	}
	words := strings.Fields(value)
	for i, word := range words {
		if word == strings.ToUpper(word) {
			continue
		}
		words[i] = strings.ToUpper(word[:1]) + word[1:]
	}
	return strings.Join(words, " ")
}

func RenderDocument(projectRoot string, relPath string) (RenderedDocument, error) {
	bundle, err := ScanBundle(projectRoot)
	if err != nil {
		return RenderedDocument{}, err
	}
	clean := filepath.ToSlash(filepath.Clean(relPath))
	for _, doc := range bundle.Documents {
		if doc.Path == clean {
			title := doc.Frontmatter.Title
			if title == "" {
				title = doc.Path
			}
			return RenderedDocument{Path: doc.Path, Title: title, Frontmatter: doc.Frontmatter, HTML: renderMarkdown(doc.Body), Raw: doc.Raw}, nil
		}
	}
	return RenderedDocument{}, fmt.Errorf("knowledge document %s not found", relPath)
}

func renderMarkdown(body string) string {
	var out []string
	inCode := false
	var code []string
	inUL := false
	inOL := false
	inTable := false
	var tableRows [][]string
	closeLists := func() {
		if inUL {
			out = append(out, "</ul>")
			inUL = false
		}
		if inOL {
			out = append(out, "</ol>")
			inOL = false
		}
	}
	flushTable := func() {
		if !inTable {
			return
		}
		out = append(out, renderTable(tableRows))
		tableRows = nil
		inTable = false
	}
	for _, line := range strings.Split(body, "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "```") {
			flushTable()
			closeLists()
			if inCode {
				out = append(out, "<pre><code>"+html.EscapeString(strings.Join(code, "\n"))+"</code></pre>")
				code = nil
				inCode = false
			} else {
				inCode = true
			}
			continue
		}
		if inCode {
			code = append(code, line)
			continue
		}
		if isMarkdownTableRow(trimmed) {
			closeLists()
			inTable = true
			tableRows = append(tableRows, splitMarkdownTableRow(trimmed))
			continue
		}
		flushTable()
		switch {
		case strings.HasPrefix(trimmed, "# "):
			closeLists()
			out = append(out, "<h1>"+renderInline(strings.TrimSpace(strings.TrimPrefix(trimmed, "# ")))+"</h1>")
		case strings.HasPrefix(trimmed, "## "):
			closeLists()
			out = append(out, "<h2>"+renderInline(strings.TrimSpace(strings.TrimPrefix(trimmed, "## ")))+"</h2>")
		case strings.HasPrefix(trimmed, "### "):
			closeLists()
			out = append(out, "<h3>"+renderInline(strings.TrimSpace(strings.TrimPrefix(trimmed, "### ")))+"</h3>")
		case strings.HasPrefix(trimmed, "- "):
			if inOL {
				out = append(out, "</ol>")
				inOL = false
			}
			if !inUL {
				out = append(out, "<ul>")
				inUL = true
			}
			out = append(out, "<li>"+renderInline(strings.TrimSpace(strings.TrimPrefix(trimmed, "- ")))+"</li>")
		case isOrderedListItem(trimmed):
			if inUL {
				out = append(out, "</ul>")
				inUL = false
			}
			if !inOL {
				out = append(out, "<ol>")
				inOL = true
			}
			_, item, _ := strings.Cut(trimmed, ".")
			out = append(out, "<li>"+renderInline(strings.TrimSpace(item))+"</li>")
		case strings.HasPrefix(trimmed, ">"):
			closeLists()
			out = append(out, "<blockquote>"+renderInline(strings.TrimSpace(strings.TrimPrefix(trimmed, ">")))+"</blockquote>")
		case trimmed == "":
			closeLists()
			out = append(out, "")
		default:
			closeLists()
			out = append(out, "<p>"+renderInline(trimmed)+"</p>")
		}
	}
	flushTable()
	closeLists()
	if inCode {
		out = append(out, "<pre><code>"+html.EscapeString(strings.Join(code, "\n"))+"</code></pre>")
	}
	return strings.Join(out, "\n")
}

func renderInline(text string) string {
	escaped := html.EscapeString(text)
	escaped = markdownInlineLinkPattern.ReplaceAllStringFunc(escaped, func(match string) string {
		parts := markdownInlineLinkPattern.FindStringSubmatch(match)
		if len(parts) != 3 {
			return match
		}
		label := parts[1]
		target := parts[2]
		if strings.HasPrefix(target, "http://") || strings.HasPrefix(target, "https://") {
			return `<a href="` + target + `" target="_blank" rel="noreferrer">` + label + `</a>`
		}
		return `<a href="#" data-kb-link="` + html.EscapeString(target) + `">` + label + `</a>`
	})
	return inlineCodePattern.ReplaceAllString(escaped, "<code>$1</code>")
}

func isOrderedListItem(text string) bool {
	index := strings.Index(text, ". ")
	if index <= 0 {
		return false
	}
	for _, ch := range text[:index] {
		if ch < '0' || ch > '9' {
			return false
		}
	}
	return true
}

func isMarkdownTableRow(text string) bool {
	return strings.HasPrefix(text, "|") && strings.HasSuffix(text, "|") && strings.Count(text, "|") >= 2
}

func splitMarkdownTableRow(text string) []string {
	text = strings.Trim(text, "|")
	parts := strings.Split(text, "|")
	for i := range parts {
		parts[i] = strings.TrimSpace(parts[i])
	}
	return parts
}

func isTableSeparator(row []string) bool {
	if len(row) == 0 {
		return false
	}
	for _, cell := range row {
		cleaned := strings.Trim(cell, " :-")
		if cleaned != "" {
			return false
		}
		if !strings.Contains(cell, "-") {
			return false
		}
	}
	return true
}

func renderTable(rows [][]string) string {
	if len(rows) == 0 {
		return ""
	}
	var out []string
	out = append(out, "<table>")
	startBody := 0
	if len(rows) > 1 && isTableSeparator(rows[1]) {
		out = append(out, "<thead><tr>")
		for _, cell := range rows[0] {
			out = append(out, "<th>"+renderInline(cell)+"</th>")
		}
		out = append(out, "</tr></thead>")
		startBody = 2
	}
	out = append(out, "<tbody>")
	for _, row := range rows[startBody:] {
		if isTableSeparator(row) {
			continue
		}
		out = append(out, "<tr>")
		for _, cell := range row {
			out = append(out, "<td>"+renderInline(cell)+"</td>")
		}
		out = append(out, "</tr>")
	}
	out = append(out, "</tbody></table>")
	return strings.Join(out, "")
}
