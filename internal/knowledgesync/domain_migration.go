package knowledgesync

import (
	"context"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"nexus-agents/internal/knowledgebase"
)

const domainMigrationVersion = "nexus-domain-knowledge-v2"

type domainMigrationPage struct {
	Slug           string
	Title          string
	Description    string
	Tags           []string
	Bodies         []string
	SourceRevision string
}

func BuildDomainMigrationBundle(projectRoot string) (map[string][]byte, error) {
	projectKnowledge := filepath.Join(projectRoot, filepath.FromSlash(DefaultKnowledgeRoot))
	entries, err := os.ReadDir(projectKnowledge)
	if err != nil {
		return nil, err
	}
	pages := map[string]*domainMigrationPage{}
	for _, entry := range entries {
		if entry.IsDir() || strings.ToLower(filepath.Ext(entry.Name())) != ".md" || strings.EqualFold(entry.Name(), "index.md") {
			continue
		}
		relative := path.Join(DefaultKnowledgeRoot, entry.Name())
		data, err := os.ReadFile(filepath.Join(projectKnowledge, entry.Name()))
		if err != nil {
			return nil, err
		}
		frontmatter, body, _ := knowledgebase.ParseFrontmatter(string(data))
		if frontmatter.ManagedBy != "openwiki" {
			continue
		}
		body = cleanMigratedMarkdown(body)
		lowerName := strings.ToLower(strings.TrimSuffix(entry.Name(), filepath.Ext(entry.Name())))
		switch lowerName {
		case "quickstart":
			addMigrationBody(pages, domainMigrationPage{
				Slug: "runtime-platform", Title: "Runtime Platform",
				Description: "Nexus service startup, embedded console, runtime composition, state boundaries, and project control-plane operations.",
				Tags:        []string{"runtime", "platform", "control-plane"},
			}, "## Project overview\n\n"+demoteMarkdownHeadings(body), frontmatter.SourceRevision)
		case "architecture":
			addMigrationBody(pages, domainMigrationPage{
				Slug: "runtime-platform", Title: "Runtime Platform",
				Description: "Nexus service startup, embedded console, runtime composition, state boundaries, and project control-plane operations.",
				Tags:        []string{"runtime", "platform", "architecture"},
			}, "## Runtime architecture\n\n"+demoteMarkdownHeadings(body), frontmatter.SourceRevision)
		case "api-routing":
			addMigrationBody(pages, domainMigrationPage{
				Slug: "model-routing", Title: "Model Routing",
				Description: "HTTP API surface, Codex-compatible proxy behavior, provider selection, protocol conversion, and session telemetry.",
				Tags:        []string{"model-routing", "api", "codex", "proxy"},
			}, trimFirstHeading(body), frontmatter.SourceRevision)
		case "templates":
			addMigrationBody(pages, domainMigrationPage{
				Slug: "template-management", Title: "Template Management",
				Description: "Portable Agent, Rule, Skill, Workflow, and KnowledgeBase template initialization and synchronization.",
				Tags:        []string{"templates", "agents", "skills", "workflows"},
			}, trimFirstHeading(body), frontmatter.SourceRevision)
		case "workflow-knowledgebase":
			workflowBody, knowledgeBody := splitWorkflowKnowledgeBody(body)
			addMigrationBody(pages, domainMigrationPage{
				Slug: "workflow-evaluation", Title: "Workflow Evaluation",
				Description: "Workflow runs, session attribution, task-run submission, evaluation, telemetry, and learning-case lifecycle.",
				Tags:        []string{"workflow", "evaluation", "task-runs", "telemetry"},
			}, workflowBody, frontmatter.SourceRevision)
			addMigrationBody(pages, domainMigrationPage{
				Slug: "knowledgebase", Title: "KnowledgeBase",
				Description: "Project knowledge scanning, Domain routing, retrieval, validation, rendering, export, and AI context loading.",
				Tags:        []string{"knowledgebase", "retrieval", "domains", "context"},
			}, knowledgeBody, frontmatter.SourceRevision)
		default:
			slug := migrationSlug(lowerName)
			title := strings.TrimSpace(frontmatter.Title)
			if title == "" {
				title = migrationTitle(slug)
			}
			description := strings.TrimSpace(frontmatter.Description)
			if description == "" {
				description = "Stable project capability migrated from approved OpenWiki knowledge."
			}
			addMigrationBody(pages, domainMigrationPage{
				Slug: slug, Title: title, Description: description,
				Tags: append([]string{slug, "domain"}, frontmatter.Tags...),
			}, trimFirstHeading(body), frontmatter.SourceRevision)
		}
		_ = relative
	}
	if len(pages) == 0 {
		return BuildDomainDetailBundle(projectRoot)
	}

	linkTargets := map[string]string{
		"quickstart.md":             "../runtime-platform/index.md",
		"architecture.md":           "../runtime-platform/index.md",
		"api-routing.md":            "../model-routing/index.md",
		"templates.md":              "../template-management/index.md",
		"workflow-knowledgebase.md": "../workflow-evaluation/index.md",
		"knowledgebase.md":          "../knowledgebase/index.md",
		"workflow-evaluation.md":    "../workflow-evaluation/index.md",
		"template-management.md":    "../template-management/index.md",
		"runtime-platform.md":       "../runtime-platform/index.md",
		"model-routing.md":          "../model-routing/index.md",
	}
	relations := map[string][]string{
		"runtime-platform":    {"model-routing", "template-management", "workflow-evaluation", "knowledgebase"},
		"model-routing":       {"runtime-platform", "workflow-evaluation"},
		"template-management": {"runtime-platform", "workflow-evaluation", "knowledgebase"},
		"workflow-evaluation": {"runtime-platform", "model-routing", "template-management", "knowledgebase"},
		"knowledgebase":       {"runtime-platform", "template-management", "workflow-evaluation"},
	}

	files := map[string][]byte{}
	slugs := make([]string, 0, len(pages))
	for slug := range pages {
		slugs = append(slugs, slug)
	}
	sort.Strings(slugs)
	for _, slug := range slugs {
		page := pages[slug]
		var body strings.Builder
		body.WriteString("# " + page.Title + "\n\n")
		for _, part := range page.Bodies {
			part = rewriteMigratedLinks(part, linkTargets)
			if strings.TrimSpace(part) != "" {
				body.WriteString(strings.TrimSpace(part) + "\n\n")
			}
		}
		if related := relations[slug]; len(related) > 0 {
			body.WriteString("## Related Domains\n\n")
			for _, target := range related {
				if _, exists := pages[target]; !exists {
					continue
				}
				body.WriteString("- [" + pages[target].Title + "](../" + target + "/index.md)\n")
			}
			body.WriteString("\n")
		}
		resource := path.Join(DefaultKnowledgeRoot, "domains", slug, "index.md")
		sourceRevision := strings.TrimSpace(page.SourceRevision)
		if sourceRevision == "" {
			sourceRevision = "unknown"
		}
		content := "---\n" +
			"type: Domain\n" +
			"title: " + page.Title + "\n" +
			"description: " + page.Description + "\n" +
			"resource: " + resource + "\n" +
			"tags: [" + strings.Join(uniqueMigrationStrings(append(page.Tags, "domain")), ", ") + "]\n" +
			"timestamp: " + time.Now().Format(time.RFC3339) + "\n" +
			"managedBy: openwiki\n" +
			"sourceRevision: " + sourceRevision + "\n" +
			"generatedBy: " + domainMigrationVersion + "\n" +
			"---\n" + body.String()
		files[resource] = []byte(content)
	}

	var index strings.Builder
	index.WriteString("# Project Domains\n\n")
	for _, slug := range slugs {
		page := pages[slug]
		index.WriteString("- [" + page.Title + "](domains/" + slug + "/index.md) - " + page.Description + "\n")
	}
	files[path.Join(DefaultKnowledgeRoot, "index.md")] = []byte(index.String())
	return files, nil
}

func (s *Service) MigrateDomainsPreview(ctx context.Context, request ProjectRequest) (result SyncResult, returnErr error) {
	release, err := s.acquire(request.ProjectID)
	if err != nil {
		return SyncResult{}, err
	}
	defer release()
	if err := validateProjectRequest(request); err != nil {
		return SyncResult{}, err
	}
	profile, err := LoadProfile(request.ProjectRoot)
	if err != nil {
		return SyncResult{}, err
	}
	gitState, err := ReadGitState(ctx, s.git, request.ProjectRoot)
	if err != nil {
		return SyncResult{}, err
	}
	started := time.Now()
	run := SyncRun{
		ID: newRunID(request.ProjectID, "domain-migrate"), ProjectID: request.ProjectID,
		Kind: "domain-migrate", Status: "running", Branch: gitState.Branch,
		TargetRevision: gitState.Head, StartedAt: started.Format(time.RFC3339), Warnings: []string{},
	}
	state, _ := s.store.LoadState(request.ProjectID)
	state.ProjectID = request.ProjectID
	state.ProjectRoot = filepath.Clean(request.ProjectRoot)
	state.Branch = gitState.Branch
	state.LastCheckedAt = run.StartedAt
	state.LastError = ""
	state.Status = StatusChecking
	_ = s.store.SaveState(state)
	_ = s.store.SaveRun(run)
	defer func() {
		if returnErr == nil {
			return
		}
		run.Status = "failed"
		run.Error = boundedText(returnErr.Error(), 2000)
		run.EndedAt = time.Now().Format(time.RFC3339)
		state.Status = StatusFailed
		state.LastError = run.Error
		_ = s.store.SaveRun(run)
		_ = s.store.SaveState(state)
	}()

	files, err := BuildDomainMigrationBundle(request.ProjectRoot)
	if err != nil {
		return SyncResult{}, err
	}
	if err := ensureKnowledgeScaffold(request.ProjectRoot, files); err != nil {
		return SyncResult{}, err
	}
	validation, err := validateGeneratedBundle(request.ProjectRoot, files)
	if err != nil {
		return SyncResult{}, err
	}
	profileHash, err := ProfileHash(profile)
	if err != nil {
		return SyncResult{}, err
	}
	proposal, err := BuildProposal(
		request.ProjectID, request.ProjectRoot, gitState.Branch, state.LastProcessedCommit, gitState.Head,
		profileHash, domainMigrationVersion, files,
		[]ProposalEvidence{{
			Kind: "migration", Source: "approved project knowledge",
			Summary:  "Deterministically reorganized flat OpenWiki pages into project Domains without new model generation.",
			Revision: gitState.Head,
		}},
		validation,
	)
	if err != nil {
		return SyncResult{}, err
	}
	if err := s.store.SaveProposal(proposal); err != nil {
		return SyncResult{}, err
	}
	now := time.Now().Format(time.RFC3339)
	run.Status = "succeeded"
	run.ProposalID = proposal.ID
	run.Warnings = proposal.Validation.Warnings
	run.EndedAt = now
	state.Status = StatusProposalPending
	state.PendingProposalID = proposal.ID
	state.CompilerVersion = domainMigrationVersion
	state.ProfileHash = profileHash
	state.LastCheckedAt = now
	state.LastError = ""
	if err := s.store.SaveRun(run); err != nil {
		return SyncResult{}, err
	}
	if err := s.store.SaveState(state); err != nil {
		return SyncResult{}, err
	}
	return SyncResult{
		State: state, Run: run, Proposal: &proposal,
		Message: "Domain migration proposal generated from approved existing knowledge; project files are unchanged until approval.",
	}, nil
}

func addMigrationBody(pages map[string]*domainMigrationPage, page domainMigrationPage, body, sourceRevision string) {
	existing := pages[page.Slug]
	if existing == nil {
		copy := page
		copy.Tags = uniqueMigrationStrings(copy.Tags)
		existing = &copy
		pages[page.Slug] = existing
	}
	existing.Tags = uniqueMigrationStrings(append(existing.Tags, page.Tags...))
	if strings.TrimSpace(existing.SourceRevision) == "" {
		existing.SourceRevision = strings.TrimSpace(sourceRevision)
	}
	if strings.TrimSpace(body) != "" {
		existing.Bodies = append(existing.Bodies, body)
	}
}

func splitWorkflowKnowledgeBody(body string) (string, string) {
	body = trimFirstHeading(body)
	marker := "\n## Project KnowledgeBase"
	index := strings.Index(body, marker)
	if index < 0 {
		return body, body
	}
	workflow := strings.TrimSpace(body[:index])
	knowledge := strings.TrimSpace(body[index+1:])
	if guidance := strings.Index(knowledge, "\n## Change guidance"); guidance >= 0 {
		knowledge = strings.TrimSpace(knowledge[:guidance]) + "\n\n## Verification\n\n- Run `go test ./internal/knowledgebase` for scan, retrieval, validation, render, and export changes."
	}
	workflow += "\n\n## Verification\n\n- Run `go test ./internal/workflowrunner ./internal/taskrunsubmit ./internal/catalog ./internal/codexrouter ./internal/httpapi` for workflow, attribution, and evaluation changes."
	return workflow, knowledge
}

func cleanMigratedMarkdown(value string) string {
	replacements := map[string]string{
		"鈥檚":  "’s",
		"鈥攊":  "—i",
		"鈥攁":  "—a",
		"鈥攖":  "—t",
		"â": "’",
		"â": "—",
	}
	for old, replacement := range replacements {
		value = strings.ReplaceAll(value, old, replacement)
	}
	return strings.TrimSpace(value)
}

func trimFirstHeading(value string) string {
	lines := strings.Split(strings.TrimSpace(value), "\n")
	if len(lines) > 0 && strings.HasPrefix(strings.TrimSpace(lines[0]), "# ") {
		lines = lines[1:]
	}
	return strings.TrimSpace(strings.Join(lines, "\n"))
}

func demoteMarkdownHeadings(value string) string {
	value = trimFirstHeading(value)
	lines := strings.Split(value, "\n")
	for index, line := range lines {
		if strings.HasPrefix(line, "## ") {
			lines[index] = "#" + line
		}
	}
	return strings.Join(lines, "\n")
}

func rewriteMigratedLinks(value string, targets map[string]string) string {
	for old, target := range targets {
		value = strings.ReplaceAll(value, "("+old+")", "("+target+")")
	}
	return value
}

func migrationSlug(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	value = strings.ReplaceAll(value, "_", "-")
	value = strings.ReplaceAll(value, " ", "-")
	return strings.Trim(value, "-")
}

func migrationTitle(slug string) string {
	parts := strings.Split(slug, "-")
	for index := range parts {
		if parts[index] != "" {
			parts[index] = strings.ToUpper(parts[index][:1]) + parts[index][1:]
		}
	}
	return strings.Join(parts, " ")
}

func uniqueMigrationStrings(values []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" || seen[value] {
			continue
		}
		seen[value] = true
		out = append(out, value)
	}
	return out
}
