# Nexus Agents Template Library

This directory stores copied template files, not manifest-only references.

- Files are copied from btd-game-server first, then renamed or split for Nexus Agents.
- Go-related templates use the `go-` filename prefix.
- Non-Go templates keep their plain names.
- No YAML manifests are used in this directory.

Source project:

```text
D:\workspace\src\btd-game-server
```

Layout:

```text
templates/
  agents/
    claude/    # copied .claude agent markdown
    codex/     # copied .codex agent toml
  rules/       # copied rule markdown
  skills/      # copied skill markdown
  workflows/   # workflow markdown plus matching .graph.json expanded from rules/go-00-routing.md
```
