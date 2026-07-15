# Template Project-Root Layout Design

## Goal

Make `templates/` a directly copyable project-root overlay. Copying its contents to a project such as `D:\workspace\src\btd-client` must place every file at its final runtime path without a second category-to-project-path transformation.

## Confirmed Decisions

- Use the project-root overlay layout rather than a second export directory.
- Remove `templates/README.md`; `templates/` is a runtime overlay, not a documentation container.
- Use `KnowledgeBase/` at the project root. New templates must not put it beneath `design/`.
- Update all Nexus backend, frontend, test, and script references that rely on the old categorized template paths.
- Retain the existing `design/KnowledgeBase` only as a read-only legacy discovery path for already-installed projects. It is never emitted by the new template package and is not a second active knowledge root.

## Target Layout

```text
templates/
  AGENTS.md
  .claude/
    agents/
    commands/
    rules/
    skills/<id>/SKILL.md
    workflows/
  .codex/
    agents/
    skills/<id>/
  .agents/
    commands/
    skills/<id>/
  KnowledgeBase/
    index.md
    log.md
    framework/
    project/.gitkeep
```

The relative path after `templates/` is the exact destination path in an installed project. For example:

```text
templates/.claude/workflows/wf-unity-bugfix.md
  -> <project>/.claude/workflows/wf-unity-bugfix.md

templates/.agents/skills/wf-unity-bugfix/SKILL.md
  -> <project>/.agents/skills/wf-unity-bugfix/SKILL.md

templates/KnowledgeBase/framework/document-contract.md
  -> <project>/KnowledgeBase/framework/document-contract.md
```

## Migration Map

| Old location | New location |
| --- | --- |
| `templates/project-files/AGENTS.md` | `templates/AGENTS.md` |
| `templates/agents/claude/*.md` | `templates/.claude/agents/*.md` |
| `templates/agents/codex/*.toml` | `templates/.codex/agents/*.toml` |
| `templates/commands/claude/*.md` | `templates/.claude/commands/*.md` |
| `templates/rules/**/*.md` | `templates/.claude/rules/**/*.md` |
| `templates/workflows/*` | `templates/.claude/workflows/*` |
| `templates/skills/codex/<id>/**` | `templates/.agents/skills/<id>/**` |
| `templates/skills/*.md` | `templates/.claude/skills/<id>/SKILL.md` |
| portable Codex-only skill copies | `templates/.codex/skills/<id>/**` |
| `templates/knowledgebase/**` | `templates/KnowledgeBase/**` |
| `templates/README.md` | deleted; documentation moves to `docs/` |

## Catalog and Copy Semantics

The catalog will no longer infer a destination from an abstract category path. Each template file has an exact project-relative destination:

```go
projectRelativePath := strings.TrimPrefix(templatePath, "templates/")
```

The catalog will use the direct path for:

- `SourcePaths`, `Entry`, and `Files`;
- one-template installation and synchronization;
- paired Claude/Codex Agent projections;
- workflow markdown, graph, command, and supporting skill files;
- project scanning and origin matching.

The component library still classifies Agents, Rules, Skills, and Workflows for the UI. Only the backing file layout changes.

## KnowledgeBase Policy

`KnowledgeBase` becomes `knowledgebase.DefaultRoot`. All template instructions, generated paths, evaluation parsers, retrieval patterns, UI tree defaults, and tests use it.

Existing projects are handled deterministically:

1. If `KnowledgeBase/` exists, it is the only active knowledge root.
2. If only `design/KnowledgeBase/` exists, it is read as a legacy root and validation reports a migration warning.
3. If both roots exist, validation reports a conflict and retrieval does not merge their contents.

No automatic move is performed in `D:\workspace\src\btd-client`; migration of a real project remains an explicit user-approved operation.

## Compatibility Boundaries

- The new package intentionally does not include `design/KnowledgeBase`.
- Existing project scanning remains capable of discovering legacy knowledge so the application can explain the migration state.
- Template synchronization will write only the new canonical paths.
- The model router and global Codex configuration are not touched.

## Verification

1. A temporary project receives the template package by copying `templates/` contents directly to its root.
2. The resulting `.claude`, `.codex`, `.agents`, root `AGENTS.md`, and `KnowledgeBase` paths are present without path adaptation.
3. The project scanner recognizes installed Agents, Rules, Skills, and Workflows and associates them with the correct templates.
4. KnowledgeBase scan, validation, retrieval, rendering, export, and HTTP endpoints use `KnowledgeBase`.
5. Legacy-only and dual-root fixtures verify the migration warning and conflict behavior.
6. Backend tests, frontend build/tests, and `scripts/verify_template_catalog.py` pass.
