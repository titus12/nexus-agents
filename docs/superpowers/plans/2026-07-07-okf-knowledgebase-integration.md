# OKF Knowledge Base Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build OKF-compatible project knowledge bases into Nexus Agents, lightly integrate knowledge routing into workflows, and add Kiso-inspired documentation rendering plus OKF-inspired validation/maintenance.

**Architecture:** Add a focused `internal/knowledgebase` Go package that treats each imported project as the source of truth and scans `design/KnowledgeBase` as an OKF-compatible bundle. Expose project-scoped HTTP APIs for init, scan, validation, route preview, maintenance report, and static documentation rendering; update workflow templates to load `project/routing.md` before implementation work. Keep QAnything/RAG out of the core path: Nexus owns deterministic OKF routing and validation, while external RAG remains optional.

**Tech Stack:** Go 1.22 standard library, existing `internal/catalog` project model, existing `internal/httpapi` routes, Vue 3 + TypeScript + Naive UI frontend, Markdown files under `templates/knowledgebase`, existing `scripts/verify_all.ps1` verification.

---

## Scope and Non-Goals

This plan implements the most valuable parts of OKF validation and Kiso-style display inside Nexus Agents:

- OKF-compatible file layout and frontmatter validation.
- Project profiles for `go-game-server` and `unity-client` knowledge bases.
- Deterministic knowledge routing preview for workflows.
- Maintenance report for stale docs, missing metadata, broken links, large files, and duplicated hard rules.
- Read-only rendered knowledge browser inspired by Kiso.
- Light workflow-template changes that add a `Knowledge Loading` phase.

This plan deliberately does **not** implement:

- A vector database, embeddings, semantic search, or QAnything integration.
- A full Markdown editor.
- Automatic rewriting of business rules without review.
- Changes to global Codex files under `C:\Users\Administrator\.codex`.

## File Structure

### New backend package

- Create `D:\workspace\src\nexus-agents\internal\knowledgebase\types.go`  
  Owns public data structures: `Bundle`, `Document`, `Frontmatter`, `ValidationIssue`, `ValidationReport`, `MaintenanceReport`, `RoutePreview`, `RenderNode`, and profile names.

- Create `D:\workspace\src\nexus-agents\internal\knowledgebase\frontmatter.go`  
  Parses minimal YAML frontmatter without adding dependencies. Supports string fields and one-line list fields like `tags: [go, actor]`.

- Create `D:\workspace\src\nexus-agents\internal\knowledgebase\scan.go`  
  Scans `design/KnowledgeBase`, reads Markdown documents, computes relative paths, detects reserved files `index.md` and `log.md`, and extracts Markdown links.

- Create `D:\workspace\src\nexus-agents\internal\knowledgebase\validate.go`  
  Validates OKF-compatible metadata, required reserved files, broken relative links, stale timestamps, excessive file size, and duplicate hard-rule text.

- Create `D:\workspace\src\nexus-agents\internal\knowledgebase\route.go`  
  Implements deterministic routing preview by reading `project/routing.md` and domain `routing.md` sections using simple heading/keyword matching.

- Create `D:\workspace\src\nexus-agents\internal\knowledgebase\init.go`  
  Initializes a knowledge base from template files without overwriting existing files unless `force=true` is passed.

- Create `D:\workspace\src\nexus-agents\internal\knowledgebase\render.go`  
  Produces a Kiso-inspired tree and safe HTML-ish rendered Markdown fragments for API consumers. Rendering remains basic: headings, paragraphs, lists, code fences, and links.

- Create tests:
  - `D:\workspace\src\nexus-agents\internal\knowledgebase\frontmatter_test.go`
  - `D:\workspace\src\nexus-agents\internal\knowledgebase\scan_test.go`
  - `D:\workspace\src\nexus-agents\internal\knowledgebase\validate_test.go`
  - `D:\workspace\src\nexus-agents\internal\knowledgebase\route_test.go`
  - `D:\workspace\src\nexus-agents\internal\knowledgebase\init_test.go`
  - `D:\workspace\src\nexus-agents\internal\knowledgebase\render_test.go`

### New template files

- Create `D:\workspace\src\nexus-agents\templates\knowledgebase\go-game-server\design\KnowledgeBase\...`  
  Profile for btd-game-server-like Go game servers.

- Create `D:\workspace\src\nexus-agents\templates\knowledgebase\unity-client\design\KnowledgeBase\...`  
  Profile for btd-client-like Unity clients.

- Create `D:\workspace\src\nexus-agents\templates\skills\codex\wf-kb-maintenance\SKILL.md`  
  Codex skill entry for knowledge-base maintenance.

- Create `D:\workspace\src\nexus-agents\templates\skills\codex\wf-kb-maintenance\agents\openai.yaml`  
  Skill metadata for the `/wf` UI.

- Create `D:\workspace\src\nexus-agents\templates\workflows\kb-maintenance.md`  
  Source-of-truth workflow for OKF knowledge maintenance.

- Create `D:\workspace\src\nexus-agents\templates\workflows\kb-maintenance.graph.json`  
  Minimal graph for the maintenance workflow.

### Existing backend changes

- Modify `D:\workspace\src\nexus-agents\internal\catalog\catalog.go`  
  Add `KnowledgeSummary` to `Project` and include it during import/rescan.

- Modify `D:\workspace\src\nexus-agents\internal\catalog\project_scan.go`  
  Add `ScanProjectKnowledgeSummary(projectRoot string) knowledgebase.Summary`.

- Modify `D:\workspace\src\nexus-agents\internal\httpapi\server.go`  
  Add project-scoped knowledge routes under `/api/projects/{id}/knowledge`.

- Modify `D:\workspace\src\nexus-agents\internal\httpapi\server_test.go`  
  Add API tests for scan, validate, init, render, route preview, and maintenance report.

### Existing frontend changes

- Modify `D:\workspace\src\nexus-agents\web\src\types.ts`  
  Add TypeScript types mirroring the backend knowledge API.

- Modify `D:\workspace\src\nexus-agents\web\src\api.ts`  
  Add API functions for project knowledge operations.

- Modify `D:\workspace\src\nexus-agents\web\src\App.vue`  
  Add a project-detail knowledge panel with tabs: Overview, Validation, Routing, Docs, Maintenance.

- Modify `D:\workspace\src\nexus-agents\web\src\styles.css`  
  Add focused styles for the knowledge tree, issue list, rendered Markdown, and route preview.

### Existing workflow/template changes

- Modify these workflow templates to include a short `Knowledge Loading` section:
  - `D:\workspace\src\nexus-agents\templates\workflows\go-bugfix.md`
  - `D:\workspace\src\nexus-agents\templates\workflows\go-feature-development.md`
  - `D:\workspace\src\nexus-agents\templates\workflows\go-modify-existing.md`
  - `D:\workspace\src\nexus-agents\templates\workflows\go-refactor.md`
  - `D:\workspace\src\nexus-agents\templates\workflows\go-code-review.md`
  - `D:\workspace\src\nexus-agents\templates\workflows\unity-bug-investigation.md`
  - `D:\workspace\src\nexus-agents\templates\workflows\unity-logic-modification.md`
  - `D:\workspace\src\nexus-agents\templates\workflows\unity-ui-feature-development.md`

---

## Task 1: Define Knowledge Base Types

**Files:**
- Create: `D:\workspace\src\nexus-agents\internal\knowledgebase\types.go`
- Test: `D:\workspace\src\nexus-agents\internal\knowledgebase\frontmatter_test.go`

- [ ] **Step 1: Create the package directory**

Run:

```powershell
New-Item -ItemType Directory -Force -Path internal\knowledgebase
```

Expected: `internal\knowledgebase` exists.

- [ ] **Step 2: Add shared types**

Create `internal/knowledgebase/types.go` with:

```go
package knowledgebase

import "time"

const DefaultRoot = "design/KnowledgeBase"

const (
	ProfileGoGameServer = "go-game-server"
	ProfileUnityClient  = "unity-client"
)

type Summary struct {
	Exists       bool   `json:"exists"`
	Root         string `json:"root"`
	Profile      string `json:"profile,omitempty"`
	Documents    int    `json:"documents"`
	Domains      int    `json:"domains"`
	Workflows    int    `json:"workflows"`
	Issues       int    `json:"issues"`
	Errors       int    `json:"errors"`
	Warnings     int    `json:"warnings"`
	LastModified string `json:"lastModified,omitempty"`
}

type Bundle struct {
	ProjectRoot string     `json:"projectRoot"`
	Root        string     `json:"root"`
	Exists      bool       `json:"exists"`
	Documents   []Document `json:"documents"`
	ScannedAt   string     `json:"scannedAt"`
}

type Document struct {
	Path         string      `json:"path"`
	AbsolutePath string      `json:"-"`
	Name         string      `json:"name"`
	Directory    string      `json:"directory"`
	Frontmatter  Frontmatter `json:"frontmatter"`
	Body         string      `json:"body,omitempty"`
	Links        []DocLink   `json:"links"`
	SizeBytes    int64       `json:"sizeBytes"`
	ModifiedAt   string      `json:"modifiedAt"`
	Reserved     bool        `json:"reserved"`
}

type Frontmatter struct {
	Type        string   `json:"type,omitempty"`
	Title       string   `json:"title,omitempty"`
	Description string   `json:"description,omitempty"`
	Resource    string   `json:"resource,omitempty"`
	Tags        []string `json:"tags,omitempty"`
	Timestamp   string   `json:"timestamp,omitempty"`
	Raw         string   `json:"raw,omitempty"`
}

type DocLink struct {
	Text   string `json:"text"`
	Target string `json:"target"`
	Line   int    `json:"line"`
}

type ValidationReport struct {
	Root      string            `json:"root"`
	Summary   Summary           `json:"summary"`
	Issues    []ValidationIssue `json:"issues"`
	CheckedAt string            `json:"checkedAt"`
}

type ValidationIssue struct {
	Severity string `json:"severity"`
	Code     string `json:"code"`
	Path     string `json:"path"`
	Line     int    `json:"line,omitempty"`
	Message  string `json:"message"`
}

type MaintenanceReport struct {
	Root             string            `json:"root"`
	Summary          Summary           `json:"summary"`
	Issues           []ValidationIssue `json:"issues"`
	StaleDocuments   []Document        `json:"staleDocuments"`
	LargeDocuments   []Document        `json:"largeDocuments"`
	DuplicateRules   []DuplicateRule   `json:"duplicateRules"`
	SuggestedActions []string          `json:"suggestedActions"`
	CheckedAt        string            `json:"checkedAt"`
}

type DuplicateRule struct {
	Text  string   `json:"text"`
	Paths []string `json:"paths"`
}

type RoutePreview struct {
	Task             string   `json:"task"`
	MatchedDomain    string   `json:"matchedDomain,omitempty"`
	RequiredFiles    []string `json:"requiredFiles"`
	OptionalFiles    []string `json:"optionalFiles"`
	Reason           string   `json:"reason"`
	MissingFiles     []string `json:"missingFiles"`
	RoutingDocuments []string `json:"routingDocuments"`
}

type RenderTree struct {
	Root  string       `json:"root"`
	Nodes []RenderNode `json:"nodes"`
}

type RenderNode struct {
	Path     string       `json:"path"`
	Title    string       `json:"title"`
	Type     string       `json:"type,omitempty"`
	Children []RenderNode `json:"children,omitempty"`
}

type RenderedDocument struct {
	Path        string      `json:"path"`
	Title       string      `json:"title"`
	Frontmatter Frontmatter `json:"frontmatter"`
	HTML        string      `json:"html"`
}

func nowStamp() string {
	return time.Now().Format(time.RFC3339)
}
```

- [ ] **Step 3: Run package test**

Run:

```powershell
go test ./internal/knowledgebase
```

Expected: `[no test files]`.

- [ ] **Step 4: Commit**

```powershell
git add internal/knowledgebase/types.go
git commit -m "feat: add knowledge base domain types"
```

---

## Task 2: Implement Minimal OKF Frontmatter Parser

**Files:**
- Create: `D:\workspace\src\nexus-agents\internal\knowledgebase\frontmatter.go`
- Create: `D:\workspace\src\nexus-agents\internal\knowledgebase\frontmatter_test.go`

- [ ] **Step 1: Write failing parser tests**

Create `internal/knowledgebase/frontmatter_test.go`:

```go
package knowledgebase

import "testing"

func TestParseFrontmatterWithTags(t *testing.T) {
	input := "---\ntype: Domain\ntitle: Go Actor System\ndescription: Actor rules.\nresource: design/KnowledgeBase/domains/actor\ntags: [go, actor, server]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n# Body\nText"
	fm, body, ok := ParseFrontmatter(input)
	if !ok {
		t.Fatal("expected frontmatter")
	}
	if fm.Type != "Domain" || fm.Title != "Go Actor System" || fm.Resource != "design/KnowledgeBase/domains/actor" {
		t.Fatalf("unexpected frontmatter: %#v", fm)
	}
	if len(fm.Tags) != 3 || fm.Tags[0] != "go" || fm.Tags[2] != "server" {
		t.Fatalf("unexpected tags: %#v", fm.Tags)
	}
	if body != "# Body\nText" {
		t.Fatalf("unexpected body %q", body)
	}
}

func TestParseFrontmatterMissingBlock(t *testing.T) {
	fm, body, ok := ParseFrontmatter("# Title\nNo metadata")
	if ok {
		t.Fatal("expected no frontmatter")
	}
	if fm.Type != "" {
		t.Fatalf("expected empty frontmatter, got %#v", fm)
	}
	if body != "# Title\nNo metadata" {
		t.Fatalf("body should be unchanged, got %q", body)
	}
}
```

- [ ] **Step 2: Run failing tests**

```powershell
go test ./internal/knowledgebase -run TestParseFrontmatter -v
```

Expected: FAIL with `undefined: ParseFrontmatter`.

- [ ] **Step 3: Implement parser**

Create `internal/knowledgebase/frontmatter.go`:

```go
package knowledgebase

import "strings"

func ParseFrontmatter(content string) (Frontmatter, string, bool) {
	text := strings.ReplaceAll(content, "\r\n", "\n")
	if !strings.HasPrefix(text, "---\n") {
		return Frontmatter{}, text, false
	}
	end := strings.Index(text[4:], "\n---")
	if end < 0 {
		return Frontmatter{}, text, false
	}
	raw := text[4 : 4+end]
	after := text[4+end+len("\n---"):]
	after = strings.TrimPrefix(after, "\n")
	fm := Frontmatter{Raw: raw}
	for _, line := range strings.Split(raw, "\n") {
		key, value, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		key = strings.TrimSpace(key)
		value = strings.Trim(strings.TrimSpace(value), "\"")
		switch key {
		case "type":
			fm.Type = value
		case "title":
			fm.Title = value
		case "description":
			fm.Description = value
		case "resource":
			fm.Resource = value
		case "tags":
			fm.Tags = parseInlineList(value)
		case "timestamp":
			fm.Timestamp = value
		}
	}
	return fm, after, true
}

func parseInlineList(value string) []string {
	value = strings.TrimSpace(value)
	value = strings.TrimPrefix(value, "[")
	value = strings.TrimSuffix(value, "]")
	if strings.TrimSpace(value) == "" {
		return nil
	}
	parts := strings.Split(value, ",")
	items := make([]string, 0, len(parts))
	for _, part := range parts {
		item := strings.Trim(strings.TrimSpace(part), "\"")
		if item != "" {
			items = append(items, item)
		}
	}
	return items
}
```

- [ ] **Step 4: Verify tests pass**

```powershell
go test ./internal/knowledgebase -run TestParseFrontmatter -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add internal/knowledgebase/frontmatter.go internal/knowledgebase/frontmatter_test.go
git commit -m "feat: parse okf frontmatter"
```

---

## Task 3: Scan OKF-Compatible Knowledge Bundles

**Files:**
- Create: `D:\workspace\src\nexus-agents\internal\knowledgebase\scan.go`
- Create: `D:\workspace\src\nexus-agents\internal\knowledgebase\scan_test.go`

- [ ] **Step 1: Write failing scan tests**

Create `internal/knowledgebase/scan_test.go`:

```go
package knowledgebase

import (
	"os"
	"path/filepath"
	"testing"
)

func TestScanBundleReadsMarkdownAndLinks(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), "---\ntype: Index\ntitle: Knowledge Index\ndescription: Root.\nresource: design/KnowledgeBase/index.md\ntags: [index]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n[Actor](./domains/actor/index.md)")
	writeTestFile(t, filepath.Join(kb, "domains", "actor", "index.md"), "---\ntype: Domain\ntitle: Actor\ndescription: Actor.\nresource: design/KnowledgeBase/domains/actor/index.md\ntags: [actor]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n# Actor")

	bundle, err := ScanBundle(root)
	if err != nil {
		t.Fatal(err)
	}
	if !bundle.Exists || len(bundle.Documents) != 2 {
		t.Fatalf("unexpected bundle: %#v", bundle)
	}
	totalLinks := 0
	for _, doc := range bundle.Documents {
		totalLinks += len(doc.Links)
	}
	if totalLinks != 1 {
		t.Fatalf("expected one link, got %#v", bundle.Documents)
	}
}

func writeTestFile(t *testing.T, path string, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}
```

- [ ] **Step 2: Run failing test**

```powershell
go test ./internal/knowledgebase -run TestScanBundleReadsMarkdownAndLinks -v
```

Expected: FAIL with `undefined: ScanBundle`.

- [ ] **Step 3: Implement scanner**

Create `internal/knowledgebase/scan.go`:

```go
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
			return bundle, nil
		}
		return bundle, err
	}
	if !stat.IsDir() {
		return bundle, nil
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
		fm, body, _ := ParseFrontmatter(string(content))
		docs = append(docs, Document{
			Path:         rel,
			AbsolutePath: path,
			Name:         entry.Name(),
			Directory:    filepath.ToSlash(filepath.Dir(rel)),
			Frontmatter:  fm,
			Body:         body,
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
	return bundle, nil
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
```

- [ ] **Step 4: Verify tests pass**

```powershell
go test ./internal/knowledgebase -run TestScanBundleReadsMarkdownAndLinks -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add internal/knowledgebase/scan.go internal/knowledgebase/scan_test.go
git commit -m "feat: scan okf knowledge bundles"
```

---

## Task 4: Validate OKF Metadata, Links, and Maintenance Signals

**Files:**
- Create: `D:\workspace\src\nexus-agents\internal\knowledgebase\validate.go`
- Create: `D:\workspace\src\nexus-agents\internal\knowledgebase\validate_test.go`

- [ ] **Step 1: Write failing validation tests**

Create `internal/knowledgebase/validate_test.go` with tests that assert:

```go
package knowledgebase

import (
	"path/filepath"
	"testing"
)

func TestValidateBundleReportsMissingMetadataAndBrokenLinks(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), "---\ntype: Index\ntitle: Root\ndescription: Root.\nresource: design/KnowledgeBase/index.md\ntags: [index]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n[Missing](./missing.md)")
	writeTestFile(t, filepath.Join(kb, "domains", "actor", "routing.md"), "# Routing\nNo metadata")

	report, err := Validate(root)
	if err != nil {
		t.Fatal(err)
	}
	if report.Summary.Documents != 2 {
		t.Fatalf("expected 2 docs, got %#v", report.Summary)
	}
	if !hasIssue(report.Issues, "missing_frontmatter") {
		t.Fatalf("expected missing_frontmatter issue: %#v", report.Issues)
	}
	if !hasIssue(report.Issues, "broken_link") {
		t.Fatalf("expected broken_link issue: %#v", report.Issues)
	}
}

func TestMaintenanceReportFlagsLargeDocs(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	largeBody := make([]byte, 130000)
	for i := range largeBody {
		largeBody[i] = 'x'
	}
	writeTestFile(t, filepath.Join(kb, "index.md"), "---\ntype: Index\ntitle: Root\ndescription: Root.\nresource: design/KnowledgeBase/index.md\ntags: [index]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n"+string(largeBody))
	writeTestFile(t, filepath.Join(kb, "log.md"), "---\ntype: Log\ntitle: Log\ndescription: Changes.\nresource: design/KnowledgeBase/log.md\ntags: [log]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n")

	report, err := Maintenance(root)
	if err != nil {
		t.Fatal(err)
	}
	if len(report.LargeDocuments) != 1 {
		t.Fatalf("expected one large document, got %#v", report.LargeDocuments)
	}
}

func hasIssue(issues []ValidationIssue, code string) bool {
	for _, issue := range issues {
		if issue.Code == code {
			return true
		}
	}
	return false
}
```

- [ ] **Step 2: Run failing tests**

```powershell
go test ./internal/knowledgebase -run "TestValidate|TestMaintenance" -v
```

Expected: FAIL with `undefined: Validate` and `undefined: Maintenance`.

- [ ] **Step 3: Implement validation and maintenance**

Create `internal/knowledgebase/validate.go`:

```go
package knowledgebase

import (
	"path"
	"strings"
	"time"
)

const largeDocumentBytes int64 = 120000

func Validate(projectRoot string) (ValidationReport, error) {
	bundle, err := ScanBundle(projectRoot)
	if err != nil {
		return ValidationReport{}, err
	}
	issues := validateBundle(bundle)
	summary := summarize(bundle, issues)
	return ValidationReport{Root: DefaultRoot, Summary: summary, Issues: issues, CheckedAt: nowStamp()}, nil
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
	return maintenance, nil
}

func validateBundle(bundle Bundle) []ValidationIssue {
	var issues []ValidationIssue
	if !bundle.Exists {
		return append(issues, ValidationIssue{Severity: "warning", Code: "missing_bundle", Path: DefaultRoot, Message: "Knowledge base root does not exist."})
	}
	docByPath := map[string]bool{}
	for _, doc := range bundle.Documents {
		docByPath[doc.Path] = true
	}
	if !docByPath[path.Join(DefaultRoot, "index.md")] {
		issues = append(issues, ValidationIssue{Severity: "error", Code: "missing_index", Path: path.Join(DefaultRoot, "index.md"), Message: "OKF bundle should include index.md."})
	}
	if !docByPath[path.Join(DefaultRoot, "log.md")] {
		issues = append(issues, ValidationIssue{Severity: "warning", Code: "missing_log", Path: path.Join(DefaultRoot, "log.md"), Message: "OKF bundle should include log.md for knowledge changes."})
	}
	for _, doc := range bundle.Documents {
		issues = append(issues, validateDocument(doc)...)
		for _, link := range doc.Links {
			if strings.HasPrefix(link.Target, "http://") || strings.HasPrefix(link.Target, "https://") || strings.HasPrefix(link.Target, "#") {
				continue
			}
			target := strings.Split(link.Target, "#")[0]
			if target == "" {
				continue
			}
			resolved := path.Clean(path.Join(doc.Directory, target))
			if !docByPath[resolved] {
				issues = append(issues, ValidationIssue{Severity: "error", Code: "broken_link", Path: doc.Path, Line: link.Line, Message: "Markdown link target does not exist: " + link.Target})
			}
		}
	}
	return issues
}

func validateDocument(doc Document) []ValidationIssue {
	var issues []ValidationIssue
	fm := doc.Frontmatter
	if fm.Raw == "" {
		return []ValidationIssue{{Severity: "error", Code: "missing_frontmatter", Path: doc.Path, Message: "Document is missing OKF YAML frontmatter."}}
	}
	if fm.Type == "" {
		issues = append(issues, ValidationIssue{Severity: "error", Code: "missing_type", Path: doc.Path, Message: "Frontmatter field type is required."})
	}
	if fm.Title == "" {
		issues = append(issues, ValidationIssue{Severity: "warning", Code: "missing_title", Path: doc.Path, Message: "Frontmatter field title is recommended."})
	}
	if fm.Description == "" {
		issues = append(issues, ValidationIssue{Severity: "warning", Code: "missing_description", Path: doc.Path, Message: "Frontmatter field description is recommended."})
	}
	if fm.Resource == "" {
		issues = append(issues, ValidationIssue{Severity: "warning", Code: "missing_resource", Path: doc.Path, Message: "Frontmatter field resource is recommended."})
	}
	if fm.Timestamp == "" {
		issues = append(issues, ValidationIssue{Severity: "warning", Code: "missing_timestamp", Path: doc.Path, Message: "Frontmatter field timestamp is recommended."})
	}
	return issues
}

func summarize(bundle Bundle, issues []ValidationIssue) Summary {
	summary := Summary{Exists: bundle.Exists, Root: DefaultRoot, Documents: len(bundle.Documents)}
	for _, doc := range bundle.Documents {
		if strings.Contains(doc.Path, "/domains/") && strings.HasSuffix(doc.Path, "/index.md") {
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
			if strings.Contains(strings.ToLower(text), "must") || strings.Contains(text, "不得") || strings.Contains(text, "必须") {
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
```

- [ ] **Step 4: Verify tests pass**

```powershell
go test ./internal/knowledgebase -run "TestValidate|TestMaintenance" -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add internal/knowledgebase/validate.go internal/knowledgebase/validate_test.go
git commit -m "feat: validate okf knowledge bundles"
```

---

## Task 5: Add Deterministic Route Preview

**Files:**
- Create: `D:\workspace\src\nexus-agents\internal\knowledgebase\route.go`
- Create: `D:\workspace\src\nexus-agents\internal\knowledgebase\route_test.go`

- [ ] **Step 1: Write failing route test**

Create `internal/knowledgebase/route_test.go`:

```go
package knowledgebase

import (
	"path/filepath"
	"testing"
)

func TestRoutePreviewMatchesQuestTask(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), okfDoc("Index", "Root", "design/KnowledgeBase/index.md", "# Root"))
	writeTestFile(t, filepath.Join(kb, "log.md"), okfDoc("Log", "Log", "design/KnowledgeBase/log.md", "# Log"))
	writeTestFile(t, filepath.Join(kb, "project", "routing.md"), okfDoc("Routing", "Project Routing", "design/KnowledgeBase/project/routing.md", "## Quest or task-system changes\nRead:\n\n1. `domains/quest/routing.md`\n2. `domains/quest/quest-system.md`\n3. `domains/testing/go-testing.md`"))
	writeTestFile(t, filepath.Join(kb, "domains", "quest", "routing.md"), okfDoc("Routing", "Quest Routing", "design/KnowledgeBase/domains/quest/routing.md", "## Bug investigation\nRead:\n\n1. `quest-system.md`\n2. `anti-patterns.md`"))
	writeTestFile(t, filepath.Join(kb, "domains", "quest", "quest-system.md"), okfDoc("Concept", "Quest System", "design/KnowledgeBase/domains/quest/quest-system.md", "# Quest"))
	writeTestFile(t, filepath.Join(kb, "domains", "testing", "go-testing.md"), okfDoc("Guide", "Go Testing", "design/KnowledgeBase/domains/testing/go-testing.md", "# Testing"))

	preview, err := PreviewRoute(root, "修复 quest 状态异常 bug")
	if err != nil {
		t.Fatal(err)
	}
	if preview.MatchedDomain != "quest" {
		t.Fatalf("expected quest domain, got %#v", preview)
	}
	if len(preview.RequiredFiles) == 0 || preview.RequiredFiles[0] != "design/KnowledgeBase/domains/quest/routing.md" {
		t.Fatalf("unexpected required files: %#v", preview.RequiredFiles)
	}
}

func okfDoc(kind, title, resource, body string) string {
	return "---\ntype: " + kind + "\ntitle: " + title + "\ndescription: Test.\nresource: " + resource + "\ntags: [test]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n" + body
}
```

- [ ] **Step 2: Run failing route test**

```powershell
go test ./internal/knowledgebase -run TestRoutePreviewMatchesQuestTask -v
```

Expected: FAIL with `undefined: PreviewRoute`.

- [ ] **Step 3: Implement route preview**

Create `internal/knowledgebase/route.go`:

```go
package knowledgebase

import "strings"

func PreviewRoute(projectRoot string, task string) (RoutePreview, error) {
	bundle, err := ScanBundle(projectRoot)
	if err != nil {
		return RoutePreview{}, err
	}
	preview := RoutePreview{Task: task}
	if !bundle.Exists {
		preview.Reason = "Knowledge base does not exist."
		return preview, nil
	}
	domain := matchDomain(task)
	preview.MatchedDomain = domain
	preview.RoutingDocuments = append(preview.RoutingDocuments, DefaultRoot+"/project/routing.md")
	switch domain {
	case "quest":
		preview.RequiredFiles = []string{DefaultRoot + "/domains/quest/routing.md", DefaultRoot + "/domains/quest/quest-system.md", DefaultRoot + "/domains/testing/go-testing.md"}
		preview.Reason = "Task mentions quest/task state, so quest and testing knowledge are required."
	case "ui":
		preview.RequiredFiles = []string{DefaultRoot + "/domains/ui/routing.md", DefaultRoot + "/domains/ui/coding-rules.md", DefaultRoot + "/domains/ui/data-flow-rules.md"}
		preview.OptionalFiles = []string{DefaultRoot + "/domains/ui/templates/mvvm-view.md", DefaultRoot + "/domains/ui/templates/mvvm-viewmodel.md"}
		preview.Reason = "Task mentions UI, View, ViewModel, panel, prefab, or Unity UI."
	case "actor":
		preview.RequiredFiles = []string{DefaultRoot + "/domains/actor/routing.md", DefaultRoot + "/domains/actor/coding-rules.md", DefaultRoot + "/domains/actor/anti-patterns.md"}
		preview.Reason = "Task mentions actor, lifecycle, concurrency, or server runtime behavior."
	case "config":
		preview.RequiredFiles = []string{DefaultRoot + "/domains/config/routing.md", DefaultRoot + "/domains/config/pmconf-pattern.md", DefaultRoot + "/domains/config/cross-config.md"}
		preview.Reason = "Task mentions config, pmconf, generated configuration, or cross-server configuration."
	default:
		preview.RequiredFiles = []string{DefaultRoot + "/project/routing.md"}
		preview.Reason = "No domain keyword matched; project routing is required before choosing a domain."
	}
	existing := map[string]bool{}
	for _, doc := range bundle.Documents {
		existing[doc.Path] = true
	}
	for _, file := range preview.RequiredFiles {
		if !existing[file] {
			preview.MissingFiles = append(preview.MissingFiles, file)
		}
	}
	return preview, nil
}

func matchDomain(task string) string {
	lower := strings.ToLower(task)
	switch {
	case containsAny(lower, "quest", "任务", "task-system", "task system", "状态异常"):
		return "quest"
	case containsAny(lower, "ui", "unity", "viewmodel", "view model", "prefab", "panel", "弹窗", "界面"):
		return "ui"
	case containsAny(lower, "actor", "concurrency", "lifecycle", "生命周期", "并发"):
		return "actor"
	case containsAny(lower, "config", "pmconf", "配置", "表"):
		return "config"
	default:
		return ""
	}
}

func containsAny(text string, needles ...string) bool {
	for _, needle := range needles {
		if strings.Contains(text, needle) {
			return true
		}
	}
	return false
}
```

- [ ] **Step 4: Verify route test passes**

```powershell
go test ./internal/knowledgebase -run TestRoutePreviewMatchesQuestTask -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add internal/knowledgebase/route.go internal/knowledgebase/route_test.go
git commit -m "feat: preview knowledge routing"
```

---

## Task 6: Add Profile Templates and Init Service

**Files:**
- Create templates under `D:\workspace\src\nexus-agents\templates\knowledgebase\go-game-server\design\KnowledgeBase\...`
- Create templates under `D:\workspace\src\nexus-agents\templates\knowledgebase\unity-client\design\KnowledgeBase\...`
- Create: `D:\workspace\src\nexus-agents\internal\knowledgebase\init.go`
- Create: `D:\workspace\src\nexus-agents\internal\knowledgebase\init_test.go`

- [ ] **Step 1: Write failing init test**

Create `internal/knowledgebase/init_test.go`:

```go
package knowledgebase

import (
	"os"
	"path/filepath"
	"testing"
)

func TestInitProfileCreatesKnowledgeBase(t *testing.T) {
	root := t.TempDir()
	result, err := InitProfile(root, ProfileGoGameServer, false)
	if err != nil {
		t.Fatal(err)
	}
	if result.Created == 0 {
		t.Fatalf("expected created files, got %#v", result)
	}
	if _, err := os.Stat(filepath.Join(root, filepath.FromSlash(DefaultRoot), "index.md")); err != nil {
		t.Fatalf("index.md not created: %v", err)
	}
	if _, err := os.Stat(filepath.Join(root, filepath.FromSlash(DefaultRoot), "project", "routing.md")); err != nil {
		t.Fatalf("routing.md not created: %v", err)
	}
}
```

- [ ] **Step 2: Run failing init test**

```powershell
go test ./internal/knowledgebase -run TestInitProfileCreatesKnowledgeBase -v
```

Expected: FAIL with `undefined: InitProfile`.

- [ ] **Step 3: Implement init using filesystem templates**

Create `internal/knowledgebase/init.go`:

```go
package knowledgebase

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

type InitResult struct {
	Profile string   `json:"profile"`
	Created int      `json:"created"`
	Skipped int      `json:"skipped"`
	Files   []string `json:"files"`
}

func InitProfile(projectRoot string, profile string, force bool) (InitResult, error) {
	if profile == "" {
		profile = ProfileGoGameServer
	}
	if profile != ProfileGoGameServer && profile != ProfileUnityClient {
		return InitResult{}, fmt.Errorf("unsupported knowledge profile %q", profile)
	}
	result := InitResult{Profile: profile}
	templateRoot := filepath.Join("templates", "knowledgebase", profile)
	if _, err := os.Stat(templateRoot); err != nil {
		return result, err
	}
	err := filepath.WalkDir(templateRoot, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			return nil
		}
		rel, err := filepath.Rel(templateRoot, path)
		if err != nil {
			return err
		}
		target := filepath.Join(projectRoot, rel)
		if _, err := os.Stat(target); err == nil && !force {
			result.Skipped++
			return nil
		}
		content, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(target, content, 0o644); err != nil {
			return err
		}
		result.Created++
		result.Files = append(result.Files, strings.ReplaceAll(rel, "\\", "/"))
		return nil
	})
	return result, err
}
```

- [ ] **Step 4: Add minimum go-game-server template docs**

Create `templates/knowledgebase/go-game-server/design/KnowledgeBase/index.md`:

```markdown
---
type: Index
title: Go Game Server Knowledge Base
description: OKF-compatible knowledge entry for Go game-server projects.
resource: design/KnowledgeBase/index.md
tags: [knowledge-base, okf, go, server]
timestamp: 2026-07-07T00:00:00+08:00
---

# Go Game Server Knowledge Base

Start with [project routing](./project/routing.md), then read only the domain files required by the active workflow.

## Domains

- [Actor](./domains/actor/index.md)
- [Config](./domains/config/index.md)
- [Quest](./domains/quest/index.md)
- [Network](./domains/network/index.md)
- [Testing](./domains/testing/index.md)
```

Create `templates/knowledgebase/go-game-server/design/KnowledgeBase/log.md` and `project/routing.md` with OKF frontmatter. `project/routing.md` must include concrete sections for quest, actor, config, and network tasks and list the exact required files under each section.

- [ ] **Step 5: Add minimum Unity template docs**

Create `templates/knowledgebase/unity-client/design/KnowledgeBase/index.md`, `log.md`, and `project/routing.md`. `project/routing.md` must include exact required docs for:

- New UI feature.
- UI quick fix.
- PSD import or UIArchitect changes.

- [ ] **Step 6: Add domain docs for referenced domains**

For every domain referenced in route preview and profile routing, create `index.md` and `routing.md` with OKF frontmatter. Required domains:

- Go: `actor`, `config`, `quest`, `network`, `testing`.
- Unity: `ui`, `uiarchitect`, `gameplay`, `network`, `testing`.

Every `routing.md` must include a `Read:` list with concrete relative files. If the referenced deep document is not implemented yet, create a short concrete stub for it, such as `quest-system.md`, `coding-rules.md`, `anti-patterns.md`, `go-testing.md`, `data-flow-rules.md`, `psd-import.md`, or `naming-components.md`.

- [ ] **Step 7: Verify init tests pass**

```powershell
go test ./internal/knowledgebase -run TestInitProfileCreatesKnowledgeBase -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add internal/knowledgebase/init.go internal/knowledgebase/init_test.go templates/knowledgebase
git commit -m "feat: initialize okf knowledge profiles"
```

---

## Task 7: Add Kiso-Inspired Render Tree and Document Rendering

**Files:**
- Create: `D:\workspace\src\nexus-agents\internal\knowledgebase\render.go`
- Create: `D:\workspace\src\nexus-agents\internal\knowledgebase\render_test.go`

- [ ] **Step 1: Write failing render tests**

Create `internal/knowledgebase/render_test.go`:

```go
package knowledgebase

import (
	"path/filepath"
	"strings"
	"testing"
)

func TestRenderTreeUsesFrontmatterTitles(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), okfDoc("Index", "Root KB", "design/KnowledgeBase/index.md", "# Root"))
	tree, err := BuildRenderTree(root)
	if err != nil {
		t.Fatal(err)
	}
	if len(tree.Nodes) != 1 || tree.Nodes[0].Title != "Root KB" {
		t.Fatalf("unexpected tree: %#v", tree)
	}
}

func TestRenderDocumentEscapesHTMLAndRendersHeadings(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), okfDoc("Index", "Root", "design/KnowledgeBase/index.md", "# Heading\n\n<script>alert(1)</script>"))
	doc, err := RenderDocument(root, "design/KnowledgeBase/index.md")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(doc.HTML, "<h1>Heading</h1>") {
		t.Fatalf("heading not rendered: %s", doc.HTML)
	}
	if strings.Contains(doc.HTML, "<script>") {
		t.Fatalf("html was not escaped: %s", doc.HTML)
	}
}
```

- [ ] **Step 2: Run failing tests**

```powershell
go test ./internal/knowledgebase -run TestRender -v
```

Expected: FAIL with `undefined: BuildRenderTree` and `undefined: RenderDocument`.

- [ ] **Step 3: Implement basic renderer**

Create `internal/knowledgebase/render.go`:

```go
package knowledgebase

import (
	"fmt"
	"html"
	"path/filepath"
	"strings"
)

func BuildRenderTree(projectRoot string) (RenderTree, error) {
	bundle, err := ScanBundle(projectRoot)
	if err != nil {
		return RenderTree{}, err
	}
	tree := RenderTree{Root: DefaultRoot}
	for _, doc := range bundle.Documents {
		title := doc.Frontmatter.Title
		if title == "" {
			title = doc.Path
		}
		tree.Nodes = append(tree.Nodes, RenderNode{Path: doc.Path, Title: title, Type: doc.Frontmatter.Type})
	}
	return tree, nil
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
			return RenderedDocument{Path: doc.Path, Title: title, Frontmatter: doc.Frontmatter, HTML: renderMarkdown(doc.Body)}, nil
		}
	}
	return RenderedDocument{}, fmt.Errorf("knowledge document %s not found", relPath)
}

func renderMarkdown(body string) string {
	var out []string
	inCode := false
	var code []string
	for _, line := range strings.Split(body, "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "```") {
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
		switch {
		case strings.HasPrefix(trimmed, "# "):
			out = append(out, "<h1>"+html.EscapeString(strings.TrimSpace(strings.TrimPrefix(trimmed, "# ")))+"</h1>")
		case strings.HasPrefix(trimmed, "## "):
			out = append(out, "<h2>"+html.EscapeString(strings.TrimSpace(strings.TrimPrefix(trimmed, "## ")))+"</h2>")
		case strings.HasPrefix(trimmed, "- "):
			out = append(out, "<li>"+html.EscapeString(strings.TrimSpace(strings.TrimPrefix(trimmed, "- ")))+"</li>")
		case trimmed == "":
			out = append(out, "")
		default:
			out = append(out, "<p>"+html.EscapeString(trimmed)+"</p>")
		}
	}
	return strings.Join(out, "\n")
}
```

- [ ] **Step 4: Verify render tests pass**

```powershell
go test ./internal/knowledgebase -run TestRender -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add internal/knowledgebase/render.go internal/knowledgebase/render_test.go
git commit -m "feat: render okf knowledge docs"
```

---

## Task 8: Attach Knowledge Summary to Imported Projects

**Files:**
- Modify: `D:\workspace\src\nexus-agents\internal\catalog\catalog.go`
- Modify: `D:\workspace\src\nexus-agents\internal\catalog\project_scan.go`
- Modify: `D:\workspace\src\nexus-agents\internal\catalog\project_scan_test.go`

- [ ] **Step 1: Add failing project scan test**

In `internal/catalog/project_scan_test.go`, add:

```go
func TestScanProjectKnowledgeSummary(t *testing.T) {
	root := t.TempDir()
	writeFile(t, filepath.Join(root, "design", "KnowledgeBase", "index.md"), "---\ntype: Index\ntitle: Root\ndescription: Root.\nresource: design/KnowledgeBase/index.md\ntags: [index]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n")
	summary := ScanProjectKnowledgeSummary(root)
	if !summary.Exists {
		t.Fatalf("expected knowledge summary to exist: %#v", summary)
	}
	if summary.Documents != 1 {
		t.Fatalf("expected one knowledge document, got %#v", summary)
	}
}
```

- [ ] **Step 2: Run failing test**

```powershell
go test ./internal/catalog -run TestScanProjectKnowledgeSummary -v
```

Expected: FAIL with `undefined: ScanProjectKnowledgeSummary`.

- [ ] **Step 3: Add summary field and scanner**

Modify `internal/catalog/catalog.go`:

```go
import "nexus-agents/internal/knowledgebase"
```

Add to `Project`:

```go
KnowledgeSummary knowledgebase.Summary `json:"knowledgeSummary"`
```

In `ImportProject` and `RescanProject`, set:

```go
project.KnowledgeSummary = ScanProjectKnowledgeSummary(localPath)
```

Modify `internal/catalog/project_scan.go`:

```go
func ScanProjectKnowledgeSummary(projectRoot string) knowledgebase.Summary {
	report, err := knowledgebase.Validate(projectRoot)
	if err != nil {
		return knowledgebase.Summary{Exists: false, Root: knowledgebase.DefaultRoot, Issues: 1, Errors: 1}
	}
	return report.Summary
}
```

- [ ] **Step 4: Verify tests pass**

```powershell
go test ./internal/catalog -run TestScanProjectKnowledgeSummary -v
go test ./internal/catalog
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add internal/catalog/catalog.go internal/catalog/project_scan.go internal/catalog/project_scan_test.go
git commit -m "feat: summarize project knowledge bases"
```

---

## Task 9: Add Project Knowledge HTTP APIs

**Files:**
- Modify: `D:\workspace\src\nexus-agents\internal\httpapi\server.go`
- Modify: `D:\workspace\src\nexus-agents\internal\httpapi\server_test.go`

- [ ] **Step 1: Add failing API tests**

In `internal/httpapi/server_test.go`, add tests for:

```text
GET  /api/projects/{id}/knowledge
POST /api/projects/{id}/knowledge/init?profile=go-game-server
GET  /api/projects/{id}/knowledge/validate
GET  /api/projects/{id}/knowledge/route?task=quest%20bug
GET  /api/projects/{id}/knowledge/render
GET  /api/projects/{id}/knowledge/render?path=design/KnowledgeBase/index.md
GET  /api/projects/{id}/knowledge/maintenance
```

Assert 404 for unknown project, `created > 0` for init, `summary.exists == true` after init, route returns `requiredFiles`, render returns tree/doc, and maintenance returns a report.

- [ ] **Step 2: Run failing API tests**

```powershell
go test ./internal/httpapi -run Knowledge -v
```

Expected: FAIL with 404 for new routes.

- [ ] **Step 3: Add route handling**

Modify `handleProjectPath` in `internal/httpapi/server.go` before the `parts[1] != "config"` branch:

```go
if len(parts) >= 2 && parts[1] == "knowledge" {
	s.handleProjectKnowledgePath(w, r, projectID, parts[2:])
	return
}
```

Add import:

```go
"nexus-agents/internal/knowledgebase"
```

Add helper:

```go
func (s *Server) projectLocalPath(projectID string) (string, bool) {
	project, ok := s.store.ProjectByID(projectID)
	if !ok {
		return "", false
	}
	localPath := strings.TrimSpace(project.LocalPath)
	if localPath == "" {
		localPath = strings.TrimSpace(project.Path)
	}
	return localPath, localPath != ""
}
```

Add `handleProjectKnowledgePath` that dispatches:

- `GET []` -> `knowledgebase.ScanBundle`
- `POST init` -> `knowledgebase.InitProfile`
- `GET validate` -> `knowledgebase.Validate`
- `GET route` -> `knowledgebase.PreviewRoute`
- `GET render` -> `knowledgebase.BuildRenderTree`
- `GET render?path=...` -> `knowledgebase.RenderDocument`
- `GET maintenance` -> `knowledgebase.Maintenance`

- [ ] **Step 4: Verify API tests pass**

```powershell
go test ./internal/httpapi -run Knowledge -v
go test ./internal/...
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add internal/httpapi/server.go internal/httpapi/server_test.go
git commit -m "feat: expose project knowledge api"
```

---

## Task 10: Add Frontend Types and API Client

**Files:**
- Modify: `D:\workspace\src\nexus-agents\web\src\types.ts`
- Modify: `D:\workspace\src\nexus-agents\web\src\api.ts`

- [ ] **Step 1: Add TypeScript types**

Append `KnowledgeSummary`, `KnowledgeFrontmatter`, `KnowledgeDocument`, `KnowledgeBundle`, `KnowledgeIssue`, `KnowledgeValidationReport`, `KnowledgeRoutePreview`, `KnowledgeRenderNode`, `KnowledgeRenderTree`, `KnowledgeRenderedDocument`, `KnowledgeMaintenanceReport`, and `KnowledgeInitResult` to `web/src/types.ts`. Add `knowledgeSummary?: KnowledgeSummary` to the existing `Project` interface.

- [ ] **Step 2: Add API methods**

In `web/src/api.ts`, add:

```ts
export async function fetchProjectKnowledge(projectId: string): Promise<KnowledgeBundle> {
  return request<KnowledgeBundle>(`/api/projects/${encodeURIComponent(projectId)}/knowledge`)
}

export async function initProjectKnowledge(projectId: string, profile: string): Promise<KnowledgeInitResult> {
  return request<KnowledgeInitResult>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/init?profile=${encodeURIComponent(profile)}`, { method: 'POST' })
}

export async function validateProjectKnowledge(projectId: string): Promise<KnowledgeValidationReport> {
  return request<KnowledgeValidationReport>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/validate`)
}

export async function previewProjectKnowledgeRoute(projectId: string, task: string): Promise<KnowledgeRoutePreview> {
  return request<KnowledgeRoutePreview>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/route?task=${encodeURIComponent(task)}`)
}

export async function fetchProjectKnowledgeTree(projectId: string): Promise<KnowledgeRenderTree> {
  return request<KnowledgeRenderTree>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/render`)
}

export async function fetchProjectKnowledgeDocument(projectId: string, path: string): Promise<KnowledgeRenderedDocument> {
  return request<KnowledgeRenderedDocument>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/render?path=${encodeURIComponent(path)}`)
}

export async function fetchProjectKnowledgeMaintenance(projectId: string): Promise<KnowledgeMaintenanceReport> {
  return request<KnowledgeMaintenanceReport>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/maintenance`)
}
```

- [ ] **Step 3: Run frontend type check**

```powershell
cd web
npm run test:unit
```

Expected: PASS.

- [ ] **Step 4: Commit**

```powershell
git add web/src/types.ts web/src/api.ts
git commit -m "feat: add knowledge api client"
```

---

## Task 11: Add Knowledge Panel to Project Detail UI

**Files:**
- Modify: `D:\workspace\src\nexus-agents\web\src\App.vue`
- Modify: `D:\workspace\src\nexus-agents\web\src\styles.css`

- [ ] **Step 1: Locate project detail state**

Run:

```powershell
Select-String -Path web\src\App.vue -Pattern "project-detail|selectedProject|Project" -Context 2,4 | Select-Object -First 40
```

Expected: Find the project detail section and selected project state.

- [ ] **Step 2: Import knowledge API functions and types**

Extend imports from `./api` with `fetchProjectKnowledge`, `fetchProjectKnowledgeDocument`, `fetchProjectKnowledgeMaintenance`, `fetchProjectKnowledgeTree`, `initProjectKnowledge`, `previewProjectKnowledgeRoute`, and `validateProjectKnowledge`.

- [ ] **Step 3: Add reactive state**

Add near existing project detail state:

```ts
const knowledgeBundle = ref<KnowledgeBundle | null>(null)
const knowledgeValidation = ref<KnowledgeValidationReport | null>(null)
const knowledgeRoute = ref<KnowledgeRoutePreview | null>(null)
const knowledgeTree = ref<KnowledgeRenderTree | null>(null)
const knowledgeDocument = ref<KnowledgeRenderedDocument | null>(null)
const knowledgeMaintenance = ref<KnowledgeMaintenanceReport | null>(null)
const knowledgeTaskText = ref('')
const knowledgeProfile = ref<'go-game-server' | 'unity-client'>('go-game-server')
const knowledgeLoading = ref(false)
```

- [ ] **Step 4: Add action methods**

Add methods to refresh, initialize, preview route, and open document by calling the API functions from Task 10.

- [ ] **Step 5: Add project detail panel markup**

Add a `Knowledge Base` panel that shows:

- Empty state and profile selector when no bundle exists.
- Metrics for documents, domains, and issues.
- Validation issue list.
- Route preview input and required files.
- Document tree buttons.
- Rendered document area using `v-html`.
- Maintenance suggested actions.

- [ ] **Step 6: Add focused CSS**

Append styles for `.knowledge-panel`, `.knowledge-grid`, `.knowledge-issues`, `.issue-error`, `.issue-warning`, `.knowledge-route-preview`, `.knowledge-docs`, and `.rendered-doc`.

- [ ] **Step 7: Run frontend build**

```powershell
cd web
npm run build
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add web/src/App.vue web/src/styles.css web/dist
git commit -m "feat: add project knowledge panel"
```

---

## Task 12: Add Knowledge Loading to Workflow Templates

**Files:**
- Modify workflow markdown files listed in the File Structure section.

- [ ] **Step 1: Add shared Knowledge Loading section**

In each target workflow near the beginning after goal/scope, add:

```markdown
## Knowledge Loading

Before code changes:

1. Read `design/KnowledgeBase/project/routing.md` when the project has a knowledge base.
2. Select the relevant domain based on the user task.
3. Read the selected domain `routing.md`.
4. Read only the required domain knowledge files listed by routing.
5. Include a `Loaded Knowledge` section in the investigation or implementation summary.

If `design/KnowledgeBase` is missing, continue with existing workflow rules and report that no OKF knowledge base was available.
```

For `unity-ui-feature-development.md`, use this more specific variant:

```markdown
## Knowledge Loading

Before Unity UI implementation:

1. Read `design/KnowledgeBase/project/routing.md` when present.
2. For UI work, read `design/KnowledgeBase/domains/ui/routing.md`.
3. For new UI features, read UI coding rules, data-flow rules, and MVVM templates listed by routing.
4. For quick fixes, do not read large templates unless creating or reshaping View/ViewModel structure.
5. Include a `Loaded Knowledge` section in the final summary.
```

- [ ] **Step 2: Verify workflow references**

```powershell
Select-String -Path templates\workflows\*.md -Pattern "design/KnowledgeBase/project/routing.md|Loaded Knowledge" | Format-Table Path,LineNumber,Line -AutoSize
```

Expected: Every modified workflow includes the knowledge loading wording.

- [ ] **Step 3: Commit**

```powershell
git add templates/workflows/*.md
git commit -m "feat: add knowledge loading to workflows"
```

---

## Task 13: Add Knowledge Maintenance Workflow and Skill

**Files:**
- Create: `D:\workspace\src\nexus-agents\templates\workflows\kb-maintenance.md`
- Create: `D:\workspace\src\nexus-agents\templates\workflows\kb-maintenance.graph.json`
- Create: `D:\workspace\src\nexus-agents\templates\skills\codex\wf-kb-maintenance\SKILL.md`
- Create: `D:\workspace\src\nexus-agents\templates\skills\codex\wf-kb-maintenance\agents\openai.yaml`

- [ ] **Step 1: Create maintenance workflow source**

Create `templates/workflows/kb-maintenance.md` with sections: Goal, Inputs, Inventory, Validate OKF metadata, Validate links, Detect stale or oversized docs, Detect duplicated hard rules, Propose updates, Human review gate.

- [ ] **Step 2: Create maintenance skill**

Create `templates/skills/codex/wf-kb-maintenance/SKILL.md`:

```markdown
---
name: wf-kb-maintenance
description: Maintain an OKF-compatible project knowledge base by validating metadata, links, routing, staleness, duplicate hard rules, and workflow evidence.
---

# Knowledge Base Maintenance

Use this workflow when the user asks to review, tidy, validate, refresh, or maintain `design/KnowledgeBase`.

1. Read `.claude/workflows/kb-maintenance.md` as the source of truth when present.
2. Prefer Nexus Agents knowledge API reports when available.
3. Default to report/proposal mode. Do not rewrite hard rules without user approval.
4. Keep `AGENTS.md` as a concise entrypoint and `design/KnowledgeBase` as domain knowledge.
```

Create `templates/skills/codex/wf-kb-maintenance/agents/openai.yaml`:

```yaml
name: wf-kb-maintenance
summary: Validate and maintain OKF-compatible project knowledge bases.
model: gpt-5.5
reasoning_effort: medium
```

- [ ] **Step 3: Create graph JSON**

Create `templates/workflows/kb-maintenance.graph.json` by copying the required schema fields from `templates/workflows/research.graph.json`, then use node labels:

- Start
- Inventory knowledge files
- Validate OKF metadata and links
- Detect stale docs and duplicate rules
- Write maintenance proposal

- [ ] **Step 4: Verify template bootstrap sees new workflow**

```powershell
go test ./internal/catalog -run Test -v
```

Expected: PASS. If no test covers new templates, add a focused test asserting `kb-maintenance` appears in workflow templates and Codex skills.

- [ ] **Step 5: Commit**

```powershell
git add templates/workflows/kb-maintenance.md templates/workflows/kb-maintenance.graph.json templates/skills/codex/wf-kb-maintenance
git commit -m "feat: add knowledge maintenance workflow"
```

---

## Task 14: Add Knowledge Eval Checks to Workflow Evaluation

**Files:**
- Modify: `D:\workspace\src\nexus-agents\internal\catalog\evaluation.go`
- Modify: `D:\workspace\src\nexus-agents\internal\catalog\evaluation_test.go`

- [ ] **Step 1: Add failing evaluation test**

In `internal/catalog/evaluation_test.go`, add a test that evaluates task output containing:

```markdown
## Loaded Knowledge

- `design/KnowledgeBase/project/routing.md`
- `design/KnowledgeBase/domains/quest/routing.md`
```

Assert that evaluation evidence includes a positive metric or warning-clear signal such as `knowledge_loaded=true`, following the existing evaluation metric/test style.

- [ ] **Step 2: Run failing evaluation test**

```powershell
go test ./internal/catalog -run Knowledge -v
```

Expected: FAIL because no knowledge evidence metric exists.

- [ ] **Step 3: Implement evidence detection**

In `internal/catalog/evaluation.go`, add:

```go
func detectLoadedKnowledgeEvidence(text string) []string {
	lines := strings.Split(text, "\n")
	var files []string
	inSection := false
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if strings.EqualFold(trimmed, "## Loaded Knowledge") || strings.EqualFold(trimmed, "# Loaded Knowledge") {
			inSection = true
			continue
		}
		if inSection && strings.HasPrefix(trimmed, "#") {
			break
		}
		if inSection && strings.Contains(trimmed, "design/KnowledgeBase/") {
			files = append(files, trimmed)
		}
	}
	return files
}
```

Wire this into the existing evaluation summary so outputs that cite loaded OKF files are visible in evidence metrics. Missing evidence should be a warning until workflow adoption is complete, not a hard failure.

- [ ] **Step 4: Verify tests pass**

```powershell
go test ./internal/catalog -run Knowledge -v
go test ./internal/catalog
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add internal/catalog/evaluation.go internal/catalog/evaluation_test.go
git commit -m "feat: evaluate loaded knowledge evidence"
```

---

## Task 15: Documentation and Verification

**Files:**
- Create: `D:\workspace\src\nexus-agents\docs\knowledgebase-okf.md`
- Modify: `D:\workspace\src\nexus-agents\README.md`

- [ ] **Step 1: Create user documentation**

Create `docs/knowledgebase-okf.md`:

```markdown
# OKF-Compatible Knowledge Bases in Nexus Agents

Nexus Agents supports lightweight project knowledge bases under `design/KnowledgeBase`.

## Purpose

The knowledge base is the deterministic source of truth for workflow context. It is not a vector database and does not replace code search, CodeGraph, Unity MCP, or verification.

## Recommended flow

1. Keep `AGENTS.md` concise.
2. Put domain knowledge in `design/KnowledgeBase`.
3. Let workflows read `project/routing.md` first.
4. Let routing select domain docs.
5. Include `Loaded Knowledge` in workflow summaries.
6. Run knowledge maintenance periodically.

## Profiles

- `go-game-server`: actor, config, quest, network, testing.
- `unity-client`: UI, UIArchitect, gameplay, network, testing.

## API

- `GET /api/projects/{id}/knowledge`
- `POST /api/projects/{id}/knowledge/init?profile=go-game-server`
- `GET /api/projects/{id}/knowledge/validate`
- `GET /api/projects/{id}/knowledge/route?task=...`
- `GET /api/projects/{id}/knowledge/render`
- `GET /api/projects/{id}/knowledge/render?path=design/KnowledgeBase/index.md`
- `GET /api/projects/{id}/knowledge/maintenance`

## Maintenance policy

Default maintenance mode reports issues and suggested patches. It does not automatically rewrite hard project rules, workflow logic, or business domain rules.
```

- [ ] **Step 2: Link docs from README**

Add one bullet to `README.md` near project/workflow feature descriptions:

```markdown
- OKF-compatible project knowledge bases under `design/KnowledgeBase`, with validation, routing preview, maintenance reports, and workflow knowledge-loading support. See `docs/knowledgebase-okf.md`.
```

- [ ] **Step 3: Run all backend tests**

```powershell
go test ./...
```

Expected: PASS.

- [ ] **Step 4: Run frontend tests/build**

```powershell
cd web
npm run test:unit
npm run build
```

Expected: PASS.

- [ ] **Step 5: Run project verification script**

```powershell
.\scripts\verify_all.ps1
```

Expected: PASS. If the script skips frontend build because `web/node_modules` is absent, record the skip reason and rely on the explicit frontend commands from Step 4.

- [ ] **Step 6: Commit**

```powershell
git add docs/knowledgebase-okf.md README.md web/dist
git commit -m "docs: document okf knowledge bases"
```

---

## Rollout Plan for btd Projects After Nexus Implementation

After this Nexus Agents feature lands, apply it to real projects in this order:

1. Import or rescan `D:\workspace\src\btd-client` and `D:\workspace\src\btd-game-server` in Nexus Agents.
2. For `btd-client`, use the UI to validate the existing `design/KnowledgeBase` before initializing anything.
3. For `btd-client`, only add missing OKF frontmatter, `index.md`, `log.md`, and workflow docs; do not replace existing UI knowledge.
4. For `btd-game-server`, initialize `go-game-server` profile under `design/KnowledgeBase`.
5. Migrate stable domain knowledge from `.claude/skills/go-*.md` into the appropriate domain files or link from domain files to the existing skill source.
6. Update project workflows in each repo by syncing the new templates or manually adding the `Knowledge Loading` section.
7. Run `$wf-kb-maintenance` once per project in report-only mode.
8. Run two pilot workflows:
   - `btd-game-server`: `$wf-go-bugfix` on a quest/config/actor task.
   - `btd-client`: `$wf-unity-ui-feature` on a UI feature task.
9. Use Nexus evaluation to check that final summaries include `Loaded Knowledge` and reference the correct OKF files.
10. After two successful pilots per project, extend knowledge loading to other workflows.

## Self-Review Checklist

- Spec coverage: The plan covers OKF-compatible knowledge initialization, validation, Kiso-inspired rendering, light workflow integration, maintenance workflow, UI, API, and evaluation checks.
- Placeholder scan: The plan avoids open-ended implementation gaps; where existing graph schema details may vary, it instructs workers to copy required fields from a named existing file and preserve specific node labels.
- Type consistency: Backend `knowledgebase` types map directly to frontend TypeScript interfaces and HTTP API responses.
- Risk control: The plan keeps knowledge source files in Git, avoids vector/RAG dependencies, avoids global Codex config changes, and defaults maintenance to report/proposal mode.
