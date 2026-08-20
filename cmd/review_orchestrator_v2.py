from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


JSONValue = Any

CONFIG: Dict[str, Any] = {
    "token": os.environ.get("MULTICA_TOKEN", ""),
    "base_url": os.environ.get("MULTICA_BASE_URL", "https://api.multica.com").rstrip("/"),
    "workspace_id": os.environ.get("WORKSPACE_ID", ""),
    "project_id": os.environ.get("REVIEW_PROJECT_ID", ""),
    "agent_analyst": os.environ.get("AGENT_ANALYST_ID", ""),
    "agent_solver": os.environ.get("AGENT_SOLVER_ID", ""),
    "agent_critic": os.environ.get("AGENT_CRITIC_ID", ""),
    "max_rounds": int(os.environ.get("MAX_ROUNDS", "15")),
    "zhongshu_max_rounds": int(os.environ.get("ZHONGSHU_MAX_ROUNDS", "5")),
    "group_max_rounds": int(os.environ.get("GROUP_MAX_ROUNDS", "3")),
    "item_max_rounds": int(os.environ.get("ITEM_MAX_ROUNDS", "3")),
    "poll_interval": int(os.environ.get("POLL_INTERVAL_SEC", "8")),
    "phase_timeout": int(os.environ.get("PHASE_TIMEOUT_SEC", "900")),
    "human_gate_enabled": os.environ.get("HUMAN_GATE_ENABLED", "true").lower() not in ("0", "false", "no"),
    "human_gate_user_open_id": os.environ.get("HUMAN_GATE_USER_OPEN_ID", ""),
    "human_gate_timeout": int(os.environ.get("HUMAN_GATE_TIMEOUT_SEC", "86400")),
    "feishu_base_url": os.environ.get("FEISHU_BASE_URL", "https://open.feishu.cn").rstrip("/"),
    "feishu_app_id": os.environ.get("FEISHU_GATE_APP_ID", ""),
    "feishu_app_secret": os.environ.get("FEISHU_GATE_APP_SECRET", ""),
    "human_gate_chat_id": os.environ.get("HUMAN_GATE_CHAT_ID", ""),
    "project_root": os.environ.get("PROJECT_ROOT", ""),
}

RUNS_ROOT = Path(os.environ.get("ORCHESTRATOR_RUNS_ROOT", "runs"))
RUNS_ROOT.mkdir(parents=True, exist_ok=True)
LOG_DIR = Path(os.environ.get("ORCHESTRATOR_LOG_DIR", "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / ("orchestrator_v2_%s.log" % datetime.now().strftime("%Y%m%d_%H%M%S"))

logger = logging.getLogger("orchestrator_v2")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] orchestrator_v2 — %(message)s",
                                  datefmt="%Y-%m-%dT%H:%M:%S")
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dump(value: Any, limit: Optional[int] = None) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return text[:limit] if limit and len(text) > limit else text


def _run_cli(*args: str) -> Any:
    command = ["multica"] + list(args)
    logger.debug("CLI: %s", " ".join(command))
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError("multica CLI failed rc=%s: %s" % (result.returncode, result.stderr[:500]))
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.stdout.strip()


def cli_comment_list(issue_id: str, since: str = "", recent: int = 50, summary: bool = False) -> List[Dict[str, Any]]:
    args = ["issue", "comment", "list", issue_id]
    if since:
        args += ["--since", since]
    else:
        args += ["--recent", str(recent)]
    if summary:
        args.append("--summary")
    args += ["--output", "json"]
    value = _run_cli(*args)
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        comments = value.get("comments") or value.get("items") or []
        return [x for x in comments if isinstance(x, dict)]
    return []


def cli_comment_add(issue_id: str, content: str) -> Any:
    path = LOG_DIR / ("comment_%s_%d.md" % (issue_id, int(time.time() * 1000)))
    path.write_text(content, encoding="utf-8")
    return _run_cli("issue", "comment", "add", issue_id, "--content-file", str(path), "--output", "json")


def cli_issue_create(title: str, body: str, project_id: str = "") -> str:
    path = LOG_DIR / ("new_issue_%d.md" % int(time.time()))
    path.write_text(body, encoding="utf-8")
    args = ["issue", "create", "--title", title, "--description-file", str(path), "--output", "json"]
    if project_id:
        args += ["--project", project_id]
    result = _run_cli(*args)
    issue_id = result.get("id") if isinstance(result, dict) else None
    if not issue_id and isinstance(result, dict):
        issue_id = result.get("issue", {}).get("id")
    if not issue_id:
        raise RuntimeError("issue create returned no id")
    return str(issue_id)


def cli_issue_get(issue_id: str) -> Dict[str, Any]:
    value = _run_cli("issue", "get", issue_id, "--output", "json")
    return value if isinstance(value, dict) else {}


def cli_issue_update(issue_id: str, **kwargs: str) -> Any:
    args = ["issue", "update", issue_id]
    for key, value in kwargs.items():
        args += ["--" + key.replace("_", "-"), str(value)]
    args += ["--output", "json"]
    return _run_cli(*args)


def cli_label_add(issue_id: str, label: str) -> None:
    try:
        _run_cli("issue", "label", "add", issue_id, "--label", label, "--output", "json")
    except Exception as error:
        logger.debug("label add skipped: %s", error)


def extract_json_from_comment(comment: Dict[str, Any]) -> Optional[Any]:
    if comment.get("content_truncated") or comment.get("truncated"):
        return None
    body = str(comment.get("content") or comment.get("body") or "")
    candidates = re.findall(r"```json\s*([\s\S]*?)\s*```", body, re.IGNORECASE)
    candidates += re.findall(r"```[^\n]*\s*([\{\[][\s\S]*?)\s*```", body)
    candidates.append(body)
    for candidate in candidates:
        candidate = candidate.strip()
        for start in ("{", "["):
            index = candidate.find(start)
            if index < 0:
                continue
            fragment = candidate[index:]
            try:
                return json.JSONDecoder().raw_decode(fragment)[0]
            except json.JSONDecodeError:
                continue
    return None


def extract_canonical_action(payload: Any, workflow_state: str = "") -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    action = payload.get("action")
    if isinstance(action, str) and action.strip():
        return action.strip().upper()
    status = payload.get("status")
    if isinstance(status, str):
        status = status.strip().upper()
        mapping = {
            "READY": "READY_FOR_SOLVER",
            "READY_FOR_SOLVER": "READY_FOR_SOLVER",
            "READY_FOR_CRITIC": "READY_FOR_CRITIC",
            "APPROVED": "APPROVE_FREEZE",
            "PASS": "APPROVE_ITEM" if "ITEM" in workflow_state else "APPROVE_GROUP",
            "REJECT": "REVISE_ITEM" if "ITEM" in workflow_state else "REQUEST_SOLVER_REVISION",
        }
        return mapping.get(status, status)
    return None


def payload_role_matches(payload: Any, role: str) -> bool:
    if not isinstance(payload, dict):
        return True
    declared = payload.get("role")
    if not declared:
        return True
    return str(declared).lower() in {role.lower(), "review-" + role.lower()}


def _comment_author_id(comment: Dict[str, Any]) -> str:
    return str(comment.get("author_id") or comment.get("creator_id") or comment.get("user_id") or "")


def _is_runtime_comment(comment: Dict[str, Any]) -> bool:
    text = str(comment.get("content") or comment.get("body") or "").lower()
    if comment.get("type") in ("system", "runtime"):
        return True
    return any(token in text for token in ("no new messages", "inappropriate content", "agent timeout"))


def find_agent_comments(comments: List[Dict[str, Any]], agent_id: str, after: str) -> List[Dict[str, Any]]:
    result = []
    for comment in comments:
        if _is_runtime_comment(comment) or comment.get("content_truncated"):
            continue
        if _comment_author_id(comment) != str(agent_id):
            continue
        if str(comment.get("created_at", "")) <= str(after):
            continue
        payload = extract_json_from_comment(comment)
        if payload is None or not isinstance(payload, dict):
            continue
        result.append(comment)
    result.sort(key=lambda x: str(x.get("created_at", "")))
    return result


def find_latest_agent_comment(comments: List[Dict[str, Any]], agent_id: str, after: str,
                              role: str = "") -> Optional[Dict[str, Any]]:
    candidates = find_agent_comments(comments, agent_id, after)
    for comment in reversed(candidates):
        payload = extract_json_from_comment(comment)
        if payload_role_matches(payload, role):
            return comment
    return None


def build_metadata_update(raw_request: str, project_type: Optional[str] = None,
                          task_type: Optional[str] = None, preserve_existing: bool = False) -> Dict[str, Any]:
    value = {"raw_request": raw_request}
    if project_type is not None:
        value["project_type"] = project_type
    elif not preserve_existing:
        value["project_type"] = "unknown"
    if task_type is not None:
        value["task_type"] = task_type
    elif not preserve_existing:
        value["task_type"] = "feature"
    return value


class TaskStore:
    def __init__(self, task_id: str, root: Optional[Path] = None) -> None:
        self.task_id = task_id
        self.root = (root or RUNS_ROOT) / task_id
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("artifacts", "evidence", "decisions", "transitions"):
            (self.root / name).mkdir(exist_ok=True)
        self.state_path = self.root / "state.json"
        self._state = self.load_state()

    def load_state(self) -> Dict[str, Any]:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"task_id": self.task_id, "workflow_state": "REQUEST_INTAKE", "agent_call_count": 0}

    def save_state(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        self._state.update(updates)
        self._state["task_id"] = self.task_id
        self._state["updated_at"] = _now_iso()
        self.state_path.write_text(_json_dump(self._state), encoding="utf-8")
        return dict(self._state)

    def _next_id(self, prefix: str, folder: str) -> str:
        count = len(list((self.root / folder).glob("*.json"))) + 1
        return "%s-%06d" % (prefix, count)

    def save_artifact(self, artifact_type: str, payload: Any, role: str = "orchestrator",
                      group_id: Optional[str] = None, item_id: Optional[str] = None) -> Dict[str, Any]:
        artifact_id = self._next_id("artifact", "artifacts")
        envelope = {
            "schema_version": "2.1", "artifact_type": artifact_type, "artifact_id": artifact_id,
            "task_id": self.task_id, "plan_id": self._state.get("plan_id"),
            "plan_version": self._state.get("plan_version", 1), "group_id": group_id,
            "item_id": item_id, "revision": 1, "created_at": _now_iso(),
            "created_by": role, "skill_lock_hash": self._state.get("skill_lock_hash"), "payload": payload,
        }
        (self.root / "artifacts" / (artifact_id + "-r1.json")).write_text(_json_dump(envelope), encoding="utf-8")
        self.save_state({"last_artifact_id": artifact_id})
        return envelope

    def save_decision(self, decision: Dict[str, Any]) -> None:
        path = self.root / "decisions" / (decision["decision_id"] + ".json")
        path.write_text(_json_dump(decision), encoding="utf-8")

    def save_frozen_plan(self, plan: Dict[str, Any]) -> str:
        raw = _json_dump(plan)
        digest = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
        plan = dict(plan)
        plan["content_hash"] = digest
        (self.root / "frozen_plan.json").write_text(_json_dump(plan), encoding="utf-8")
        return digest

    def load_frozen_plan(self, version: int = 1) -> Optional[Dict[str, Any]]:
        path = self.root / "frozen_plan.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def load_latest_artifact_payload(self, artifact_type: str) -> Optional[Any]:
        files = sorted((self.root / "artifacts").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("artifact_type") == artifact_type:
                    return data.get("payload")
            except Exception:
                continue
        return None

    def log_transition(self, from_state: str, action: str, to_state: str, meta: Optional[Dict[str, Any]] = None) -> None:
        path = self.root / "transitions" / ("%d.json" % len(list((self.root / "transitions").glob("*.json"))))
        path.write_text(_json_dump({
            "from": from_state, "action": action, "to": to_state, "meta": meta or {}, "created_at": _now_iso(),
        }), encoding="utf-8")


class FeishuClient:
    def configured(self) -> bool:
        return bool(CONFIG["feishu_app_id"] and CONFIG["feishu_app_secret"] and CONFIG["human_gate_chat_id"])

    def send_text(self, text: str, role: str = "") -> Optional[str]:
        logger.info("Feishu[%s]: %s", role or "orchestrator", text[:500].replace("\n", " | "))
        return None

    def list_messages(self, limit: int = 50) -> List[Dict[str, Any]]:
        return []


def _format_human_gate_question(value: Any, limit: int = 200) -> str:
    if isinstance(value, dict):
        text = value.get("question") or value.get("text") or value.get("title")
        if text is None:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = value
    text = str(text or "").replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def handle_human_gate(store: TaskStore, feishu: FeishuClient, issue_id: str,
                      gate_request: Dict[str, Any], phase: str,
                      context_payload: Optional[Dict[str, Any]] = None,
                      role: str = "") -> Optional[str]:
    questions = (context_payload or {}).get("questions_for_user", []) if isinstance(context_payload, dict) else []
    options = gate_request.get("options") or [{"id": "A", "label": "Continue"}, {"id": "B", "label": "Stop"}]
    options = [(str(x.get("id")), str(x.get("label", x.get("id")))) for x in options if isinstance(x, dict) and x.get("id")]
    if not options:
        return None
    decision_id = "decision-" + uuid.uuid4().hex[:12]
    decision = {
        "decision_id": decision_id, "task_id": store.task_id, "issue_id": issue_id, "phase": phase,
        "question": _format_human_gate_question(gate_request.get("question", "Please choose an option.")),
        "options": [{"id": x, "label": y} for x, y in options], "status": "open", "created_at": _now_iso(),
    }
    if questions:
        decision["questions_for_user"] = questions
    store.save_decision(decision)
    store.save_state({"workflow_state": "WAITING_HUMAN", "active_decision_id": decision_id})
    lines = ["【%s】⚡ 需要你做个决定（%s阶段）" % ({"analyst": "分析师", "solver": "规划师", "critic": "审查员"}.get(role, role), phase),
             "", decision["question"], ""]
    for question in questions[:3]:
        lines.append("  • %s" % _format_human_gate_question(question))
    lines += ["", *("%s. %s" % pair for pair in options), "",
              "回复本消息：输入选项字母，或回复：%s <选项>" % decision_id]
    feishu.send_text("\n".join(lines), role=role)
    deadline = time.time() + int(gate_request.get("timeout_sec", CONFIG["human_gate_timeout"]))
    option_ids = [item_id for item_id, _ in options]
    while time.time() < deadline:
        if issue_id:
            try:
                for comment in cli_comment_list(issue_id, since=decision["created_at"]):
                    body = str(comment.get("content") or comment.get("body") or "")
                    if decision_id not in body:
                        continue
                    choice = re.search(r"\b([A-Za-z])\b", body[body.index(decision_id) + len(decision_id):])
                    if choice and choice.group(1).upper() in option_ids:
                        selected = choice.group(1).upper()
                        decision.update({"status": "accepted", "selected": selected,
                                         "consumed_message_id": comment.get("id"), "selected_at": _now_iso()})
                        store.save_decision(decision)
                        store.save_state({"active_decision_id": None})
                        return selected
            except Exception as error:
                logger.warning("HUMAN_GATE issue poll error: %s", error)
        try:
            for message in feishu.list_messages(50):
                body = message.get("body", {})
                content = body.get("content", "") if isinstance(body, dict) else ""
                if decision_id not in str(content):
                    continue
                match = re.search(r"\b([A-Za-z])\b", str(content))
                if match and match.group(1).upper() in option_ids:
                    selected = match.group(1).upper()
                    decision.update({"status": "accepted", "selected": selected,
                                     "consumed_message_id": message.get("message_id"), "selected_at": _now_iso()})
                    store.save_decision(decision)
                    store.save_state({"active_decision_id": None})
                    return selected
        except Exception as error:
            logger.debug("HUMAN_GATE Feishu poll error: %s", error)
        time.sleep(max(1, int(os.environ.get("HUMAN_GATE_POLL_SEC", "8"))))
    store.save_decision(dict(decision, status="timeout", timed_out_at=_now_iso()))
    return None


class OrchestratorV2:
    _ROLE_NAMES = {"analyst": "分析师", "solver": "规划师", "critic": "审查员"}
    _PHASE_NAMES = {"zhongshu": "方案规划", "menxia": "方案审议"}

    def __init__(self, task_id: str, issue_id: str = "", raw_request: str = "",
                 project_type: str = "unknown", task_type: str = "review") -> None:
        self.task_id = task_id
        self.issue_id = issue_id
        self.raw_request = raw_request
        self.store = TaskStore(task_id)
        self._state = self.store._state
        self.feishu = FeishuClient()
        self.project_type = project_type
        self.task_type = task_type
        self._last_wait_failure = ""
        self.store.save_state({"issue_id": issue_id, "raw_request": raw_request})

    def _role(self, role: str) -> str:
        return self._ROLE_NAMES.get(role, role)

    def _phase(self, phase: str) -> str:
        return self._PHASE_NAMES.get(phase, phase)

    def _fmt_elapsed(self, seconds: int) -> str:
        return "%d 秒" % seconds if seconds < 60 else "%d 分 %d 秒" % (seconds // 60, seconds % 60)

    def _task_ref(self) -> str:
        return "（任务 %s）" % self.task_id[-6:]

    @staticmethod
    def _clip_text(value: Any, limit: int = 180) -> str:
        text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
        return text if len(text) <= limit else text[:limit - 1] + "…"

    def _notify(self, text: str, role: str = "") -> None:
        try:
            self.feishu.send_text(text, role=role)
        except Exception as error:
            logger.debug("Feishu notify failed: %s", error)

    @staticmethod
    def _solver_plan_payload(payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        plan = payload.get("plan")
        return plan if isinstance(plan, dict) else payload

    def _plan_summary(self, payload: Any, limit: int = 500) -> str:
        plan = self._solver_plan_payload(payload)
        groups = plan.get("groups") or plan.get("formal_groups") or []
        if not isinstance(groups, list) or not groups:
            return "当前尚未形成可展示的正式工作组。"
        titles = [self._clip_text(g.get("title") or g.get("objective") or g.get("group_id"), 90)
                  for g in groups[:5] if isinstance(g, dict)]
        return self._clip_text("共 %d 个正式工作组：%s" % (len(groups), "；".join(titles)), limit)

    def _notify_agent_result(self, role: str, phase: str, payload: Dict[str, Any],
                             action: str, elapsed: int = 0) -> None:
        lines = ["【%s】✅ 已完成 %s 阶段工作" % (self._role(role), self._phase(phase))]
        if elapsed:
            lines.append("耗时：%s" % self._fmt_elapsed(elapsed))
        lines.append("动作：%s" % action)
        if role == "analyst":
            context = payload.get("context_check", {}) if isinstance(payload, dict) else {}
            revalidation = payload.get("source_revalidation", {}) if isinstance(payload, dict) else {}
            findings = context.get("new_findings", []) if isinstance(context, dict) else []
            evidence = revalidation.get("new_evidence", []) if isinstance(revalidation, dict) else []
            lines += ["", "本轮分析内容：", "• 核对需求、文档、代码和前端入口",
                      "• 提取并验证证据，检查方案覆盖完整性",
                      "• 识别方向性未知项和风险",
                      "主要产出：%d 条新发现，%d 条新证据" % (len(findings), len(evidence))]
            for finding in findings[:3]:
                if isinstance(finding, dict):
                    lines.append("• 发现：%s" % self._clip_text(finding.get("statement") or finding.get("title")))
            lines.append("交付物：EvidencePacket / CandidateGroups")
        elif role == "solver":
            plan = self._solver_plan_payload(payload)
            groups = plan.get("groups") or plan.get("formal_groups") or []
            groups = groups if isinstance(groups, list) else []
            count = sum(len(g.get("items", [])) for g in groups if isinstance(g, dict) and isinstance(g.get("items"), list))
            direction = plan.get("selected_direction", {})
            direction = direction.get("option_id") if isinstance(direction, dict) else direction
            lines += ["", "本轮方案内容：", "• 先拆 item，再按目标和依赖组织 group",
                      "• 方案方向：%s" % self._clip_text(direction or plan.get("objective") or "未声明"),
                      "• 方案结构：%d 个 group，%d 个 item" % (len(groups), count)]
            for index, group in enumerate(groups[:4], 1):
                if not isinstance(group, dict):
                    continue
                lines += ["", "Group %d：%s" % (index, self._clip_text(group.get("title") or group.get("group_id"), 120)),
                          "目标：%s" % self._clip_text(group.get("objective") or "未提供", 180)]
                items = group.get("items") if isinstance(group.get("items"), list) else []
                for item in items[:4]:
                    if isinstance(item, dict):
                        lines.append("  • %s" % self._clip_text(item.get("title") or item.get("item_id"), 130))
            lines.append("交付物：ZhongshuPlan，完整字段已保存到 artifact")
        else:
            findings = payload.get("findings", []) if isinstance(payload, dict) else []
            questions = payload.get("questions_for_user", []) if isinstance(payload, dict) else []
            blockers = [f for f in findings if isinstance(f, dict) and f.get("severity") in ("P0", "P1")
                        and f.get("status") in ("open", "needs_human")]
            lines += ["", "本轮审查内容：", "• 检查需求覆盖、证据、group/item、依赖和可验证性",
                      "• 结论：%d 个发现，其中 %d 个 P0/P1 阻塞问题" % (len(findings), len(blockers))]
            for finding in findings[:4]:
                if isinstance(finding, dict):
                    lines.append("• 【%s】%s" % (finding.get("severity", "P?"),
                                                self._clip_text(finding.get("title") or finding.get("claim"))))
            if questions:
                lines.append("需要人工确认：")
                lines.extend("  • %s" % _format_human_gate_question(q) for q in questions[:3])
            lines.append("交付物：ZhongshuCriticFindings")
        lines.append(self._task_ref())
        self._notify("\n".join(lines), role=role)

    def _build_frozen_plan(self, solver_payload: Any, analyst_payload: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(solver_payload, dict):
            return None
        plan = self._solver_plan_payload(solver_payload)
        groups = plan.get("groups") or plan.get("formal_groups") or solver_payload.get("formal_groups") or []
        if not isinstance(groups, list) or not groups:
            logger.warning("Freeze check: solver payload missing formal groups")
            return None
        normalized = []
        for group in groups:
            if not isinstance(group, dict):
                return None
            items = group.get("items")
            if not isinstance(items, list) or not items:
                logger.warning("Freeze check: group %s has no formal items", group.get("group_id"))
                return None
            for item in items:
                if not isinstance(item, dict) or not item.get("item_id") or not (item.get("title") or item.get("objective")):
                    return None
            normalized.append(group)
        return {
            "plan_id": plan.get("plan_id") or self._state.get("plan_id") or "plan-000001",
            "version": int(plan.get("version", self._state.get("plan_version", 1))),
            "groups": normalized,
            "dependencies": plan.get("dependencies", []),
            "scope": plan.get("scope", {}),
            "non_goals": plan.get("non_goals", []),
            "assumptions": plan.get("assumptions", []),
            "unknowns": plan.get("unknowns", []),
            "evidence_snapshot": analyst_payload,
            "frozen_at": _now_iso(),
        }

    def _dispatch_and_wait(self, workflow_state: str, phase: str, role: str,
                           extra_context: Optional[Dict[str, Any]] = None) -> Tuple[Optional[Any], Optional[str]]:
        agent_id = CONFIG.get("agent_" + role, "")
        prompt = "你当前作为 review-%s，phase=%s。\n任务：%s\n\n上下文：\n%s" % (
            role, phase.upper(), self.raw_request, _json_dump(extra_context or {}, 12000))
        sent_at = _now_iso()
        self._state = self.store.save_state({
            "workflow_state": workflow_state, "last_sent_at": sent_at,
            "last_agent_role": role, "last_workflow_state": workflow_state,
            "agent_call_count": self._state.get("agent_call_count", 0) + 1,
        })
        if self.issue_id:
            cli_comment_add(self.issue_id, prompt)
            if agent_id:
                try:
                    cli_issue_update(self.issue_id, assignee_id=agent_id)
                except Exception as error:
                    logger.warning("agent assignment failed: %s", error)
        deadline = time.time() + CONFIG["phase_timeout"]
        while time.time() < deadline:
            comments = cli_comment_list(self.issue_id, since=sent_at) if self.issue_id else []
            comment = find_latest_agent_comment(comments, agent_id, sent_at, role)
            if comment:
                payload = extract_json_from_comment(comment)
                action = extract_canonical_action(payload, workflow_state)
                elapsed = max(0, int(time.time() - datetime.fromisoformat(sent_at).timestamp()))
                if isinstance(payload, dict):
                    self.store.save_artifact("AgentReply_%s_%s" % (phase.capitalize(), role),
                                             {"comment_id": comment.get("id"), "parsed": payload, "elapsed_sec": elapsed},
                                             role="review-" + role)
                    self._notify_agent_result(role, phase, payload, action or "RECEIVED", elapsed)
                return payload, action
            time.sleep(CONFIG["poll_interval"])
        self._last_wait_failure = "timeout_no_reply"
        return None, None

    def run_zhongshu(self) -> bool:
        analyst = self.store.load_latest_artifact_payload("EvidencePacket")
        solver = self.store.load_latest_artifact_payload("ZhongshuPlan")
        state = self._state.get("workflow_state", "ZHONGSHU_ANALYST")
        rounds = int(self._state.get("zhongshu_revision_round", 0))
        while rounds <= CONFIG["zhongshu_max_rounds"]:
            if state == "ZHONGSHU_ANALYST":
                analyst, action = self._dispatch_and_wait(state, "zhongshu", "analyst")
                if analyst is None:
                    return False
                self.store.save_artifact("EvidencePacket", analyst, role="review-analyst")
                state = "ZHONGSHU_SOLVER" if action == "READY_FOR_SOLVER" else state
            elif state == "ZHONGSHU_SOLVER":
                solver, action = self._dispatch_and_wait(state, "zhongshu", "solver", {"EvidencePacket": analyst})
                if solver is None:
                    return False
                self.store.save_artifact("ZhongshuPlan", solver, role="review-solver")
                state = "ZHONGSHU_CRITIC" if action == "READY_FOR_CRITIC" else "ZHONGSHU_ANALYST"
            elif state == "ZHONGSHU_CRITIC":
                critic, action = self._dispatch_and_wait(state, "zhongshu", "critic",
                                                         {"EvidencePacket": analyst, "ZhongshuPlan": solver})
                if critic is None:
                    return False
                self.store.save_artifact("ZhongshuCriticFindings", critic, role="review-critic")
                if action == "HUMAN_GATE":
                    handle_human_gate(self.store, self.feishu, self.issue_id, critic.get("human_gate", critic),
                                      "zhongshu", critic, "critic")
                    return False
                if action == "APPROVE_FREEZE":
                    state = "ZHONGSHU_FREEZE_CHECK"
                elif action in ("REQUEST_ANALYST_EVIDENCE",):
                    state = "ZHONGSHU_ANALYST"
                else:
                    state = "ZHONGSHU_SOLVER"
            elif state == "ZHONGSHU_FREEZE_CHECK":
                frozen = self._build_frozen_plan(solver, analyst)
                if not frozen:
                    rounds += 1
                    state = "ZHONGSHU_SOLVER"
                    self.store.save_state({"zhongshu_revision_round": rounds, "workflow_state": state})
                    continue
                digest = self.store.save_frozen_plan(frozen)
                self.store.save_state({"workflow_state": "ZHONGSHU_PLAN_FROZEN", "plan_version": frozen["version"],
                                       "plan_id": frozen["plan_id"], "skill_lock_hash": digest})
                return True
            self.store.save_state({"workflow_state": state})
            if state == "ZHONGSHU_ANALYST" and rounds > 0:
                rounds += 1
        return False

    def run(self) -> bool:
        if not self.raw_request and self.issue_id:
            issue = cli_issue_get(self.issue_id)
            self.raw_request = str(issue.get("description") or issue.get("title") or "")
        self.store.save_state({"raw_request": self.raw_request, "project_type": self.project_type, "task_type": self.task_type})
        if self._state.get("workflow_state") in ("REQUEST_INTAKE", "PROJECT_ROUTING", "WAITING_HUMAN"):
            self.store.save_state({"workflow_state": "ZHONGSHU_ANALYST"})
        if not self.run_zhongshu():
            return False
        return self.run_menxia()

    def run_menxia(self) -> bool:
        frozen = self.store.load_frozen_plan(self._state.get("plan_version", 1))
        if not isinstance(frozen, dict):
            return False
        groups = frozen.get("groups") if isinstance(frozen.get("groups"), list) else []
        reviewed_groups = copy.deepcopy(groups)
        for group in reviewed_groups:
            if not isinstance(group, dict):
                return False
            group_id = group.get("group_id")
            items = group.get("items") if isinstance(group.get("items"), list) else []
            if not items:
                return False
            self.store.save_state({"workflow_state": "MENXIA_GROUP_START", "active_group_id": group_id,
                                   "active_item_id": None})
            for item in items:
                if not isinstance(item, dict):
                    return False
                item_id = item.get("item_id")
                self.store.save_state({"workflow_state": "MENXIA_ITEM_ANALYST",
                                       "active_group_id": group_id, "active_item_id": item_id})
                evidence, action = self._dispatch_and_wait(
                    "MENXIA_ITEM_ANALYST", "menxia", "analyst",
                    {"FrozenPlan": frozen, "Group": group, "Item": item},
                )
                if evidence is None or action not in ("EVIDENCE_SUFFICIENT", "READY_FOR_SOLVER"):
                    return False
                self.store.save_artifact("ItemEvidencePacket", evidence, role="review-analyst",
                                         group_id=group_id, item_id=item_id)
                feasibility, action = self._dispatch_and_wait(
                    "MENXIA_ITEM_SOLVER", "menxia", "solver",
                    {"FrozenPlan": frozen, "Group": group, "Item": item, "EvidencePacket": evidence},
                )
                if feasibility is None or action not in ("FEASIBLE", "READY_FOR_CRITIC"):
                    return False
                self.store.save_artifact("ItemFeasibility", feasibility, role="review-solver",
                                         group_id=group_id, item_id=item_id)
                review, action = self._dispatch_and_wait(
                    "MENXIA_ITEM_CRITIC", "menxia", "critic",
                    {"FrozenPlan": frozen, "Group": group, "Item": item,
                     "EvidencePacket": evidence, "Feasibility": feasibility},
                )
                if review is None or action not in ("APPROVE_ITEM", "APPROVE_FREEZE"):
                    return False
                self.store.save_artifact("ItemReview", review, role="review-critic",
                                         group_id=group_id, item_id=item_id)
            self.store.save_state({"workflow_state": "MENXIA_GROUP_GATE",
                                   "active_group_id": group_id, "active_item_id": None})
            gate, action = self._dispatch_and_wait(
                "MENXIA_GROUP_GATE", "menxia", "critic", {"FrozenPlan": frozen, "Group": group},
            )
            if gate is None or action not in ("APPROVE_GROUP", "APPROVE_FREEZE"):
                return False
            self.store.save_artifact("GroupReview", gate, role="review-critic", group_id=group_id)
        self.store.save_artifact("ReviewedPlan", {"frozen_plan": frozen, "groups": reviewed_groups},
                                 role="orchestrator")
        self.store.save_state({"workflow_state": "DONE", "active_group_id": None, "active_item_id": None})
        return True


def validate_action(action: str, workflow_state: str) -> bool:
    allowed = {
        "ZHONGSHU_ANALYST": {"READY_FOR_SOLVER", "NEEDS_MORE_EVIDENCE", "HUMAN_GATE", "BLOCKED"},
        "ZHONGSHU_SOLVER": {"READY_FOR_CRITIC", "REQUEST_ANALYST_EVIDENCE", "HUMAN_GATE", "BLOCKED"},
        "ZHONGSHU_CRITIC": {"APPROVE_FREEZE", "REQUEST_ANALYST_EVIDENCE", "REQUEST_SOLVER_REVISION",
                            "REQUEST_REGROUP", "HUMAN_GATE", "BLOCKED"},
    }
    return action in allowed.get(workflow_state, set())


def validate_config() -> List[str]:
    required = ["agent_analyst", "agent_solver", "agent_critic"]
    return [name for name in required if not CONFIG.get(name)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume")
    parser.add_argument("--issue")
    parser.add_argument("--new")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--project")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        print(json.dumps({
            k: ("****" if any(marker in k.lower() for marker in ("token", "secret", "api_key", "password")) and v else v)
            for k, v in CONFIG.items()
        },
                         ensure_ascii=False, indent=2))
        return
    if args.resume:
        task_id = args.resume
        state = TaskStore(task_id)._state
        issue_id = state.get("issue_id", "")
        raw_request = state.get("raw_request", "")
    else:
        raw_request = args.new or ""
        issue_id = args.issue or ""
        task_id = "task-" + datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6]
        if not issue_id:
            issue_id = cli_issue_create("Review: " + raw_request[:60], raw_request, args.project or CONFIG["project_id"])
    if not raw_request and issue_id:
        raw_request = cli_issue_get(issue_id).get("description", "")
    OrchestratorV2(task_id, issue_id, raw_request).run()


if __name__ == "__main__":
    main()
