package knowledgebase

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

var markdownLinkPattern = regexp.MustCompile(`\[([^\]]+)\]\(([^)]+)\)`)

func ScanBundle(projectRoot string) (Bundle, error) {
	bundle := Bundle{ProjectRoot: projectRoot, Root: DefaultRoot, ScannedAt: nowStamp()}
	root := filepath.Join(projectRoot, filepath.FromSlash(DefaultRoot))
	stat, err := os.Stat(root)
	if err != nil {
		if os.IsNotExist(err) {
			return normalizeBundle(bundle), nil
		}
		return bundle, err
	}
	if !stat.IsDir() {
		return normalizeBundle(bundle), nil
	}
	bundle.Exists = true
	var docs []Document
	err = filepath.WalkDir(root, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() || strings.ToLower(filepath.Ext(entry.Name())) != ".md" {
			return nil
		}
		content, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(projectRoot, path)
		if err != nil {
			return err
		}
		rel = filepath.ToSlash(rel)
		raw := string(content)
		fm, body, _ := ParseFrontmatter(raw)
		docs = append(docs, Document{
			Path:         rel,
			AbsolutePath: path,
			Name:         entry.Name(),
			Directory:    filepath.ToSlash(filepath.Dir(rel)),
			Frontmatter:  fm,
			Body:         body,
			Raw:          raw,
			Links:        extractMarkdownLinks(body),
			SizeBytes:    info.Size(),
			ModifiedAt:   info.ModTime().Format(time.RFC3339),
			Reserved:     entry.Name() == "index.md" || entry.Name() == "log.md",
		})
		return nil
	})
	if err != nil {
		return bundle, err
	}
	sort.SliceStable(docs, func(i, j int) bool { return docs[i].Path < docs[j].Path })
	bundle.Documents = docs
	return normalizeBundle(bundle), nil
}

func extractMarkdownLinks(body string) []DocLink {
	lines := strings.Split(body, "\n")
	var links []DocLink
	for index, line := range lines {
		matches := markdownLinkPattern.FindAllStringSubmatch(line, -1)
		for _, match := range matches {
			if len(match) == 3 {
				links = append(links, DocLink{Text: match[1], Target: match[2], Line: index + 1})
			}
		}
	}
	return links
}
