# Unity Workflows Template Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Add the three audited btd-client Unity workflows, their supporting roles/rules/skills, and Unity-specific evaluation roles/workflow into Nexus Agents.

**Architecture:** Extend the existing file-backed template library under 	emplates/ and wire it through internal/catalog/catalog.go specs. Keep project sync behavior consistent with current Go templates, and extend deterministic evaluation policy so Unity workflows are treated as high-risk task families.

**Tech Stack:** Go catalog/http tests, markdown templates, TOML Codex agent projections, JSON workflow graphs.

---

### Task 1: Add Unity template files

**Files:**
- Create/modify files under D:\workspace\src\nexus-agents\templates\agents\claude and 	emplates\agents\codex.
- Create/modify files under D:\workspace\src\nexus-agents\templates\rules.
- Create/modify files under D:\workspace\src\nexus-agents\templates\skills and 	emplates\skills\codex.
- Create workflow markdown and graph files under D:\workspace\src\nexus-agents\templates\workflows.

- [ ] Add Unity agent markdown/TOML templates for workflow execution and evaluation.
- [ ] Add Unity rules copied/adapted from btd-client canonical rules.
- [ ] Add Unity skills copied/adapted from btd-client canonical skills, plus Codex wf entry skills.
- [ ] Add three Unity workflow markdown files from .claude\workflows and one evaluation workflow.
- [ ] Add workflow graph JSON files matching Nexus WorkflowGraph schema.

### Task 2: Wire templates into catalog specs

**Files:**
- Modify D:\workspace\src\nexus-agents\internal\catalog\catalog.go.

- [ ] Add Unity agents to tdAgentTemplates() with model tier, skills, tools, MCP, and source paths.
- [ ] Add Unity rules to tdRuleTemplates() and route paths in tdRuleTemplatePath() when needed.
- [ ] Add Unity skills and wf-* Codex entries to tdSkillTemplates(), tdSkillTemplatePath(), and workflowSkillTemplateID().
- [ ] Add Unity workflow specs to tdWorkflowSpecs() with triggers $wf-unity-bugfix, $wf-unity-logic-mod, $wf-unity-ui-feature.
- [ ] Add tdWorkflowTemplatePath() mapping for Unity workflows.
- [ ] Extend graph generation so Unity workflows include developer/tester/reviewer/evaluator nodes, while existing Go graphs remain stable.

### Task 3: Extend deterministic evaluation policy

**Files:**
- Modify D:\workspace\src\nexus-agents\internal\catalog\evaluation.go.
- Modify tests in D:\workspace\src\nexus-agents\internal\catalog\evaluation_test.go.

- [ ] Treat Unity workflows as high-risk workflows for escalation policy.
- [ ] Add tests that Unity bugfix/UI/logic workflow task runs get highRiskWorkflow=true and appropriate escalation reasons.

### Task 4: Update API/catalog tests

**Files:**
- Modify D:\workspace\src\nexus-agents\internal\httpapi\server_test.go and/or internal\catalog\project_scan_test.go.

- [ ] Update template ID expectations to include Unity agents, rules, skills, and workflows.
- [ ] Add or update workflow graph endpoint assertions for one Unity workflow.
- [ ] Keep tests specific enough to catch missing graph/template files.

### Task 5: Verify

**Files:**
- All changed Go files.

- [ ] Run gofmt on changed Go files.
- [ ] Run go test ./....
- [ ] Inspect git diff for forbidden files and unrelated changes.
