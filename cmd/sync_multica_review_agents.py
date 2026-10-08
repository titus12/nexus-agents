"""Check/apply canonical Zhongshu/Menxia instructions and skill assets on Multica.

Only the three listed agent instructions, six phase skill bodies and Zhongshu
skill assets are mutable. Model, runtime configuration, skill bindings and
Menxia attachments are snapshotted and checked unchanged. No runs or messages
are created.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "docs/multi/runtime"
TARGETS = {
    "analyst": ("71dce355-1ede-4a56-9969-9be4420d5fbc", "cc02ddbf-bd98-4874-80ef-bdb402c1e452", "baa3eb30-bcef-4a7c-818a-4146c4e8b793"),
    "solver": ("4d0e9fab-769f-4b0f-a8a3-57a170c477eb", "03a67bb2-a7b2-44ac-9de3-e8bc8bcb8830", "bef10843-5fcf-4064-bf8b-4f05c8533b76"),
    "critic": ("1a9139c5-be70-4a3b-a943-a3ff8b27b880", "ff7e1c25-750e-4cd3-9820-d286e8312cbf", "c1acb9bb-f692-4af4-b5bb-af6799490112"),
}
LANG_SKILL = "31d75b47-ea41-48df-a9e5-e63c9bf57049"
PROTECTED = (
    "model", "thinking_level", "service_tier", "runtime_id", "runtime_config",
    "runtime_mode", "permission_mode", "max_concurrent_tasks", "skills",
    "disabled_runtime_skills", "custom_args", "custom_env_key_count", "mcp_config",
)


def cli(*args):
    completed = subprocess.run(["multica", *args, "--output", "json"],
                               capture_output=True, encoding="utf-8", timeout=90)
    if completed.returncode:
        raise RuntimeError(f"Multica command failed ({' '.join(args[:3])}): {completed.stderr[:500]}")
    return json.loads(completed.stdout)


def normalize(text):
    return str(text or "").replace("\r\n", "\n")


def digest(text):
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def desired(role):
    files = {"REQUIRES.md": RUNTIME / "skill-assets/REQUIRES.md"}
    if role == "solver":
        files["references/option-comparison-guide.md"] = RUNTIME / "skill-assets/option-comparison-guide.md"
    return (RUNTIME / f"agent-instructions/{role}.md",
            RUNTIME / f"requirements-{role}-skill.md", files)


def differences(role, agent, skill):
    instruction, content, files = desired(role)
    errors = []
    if normalize(agent.get("instructions")) != instruction.read_text(encoding="utf-8"):
        errors.append("agent.instructions")
    if normalize(skill.get("content")) != content.read_text(encoding="utf-8"):
        errors.append("skill.content")
    actual_files = {file["path"]: file.get("content", "") for file in skill.get("files", [])}
    if set(actual_files) != set(files):
        errors.append("skill.files.inventory")
    for path, local in files.items():
        if normalize(actual_files.get(path)) != local.read_text(encoding="utf-8"):
            errors.append("skill.file:" + path)
    bound = {skill["id"] for skill in agent.get("skills", []) if skill.get("enabled")}
    expected = {TARGETS[role][1], TARGETS[role][2], LANG_SKILL}
    if bound != expected or agent.get("disabled_runtime_skills"):
        errors.append("agent.skills.binding")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Publish canonical instructions/assets and verify")
    parser.add_argument("--check", action="store_true", help="Read-only drift check (default)")
    parser.add_argument(
        "--publish-for-next-dispatch", action="store_true",
        help="Permit a working agent's static configuration update; never stop or redispatch its run. Use only when active skills are snapshotted in locked prompt bundles.",
    )
    parser.add_argument("--snapshot-dir", type=Path)
    args = parser.parse_args()
    if args.apply and args.check:
        parser.error("choose --apply or --check")
    if args.publish_for_next_dispatch and not args.apply:
        parser.error("--publish-for-next-dispatch requires --apply")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = args.snapshot_dir or ROOT / f"runs/maintenance/multica-agent-sync-{stamp}"
    directory.mkdir(parents=True, exist_ok=True)

    def save(name, value):
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    before = {}
    # Finish every preflight and snapshot before making any remote mutation.
    for role, (agent_id, skill_id, menxia_id) in TARGETS.items():
        agent = cli("agent", "get", agent_id)
        skill = cli("skill", "get", skill_id, "--with-content")
        menxia = cli("skill", "get", menxia_id, "--with-content")
        before[role] = {"agent": agent, "skill": skill, "menxia": menxia}
    lang = cli("skill", "get", LANG_SKILL, "--with-content")
    save("before.json", {"agents": before, "lang": lang})
    if args.apply:
        for role, entry in before.items():
            errors = differences(role, entry["agent"], entry["skill"])
            if "agent.skills.binding" in errors or "skill.files.inventory" in errors:
                raise RuntimeError(f"{role}: unexpected skill bindings/files; inspect snapshot before publishing")
            status = entry["agent"].get("status")
            if status != "idle" and not (
                args.publish_for_next_dispatch and status == "working"
            ):
                raise RuntimeError(f"{role}: agent is not idle; no configuration changed")
        for role, (agent_id, skill_id, menxia_id) in TARGETS.items():
            instruction, content, files = desired(role)
            if normalize(before[role]["skill"].get("content")) != content.read_text(encoding="utf-8"):
                cli("skill", "update", skill_id, "--content-file", str(content))
            actual_files = {entry["path"]: entry.get("content", "")
                            for entry in before[role]["skill"].get("files", [])}
            for path, local in files.items():
                if normalize(actual_files.get(path)) != local.read_text(encoding="utf-8"):
                    cli("skill", "files", "upsert", skill_id, "--path", path, "--content-file", str(local))
            if normalize(before[role]["agent"].get("instructions")) != instruction.read_text(encoding="utf-8"):
                cli("agent", "update", agent_id, "--instructions", instruction.read_text(encoding="utf-8"))
            local_menxia = ROOT / f"docs/multi/menxia-{role}-skill.md"
            if normalize(before[role]["menxia"].get("content")) != local_menxia.read_text(encoding="utf-8"):
                cli("skill", "update", menxia_id, "--content-file", str(local_menxia))

    after = {}
    report = {"mode": "apply" if args.apply else "check", "snapshot_dir": str(directory),
              "publish_for_next_dispatch": args.publish_for_next_dispatch,
              "running_tasks_modified": False, "agents": {}}
    all_errors = []
    for role, (agent_id, skill_id, menxia_id) in TARGETS.items():
        agent = cli("agent", "get", agent_id)
        skill = cli("skill", "get", skill_id, "--with-content")
        menxia = cli("skill", "get", menxia_id, "--with-content")
        after[role] = {"agent": agent, "skill": skill, "menxia": menxia}
        errors = differences(role, agent, skill)
        protected_changes = [key for key in PROTECTED if agent.get(key) != before[role]["agent"].get(key)]
        if protected_changes:
            errors.append("protected.settings:" + ",".join(protected_changes))
        if menxia.get("files") != before[role]["menxia"].get("files"):
            errors.append("protected.menxia.files.changed")
        local_menxia = ROOT / f"docs/multi/menxia-{role}-skill.md"
        menxia_matches = normalize(menxia.get("content")) == local_menxia.read_text(encoding="utf-8")
        if not menxia_matches:
            errors.append("menxia.skill.content")
        report["agents"][role] = {"id": agent_id, "name": agent.get("name"), "drift": errors,
            "status_before_publish": before[role]["agent"].get("status"),
            "instruction_sha256": digest(agent.get("instructions")), "skill_sha256": digest(skill.get("content")),
            "file_sha256": {f["path"]: digest(f.get("content")) for f in skill.get("files", [])},
            "protected_settings_unchanged": not protected_changes, "menxia_matches_local": menxia_matches,
            "menxia_skill_id": menxia_id, "menxia_skill_sha256": digest(menxia.get("content")),
            "menxia_files_unchanged": menxia.get("files") == before[role]["menxia"].get("files")}
        all_errors.extend(f"{role}:{error}" for error in errors)
    after_lang = cli("skill", "get", LANG_SKILL, "--with-content")
    if after_lang != lang:
        all_errors.append("protected.lang.changed")
    save("after.json", {"agents": after, "lang": after_lang})
    report["ok"] = not all_errors
    report["errors"] = all_errors
    save("verification.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
