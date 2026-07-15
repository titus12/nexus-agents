# KnowledgeBase Templates

This directory stores portable KnowledgeBase templates.

Rules:

- Keep only reusable framework and template content here.
- Do not store project-specific architecture facts, business modules, concrete paths, config IDs, asset names, or protocol details here.
- Project facts remain in each project's own `KnowledgeBase`.
- Use these templates for initialization, gap filling, or human-approved upgrades; do not blindly overwrite a project's existing KnowledgeBase.

Current layout:

```text
KnowledgeBase/
  index.md
  log.md
  framework/  # OKF document contract, scope rules, source-of-truth rules, quality gates, and sync checklist
  project/    # project-specific routing and domains are added after project discovery
```
