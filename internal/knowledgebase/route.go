package knowledgebase

func PreviewRoute(projectRoot string, task string) (RoutePreview, error) {
	result, err := Retrieve(projectRoot, task, RetrieveOptions{Mode: RetrieveModeRouting, Limit: defaultRetrieveLimit, MaxTokens: defaultRetrieveMaxTokens})
	if err != nil {
		return RoutePreview{}, err
	}
	preview := RoutePreview{
		Task:             task,
		MatchedDomain:    result.MatchedDomain,
		Reason:           result.Reason,
		MissingFiles:     result.MissingFiles,
		RoutingDocuments: result.RoutingDocuments,
		Matches:          result.Required,
		Terms:            result.Terms,
		TokenBudget:      result.TokenBudget,
		LoadedKnowledge:  result.LoadedKnowledgeMarkdown,
	}
	for _, item := range result.Required {
		preview.RequiredFiles = appendUnique(preview.RequiredFiles, item.Path)
	}
	for _, item := range result.Optional {
		preview.OptionalFiles = appendUnique(preview.OptionalFiles, item.Path)
	}
	return normalizeRoutePreview(preview), nil
}

func appendUnique(values []string, value string) []string {
	for _, existing := range values {
		if existing == value {
			return values
		}
	}
	return append(values, value)
}
