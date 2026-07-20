package gbrain

import (
	"context"
	"fmt"
	"sort"
	"strings"

	"nexus-agents/internal/knowledgegraph"
)

type sourceListResult struct {
	Sources []struct {
		ID       string `json:"id"`
		SourceID string `json:"source_id"`
		Name     string `json:"name"`
	} `json:"sources"`
}

func (p *Provider) SyncSource(ctx context.Context, source knowledgegraph.GraphSource) (knowledgegraph.GraphSyncResult, error) {
	p.mu.Lock()
	defer p.mu.Unlock()

	if strings.TrimSpace(source.ID) == "" {
		return knowledgegraph.GraphSyncResult{}, fmt.Errorf("GBrain source id is empty")
	}
	providerSourceID := strings.TrimSpace(source.ProviderSourceID)
	expectedProviderSourceID := knowledgegraph.StableProviderSourceID(source.ID)
	if providerSourceID == "" {
		providerSourceID = expectedProviderSourceID
	}
	if providerSourceID != expectedProviderSourceID {
		return knowledgegraph.GraphSyncResult{}, fmt.Errorf(
			"GBrain provider source id %q does not match deterministic id %q",
			providerSourceID, expectedProviderSourceID,
		)
	}
	sourceCreated, err := p.ensureSource(ctx, providerSourceID, source.ProjectID)
	if err != nil {
		return knowledgegraph.GraphSyncResult{}, unavailable("ensure source", err)
	}
	if err := p.process.RestartWithEnvironment(ctx, map[string]string{"GBRAIN_SOURCE": providerSourceID}); err != nil {
		return knowledgegraph.GraphSyncResult{}, unavailable("switch source to "+providerSourceID, err)
	}
	client, err := p.process.Client()
	if err != nil {
		return knowledgegraph.GraphSyncResult{}, unavailable("get MCP client", err)
	}

	documents := append([]knowledgegraph.GraphDocument(nil), source.Documents...)
	if sourceCreated && len(source.AllDocuments) > 0 {
		documents = append([]knowledgegraph.GraphDocument(nil), source.AllDocuments...)
	}
	sort.Slice(documents, func(i, j int) bool { return documents[i].Slug < documents[j].Slug })
	for _, document := range documents {
		if strings.TrimSpace(document.Slug) == "" {
			return knowledgegraph.GraphSyncResult{}, fmt.Errorf("GBrain document slug is empty for %s", document.Path)
		}
		if strings.TrimSpace(document.Content) == "" {
			return knowledgegraph.GraphSyncResult{}, fmt.Errorf("GBrain document content is empty for %s", document.Slug)
		}
		var result map[string]any
		if err := client.CallTool(ctx, "put_page", map[string]any{
			"slug": document.Slug, "content": document.Content,
		}, &result); err != nil {
			return knowledgegraph.GraphSyncResult{}, unavailable("put page "+document.Slug, err)
		}
	}

	deleteSlugs := append([]string(nil), source.DeleteSlugs...)
	if sourceCreated {
		deleteSlugs = nil
	}
	sort.Strings(deleteSlugs)
	for _, slug := range deleteSlugs {
		if strings.TrimSpace(slug) == "" {
			continue
		}
		var result map[string]any
		if err := client.CallTool(ctx, "delete_page", map[string]any{"slug": slug}, &result); err != nil {
			if isNotFoundToolError(err) {
				continue
			}
			return knowledgegraph.GraphSyncResult{}, unavailable("delete page "+slug, err)
		}
	}
	return knowledgegraph.GraphSyncResult{
		SourceID: source.ID, ProviderSourceID: providerSourceID,
		Documents: len(documents), Updated: len(documents), Deleted: len(deleteSlugs),
	}, nil
}

func (p *Provider) RemoveSource(ctx context.Context, sourceID string) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	providerSourceID := knowledgegraph.StableProviderSourceID(sourceID)
	client, err := p.process.Client()
	if err != nil {
		return unavailable("get MCP client", err)
	}
	var result map[string]any
	if err := client.CallTool(ctx, "sources_remove", map[string]any{
		"id": providerSourceID, "confirm": true, "keep_storage": false,
	}, &result); err != nil {
		if isNotFoundToolError(err) {
			return nil
		}
		return unavailable("remove source "+providerSourceID, err)
	}
	return nil
}

func unavailable(operation string, err error) error {
	return fmt.Errorf("%w: GBrain %s: %v", knowledgegraph.ErrProviderUnavailable, operation, err)
}

func isNotFoundToolError(err error) bool {
	message := strings.ToLower(err.Error())
	return strings.Contains(message, "not found") || strings.Contains(message, "does not exist")
}

func (p *Provider) ensureSource(ctx context.Context, providerSourceID, projectID string) (bool, error) {
	client, err := p.process.Client()
	if err != nil {
		return false, err
	}
	var sources sourceListResult
	if err := client.CallTool(ctx, "sources_list", map[string]any{}, &sources); err != nil {
		return false, fmt.Errorf("list GBrain sources: %w", err)
	}
	for _, source := range sources.Sources {
		id := strings.TrimSpace(source.ID)
		if id == "" {
			id = strings.TrimSpace(source.SourceID)
		}
		if id == providerSourceID {
			return false, nil
		}
	}
	name := strings.TrimSpace(projectID)
	if name == "" {
		name = providerSourceID
	}
	nameRunes := []rune(name)
	if len(nameRunes) > 64 {
		name = string(nameRunes[:64])
	}
	var result map[string]any
	if err := client.CallTool(ctx, "sources_add", map[string]any{
		"id": providerSourceID, "name": name, "federated": true,
	}, &result); err != nil {
		return false, fmt.Errorf("register GBrain source %s: %w", providerSourceID, err)
	}
	return true, nil
}
