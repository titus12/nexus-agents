# go-btd-game-server

This project template was copied from `D:\workspace\src\btd-game-server`.

Stack: `go`
Domain: `btd`, `game-server`

Imported template groups:

- Agents: `templates/agents/claude/go-*.md` and `templates/agents/codex/go-*.toml`
- Rules: `templates/rules/go-*.md` plus shared rules
- Skills: `templates/skills/go-*.md`, `templates/skills/high-risk-api.md`, and btd cross-service skills
- Workflows: `templates/workflows/go-*.md` plus shared workflow files

Verification commands:

```powershell
go build -tags actor_id_uint64 ./cmd/server/
go test -tags actor_id_uint64 ./...
go vet -tags actor_id_uint64 ./...
```
