# Nexus Agents Prototype Roadmap

## Goal
Build the static HTML design document for Nexus Agents as an AI development configuration manager. V1 models two layers:

- `Agents`, `Rules`, `Skills`, and `Workflows` are the global Template Library.
- `Projects` manages each project's own Project Config Set copied from, or created independently of, the Template Library.

## Scope
- Create and maintain a `design/` static prototype using plain HTML, CSS, and JavaScript.
- Follow the fragment-based pattern from `D:\workspace\src\rescenter\design`.
- Use mock data only; do not connect to Go backend, LiteLLM, Winky, Codex, Claude, or local project files.
- Represent `btd-game-server` as the first imported project and show its `.claude`, `.codex`, `.mcp.json`, and `.proxy` status.
- Show template lineage and manual sync concepts in the prototype; do not write real project files.

## Core Concepts
- `Template Library`: global Agents, Rules, Skills, and Workflows that can be copied into projects.
- `Project Config Set`: a project's local Agents, Rules, Skills, and Workflows.
- `Origin`: project copies record the source template id, base version, and base hash.
- `Manual Sync`: V1 never auto-overwrites project configuration.
- `Template Creation`: users can manually create templates in the global library.
- `No Promotion`: V1 does not support one-click promotion from project changes back into templates.

## Metadata Model
Template item:

```yaml
id: tpl_rule_project_model
kind: rule # agent | rule | skill | workflow
slug: project-model
version: 3
entry: RULE.md
files:
  - RULE.md
```

Project copy:

```yaml
id: proj_rule_btd_project_model
kind: rule
origin:
  templateId: tpl_rule_project_model
  baseVersion: 3
  baseHash: sha256:xxxx
localVersion: 1
syncMode: manual
status: synced # synced | project_modified | template_updated | diverged | detached
```

Skill package example:

```text
skills/testing/
  nexus.skill.yaml
  SKILL.md
  references/
  scripts/
```

## Directory Contract
```text
design/
  demo.html
  shared.css
  README.md
  serve.ps1
  pages/
  overlays/
  js/
```

## Acceptance Criteria
- `design/demo.html` loads over HTTP and renders the Projects page by default.
- Navigation can switch between Projects, Agents, Rules, Skills, Workflows, and Model Proxy.
- Agents, Rules, Skills, and Workflows behave as card-based Template Library entries.
- Projects can show project-local config copies, origin lineage, version/hash metadata, and manual sync states.
- Drawers and modals open for import project, sync preview, agent/rule/skill details, workflow node config, workflow run details, route edit, and proxy test.
- The workflow page shows a ComfyUI-style node canvas mock with selectable nodes and simulated run state inside Workflows, not as a first-level Runs page.
- `scripts/verify_design.py` passes.
