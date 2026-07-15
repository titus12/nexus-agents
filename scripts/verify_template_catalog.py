from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"

AGENTS = [
    "sisyphus",
    "prometheus",
    "hephaestus",
    "quick",
    "oracle",
    "debugger",
    "librarian",
    "worker",
    "reviewer-logic",
    "reviewer-perf",
    "reviewer-security",
    "gatekeeper",
]

RULE_FILES = [
    "go-00-routing.md",
    "01-communication.md",
    "go-02-safety.md",
    "go-03-project-model.md",
    "go-04-task-decomposition.md",
    "knowledge-retrieval.md",
    "uiarchitect/uiarchitect-asset-safety.md",
    "uiarchitect/uiarchitect-editor-runtime-boundary.md",
    "uiarchitect/uiarchitect-generated-safety.md",
    "uiarchitect/uiarchitect-portability.md",
]

SKILL_FILES = [
    "go-dev-workflow.md",
    "skill-standard.md",
    "review-feedback.md",
    "go-coding-rules.md",
    "go-testing.md",
    "go-pmconf-pattern.md",
    "high-risk-api.md",
    "go-quest-system.md",
    "go-cross-config.md",
    "cross-client.md",
    "cross-gate.md",
    "cross-social.md",
]

GO_WORKFLOW_FILES = [
    "go-feature-development.md",
    "go-bugfix.md",
    "go-code-review.md",
]

GO_FEATURE_WORKFLOW_TOKENS = [
    "## 强约束",
    "先探索，后实施",
    "## 工作流",
    "生成待审核目标契约",
    "目标门",
    "质量门",
    "父工作流最多 3 个完整 Loop",
    "每个子任务最多 2 次",
    "## 任务拆分决策",
    "## 角色与阶段",
    "## 需求来源与事实冲突",
    "## Diagnosis-First 方案",
    "## 探索上下文包",
    "## KnowledgeBase Recommendation Gate",
    "## Nexus TaskRun Start Gate",
    "taskrun.mjs start",
    "sessionId",
    "success | partial_success | blocked | failed | cancelled",
]

GO_BUGFIX_WORKFLOW_TOKENS = [
    "## 需求来源与事实冲突",
    "## Diagnosis-First 方案",
    "## 修复范围与拆分决策",
    "go-04-task-decomposition.md",
    "## KnowledgeBase Recommendation Gate",
    "KB Recommendation:",
    "## Nexus TaskRun Start Gate",
    "taskrun.mjs start",
    "sessionId",
]

GO_REVIEW_WORKFLOW_TOKENS = [
    "## Nexus TaskRun Start Gate",
    "## 强约束",
    "## 审查上下文包",
    "## 审查范围与拆分决策",
    "Reviewer Logic",
    "Reviewer Perf",
    "Reviewer Security",
    "## KnowledgeBase Recommendation Gate",
    "taskrun.mjs submit",
]

CORE_WORKFLOW_FILES = [
    "design.md",
    "research.md",
    "commit-gate.md",
    "lark-integration.md",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def read(rel: str) -> str:
    path = ROOT / rel
    if not path.is_file():
        fail(f"missing template file: {rel}")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        fail(f"template file is not utf-8: {rel} ({exc})")


def expect(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def require_tokens(rel: str, tokens: list[str]) -> str:
    text = read(rel)
    for token in tokens:
        expect(token in text, f"{rel} missing token {token}")
    return text


def require_workflow_graph(markdown_filename: str) -> None:
    graph_rel = f"templates/.claude/workflows/{Path(markdown_filename).stem}.graph.json"
    graph_text = read(graph_rel)
    try:
        graph = json.loads(graph_text)
    except json.JSONDecodeError as exc:
        fail(f"{graph_rel} is not valid json: {exc}")
    expect(graph.get("id"), f"{graph_rel} missing id")
    expect(isinstance(graph.get("nodes"), list) and graph["nodes"], f"{graph_rel} missing nodes")
    expect(isinstance(graph.get("edges"), list), f"{graph_rel} missing edges")


def main() -> None:
    expect(TEMPLATES.is_dir(), "templates directory must exist")
    expect(not (TEMPLATES / "profiles").exists(), "templates/profiles must not exist")
    expect(not (TEMPLATES / "agents").exists(), "legacy templates/agents must not exist")
    expect(not (TEMPLATES / "rules").exists(), "legacy templates/rules must not exist")
    expect(not (TEMPLATES / "skills").exists(), "legacy templates/skills must not exist")
    expect(not (TEMPLATES / "workflows").exists(), "legacy templates/workflows must not exist")
    expect(not (TEMPLATES / "README.md").exists(), "templates/README.md must not exist")
    expect((TEMPLATES / ".claude" / "agents").is_dir(), "templates/.claude/agents must exist")
    expect((TEMPLATES / ".codex" / "agents").is_dir(), "templates/.codex/agents must exist")
    expect((TEMPLATES / ".agents" / "skills").is_dir(), "templates/.agents/skills must exist")
    expect((TEMPLATES / "KnowledgeBase" / "framework").is_dir(), "templates/KnowledgeBase/framework must exist")

    yaml_files = [
        path
        for path in TEMPLATES.rglob("*")
        if path.suffix.lower() in {".yaml", ".yml"}
        and not (
            path.name == "openai.yaml"
            and path.parent.name == "agents"
            and TEMPLATES / ".agents" / "skills" in path.parents
        )
    ]
    expect(not yaml_files, "templates must use copied md/toml files, not yaml manifests")

    for agent in AGENTS:
        require_tokens(
            f"templates/.claude/agents/go-{agent}.md",
            ["---", "name:", "description:"],
        )
        require_tokens(
            f"templates/.codex/agents/go-{agent}.toml",
            [f'name = "{agent}"', "model =", "developer_instructions"],
        )

    for filename in RULE_FILES:
        require_tokens(f"templates/.claude/rules/{filename}", ["# "])

    for filename in SKILL_FILES:
        skill_id = Path(filename).stem
        require_tokens(f"templates/.claude/skills/{skill_id}/SKILL.md", ["# "])

    for filename in GO_WORKFLOW_FILES:
        read(f"templates/.claude/workflows/{filename}")
        require_workflow_graph(filename)

    require_tokens(
        "templates/.claude/workflows/go-feature-development.md",
        GO_FEATURE_WORKFLOW_TOKENS,
    )
    require_tokens(
        "templates/.claude/workflows/go-bugfix.md",
        GO_BUGFIX_WORKFLOW_TOKENS,
    )
    require_tokens(
        "templates/.claude/workflows/go-code-review.md",
        GO_REVIEW_WORKFLOW_TOKENS,
    )

    for rel in [
        "templates/.claude/workflows/go-modify-existing.md",
        "templates/.claude/workflows/go-modify-existing.graph.json",
        "templates/.claude/commands/wf-go-mod.md",
        "templates/.agents/skills/wf-go-mod/SKILL.md",
        "templates/.claude/workflows/go-refactor.md",
        "templates/.claude/workflows/go-refactor.graph.json",
        "templates/.claude/commands/wf-go-refactor.md",
        "templates/.agents/skills/wf-go-refactor/SKILL.md",
    ]:
        expect(not (ROOT / rel).exists(), f"obsolete Go workflow template remains: {rel}")

    for filename in CORE_WORKFLOW_FILES:
        read(f"templates/.claude/workflows/{filename}")
        require_workflow_graph(filename)

    for skill_dir, required_files in {
        "kb-maintenance": ["SKILL.md", "agents/openai.yaml"],
        "kb-system-curator": [
            "SKILL.md",
            "agents/openai.yaml",
            "references/decomposition-rules.md",
            "references/entry-plan-template.md",
            "references/exploration-and-validation.md",
            "references/kb-gates.md",
            "references/okf-checklist.md",
        ],
        "nexus-evaluation-review": ["SKILL.md"],
        "nexus-taskrun-submit": [
            "SKILL.md",
            "task-run-template.json",
            "start-workflow-run.ps1",
            "submit-workflow-result.ps1",
            "taskrun.mjs",
        ],
    }.items():
        for filename in required_files:
            read(f"templates/.agents/skills/{skill_dir}/{filename}")

    print("OK: template catalog verified")


if __name__ == "__main__":
    main()
