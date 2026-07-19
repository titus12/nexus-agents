from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


REQUIRED_FILES = [
    "go.mod",
    "cmd/nexus-agents/main.go",
    "internal/httpapi/server.go",
    "internal/httpapi/server_test.go",
    "web/package.json",
    "web/index.html",
    "web/assets.go",
    "web/vite.config.ts",
    "web/tsconfig.json",
    "web/src/main.ts",
    "web/src/api.ts",
    "web/src/App.vue",
    "web/src/styles.css",
    "web/src/types.ts",
    "scripts/verify_all.ps1",
    "scripts/smoke_app.ps1",
]

REQUIRED_PLAN_FILES = [
    "docs/plans/00-prototype-roadmap.md",
    "docs/plans/01-design-shell-and-style.md",
    "docs/plans/02-project-management-prototype.md",
    "docs/plans/03-agent-rule-skill-prototype.md",
    "docs/plans/04-workflow-editor-prototype.md",
    "docs/plans/05-model-proxy-prototype.md",
    "docs/plans/06-prototype-verification.md",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def read(rel: str) -> str:
    path = ROOT / rel
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail(f"missing required file: {rel}")
    except UnicodeDecodeError as exc:
        fail(f"not utf-8: {rel} ({exc})")


def expect(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def main() -> None:
    for rel in REQUIRED_FILES:
        expect((ROOT / rel).is_file(), f"missing required file: {rel}")
    for rel in REQUIRED_PLAN_FILES:
        expect((ROOT / rel).is_file(), f"missing required plan file: {rel}")

    go_mod = read("go.mod")
    expect("module nexus-agents" in go_mod, "go.mod must use module nexus-agents")

    server_go = read("internal/httpapi/server.go")
    expect("func NewServer()" in server_go, "server.go must expose NewServer")
    expect("func NewServerWithStore" in server_go, "server.go must expose NewServerWithStore")
    expect('"/api/health"' in server_go, "server.go must register /api/health")
    for token in ['"/api/bootstrap"', '"/api/projects"', '"/api/templates/"', '"/api/workflows"', '"/api/model-routes"']:
        expect(token in server_go, f"server.go missing endpoint {token}")
    for token in ["handleProjectImport", "SyncProjectCopy", "DetachProjectCopy", "SyncPreview", "CreateTemplate", "CreateWorkflow", "DuplicateWorkflow"]:
        expect(token in server_go, f"server.go missing V1 mutation behavior {token}")
    for token in ["handleWebUI", "webui.Dist()", "http.FileServer", "index.html", "handleAPINotFound"]:
        expect(token in server_go, f"server.go missing embedded Vue behavior {token}")

    assets_go = read("web/assets.go")
    for token in ["//go:embed dist", "func Dist() fs.FS", "fs.Sub"]:
        expect(token in assets_go, f"web/assets.go missing embed token {token}")

    package_json = json.loads(read("web/package.json"))
    expect(package_json.get("scripts", {}).get("dev"), "web/package.json missing dev script")
    expect(package_json.get("scripts", {}).get("build"), "web/package.json missing build script")
    deps = package_json.get("dependencies", {})
    dev_deps = package_json.get("devDependencies", {})
    for dep in ["vue"]:
        expect(dep in deps, f"web/package.json missing dependency {dep}")
    for dep in ["@vitejs/plugin-vue", "typescript", "vite"]:
        expect(dep in dev_deps, f"web/package.json missing dev dependency {dep}")

    api_ts = read("web/src/api.ts")
    for token in ["fetchBootstrap", "fetchTemplates", "fetchProjectConfig", "fetchWorkflows", "fetchModelRoutes", "fetchWorkflowGraph"]:
        expect(token in api_ts, f"api.ts missing client function {token}")
    for token in ["importProject", "createTemplate", "updateTemplate", "deleteTemplate", "fetchSyncPreview", "syncProjectCopy", "detachProjectCopy", "fetchProjectWorkflowGraph", "updateProjectWorkflowGraph", "createProjectWorkflow", "deleteProjectWorkflow", "createWorkflow", "updateWorkflow", "duplicateWorkflow", "deleteWorkflow", "resolveModelRoute"]:
        expect(token in api_ts, f"api.ts missing mutation client function {token}")

    types_ts = read("web/src/types.ts")
    for token in ["TemplateLibrary", "ProjectConfigSet", "ProjectCopy", "ModelRoute", "WorkflowGraph"]:
        expect(token in types_ts, f"types.ts missing type {token}")
    for token in ["modelTier", "rulesCount", "skillsCount", "applicableAgents", "relatedRules", "relatedSkills", "codexProjection", "claudeSource"]:
        expect(token in types_ts, f"types.ts missing template metadata field {token}")

    app_vue = read("web/src/App.vue")
    for token in ["Template Library", "Projects", "Model Proxy", "Project Config Set", "Nexus Agents"]:
        expect(token in app_vue, f"App.vue missing design token {token}")
    for token in [
        "activePage",
        "selectedProjectId",
        "templateLibrary",
        "projectConfigSets",
        "modelRoutes",
        "workflowGraph",
        "drawerMode",
        "showImportModal",
        "Node Palette",
        "Project Workflows",
        "createTemplateForCurrentPage",
        "syncSelectedProjectCopy",
        "detachSelectedProjectCopy",
        "openSyncPreviewDrawer",
        "syncPreview",
        "duplicateWorkflow",
        "runProxyFailureTest",
        "saveRouteMock",
        "openProxyTest",
        "runWorkflow",
    ]:
        expect(token in app_vue, f"App.vue missing V1 page behavior token {token}")

    styles = read("web/src/styles.css")
    for token in ["--bg-base", ".sidebar", ".topbar", ".asset-card", ".drawer-backdrop", ".modal", ".detail-list", ".sync-preview-item"]:
        expect(token in styles, f"styles.css missing design style token {token}")

    verify_all = read("scripts/verify_all.ps1")
    for token in ["verify_design.py", "verify_app_scaffold.py", "go test ./...", "npm run build", "smoke_app.ps1"]:
        expect(token in verify_all, f"verify_all.ps1 missing command token {token}")

    smoke_app = read("scripts/smoke_app.ps1")
    for token in ["Start-Process", "Get-Random", "$base/api/health", "$base/api/bootstrap", "$base/projects/btd-game-server/workflows", "model-routes/resolve", "templates/rules", "sync-preview", "duplicate", "proj_agent_btd_go_worker", "Project workflow graph", "Stop-Process"]:
        expect(token in smoke_app, f"smoke_app.ps1 missing smoke token {token}")

    readme = read("README.md")
    expect("Go backend" in readme or "Go 后端" in readme, "README.md missing setup token Go backend/Go 后端")
    for token in ["Vue 3", "scripts\\verify_all.ps1", "NEXUS_ADDR"]:
        expect(token in readme, f"README.md missing setup token {token}")

    for rel in REQUIRED_PLAN_FILES:
        plan = read(rel)
        expect("## Status" in plan, f"{rel} missing status section")
        expect("Status: Done." in plan, f"{rel} must be marked done")
        expect("Completed on: 2026-06-20." in plan, f"{rel} missing completion date")
        expect("scripts\\verify_all.ps1" in plan, f"{rel} missing verification evidence")

    print("OK: app scaffold verified")


if __name__ == "__main__":
    main()
