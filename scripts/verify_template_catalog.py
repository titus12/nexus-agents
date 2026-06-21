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
    "go-modify-existing.md",
    "go-bugfix.md",
    "go-code-review.md",
    "go-refactor.md",
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
    graph_rel = f"templates/workflows/{Path(markdown_filename).stem}.graph.json"
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
    expect(not (TEMPLATES / "agents" / "profiles").exists(), "agent profiles must not exist")
    expect(not (TEMPLATES / "workflows" / "profiles").exists(), "workflow profiles must not exist")
    expect((TEMPLATES / "agents" / "claude").is_dir(), "templates/agents/claude must exist")
    expect((TEMPLATES / "agents" / "codex").is_dir(), "templates/agents/codex must exist")
    direct_agent_files = [path for path in (TEMPLATES / "agents").iterdir() if path.is_file()]
    expect(not direct_agent_files, "agent templates must be separated into claude/ and codex/")

    yaml_files = [path for path in TEMPLATES.rglob("*") if path.suffix.lower() in {".yaml", ".yml"}]
    expect(not yaml_files, "templates must use copied md/toml files, not yaml manifests")

    require_tokens(
        "templates/README.md",
        ["Template Library", "copied from btd-game-server", "go-", "No YAML manifests"],
    )

    for agent in AGENTS:
        require_tokens(
            f"templates/agents/claude/go-{agent}.md",
            ["---", "name:", "description:"],
        )
        require_tokens(
            f"templates/agents/codex/go-{agent}.toml",
            [f'name = "{agent}"', "model =", "developer_instructions"],
        )

    for filename in RULE_FILES:
        require_tokens(f"templates/rules/{filename}", ["# "])

    for filename in SKILL_FILES:
        require_tokens(f"templates/skills/{filename}", ["# "])

    for filename in GO_WORKFLOW_FILES:
        require_tokens(
            f"templates/workflows/{filename}",
            ["Source: `templates/rules/go-00-routing.md`", "Stack: `go`"],
        )
        require_workflow_graph(filename)

    for filename in CORE_WORKFLOW_FILES:
        require_tokens(
            f"templates/workflows/{filename}",
            ["Source: `templates/rules/go-00-routing.md`"],
        )
        require_workflow_graph(filename)

    require_tokens(
        "templates/go-btd-game-server.md",
        ["# go-btd-game-server", "go build -tags actor_id_uint64 ./cmd/server/"],
    )

    print("OK: template catalog verified")


if __name__ == "__main__":
    main()
