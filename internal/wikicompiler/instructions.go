package wikicompiler

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

func BuildInstructions(input CompileInput) string {
	topics := append([]string(nil), input.RequiredTopics...)
	sort.Strings(topics)
	manifest := append([]string(nil), input.ScanManifest...)
	sort.Strings(manifest)
	var builder strings.Builder
	builder.WriteString("# Nexus OpenWiki compilation contract\n\n")
	builder.WriteString("Generate concise, AI-friendly and developer-friendly project knowledge in Markdown.\n\n")
	builder.WriteString("## Source precedence\n\n")
	builder.WriteString("1. Current source code, configuration, schemas, contracts, and CodeGraph facts.\n")
	builder.WriteString("2. Repository-owned ADRs, docs, and approved existing project knowledge.\n")
	builder.WriteString("3. Run-scoped Feishu/external references as auxiliary intent only.\n")
	builder.WriteString("4. AI inference, clearly labeled and never presented as current fact without evidence.\n\n")
	builder.WriteString("Do not claim that an external design plan is implemented when code does not support it. Record conflicts as warnings.\n")
	builder.WriteString("Do not modify root AGENTS.md, CLAUDE.md, CI workflows, or files outside openwiki/.\n\n")
	builder.WriteString("## Snapshot semantics\n\n")
	builder.WriteString("This directory is a security-isolated snapshot of committed revision `" + strings.TrimSpace(input.Revision) + "`.\n")
	builder.WriteString("The snapshot intentionally omits `.git`. Do not describe missing Git metadata/history as a project limitation and do not infer that the source repository lacks Git history.\n\n")
	if language := strings.TrimSpace(input.Language); language != "" {
		builder.WriteString("Write titles, descriptions, and explanatory prose in `" + language + "` unless a product name, API identifier, or source quotation must remain in its original language.\n")
		builder.WriteString("Regardless of the primary document language, every Domain must provide both Chinese and English routing aliases.\n\n")
	}
	builder.WriteString("## Nexus OKF output contract\n\n")
	builder.WriteString("- OpenWiki deterministically owns every `openwiki/**/index.md` file as directory navigation. Never place substantive knowledge in an `index.md` file because OpenWiki overwrites it after the agent run.\n")
	builder.WriteString("- `openwiki/index.md` and `openwiki/domains/<slug>/index.md` are generated navigation metadata; Nexus ignores Domain directory indexes during normalization.\n")
	builder.WriteString("- Project knowledge is domains-only: do not create top-level concept files or non-domain category directories.\n")
	builder.WriteString("- First identify the project's stable core capabilities, then create one substantive page per capability at `openwiki/domains/<stable-domain-slug>/domain.md`; Nexus promotes that file to the canonical `KnowledgeBase/project/domains/<stable-domain-slug>/index.md` page.\n")
	builder.WriteString("- A Domain is a user-visible business or technical capability, not a source directory, package, deployment unit, document category, or required topic copied mechanically.\n")
	builder.WriteString("- Keep relationships, HTTP entrypoints, data ownership, source paths, dependencies, verification, and limitations inside the owning Domain page.\n")
	builder.WriteString("- Every Domain `domain.md` must contain substantive knowledge: responsibility/boundary, important entrypoints, owned data or state, related Domains, source paths, and verification.\n")
	builder.WriteString("- Preserve and reorganize the stable facts from approved existing knowledge. A directory-only migration that drops the existing explanations is invalid.\n")
	builder.WriteString("- Only add supporting files below the same Domain when its canonical page would become too large; do not create `services/`, `integrations/`, `workflows/`, or `overview/` roots.\n")
	builder.WriteString("- Do not create a substantive `openwiki/domains/index.md`, a Domain named `domains` or `index`, or a Domain page named `index.md`.\n")
	builder.WriteString("- Other Markdown files must use one of these exact `type` values: Project, Routing, Domain, Guide, Workflow, Schema, Template, Decision, Reference, CodingRules, Rules, Checklist.\n")
	builder.WriteString("- Every `domains/<slug>/domain.md` page must use `type: Domain`, a stable English slug, tags, a concise title/description, and explicit Markdown links to related Domain `domain.md` pages.\n")
	builder.WriteString("- Every Domain frontmatter must declare `routing.aliases.zh` and `routing.aliases.en` with at least one specific, user-facing alias in each language. Use product/API names and common task terminology; keep broad words such as `system`, `data`, or `module` under `routing.keywords` instead.\n")
	builder.WriteString("- Concept frontmatter must include title, description, resource, tags, and an RFC3339 timestamp.\n")
	builder.WriteString("- Use `resource` for the generated document identity and `sourcePaths` for repository files that support its claims.\n")
	builder.WriteString("- Do not leave TODO, TBD, FIXME, placeholder, or coming-soon text in generated knowledge.\n\n")
	builder.WriteString("`openwiki/INSTRUCTIONS.md` is run-control metadata supplied by Nexus. Never cite it, link to it, or describe it as project documentation.\n\n")
	builder.WriteString("## Required topics\n\n")
	for _, topic := range topics {
		builder.WriteString("- " + topic + "\n")
	}
	builder.WriteString("\n## Scan manifest\n\n")
	for _, file := range manifest {
		builder.WriteString("- `" + file + "`\n")
	}
	if strings.TrimSpace(input.AdditionalInstructions) != "" {
		builder.WriteString("\n## Project-specific instructions\n\n")
		builder.WriteString(strings.TrimSpace(input.AdditionalInstructions))
		builder.WriteString("\n")
	}
	if len(input.ExternalSources) > 0 {
		builder.WriteString("\n## Auxiliary initialization references\n\n")
		for _, source := range input.ExternalSources {
			builder.WriteString(fmt.Sprintf("- %s (%s), local snapshot: `%s`\n", source.Title, source.SourceURL, source.LocalPath))
		}
	}
	return builder.String()
}

func WriteInstructions(repositoryRoot, content string) error {
	target := filepath.Join(repositoryRoot, "openwiki", "INSTRUCTIONS.md")
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return err
	}
	return os.WriteFile(target, []byte(content), 0o644)
}
