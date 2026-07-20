package gbrain

import (
	"context"

	"nexus-agents/internal/knowledgegraph"
)

func (p *Provider) Snapshot() knowledgegraph.GraphHealth {
	return p.process.Snapshot()
}

func (p *Provider) Check(ctx context.Context) (knowledgegraph.GraphHealth, error) {
	return p.process.Health(ctx)
}
