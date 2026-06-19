# Agent Rule Skill Prototype Plan

## Goal
Prototype the global Template Library for Agents, Rules, and Skills, plus how project copies keep lineage to templates.

## Files
- `design/pages/agents.html`
- `design/pages/rules.html`
- `design/pages/skills.html`
- `design/overlays/drawer-agent.html`
- `design/overlays/drawer-rule.html`
- `design/overlays/drawer-skill.html`

## Implementation
- Agents, Rules, and Skills pages are global Template Library views.
- Use horizontal resource cards, not compact tables.
- Cards show template name, summary, version, usage project count, last updated time, and status.
- Project dropdown filters can show all templates or templates relevant to a selected project.
- Drawers show template metadata, package/source paths, project usage, and manual create/edit controls.
- Users can manually create templates in the global library.
- Project copies record lineage using `origin.templateId + origin.baseVersion + origin.baseHash`.
- Rule can be a single file with metadata frontmatter or a sidecar manifest.
- Skill defaults to a directory package with package-level metadata in `nexus.skill.yaml`.
- Skill package metadata owns immutable id, kind, slug, version, entry file, file list, origin, local version, sync mode, and sync status.
- File names are display and legacy import helpers only; they are not synchronization identity.
- V1 does not support project-change promotion back into templates.

## Metadata Examples
Template Skill package:

```yaml
id: tpl_skill_testing
kind: skill
slug: testing
name: testing
version: 5
entry: SKILL.md
files:
  - SKILL.md
  - references/test-policy.md
  - scripts/verify.ps1
```

Project Skill copy:

```yaml
id: proj_skill_btd_testing
kind: skill
slug: testing
origin:
  templateId: tpl_skill_testing
  baseVersion: 5
  baseHash: sha256:xxxx
localVersion: 1
syncMode: manual
status: project_modified
entry: SKILL.md
files:
  - SKILL.md
  - references/test-policy.md
  - scripts/verify.ps1
```

## Acceptance Criteria
- Agent, Rule, and Skill cards open their drawers.
- Cards and drawers make Template Library identity and project usage obvious.
- Rules and Skills show editable content in drawers for the prototype.
- Skill multi-file package metadata is visible in the prototype content.
- Filters use dropdown project selection rather than many project chips.
- The UI clearly states that template sync is manual and promotion is not available in V1.
