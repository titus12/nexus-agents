package knowledgebase

import (
	"fmt"
	"log"
	"path"
	"regexp"
	"sort"
	"strings"
)

const (
	RetrieveModeSearch  = "search"
	RetrieveModeRouting = "routing"
	RetrieveModeContext = "context"

	defaultRetrieveLimit     = 8
	defaultRetrieveMaxTokens = 6000
	maxSectionTokens         = 500
	maxFileTokens            = 1500
)

var knowledgePathPattern = regexp.MustCompile(`(?:KnowledgeBase/[^\s\])'"` + "`" + `]+|(?:\./|\.\./)[^\s\])'"` + "`" + `]+\.md)`)

func Retrieve(projectRoot string, query string, options RetrieveOptions) (RetrievalResult, error) {
	options = normalizeRetrieveOptions(options)
	query = strings.TrimSpace(query)
	bundle, err := ScanBundle(projectRoot)
	if err != nil {
		return RetrievalResult{}, err
	}
	if !bundle.Exists {
		return normalizeRetrievalResult(RetrievalResult{
			Query:       query,
			Mode:        options.Mode,
			TokenBudget: TokenBudget{MaxTokens: options.MaxTokens},
			Reason:      "Knowledge base does not exist.",
		}), nil
	}
	sections := BuildKnowledgeSections(bundle)
	sectionByID := map[string]KnowledgeSection{}
	sectionsByPath := map[string][]KnowledgeSection{}
	docByPath := map[string]Document{}
	for _, doc := range bundle.Documents {
		docByPath[doc.Path] = doc
	}
	for _, section := range sections {
		sectionByID[section.ID] = section
		sectionsByPath[section.Path] = append(sectionsByPath[section.Path], section)
	}
	queryRewrite := rewriteKnowledgeQuery(query, options.QueryRewrite)
	terms := expandQueryTerms(query)
	if queryRewrite.Used {
		terms = mergeQueryTerms(terms, termsFromQueryRewrite(queryRewrite))
	}
	aliasIndex := buildRoutingAliasIndex(bundle)
	matchedDomain, matchedAlias := inferMatchedDomain(terms, sections, aliasIndex)
	routingDocs, routingTargets, missing := parseRoutingKnowledge(bundle, matchedDomain)

	index, err := NewSectionSearchIndex(sections)
	if err != nil {
		return RetrievalResult{}, err
	}
	defer index.Close()
	hits, err := index.Search(strings.Join(terms, " "), max(options.Limit*6, 24))
	if err != nil {
		return RetrievalResult{}, err
	}

	items := map[string]KnowledgeContextItem{}
	addSection := func(section KnowledgeSection, score float64, required bool, reasons ...string) {
		key := section.ID
		item := items[key]
		if item.Path == "" {
			item = contextItemFromSection(section)
		}
		item.Score += score
		item.Required = item.Required || required
		item.Reasons = mergeStrings(item.Reasons, reasons...)
		if item.Snippet == "" {
			item.Snippet = compressSnippet(section.Body, terms, maxSectionTokens)
			item.Tokens = estimateTokens(item.Snippet)
		}
		items[key] = item
	}

	for _, docPath := range routingDocs {
		for _, section := range pickSectionsForPath(sectionsByPath, docPath, terms, 1) {
			score := 100.0
			required := true
			if matchedDomain != "" && docPath == DefaultRoot+"/project/routing.md" {
				score = 25
				required = false
			}
			addSection(section, score, required, "routing entrypoint")
		}
	}
	for target, sources := range routingTargets {
		for _, section := range pickSectionsForPath(sectionsByPath, target, terms, 1) {
			addSection(section, 60, true, "referenced by "+strings.Join(sources, ", "))
		}
	}
	for _, hit := range hits {
		section, ok := sectionByID[hit.SectionID]
		if !ok {
			continue
		}
		if !sectionAllowedForDomain(section, matchedDomain) {
			continue
		}
		score := 30.0 + (-hit.Rank * 1000)
		item := contextItemFromSection(section)
		item.Score = score
		item.Snippet = hit.Snippet
		item.Tokens = estimateTokens(stripHTMLTags(hit.Snippet))
		item.Reasons = append(item.Reasons, "fts5 bm25 match")
		items[hit.SectionID] = mergeContextItem(items[hit.SectionID], item)
	}
	for id, item := range items {
		section := sectionByID[id]
		boost, reasons := okfBoost(section, terms, matchedDomain)
		item.Score += boost
		item.Reasons = mergeStrings(item.Reasons, reasons...)
		if item.Score >= 70 {
			item.Required = true
		}
		items[id] = item
	}

	all := make([]KnowledgeContextItem, 0, len(items))
	for _, item := range items {
		all = append(all, item)
	}
	sortContextItems(all)
	required, optional, related := classifyItems(all, options.Limit)
	required, optional, related, omitted, usedTokens := packKnowledgeBudget(required, optional, related, options.MaxTokens)
	result := RetrievalResult{
		Query:                   query,
		Mode:                    options.Mode,
		MatchedDomain:           matchedDomain,
		MatchedAlias:            matchedAlias,
		Confidence:              confidence(required, optional, matchedDomain),
		Terms:                   terms,
		Required:                required,
		Optional:                optional,
		Related:                 related,
		MissingFiles:            missing,
		Omitted:                 omitted,
		RoutingDocuments:        routingDocs,
		QueryRewrite:            queryRewrite,
		TokenBudget:             TokenBudget{MaxTokens: options.MaxTokens, UsedTokens: usedTokens},
		Reason:                  retrievalReason(matchedDomain, required),
		LoadedKnowledgeMarkdown: buildLoadedKnowledgeMarkdown(required),
	}
	logRetrievalResult(result)
	_ = docByPath
	return normalizeRetrievalResult(result), nil
}

func normalizeRetrieveOptions(options RetrieveOptions) RetrieveOptions {
	if options.Mode == "" {
		options.Mode = RetrieveModeRouting
	}
	if options.Limit <= 0 {
		options.Limit = defaultRetrieveLimit
	}
	if options.MaxTokens <= 0 {
		options.MaxTokens = defaultRetrieveMaxTokens
	}
	return options
}

func logRetrievalResult(result RetrievalResult) {
	log.Printf("[knowledge] retrieve query=%q mode=%s rewrite_triggered=%t rewrite_used=%t rewrite_model=%s rewrite_english=%q rewrite_keywords=%q rewrite_error=%q matched_domain=%s confidence=%.2f terms=%q required=%s optional=%s related=%s missing=%q reason=%q",
		result.Query,
		result.Mode,
		result.QueryRewrite.Triggered,
		result.QueryRewrite.Used,
		result.QueryRewrite.Model,
		result.QueryRewrite.EnglishQuery,
		strings.Join(result.QueryRewrite.Keywords, ", "),
		result.QueryRewrite.Error,
		result.MatchedDomain,
		result.Confidence,
		strings.Join(result.Terms, ", "),
		logContextItems(result.Required, 5),
		logContextItems(result.Optional, 3),
		logContextItems(result.Related, 3),
		strings.Join(result.MissingFiles, ", "),
		result.Reason,
	)
}

func logContextItems(items []KnowledgeContextItem, limit int) string {
	if len(items) == 0 {
		return "[]"
	}
	if limit <= 0 || limit > len(items) {
		limit = len(items)
	}
	parts := make([]string, 0, limit)
	for _, item := range items[:limit] {
		parts = append(parts, fmt.Sprintf("{path:%s score:%.1f required:%t reasons:%s}", item.Path, item.Score, item.Required, strings.Join(item.Reasons, "|")))
	}
	if len(items) > limit {
		parts = append(parts, fmt.Sprintf("...+%d", len(items)-limit))
	}
	return "[" + strings.Join(parts, " ") + "]"
}

func contextItemFromSection(section KnowledgeSection) KnowledgeContextItem {
	return KnowledgeContextItem{
		Path:      section.Path,
		Title:     section.Title,
		Type:      section.Type,
		Domain:    section.Domain,
		Heading:   section.Heading,
		StartLine: section.StartLine,
		EndLine:   section.EndLine,
		Tokens:    min(section.Tokens, maxSectionTokens),
		Snippet:   compressSnippet(section.Body, nil, maxSectionTokens),
	}
}

func parseRoutingKnowledge(bundle Bundle, matchedDomain string) ([]string, map[string][]string, []string) {
	docByPath := map[string]bool{}
	documents := map[string]Document{}
	for _, doc := range bundle.Documents {
		docByPath[doc.Path] = true
		documents[doc.Path] = doc
	}
	var routingDocs []string
	projectRouting := DefaultRoot + "/project/routing.md"
	if matchedDomain == "" && docByPath[projectRouting] {
		routingDocs = append(routingDocs, projectRouting)
	}
	if matchedDomain != "" {
		for _, domainRouting := range domainRoutingPaths(matchedDomain) {
			if docByPath[domainRouting] {
				routingDocs = append(routingDocs, domainRouting)
			}
		}
		for _, overview := range domainOverviewPaths(matchedDomain) {
			if docByPath[overview] && !containsString(routingDocs, overview) {
				routingDocs = append(routingDocs, overview)
			}
		}
	}
	for _, doc := range bundle.Documents {
		if doc.Name == "routing.md" && !containsString(routingDocs, doc.Path) && (matchedDomain == "" || inferDomainFromPath(doc.Path) == matchedDomain || strings.Contains(doc.Path, "/domains/"+matchedDomain+"/")) {
			routingDocs = append(routingDocs, doc.Path)
		}
	}
	targets := map[string][]string{}
	missingSet := map[string]bool{}
	for _, routingDoc := range routingDocs {
		doc := documents[routingDoc]
		for _, target := range extractKnowledgeTargets(doc) {
			if target == "" {
				continue
			}
			if matchedDomain != "" {
				targetDomain := inferDomainFromPath(target)
				if targetDomain != "" && targetDomain != matchedDomain {
					continue
				}
			}
			targets[target] = append(targets[target], routingDoc)
			if strings.HasPrefix(target, DefaultRoot+"/") && !docByPath[target] {
				missingSet[target] = true
			}
		}
	}
	var missing []string
	for target := range missingSet {
		missing = append(missing, target)
	}
	sort.Strings(missing)
	return routingDocs, targets, missing
}

func extractKnowledgeTargets(doc Document) []string {
	found := map[string]bool{}
	for _, link := range doc.Links {
		target := resolveTargetPath(doc.Directory, link.Target)
		if target != "" {
			found[target] = true
		}
	}
	for _, match := range knowledgePathPattern.FindAllString(doc.Body, -1) {
		target := resolveTargetPath(doc.Directory, match)
		if target != "" {
			found[target] = true
		}
	}
	var targets []string
	for target := range found {
		targets = append(targets, target)
	}
	sort.Strings(targets)
	return targets
}

func resolveTargetPath(directory string, target string) string {
	target = cleanKnowledgePath(target)
	if target == "" || strings.HasPrefix(target, "http://") || strings.HasPrefix(target, "https://") {
		return ""
	}
	if strings.HasPrefix(target, DefaultRoot+"/") {
		return target
	}
	return path.Clean(path.Join(directory, target))
}

func pickSectionsForPath(sectionsByPath map[string][]KnowledgeSection, docPath string, terms []string, limit int) []KnowledgeSection {
	sections := append([]KnowledgeSection{}, sectionsByPath[docPath]...)
	if len(sections) <= limit {
		return sections
	}
	sort.SliceStable(sections, func(i, j int) bool {
		return termHitCount(sections[i].Body+" "+sections[i].Heading, terms) > termHitCount(sections[j].Body+" "+sections[j].Heading, terms)
	})
	return sections[:limit]
}

func okfBoost(section KnowledgeSection, terms []string, matchedDomain string) (float64, []string) {
	var score float64
	var reasons []string
	if section.Path == DefaultRoot+"/project/routing.md" {
		if matchedDomain == "" {
			score += 100
			reasons = append(reasons, "project routing entrypoint")
		} else {
			score += 5
			reasons = append(reasons, "project fallback")
		}
	}
	if matchedDomain != "" {
		for _, routingPath := range domainRoutingPaths(matchedDomain) {
			if section.Path == routingPath {
				score += 80
				reasons = append(reasons, "matched domain routing")
				break
			}
		}
	}
	if matchedDomain != "" && section.Domain == matchedDomain {
		score += 20
		reasons = append(reasons, "same domain "+matchedDomain)
	}
	if matchedDomain != "" && section.Domain == matchedDomain && isDomainOverviewDocument(section.Path) {
		score += 80
		reasons = append(reasons, "matched domain overview")
	}
	if termHitCount(section.Path, terms) > 0 {
		score += 30
		reasons = append(reasons, "path matched query terms")
	}
	if termHitCount(strings.Join(section.Tags, " "), terms) > 0 {
		score += 30
		reasons = append(reasons, "frontmatter tags matched query terms")
	}
	if termHitCount(section.Title+" "+section.Heading, terms) > 0 {
		score += 25
		reasons = append(reasons, "title or heading matched query terms")
	}
	if termHitCount(section.Frontmatter.Description, terms) > 0 {
		score += 15
		reasons = append(reasons, "description matched query terms")
	}
	return score, reasons
}

func classifyItems(items []KnowledgeContextItem, limit int) ([]KnowledgeContextItem, []KnowledgeContextItem, []KnowledgeContextItem) {
	var required, optional, related []KnowledgeContextItem
	for _, item := range items {
		switch {
		case item.Required || item.Score >= 70:
			item.Required = true
			required = append(required, item)
		case item.Score >= 40:
			optional = append(optional, item)
		case item.Score >= 20:
			related = append(related, item)
		}
	}
	return limitItems(required, limit), limitItems(optional, limit), limitItems(related, limit)
}

func packKnowledgeBudget(required, optional, related []KnowledgeContextItem, maxTokens int) ([]KnowledgeContextItem, []KnowledgeContextItem, []KnowledgeContextItem, []KnowledgeOmittedItem, int) {
	fileTokens := map[string]int{}
	used := 0
	var omitted []KnowledgeOmittedItem
	pack := func(items []KnowledgeContextItem) []KnowledgeContextItem {
		var packed []KnowledgeContextItem
		for _, item := range items {
			if item.Tokens <= 0 {
				item.Tokens = estimateTokens(stripHTMLTags(item.Snippet))
			}
			if item.Tokens > maxSectionTokens {
				item.Snippet = truncateRunes(item.Snippet, maxSectionTokens*4)
				item.Tokens = estimateTokens(stripHTMLTags(item.Snippet))
			}
			if used+item.Tokens > maxTokens {
				omitted = append(omitted, KnowledgeOmittedItem{Path: item.Path, Reason: "over token budget"})
				continue
			}
			if fileTokens[item.Path]+item.Tokens > maxFileTokens {
				omitted = append(omitted, KnowledgeOmittedItem{Path: item.Path, Reason: "over per-file token budget"})
				continue
			}
			used += item.Tokens
			fileTokens[item.Path] += item.Tokens
			packed = append(packed, item)
		}
		return packed
	}
	return pack(required), pack(optional), pack(related), omitted, used
}

func buildLoadedKnowledgeMarkdown(required []KnowledgeContextItem) string {
	if len(required) == 0 {
		return "## Loaded Knowledge\n\nNo specific KnowledgeBase files matched this task. Read `KnowledgeBase/project/routing.md` first when present and select the relevant domain manually."
	}
	lines := []string{"## Loaded Knowledge", ""}
	for _, item := range required {
		location := item.Path
		if item.Heading != "" {
			location += " — " + item.Heading
		}
		lines = append(lines, fmt.Sprintf("- `%s`", location))
	}
	return strings.Join(lines, "\n")
}

func expandQueryTerms(query string) []string {
	lower := strings.ToLower(query)
	splitter := func(r rune) bool {
		return r == ' ' || r == '\t' || r == '\n' || strings.ContainsRune(",，。.;；:：/\\()[]{}+-", r)
	}
	seen := map[string]bool{}
	var terms []string
	add := func(term string) {
		term = strings.TrimSpace(strings.ToLower(term))
		if term == "" || seen[term] {
			return
		}
		seen[term] = true
		terms = append(terms, term)
	}
	for _, part := range strings.FieldsFunc(lower, splitter) {
		add(part)
	}
	if len(terms) == 0 && lower != "" {
		add(lower)
	}
	return terms
}

type routingAliasEntry struct {
	Domain      string
	Source      string
	Alias       string
	PairedAlias string
}

func buildRoutingAliasIndex(bundle Bundle) map[string]routingAliasEntry {
	index := map[string]routingAliasEntry{}
	for _, doc := range bundle.Documents {
		if !isDomainRoutingDocument(doc.Path) && !isDomainOverviewDocument(doc.Path) {
			continue
		}
		domain := inferDomainFromPath(doc.Path)
		if domain == "" {
			continue
		}
		add := func(alias, paired string) {
			alias = strings.TrimSpace(alias)
			if alias == "" {
				return
			}
			key := normalizeAlias(alias)
			if _, exists := index[key]; exists {
				return
			}
			index[key] = routingAliasEntry{Domain: domain, Source: doc.Path, Alias: alias, PairedAlias: paired}
		}
		for _, pair := range doc.Frontmatter.Routing.Aliases.Pairs {
			add(pair.ZH, pair.EN)
			add(pair.EN, pair.ZH)
		}
		for _, alias := range doc.Frontmatter.Routing.Aliases.Values {
			add(alias, "")
		}
		for _, alias := range doc.Frontmatter.Routing.Aliases.ZH {
			add(alias, "")
		}
		for _, alias := range doc.Frontmatter.Routing.Aliases.EN {
			add(alias, "")
		}
	}
	return index
}

func inferMatchedDomain(terms []string, sections []KnowledgeSection, aliasIndex map[string]routingAliasEntry) (string, MatchedAlias) {
	if domain, matched := inferDomainFromRoutingAliases(terms, aliasIndex); domain != "" {
		return domain, matched
	}
	scores := map[string]int{}
	for _, section := range sections {
		if section.Domain == "" {
			continue
		}
		text := section.Path + " " + strings.Join(section.Tags, " ") + " " + section.Title + " " + section.Heading
		scores[section.Domain] += termHitCount(text, terms)
		if containsString(terms, section.Domain) {
			scores[section.Domain] += 3
		}
	}
	best := ""
	bestScore := 0
	for domain, score := range scores {
		if score > bestScore {
			best = domain
			bestScore = score
		}
	}
	return best, MatchedAlias{Domain: best}
}

func inferDomainFromRoutingAliases(terms []string, aliasIndex map[string]routingAliasEntry) (string, MatchedAlias) {
	joined := normalizeAlias(strings.Join(terms, " "))
	for _, term := range terms {
		normalized := normalizeAlias(term)
		if entry, ok := aliasIndex[normalized]; ok {
			return entry.Domain, MatchedAlias{Alias: entry.Alias, PairedAlias: entry.PairedAlias, Domain: entry.Domain, Source: entry.Source}
		}
	}
	for alias, entry := range aliasIndex {
		if alias != "" && strings.Contains(joined, alias) {
			return entry.Domain, MatchedAlias{Alias: entry.Alias, PairedAlias: entry.PairedAlias, Domain: entry.Domain, Source: entry.Source}
		}
	}
	return "", MatchedAlias{}
}

func sectionAllowedForDomain(section KnowledgeSection, matchedDomain string) bool {
	if matchedDomain == "" {
		return true
	}
	if section.Domain == "" {
		return false
	}
	return section.Domain == matchedDomain
}

func buildFTSQuery(query string) string {
	terms := expandQueryTerms(query)
	var parts []string
	for _, term := range terms {
		term = strings.Trim(term, `"'`)
		if term == "" {
			continue
		}
		parts = append(parts, `"`+strings.ReplaceAll(term, `"`, `""`)+`"`)
	}
	return strings.Join(parts, " OR ")
}

func compressSnippet(text string, terms []string, maxTokens int) string {
	text = strings.TrimSpace(text)
	if text == "" {
		return ""
	}
	lines := strings.Split(text, "\n")
	if len(terms) == 0 {
		return truncateRunes(text, maxTokens*4)
	}
	selected := map[int]bool{}
	for i, line := range lines {
		if termHitCount(line, terms) == 0 {
			continue
		}
		for j := max(0, i-2); j <= min(len(lines)-1, i+2); j++ {
			selected[j] = true
		}
	}
	if len(selected) == 0 {
		return truncateRunes(text, maxTokens*4)
	}
	var out []string
	for i := 0; i < len(lines); i++ {
		if selected[i] {
			out = append(out, lines[i])
		}
	}
	return truncateRunes(strings.TrimSpace(strings.Join(out, "\n")), maxTokens*4)
}

func termHitCount(text string, terms []string) int {
	text = strings.ToLower(text)
	count := 0
	for _, term := range terms {
		if term != "" && strings.Contains(text, strings.ToLower(term)) {
			count++
		}
	}
	return count
}

func stripHTMLTags(text string) string {
	text = strings.ReplaceAll(text, "<mark>", "")
	text = strings.ReplaceAll(text, "</mark>", "")
	return text
}

func truncateRunes(text string, maxRunes int) string {
	runes := []rune(text)
	if maxRunes <= 0 || len(runes) <= maxRunes {
		return text
	}
	return string(runes[:maxRunes]) + "..."
}

func mergeContextItem(existing KnowledgeContextItem, next KnowledgeContextItem) KnowledgeContextItem {
	if existing.Path == "" {
		return next
	}
	existing.Score += next.Score
	existing.Required = existing.Required || next.Required
	existing.Reasons = mergeStrings(existing.Reasons, next.Reasons...)
	if next.Snippet != "" {
		existing.Snippet = next.Snippet
		existing.Tokens = next.Tokens
	}
	return existing
}

func mergeStrings(existing []string, values ...string) []string {
	seen := map[string]bool{}
	var out []string
	for _, value := range existing {
		value = strings.TrimSpace(value)
		if value != "" && !seen[value] {
			seen[value] = true
			out = append(out, value)
		}
	}
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value != "" && !seen[value] {
			seen[value] = true
			out = append(out, value)
		}
	}
	return out
}

func sortContextItems(items []KnowledgeContextItem) {
	sort.SliceStable(items, func(i, j int) bool {
		pi := contextPriority(items[i])
		pj := contextPriority(items[j])
		if pi != pj {
			return pi < pj
		}
		if items[i].Score == items[j].Score {
			return items[i].Path < items[j].Path
		}
		return items[i].Score > items[j].Score
	})
}

func contextPriority(item KnowledgeContextItem) int {
	switch {
	case item.Domain != "" && item.Path == DefaultRoot+"/domains/"+item.Domain+"/routing.md":
		return 0
	case item.Path == DefaultRoot+"/project/routing.md":
		return 1
	default:
		return 2
	}
}

func limitItems(items []KnowledgeContextItem, limit int) []KnowledgeContextItem {
	if limit <= 0 || len(items) <= limit {
		return items
	}
	return items[:limit]
}

func confidence(required, optional []KnowledgeContextItem, domain string) float64 {
	if len(required) == 0 {
		return 0.2
	}
	score := 0.55
	if domain != "" {
		score += 0.2
	}
	if len(optional) > 0 {
		score += 0.1
	}
	if score > 0.95 {
		score = 0.95
	}
	return score
}

func retrievalReason(domain string, required []KnowledgeContextItem) string {
	if len(required) == 0 {
		return "No strong KnowledgeBase match was found; keep context small and start from project routing if available."
	}
	if domain != "" {
		return "Matched domain " + domain + " using OKF routing/frontmatter and SQLite FTS5 section search."
	}
	return "Matched project knowledge using OKF routing/frontmatter and SQLite FTS5 section search."
}

func normalizeRetrievalResult(result RetrievalResult) RetrievalResult {
	if result.Terms == nil {
		result.Terms = []string{}
	}
	if result.Required == nil {
		result.Required = []KnowledgeContextItem{}
	}
	if result.Optional == nil {
		result.Optional = []KnowledgeContextItem{}
	}
	if result.Related == nil {
		result.Related = []KnowledgeContextItem{}
	}
	if result.MissingFiles == nil {
		result.MissingFiles = []string{}
	}
	if result.Omitted == nil {
		result.Omitted = []KnowledgeOmittedItem{}
	}
	if result.RoutingDocuments == nil {
		result.RoutingDocuments = []string{}
	}
	return result
}

func containsString(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}
