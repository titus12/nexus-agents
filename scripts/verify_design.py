from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "design"

REQUIRED_FILES = [
    "demo.html",
    "shared.css",
    "README.md",
    "serve.ps1",
    "pages/projects.html",
    "pages/project-detail.html",
    "pages/agents.html",
    "pages/rules.html",
    "pages/skills.html",
    "pages/workflows.html",
    "pages/model-routes.html",
    "pages/runs.html",
    "overlays/modal-import-project.html",
    "overlays/drawer-sync-preview.html",
    "overlays/drawer-agent.html",
    "overlays/drawer-rule.html",
    "overlays/drawer-skill.html",
    "overlays/drawer-node-config.html",
    "overlays/drawer-workflow-run.html",
    "overlays/drawer-route.html",
    "overlays/drawer-proxy-test.html",
    "js/mock-data.js",
    "js/common.js",
    "js/pages.js",
    "js/workflow-canvas.js",
    "js/fragment-cache.js",
    "js/nav.js",
]

REQUIRED_PAGE_IDS = {
    "projects": "page-projects",
    "project-detail": "page-project-detail",
    "agents": "page-agents",
    "rules": "page-rules",
    "skills": "page-skills",
    "workflows": "page-workflows",
    "model-routes": "page-model-routes",
}

REQUIRED_OVERLAYS = {
    "modal-import-project": "modal-import-project",
    "drawer-sync-preview": "drawer-sync-preview",
    "drawer-agent": "drawer-agent",
    "drawer-rule": "drawer-rule",
    "drawer-skill": "drawer-skill",
    "drawer-node-config": "drawer-node-config",
    "drawer-workflow-run": "drawer-workflow-run",
    "drawer-route": "drawer-route",
    "drawer-proxy-test": "drawer-proxy-test",
}

REQUIRED_FUNCTIONS = [
    "navigate",
    "loadOverlay",
    "openImportProject",
    "openSyncPreview",
    "openAgentDrawer",
    "openRuleDrawer",
    "openSkillDrawer",
    "openNodeConfig",
    "simulateWorkflowRun",
    "openWorkflowRun",
    "createWorkflow",
    "selectWorkflow",
    "editWorkflow",
    "cancelWorkflowEdit",
    "deleteWorkflow",
    "duplicateWorkflow",
    "renderWorkflowList",
    "renderNodePalette",
    "openRouteDrawer",
    "openProxyTest",
]

REQUIRED_CARD_MOUNTS = {
    "agents": "agent-card-grid",
    "rules": "rule-card-grid",
    "skills": "skill-card-grid",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail(f"missing file: {path.relative_to(ROOT)}")
    except UnicodeDecodeError as exc:
        fail(f"not utf-8: {path.relative_to(ROOT)} ({exc})")


def expect(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def function_body(source: str, name: str) -> str:
    start = source.find(f"function {name}(")
    expect(start >= 0, f"missing function body {name}")
    brace = source.find("{", start)
    expect(brace >= 0, f"missing function body brace {name}")
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1:index]
    fail(f"unterminated function body {name}")
    return ""


def main() -> None:
    for rel in REQUIRED_FILES:
        expect((DESIGN / rel).is_file(), f"missing required file: design/{rel}")

    demo = read(DESIGN / "demo.html")
    expect('href="shared.css' in demo, "demo.html must reference shared.css")
    expect('data-nav="runs"' not in demo, "runs must not be a first-level sidebar item in V1")
    expect("openProxyTest()" not in demo, "global topbar must not show proxy test action")
    topbar_match = re.search(r"<header class=\"topbar\">(?P<body>.*?)</header>", demo, re.S)
    expect(topbar_match is not None, "demo.html missing topbar")
    expect("openImportProject()" not in topbar_match.group("body"), "global topbar must not show import project action")
    for script in [
        "js/mock-data.js",
        "js/common.js",
        "js/pages.js",
        "js/workflow-canvas.js",
        "js/fragment-cache.js",
        "js/nav.js",
    ]:
        expect(script in demo, f"demo.html must reference {script}")
    expect(
        demo.index("js/fragment-cache.js") < demo.index("js/nav.js"),
        "demo.html must load fragment-cache.js before nav.js",
    )
    expect('id="page-container"' in demo, "demo.html missing #page-container")
    expect('id="overlay-container"' in demo, "demo.html missing #overlay-container")

    nav = read(DESIGN / "js/nav.js")
    expect("NEXUS_FRAGMENTS" in nav, "nav.js must fall back to embedded fragments for file:// mode")
    expect(re.search(r"['\"]runs['\"]\s*:", nav) is None, "runs must not be registered as a standalone page in V1")
    for page_id, root_id in REQUIRED_PAGE_IDS.items():
        expect(f"'{page_id}'" in nav or f'"{page_id}"' in nav, f"nav.js missing page {page_id}")
        expect(root_id in nav, f"nav.js missing root id {root_id}")
    for overlay_id, root_id in REQUIRED_OVERLAYS.items():
        expect(
            f"'{overlay_id}'" in nav or f'"{overlay_id}"' in nav,
            f"nav.js missing overlay {overlay_id}",
        )
        expect(root_id in nav, f"nav.js missing overlay root {root_id}")
    for fn in REQUIRED_FUNCTIONS:
        expect(re.search(rf"\bfunction\s+{re.escape(fn)}\b|window\.{re.escape(fn)}\b", nav + read(DESIGN / "js/common.js") + read(DESIGN / "js/workflow-canvas.js")), f"missing function {fn}")

    for page_id, root_id in REQUIRED_PAGE_IDS.items():
        rel = "project-detail" if page_id == "project-detail" else page_id
        html = read(DESIGN / "pages" / f"{rel}.html")
        expect(f'id="{root_id}"' in html, f"{rel}.html missing {root_id}")
        expect('class="page' in html or "class='page" in html, f"{rel}.html must use .page")
        if page_id in REQUIRED_CARD_MOUNTS:
            expect(REQUIRED_CARD_MOUNTS[page_id] in html, f"{rel}.html must use card grid mount")
        expect("page-title" not in html, f"{rel}.html must not duplicate topbar title in content area")
        expect("page-subtitle" not in html, f"{rel}.html must not duplicate topbar subtitle in content area")
        expect("page-description" in html, f"{rel}.html must keep a lightweight page description")
        for forbidden in ["<!DOCTYPE", "<html", "<head", "<body", "<script"]:
            expect(forbidden.lower() not in html.lower(), f"{rel}.html must be an HTML fragment without {forbidden}")

    workflow_html = read(DESIGN / "pages" / "workflows.html")
    for token in ["workflow-list", "workflow-search", "workflow-palette", "createWorkflow"]:
        expect(token in workflow_html, f"workflows.html missing workflow management token {token}")
    expect("Node Palette" not in workflow_html, "workflows.html must not show Node Palette before editing")
    expect("openWorkflowRun" not in workflow_html, "workflows page header must not show last run action")
    expect("workflow-project-filter" in workflow_html, "workflows.html missing project filter mount")
    expect("openImportProject()" not in read(DESIGN / "pages" / "projects.html"), "projects page header must not show duplicate import action")
    expect("openProxyTest()" not in read(DESIGN / "pages" / "model-routes.html"), "model routes page header must not show duplicate proxy test action")
    expect("rule-project-filter" in read(DESIGN / "pages" / "rules.html"), "rules.html missing project filter mount")
    expect("skill-project-filter" in read(DESIGN / "pages" / "skills.html"), "skills.html missing project filter mount")

    for overlay_id, root_id in REQUIRED_OVERLAYS.items():
        html = read(DESIGN / "overlays" / f"{overlay_id}.html")
        expect(f'id="{root_id}"' in html, f"{overlay_id}.html missing {root_id}")
        expect(
            "drawer-overlay" in html or "modal-backdrop" in html,
            f"{overlay_id}.html missing backdrop/overlay",
        )

    workflow_js = read(DESIGN / "js/workflow-canvas.js")
    for token in ["workflow-node", "workflow-edge", "workflow-card", "renderNodePalette", "editWorkflow", "deleteWorkflow", "duplicateWorkflow", "agent", "parallel", "human_approval"]:
        expect(token in workflow_js, f"workflow-canvas.js missing {token}")
    workflow_list = function_body(workflow_js, "renderWorkflowList")
    for token in ["workflow.runCount", "duplicateWorkflow", "openWorkflowRun"]:
        expect(token not in workflow_list, f"workflow cards should not show V1 non-essential action/detail: {token}")
    for token in ["editWorkflow", "deleteWorkflow", "workflow.nodeCount", "workflow.edgeCount"]:
        expect(token in workflow_list, f"workflow cards missing V1 management field/action: {token}")
    for token in ["workflowProjectFilter", "renderWorkflowProjectFilter", "setWorkflowProjectFilter"]:
        expect(token in workflow_js, f"workflow project filtering missing {token}")
    expect("workflowNodeCategory" in workflow_js, "workflow nodes must classify AI workflow node categories")
    expect("node-detail" in workflow_js, "workflow node renderer must expose a clampable detail area")
    for token in ["node-action", "node-control", "node-condition", "node-data", "node-human", "node-event", "node-decorator"]:
        expect(token in workflow_js, f"workflow node renderer missing category class {token}")

    pages_js = read(DESIGN / "js/pages.js")
    common_js = read(DESIGN / "js/common.js")
    for token in ["asset-icon-employee", "asset-icon-handbook", "asset-icon-manual"]:
        expect(token in pages_js, f"pages.js missing semantic asset icon {token}")
    for token in ["renderAgentProjectFilter", "renderRuleProjectFilter", "renderSkillProjectFilter", "setAgentProjectFilter", "setRuleProjectFilter", "setSkillProjectFilter"]:
        expect(token in pages_js, f"rules/skills project filtering missing {token}")
    expect("project-filter-chip" not in pages_js, "project filters must use dropdown selects, not many project chips")
    expect("project-filter-select" in pages_js, "project filters must render dropdown selects")
    agent_cards = function_body(pages_js, "renderAgentsPage")
    for token in ["agent.route", "agent.tools", "agent.mcp", "agent.source", "agent.projection"]:
        expect(token not in agent_cards, f"agent cards must move detailed field out of card: {token}")
    for token in ["agentDescription(agent)", "agent.model", "agent.rules", "agent.skills", "statusChip(agent.status"]:
        expect(token in agent_cards, f"agent cards missing compact V1 field: {token}")
    agent_body = re.search(r"<div class=\"asset-card-body\">(?P<body>.*?)<div class=\"asset-card-footer\">", agent_cards, re.S)
    expect(agent_body is not None, "agent cards missing body/footer structure")
    expect("asset-chip-row" not in agent_body.group("body"), "agent model/rule/skill chips must live in the footer")
    for token in ["角色资产", "agent.role}</span>"]:
        expect(token not in agent_cards, f"agent cards must not repeat role metadata in footer: {token}")
    rule_cards = function_body(pages_js, "renderRulesPage")
    expect("rule.projection" not in rule_cards, "rule cards must move Codex projection into drawer")
    for token in ["rule.source", "rule.summary", "rule.content", "statusChip(rule.status"]:
        expect(token in rule_cards, f"rule cards missing compact V1 field: {token}")
    for token in ["员工手册", "${chip(rule.scope"]:
        expect(token not in rule_cards, f"rule cards must not repeat scope/category chips: {token}")
    expect("rule.scope" not in rule_cards, "rule cards must not repeat scope/category metadata")
    expect("asset-stats" not in rule_cards, "rule cards must not keep duplicate scope stats")
    skill_cards = function_body(pages_js, "renderSkillsPage")
    for token in ["skill.trigger", "skill.module"]:
        expect(token not in skill_cards, f"skill cards must move detailed field out of card: {token}")
    for token in ["skill.source", "skill.appliesTo", "skill.purpose", "statusChip(skill.status"]:
        expect(token in skill_cards, f"skill cards missing compact V1 field: {token}")
    skill_body = re.search(r"<div class=\"asset-card-body\">(?P<body>.*?)<div class=\"asset-card-footer\">", skill_cards, re.S)
    expect(skill_body is not None, "skill cards missing body/footer structure")
    expect("asset-chip-row" not in skill_body.group("body"), "skill appliesTo chips must live in the footer")
    for token in ["操作手册", "适用 Agent"]:
        expect(token not in skill_cards, f"skill cards must not repeat category/apply metadata: {token}")
    route_rows = function_body(pages_js, "renderModelRoutesPage")
    expect("route.endpoint" not in route_rows, "model route table must keep endpoint details in drawer")
    for token in ["viewAgentRules", "viewAgentSkills", "navigate(\"rules\")", "navigate(\"skills\")"]:
        expect(token in common_js, f"agent drawer binding navigation missing {token}")
    expect("function setDrawerFooter" in common_js, "common.js must provide stale drawer footer fallback")
    for fn, token in [("openRuleDrawer", "rule.content"), ("openSkillDrawer", "skill.content")]:
        body = function_body(common_js, fn)
        expect("<textarea" in body and token in body, f"{fn} must render editable content textarea")
        expect("drawer-editor-layout" in body, f"{fn} must use compact editor drawer layout")
        expect("asset-editor-textarea" in body, f"{fn} must use large asset editor textarea")
        expect("code-block" not in body, f"{fn} must not add a redundant code block below editor")
        expect("asset-edit-grid" in body, f"{fn} must render editable metadata fields")
        expect("asset-edit-input" in body, f"{fn} must use editable inputs for metadata")
        expect("save" in body.lower(), f"{fn} must expose a save action for edited drawer information")
        expect("setDrawerFooter" in body, f"{fn} must use stale-safe drawer footer update")

    mock_js = read(DESIGN / "js/mock-data.js")
    for token in ["project: \"all\"", "project: \"btd-game-server\"", "content:"]:
        expect(token in mock_js, f"mock data missing project/content token {token}")

    css = read(DESIGN / "shared.css")
    for token in ["asset-icon-employee", "asset-icon-handbook", "asset-icon-manual", "asset-icon-process"]:
        expect(token in css, f"shared.css missing icon style {token}")
    expect("minmax(420px" in css or "minmax(430px" in css or "minmax(440px" in css, "asset cards must use wide card grid proportions")
    for token in ["height: 212px;", ".asset-grid.compact { grid-template-columns: repeat(auto-fill, minmax(430px, 1fr)); }", "grid-template-columns: 46px minmax(0, 1fr) max-content;", ".asset-card-header > div:nth-child(2)", "-webkit-line-clamp: 3;", ".asset-chip-row { display: flex; flex-wrap: nowrap; gap: 6px; min-height: 26px; overflow: hidden; }", ".asset-card-footer .asset-chip-row"]:
        expect(token in css, f"asset cards must align Rules/Skills with Agents sizing: {token}")
    expect(".asset-card-footer .asset-actions:only-child { margin-left: auto; }" in css, "asset footer actions must align right when stats are removed")
    for token in ["page-description", "project-filter", "project-filter-select", "workflow-library .panel-header", "drawer-editor-layout", "asset-editor-textarea", "asset-meta-strip"]:
        expect(token in css, f"shared.css missing layout style {token}")
    expect("project-filter-chip" not in css, "shared.css must not keep project chip styles for V1 project selection")
    for token in ["width: 236px;", "height: 126px;", ".node-detail", "-webkit-line-clamp: 2;", "margin-top: auto;"]:
        expect(token in css, f"workflow node cards must use fixed dimensions and truncation: {token}")
    for token in [".workflow-node.node-action", ".workflow-node.node-control", ".workflow-node.node-condition", ".workflow-node.node-data", ".workflow-node.node-human", ".workflow-node.node-event", ".workflow-node.node-decorator", ".node-type-badge"]:
        expect(token in css, f"shared.css missing workflow node category style {token}")
    for token in [
        "font-size: 16px;",
        ".page-description {\n  max-width: 780px",
        ".asset-desc {\n  color: var(--text-secondary);\n  line-height: 1.58;\n  font-size: 15px;",
        "table { width: 100%; border-collapse: collapse; font-size: 15px; }",
        ".drawer-title { font-size: 19px;",
    ]:
        expect(token in css, f"shared.css must use larger V1 typography token: {token}")

    print("OK: design prototype structure verified")


if __name__ == "__main__":
    main()
