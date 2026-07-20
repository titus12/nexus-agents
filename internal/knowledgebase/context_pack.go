package knowledgebase

import (
	"fmt"
	"strings"
)

func BuildContextPack(documents []ContextPackDocument, maxTokens int) (
	string,
	[]KnowledgeSource,
	[]KnowledgeContextItem,
	[]KnowledgeOmittedItem,
	TokenBudget,
) {
	if maxTokens <= 0 {
		maxTokens = defaultRetrieveMaxTokens
	}
	lines := []string{"## Loaded Knowledge", ""}
	sources := make([]KnowledgeSource, 0, len(documents))
	items := make([]KnowledgeContextItem, 0, len(documents))
	omitted := make([]KnowledgeOmittedItem, 0)
	usedTokens := estimateTokens(strings.Join(lines, "\n"))
	seen := map[string]bool{}

	for _, document := range documents {
		key := strings.TrimSpace(document.SourceID) + "::" + strings.TrimSpace(document.Path)
		if key == "::" || seen[key] {
			continue
		}
		content := strings.TrimSpace(document.Content)
		if content == "" {
			omitted = append(omitted, KnowledgeOmittedItem{Path: document.Path, Reason: "empty approved document"})
			continue
		}
		if estimateTokens(content) > maxFileTokens {
			content = truncateRunes(content, maxFileTokens*4)
		}
		title := strings.TrimSpace(document.Title)
		if title == "" {
			title = document.Path
		}
		block := []string{
			"### " + title,
			"",
			fmt.Sprintf("- Project: `%s`", document.ProjectID),
			fmt.Sprintf("- Source: `%s`", document.SourceID),
			fmt.Sprintf("- Path: `%s`", document.Path),
		}
		if strings.TrimSpace(document.Revision) != "" {
			block = append(block, fmt.Sprintf("- Revision: `%s`", document.Revision))
		}
		block = append(block, "", content, "")
		blockText := strings.Join(block, "\n")
		blockTokens := estimateTokens(blockText)
		if usedTokens+blockTokens > maxTokens {
			omitted = append(omitted, KnowledgeOmittedItem{Path: document.Path, Reason: "over token budget"})
			continue
		}

		seen[key] = true
		lines = append(lines, block...)
		usedTokens += blockTokens
		sources = append(sources, document.KnowledgeSource)
		items = append(items, KnowledgeContextItem{
			ProjectID: document.ProjectID, SourceID: document.SourceID, Revision: document.Revision,
			Path: document.Path, Title: title, Score: document.Score,
			Tokens: blockTokens, Required: true,
			Reasons: []string{"gbrain semantic match", "approved knowledge source"},
			Snippet: document.Snippet,
		})
	}

	if len(sources) == 0 {
		return "## Loaded Knowledge\n\nNo approved GBrain documents fit the context budget.",
			[]KnowledgeSource{}, []KnowledgeContextItem{}, omitted,
			TokenBudget{MaxTokens: maxTokens}
	}
	return strings.TrimSpace(strings.Join(lines, "\n")) + "\n",
		sources, items, omitted, TokenBudget{MaxTokens: maxTokens, UsedTokens: usedTokens}
}
