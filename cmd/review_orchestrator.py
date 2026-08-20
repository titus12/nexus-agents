"""Multica review orchestrator with optional Feishu human decision gates."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


JSONValue = Union[Dict[str, Any], List[Any], str]

CONFIG: Dict[str, Any] = {
    "token": os.environ.get("MULTICA_TOKEN", ""),
    "base_url": os.environ.get("MULTICA_BASE_URL", "https://api.multica.com").rstrip("/"),
    "workspace_id": os.environ.get("WORKSPACE_ID", "0b9766ed-3f6b-47cf-832a-afa0b28b80dd"),
    "agent_analyst": os.environ.get("AGENT_ANALYST_ID", ""),
    "agent_solver": os.environ.get("AGENT_SOLVER_ID", ""),
    "agent_critic": os.environ.get("AGENT_CRITIC_ID", ""),
    "project_id": os.environ.get("REVIEW_PROJECT_ID", ""),
    "max_rounds": int(os.environ.get("MAX_ROUNDS", "15")),
    "poll_interval": int(os.environ.get("POLL_INTERVAL_SEC", "8")),
    "phase_timeout": int(os.environ.get("PHASE_TIMEOUT_SEC", "300")),
    "human_gate_enabled": os.environ.get("HUMAN_GATE_ENABLED", "true").lower() not in ("0", "false", "no"),
    "human_gate_chat_id": os.environ.get("HUMAN_GATE_CHAT_ID", ""),
    "human_gate_user_open_id": os.environ.get("HUMAN_GATE_USER_OPEN_ID", ""),
    "human_gate_poll": int(os.environ.get("HUMAN_GATE_POLL_SEC", "8")),
    "human_gate_timeout": int(os.environ.get("HUMAN_GATE_TIMEOUT_SEC", "86400")),
    "feishu_app_id": os.environ.get("FEISHU_GATE_APP_ID", ""),
    "feishu_app_secret": os.environ.get("FEISHU_GATE_APP_SECRET", ""),
    "feishu_base_url": os.environ.get("FEISHU_BASE_URL", "https://open.feishu.cn").rstrip("/"),
}

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / ("orchestrator_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".log")

FORMATTER = logging.Formatter(
    "%(asctime)s [%(levelname)s] orchestrator - %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
LOGGER = logging.getLogger("orchestrator")
LOGGER.setLevel(logging.DEBUG)
if not LOGGER.handlers:
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(FORMATTER)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(FORMATTER)
    LOGGER.addHandler(file_handler)
    LOGGER.addHandler(stream_handler)


def _run_cli(*args: str) -> JSONValue:
    command = ["multica"] + list(args)
    LOGGER.debug("CLI: %s", " ".join(command))
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        LOGGER.error("CLI stderr: %s", result.stderr.strip())
        raise RuntimeError("multica CLI failed (rc=%d): %s" % (result.returncode, result.stderr[:500]))
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.stdout.strip()


def save_artifact(issue_id: str, name: str, data: Any) -> None:
    path = LOG_DIR / ("phase_%s_%s.json" % (issue_id, name))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    LOGGER.debug("artifact saved: %s", path)


def cli_issue_get(issue_id: str) -> Dict[str, Any]:
    result = _run_cli("issue", "get", issue_id, "--output", "json")
    return result if isinstance(result, dict) else {}


def cli_issue_create(title: str, body: str, project_id: str = "") -> str:
    description_file = LOG_DIR / ("new_issue_%d.md" % int(time.time()))
    description_file.write_text(body, encoding="utf-8")
    args = ["issue", "create", "--title", title, "--description-file", str(description_file), "--output", "json"]
    if project_id:
        args += ["--project", project_id]
    result = _run_cli(*args)
    if not isinstance(result, dict):
        raise RuntimeError("issue create returned non-object result")
    issue_id = result.get("id") or result.get("issue", {}).get("id")
    if not issue_id:
        raise RuntimeError("issue create returned no id: %s" % result)
    return str(issue_id)


def cli_comment_add(issue_id: str, content: str) -> Dict[str, Any]:
    content_file = LOG_DIR / ("comment_%s_%d.md" % (issue_id, int(time.time() * 1000)))
    content_file.write_text(content, encoding="utf-8")
    result = _run_cli(
        "issue", "comment", "add", issue_id,
        "--content-file", str(content_file),
        "--output", "json",
    )
    return result if isinstance(result, dict) else {}


def cli_comment_list(issue_id: str, recent: int = 30) -> List[Dict[str, Any]]:
    result = _run_cli(
        "issue", "comment", "list", issue_id,
        "--recent", str(recent),
        "--output", "json",
    )
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        comments = result.get("comments", [])
        return comments if isinstance(comments, list) else []
    return []


def cli_label_add(issue_id: str, label: str) -> None:
    try:
        _run_cli("issue", "update", issue_id, "--label", label, "--output", "json")
    except Exception as error:
        LOGGER.warning("label update failed: %s", error)


def cli_issue_status(issue_id: str, status: str) -> None:
    _run_cli("issue", "status", issue_id, status)


def cli_agent_assign(issue_id: str, agent_id: str) -> None:
    _run_cli("issue", "update", issue_id, "--assignee-id", agent_id, "--output", "json")


def extract_json(comment: Dict[str, Any]) -> Optional[JSONValue]:
    body = str(comment.get("body") or comment.get("content") or "")
    candidates = re.findall(r"```json\s*([\s\S]+?)\s*```", body, flags=re.IGNORECASE)
    candidates += re.findall(r"```[^\n]*\s*([\{\[][\s\S]+?)\s*```", body)
    candidates.append(body)
    for candidate in candidates:
        for start, end in (("{", "}"), ("[", "]")):
            index = candidate.find(start)
            if index < 0:
                continue
            fragment = candidate[index:]
            depth = 0
            for position, char in enumerate(fragment):
                if char == start:
                    depth += 1
                elif char == end:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(fragment[:position + 1])
                        except json.JSONDecodeError:
                            break
    return None


def latest_agent_comment(comments: List[Dict[str, Any]], agent_id: str, after: str) -> Optional[Dict[str, Any]]:
    matches = []
    for comment in comments:
        creator = comment.get("creator_id") or comment.get("author_id") or ""
        if creator != agent_id and comment.get("creator_type") != "agent":
            continue
        if str(comment.get("created_at", "")) <= after:
            continue
        matches.append(comment)
    matches.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    return matches[0] if matches else None


class FeishuClient:
    def __init__(self) -> None:
        self.base_url = CONFIG["feishu_base_url"]
        self.app_id = CONFIG["feishu_app_id"]
        self.app_secret = CONFIG["feishu_app_secret"]
        self._token: Optional[str] = None
        self._token_expires_at = 0.0

    def configured(self) -> bool:
        return bool(self.app_id and self.app_secret and CONFIG["human_gate_chat_id"])

    def _request(self, method: str, url: str, payload: Optional[Dict[str, Any]] = None,
                 token: Optional[str] = None) -> Dict[str, Any]:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError("Feishu HTTP %d: %s" % (error.code, detail))

    def tenant_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        result = self._request(
            "POST",
            self.base_url + "/open-apis/auth/v3/tenant_access_token/internal",
            {"app_id": self.app_id, "app_secret": self.app_secret},
        )
        if result.get("code") != 0:
            raise RuntimeError("Feishu token failed: %s" % result.get("msg"))
        self._token = str(result["tenant_access_token"])
        self._token_expires_at = time.time() + int(result.get("expire", 7200))
        return self._token

    def send_text(self, text: str) -> Dict[str, Any]:
        query = urllib.parse.urlencode({"receive_id_type": "chat_id"})
        result = self._request(
            "POST",
            self.base_url + "/open-apis/im/v1/messages?" + query,
            {
                "receive_id": CONFIG["human_gate_chat_id"],
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
            self.tenant_token(),
        )
        if result.get("code") != 0:
            raise RuntimeError("Feishu send failed: %s" % result.get("msg"))
        return result

    def list_messages(self, page_size: int = 50) -> List[Dict[str, Any]]:
        query = urllib.parse.urlencode({
            "container_id_type": "chat",
            "container_id": CONFIG["human_gate_chat_id"],
            "sort_type": "ByCreateTimeDesc",
            "page_size": page_size,
        })
        result = self._request(
            "GET",
            self.base_url + "/open-apis/im/v1/messages?" + query,
            token=self.tenant_token(),
        )
        if result.get("code") != 0:
            raise RuntimeError("Feishu list messages failed: %s" % result.get("msg"))
        items = result.get("data", {}).get("items", [])
        return items if isinstance(items, list) else []


def parse_choice(text: str, option_ids: List[str]) -> Optional[str]:
    normalized = text.strip().upper()
    normalized = re.sub(r"^(选择|CHOOSE|SELECT)\s*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"[\s。.!！：:]+$", "", normalized)
    for option_id in option_ids:
        if normalized == option_id.upper():
            return option_id
    return None


class ReviewOrchestrator:
    def __init__(self, issue_id: str) -> None:
        self.issue_id = issue_id
        self.round = 0
        self.state: Dict[str, Any] = {
            "requirements": None,
            "evidence": None,
            "proposal": None,
            "defects": None,
            "supplement": None,
            "verdict": None,
            "human_decisions": [],
        }
        self.feishu = FeishuClient()

    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def dispatch(self, phase: str, agent_id: str, task: str, context: str) -> str:
        sent_at = self.now_iso()
        message = task + "\n\n---\n\n" + context
        cli_comment_add(self.issue_id, message)
        try:
            cli_agent_assign(self.issue_id, agent_id)
        except Exception as error:
            LOGGER.warning("[%s] agent assignment failed: %s", phase, error)
        cli_label_add(self.issue_id, "phase:" + phase)
        save_artifact(self.issue_id, phase + "_input", {"task": message})
        return sent_at

    def wait_agent(self, phase: str, agent_id: str, sent_at: str) -> Optional[Dict[str, Any]]:
        deadline = time.time() + CONFIG["phase_timeout"]
        while time.time() < deadline:
            try:
                comment = latest_agent_comment(cli_comment_list(self.issue_id), agent_id, sent_at)
                if comment:
                    return comment
            except Exception as error:
                LOGGER.warning("[%s] poll error: %s", phase, error)
            time.sleep(CONFIG["poll_interval"])
        return None

    def human_gate(self, phase: str, gate: Dict[str, Any]) -> Optional[str]:
        if not CONFIG["human_gate_enabled"]:
            return None
        if not self.feishu.configured() or not CONFIG["human_gate_user_open_id"]:
            LOGGER.error("HUMAN_GATE is enabled but Feishu gate configuration is incomplete")
            return None
        question = str(gate.get("question", "Please choose an option"))
        raw_options = gate.get("options", [])
        options = [
            (str(item.get("id")), str(item.get("label", item.get("id"))))
            for item in raw_options
            if isinstance(item, dict) and item.get("id")
        ]
        if not options:
            LOGGER.error("HUMAN_GATE has no valid options")
            return None
        decision_id = "decision-" + uuid.uuid4().hex[:12]
        timeout = int(gate.get("timeout_sec", CONFIG["human_gate_timeout"]))
        path = LOG_DIR / ("decision_%s_%s.json" % (self.issue_id, decision_id))
        payload = {
            "decision_id": decision_id,
            "issue_id": self.issue_id,
            "phase": phase,
            "question": question,
            "options": [{"id": item_id, "label": label} for item_id, label in options],
            "status": "WAITING_HUMAN",
            "created_at": self.now_iso(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = [
            "[HUMAN_GATE] decision_id=%s" % decision_id,
            "Issue: %s | Phase: %s" % (self.issue_id, phase),
            question,
            "",
        ]
        lines.extend("%s. %s" % (item_id, label) for item_id, label in options)
        lines.append("")
        lines.append("Reply with: %s" % " or ".join(item_id for item_id, _ in options))
        self.feishu.send_text("\n".join(lines))
        LOGGER.info("[%s] waiting for human decision %s", phase, decision_id)

        option_ids = [item_id for item_id, _ in options]
        deadline = time.time() + timeout
        seen_after = time.time()
        while time.time() < deadline:
            for message in self.feishu.list_messages():
                sender = message.get("sender", {})
                if str(sender.get("id", "")) != str(CONFIG["human_gate_user_open_id"]):
                    continue
                create_ms = int(message.get("create_time", "0") or 0)
                if create_ms and create_ms / 1000.0 < seen_after:
                    continue
                body = message.get("body", {})
                content = body.get("content", "") if isinstance(body, dict) else ""
                try:
                    text = json.loads(content).get("text", content)
                except (TypeError, json.JSONDecodeError):
                    text = content
                choice = parse_choice(str(text), option_ids)
                if choice:
                    payload.update({
                        "status": "SELECTED",
                        "selected": choice,
                        "selected_at": self.now_iso(),
                        "reply_message_id": message.get("message_id"),
                    })
                    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    self.feishu.send_text("已收到选择 %s，评审继续。" % choice)
                    self.state["human_decisions"].append(payload)
                    return choice
            time.sleep(CONFIG["human_gate_poll"])
        default = gate.get("default")
        if default and str(default) in option_ids:
            payload.update({"status": "DEFAULTED", "selected": str(default), "selected_at": self.now_iso()})
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.feishu.send_text("人工决策超时，采用默认选项 %s，评审继续。" % default)
            self.state["human_decisions"].append(payload)
            return str(default)
        payload.update({"status": "TIMEOUT", "timed_out_at": self.now_iso()})
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.feishu.send_text("人工决策超时，评审暂停并转人工处理。")
        return None

    def maybe_gate(self, phase: str, parsed: Optional[JSONValue]) -> bool:
        if not isinstance(parsed, dict) or not isinstance(parsed.get("human_gate"), dict):
            return True
        return self.human_gate(phase, parsed["human_gate"]) is not None

    def run_phase(self, phase: str, agent_id: str, task: str, context: str) -> Optional[JSONValue]:
        sent_at = self.dispatch(phase, agent_id, task, context)
        comment = self.wait_agent(phase, agent_id, sent_at)
        if not comment:
            return None
        parsed = extract_json(comment)
        save_artifact(self.issue_id, phase + "_output", {"comment": comment, "parsed": parsed})
        if not self.maybe_gate(phase, parsed):
            return None
        return parsed or comment.get("body") or comment.get("content")

    def run(self, raw_requirement: str) -> None:
        requirements = self.run_phase(
            "intake", CONFIG["agent_analyst"],
            "[TASK:INTAKE] Structure and converge the requirement. Return JSON.",
            raw_requirement,
        )
        if requirements is None:
            self.escalate("intake timeout or human gate failed")
            return
        self.state["requirements"] = requirements

        evidence = self.run_phase(
            "evidence", CONFIG["agent_analyst"],
            "[TASK:EVIDENCE] Collect current-system evidence. Return JSON.",
            json.dumps(requirements, ensure_ascii=False, indent=2)
            if isinstance(requirements, (dict, list)) else str(requirements),
        )
        if evidence is None:
            self.escalate("evidence timeout or human gate failed")
            return
        self.state["evidence"] = evidence

        for self.round in range(1, CONFIG["max_rounds"] + 1):
            solve_context = json.dumps({
                "requirements": self.state["requirements"],
                "evidence": self.state["evidence"],
                "previous_defects": self.state["defects"],
                "previous_solution": self.state["proposal"],
                "human_decisions": self.state["human_decisions"],
            }, ensure_ascii=False, indent=2)
            proposal = self.run_phase(
                "solve", CONFIG["agent_solver"],
                "[TASK:SOLVE] Produce a solution proposal as JSON.",
                solve_context,
            )
            if proposal is None:
                self.escalate("solve timeout or human gate failed")
                return
            self.state["proposal"] = proposal

            defects = self.run_phase(
                "challenge", CONFIG["agent_critic"],
                "[TASK:CHALLENGE] Challenge the proposal and return defect cards as JSON.",
                json.dumps({
                    "requirements": self.state["requirements"],
                    "evidence": self.state["evidence"],
                    "proposal": self.state["proposal"],
                }, ensure_ascii=False, indent=2),
            )
            if defects is None:
                self.escalate("challenge timeout or human gate failed")
                return
            self.state["defects"] = defects

            if self.needs_supplement(defects):
                supplement = self.run_phase(
                    "supplement", CONFIG["agent_analyst"],
                    "[TASK:SUPPLEMENT] Add or upgrade evidence for the listed gaps. Return JSON.",
                    json.dumps({"evidence": self.state["evidence"], "defects": defects},
                               ensure_ascii=False, indent=2),
                )
                if supplement is None:
                    self.escalate("supplement timeout or human gate failed")
                    return
                self.state["supplement"] = supplement

            verdict = self.run_phase(
                "verdict", CONFIG["agent_critic"],
                "[TASK:VERDICT] Score the proposal and return PASS, CONDITIONAL, or REJECT as JSON.",
                json.dumps({
                    "proposal": self.state["proposal"],
                    "defects": self.state["defects"],
                    "supplement": self.state["supplement"],
                    "human_decisions": self.state["human_decisions"],
                }, ensure_ascii=False, indent=2),
            )
            if verdict is None:
                self.escalate("verdict timeout or human gate failed")
                return
            self.state["verdict"] = verdict
            result = verdict.get("verdict", "UNKNOWN") if isinstance(verdict, dict) else "UNKNOWN"
            if result == "PASS":
                self.done()
                return
            if result in ("CONDITIONAL", "REJECT") and self.round < CONFIG["max_rounds"]:
                continue
            self.escalate("max rounds reached or unknown verdict: %s" % result)
            return

    def needs_supplement(self, defects: JSONValue) -> bool:
        if not isinstance(defects, dict):
            return False
        return any(
            isinstance(item, dict)
            and item.get("type") == "evidence_insufficient"
            and item.get("severity") in ("P0", "P1")
            for item in defects.get("defects", [])
        )

    def done(self) -> None:
        cli_label_add(self.issue_id, "phase:done")
        try:
            cli_issue_status(self.issue_id, "in_review")
        except Exception as error:
            LOGGER.warning("failed to set done status: %s", error)
        cli_comment_add(self.issue_id, "Review completed with PASS.")

    def escalate(self, reason: str) -> None:
        LOGGER.warning("review escalated: %s", reason)
        cli_label_add(self.issue_id, "phase:escalate")
        try:
            cli_issue_status(self.issue_id, "blocked")
        except Exception as error:
            LOGGER.warning("failed to set blocked status: %s", error)
        cli_comment_add(self.issue_id, "Review escalated for human handling.\n\nReason: " + reason)


def validate_config() -> List[str]:
    required = ["token", "workspace_id", "agent_analyst", "agent_solver", "agent_critic"]
    missing = [name for name in required if not CONFIG[name]]
    if CONFIG["human_gate_enabled"]:
        required_gate = ["human_gate_chat_id", "human_gate_user_open_id", "feishu_app_id", "feishu_app_secret"]
        missing += [name for name in required_gate if not CONFIG[name]]
    return missing


def main() -> None:
    parser = argparse.ArgumentParser(description="Multica review orchestrator")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--issue")
    group.add_argument("--new", dest="requirement")
    parser.add_argument("--project")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    missing = validate_config()
    if args.dry_run:
        safe = dict(CONFIG)
        for key in ("token", "feishu_app_secret"):
            value = str(safe.get(key, ""))
            safe[key] = (value[:4] + "****") if value else ""
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        if missing:
            print("missing: " + ", ".join(missing))
        return
    if missing:
        LOGGER.error("missing required configuration: %s", ", ".join(missing))
        sys.exit(1)

    if args.issue:
        issue_id = args.issue
        issue = cli_issue_get(issue_id)
        requirement = issue.get("description") or issue.get("title") or ""
    else:
        requirement = args.requirement
        issue_id = cli_issue_create(
            "Review: " + requirement[:60],
            "Original requirement:\n" + requirement,
            args.project or CONFIG["project_id"],
        )
    ReviewOrchestrator(str(issue_id)).run(str(requirement))


if __name__ == "__main__":
    main()
