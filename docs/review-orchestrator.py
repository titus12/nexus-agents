"""
Multi-Agent Review Orchestrator
================================
状态机编排器：驱动 3 个 Agent (Analyst / Solver / Critic) 按评审流程协作。

依赖：
- multica SDK（用于 Agent 调度、Issue 管理、飞书消息推送）
- 适配你的 multica 实例 API，下方 MulticaClient 为接口抽象层

运行方式：
- 作为 Multica Autopilot webhook handler 部署
- 或作为独立服务监听 Issue 事件
"""

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ============================================================
# 1. 常量与配置
# ============================================================

class Phase(str, Enum):
    INTAKE = "intake"
    EVIDENCE = "evidence"
    SOLVE = "solve"
    CHALLENGE = "challenge"
    SUPPLEMENT = "supplement"
    VERDICT = "verdict"
    DONE = "done"
    ESCALATE = "escalate"
    CONDITIONAL = "conditional"


class Verdict(str, Enum):
    PASS = "PASS"
    CONDITIONAL = "CONDITIONAL"
    REJECT = "REJECT"


AGENTS = {
    Phase.INTAKE: "review-analyst",
    Phase.EVIDENCE: "review-analyst",
    Phase.SOLVE: "review-solver",
    Phase.CHALLENGE: "review-critic",
    Phase.SUPPLEMENT: "review-analyst",
    Phase.VERDICT: "review-critic",
}

TASK_PREFIX = {
    Phase.INTAKE: "[TASK:INTAKE]",
    Phase.EVIDENCE: "[TASK:EVIDENCE]",
    Phase.SOLVE: "[TASK:SOLVE]",
    Phase.CHALLENGE: "[TASK:CHALLENGE]",
    Phase.SUPPLEMENT: "[TASK:SUPPLEMENT]",
    Phase.VERDICT: "[TASK:VERDICT]",
}

MAX_ROUNDS = 3

PHASE_SEQUENCE = [
    Phase.INTAKE,
    Phase.EVIDENCE,
    Phase.SOLVE,
    Phase.CHALLENGE,
    Phase.SUPPLEMENT,
    Phase.VERDICT,
]


# ============================================================
# 2. 数据结构
# ============================================================

@dataclass
class ReviewState:
    """单次评审的完整状态，存储在 Issue metadata 中"""
    issue_id: str
    phase: Phase = Phase.INTAKE
    round: int = 1
    requirement: Optional[str] = None
    evidence: Optional[str] = None
    solution: Optional[str] = None
    defects: Optional[str] = None
    supplement: Optional[str] = None
    verdict_result: Optional[dict] = None
    history: list = field(default_factory=list)

    def record(self, phase: Phase, agent: str, output: str):
        self.history.append({
            "phase": phase.value,
            "agent": agent,
            "round": self.round,
            "output": output,
        })


# ============================================================
# 3. 上下文隔离 — 每个 Agent 只看到它该看的
# ============================================================

def build_context(state: ReviewState, phase: Phase) -> str:
    """
    核心隔离逻辑：根据当前阶段构建 Agent 的输入上下文。

    隔离规则：
    - Analyst: 看需求、系统信息、Critic 质疑（补证时）
    - Solver:  看需求、全量证据、上轮缺陷（迭代时）
    - Critic:  看需求、方案、方案引用的证据（非全量）
    """
    prefix = TASK_PREFIX[phase]

    if phase == Phase.INTAKE:
        return f"{prefix}\n\n原始需求:\n{state.requirement}"

    elif phase == Phase.EVIDENCE:
        return f"{prefix}\n\n需求单:\n{state.requirement}"

    elif phase == Phase.SOLVE:
        ctx = f"{prefix}\n\n需求单:\n{state.requirement}\n\n证据:\n{state.evidence}"
        if state.round > 1 and state.defects:
            ctx += f"\n\n上一轮缺陷（本轮必须修复）:\n{state.defects}"
            ctx += f"\n\n上一轮方案（在此基础上修订，不要重写）:\n{state.solution}"
        return ctx

    elif phase == Phase.CHALLENGE:
        # 关键隔离：只传方案中引用的证据，不传完整证据池
        cited_evidence = _extract_cited_evidence(state.solution, state.evidence)
        return (
            f"{prefix}\n\n"
            f"需求单:\n{state.requirement}\n\n"
            f"方案:\n{state.solution}\n\n"
            f"方案引用的证据（仅 Solver 引用的部分）:\n{cited_evidence}"
        )

    elif phase == Phase.SUPPLEMENT:
        questions = _extract_supplement_questions(state.defects)
        return (
            f"{prefix}\n\n"
            f"以下是 Critic 提出的证据不足质疑，请逐条补充或升级证据:\n{questions}\n\n"
            f"当前证据池（供你查证参考）:\n{state.evidence}"
        )

    elif phase == Phase.VERDICT:
        ctx = (
            f"{prefix}\n\n"
            f"需求单:\n{state.requirement}\n\n"
            f"方案:\n{state.solution}\n\n"
            f"缺陷卡:\n{state.defects}"
        )
        if state.supplement:
            ctx += f"\n\n补充证据:\n{state.supplement}"
        return ctx

    raise ValueError(f"Unknown phase: {phase}")


def _extract_cited_evidence(solution_json: str, full_evidence_json: str) -> str:
    """从方案中提取引用的 evidence ID，然后从完整证据池中过滤出对应条目"""
    cited_ids = set(re.findall(r'ev-\d+', solution_json or ""))
    if not cited_ids:
        return "(方案未引用任何证据)"

    try:
        evidence_data = json.loads(full_evidence_json)
        items = evidence_data.get("evidence", [])
        cited_items = [e for e in items if e.get("id") in cited_ids]
        return json.dumps(cited_items, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, TypeError):
        # 降级：用正则按 ID 截取
        results = []
        for eid in cited_ids:
            pattern = rf'.*{eid}.*'
            matches = re.findall(pattern, full_evidence_json or "")
            results.extend(matches)
        return "\n".join(results) if results else "(解析失败，返回全文)"


def _extract_supplement_questions(defects_json: str) -> str:
    """从缺陷卡中提取需要补证的项（type = evidence_insufficient 或 grade_violation）"""
    try:
        data = json.loads(defects_json)
        items = data.get("defects", [])
        need_evidence = [
            d for d in items
            if d.get("type") in ("evidence_insufficient", "grade_violation")
        ]
        if not need_evidence:
            return "(无需补证)"
        return json.dumps(need_evidence, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, TypeError):
        return defects_json


# ============================================================
# 4. 状态机 — 流程推进核心
# ============================================================

class ReviewOrchestrator:
    """
    评审编排器。每个 Issue 对应一个 ReviewState 实例。

    调用方式：
    1. start(issue_id, user_input) — 用户发起评审
    2. on_agent_complete(issue_id, output) — Agent 完成后回调
    """

    def __init__(self, client):
        """
        client: MulticaClient 实例，封装了 Multica API 调用。
        需实现的方法见下方 MulticaClient 接口定义。
        """
        self.client = client
        self.states: dict[str, ReviewState] = {}

    # ------ 入口 ------

    async def start(self, issue_id: str, user_input: str):
        """用户发起评审，创建状态并启动第一步"""
        state = ReviewState(issue_id=issue_id, requirement=user_input)
        self.states[issue_id] = state

        await self._set_phase(state, Phase.INTAKE)
        await self._dispatch(state)

    # ------ Agent 完成回调 ------

    async def on_agent_complete(self, issue_id: str, agent_output: str):
        """
        每个 Agent 完成任务后由 Multica webhook 调用此方法。
        根据当前 phase 存储产出，然后推进状态机。
        """
        state = self.states.get(issue_id)
        if not state:
            return

        phase = state.phase
        agent = AGENTS[phase]
        state.record(phase, agent, agent_output)

        # 存储产出
        self._store_output(state, phase, agent_output)

        # 推进到下一步
        await self._advance(state)

    # ------ 状态推进逻辑 ------

    async def _advance(self, state: ReviewState):
        phase = state.phase

        if phase == Phase.INTAKE:
            # 需求收敛完成 → 采证
            await self._set_phase(state, Phase.EVIDENCE)
            await self._dispatch(state)

        elif phase == Phase.EVIDENCE:
            # 采证完成 → 出方案
            await self._set_phase(state, Phase.SOLVE)
            await self._dispatch(state)

        elif phase == Phase.SOLVE:
            # 方案完成 → 对抗审查
            await self._set_phase(state, Phase.CHALLENGE)
            await self._dispatch(state)

        elif phase == Phase.CHALLENGE:
            # 对抗完成 → 判断是否需要补证
            if self._needs_supplement(state.defects):
                await self._set_phase(state, Phase.SUPPLEMENT)
                await self._dispatch(state)
            else:
                # 无需补证，直接裁决
                await self._set_phase(state, Phase.VERDICT)
                await self._dispatch(state)

        elif phase == Phase.SUPPLEMENT:
            # 补证完成 → 裁决
            await self._set_phase(state, Phase.VERDICT)
            await self._dispatch(state)

        elif phase == Phase.VERDICT:
            # 裁决完成 → 分支处理
            await self._handle_verdict(state)

    async def _handle_verdict(self, state: ReviewState):
        """处理裁决结果：通过 / 打回迭代 / 升级人工"""
        verdict = self._parse_verdict(state.verdict_result)

        if verdict == Verdict.PASS:
            await self._set_phase(state, Phase.DONE)
            await self.client.post_to_feishu(
                state.issue_id,
                self._format_pass_message(state)
            )

        elif verdict == Verdict.CONDITIONAL:
            await self._set_phase(state, Phase.CONDITIONAL)
            await self.client.post_to_feishu(
                state.issue_id,
                self._format_conditional_message(state)
            )

        elif verdict == Verdict.REJECT:
            if state.round < MAX_ROUNDS:
                state.round += 1
                await self._set_phase(state, Phase.SOLVE)
                await self.client.post_to_feishu(
                    state.issue_id,
                    f"🔄 第 {state.round}/{MAX_ROUNDS} 轮迭代，Solver 修订方案中..."
                )
                await self._dispatch(state)
            else:
                await self._set_phase(state, Phase.ESCALATE)
                await self.client.post_to_feishu(
                    state.issue_id,
                    self._format_escalate_message(state)
                )

    # ------ 辅助方法 ------

    async def _dispatch(self, state: ReviewState):
        """将任务分派给当前阶段对应的 Agent"""
        phase = state.phase
        agent_name = AGENTS[phase]
        context = build_context(state, phase)

        await self.client.assign_agent(
            issue_id=state.issue_id,
            agent_name=agent_name,
            context=context,
        )

        await self.client.update_label(
            state.issue_id,
            f"phase:{phase.value}",
            f"round:{state.round}"
        )

    async def _set_phase(self, state: ReviewState, phase: Phase):
        state.phase = phase

    def _store_output(self, state: ReviewState, phase: Phase, output: str):
        if phase == Phase.INTAKE:
            state.requirement = output
        elif phase == Phase.EVIDENCE:
            state.evidence = output
        elif phase == Phase.SOLVE:
            state.solution = output
        elif phase == Phase.CHALLENGE:
            state.defects = output
        elif phase == Phase.SUPPLEMENT:
            state.supplement = output
        elif phase == Phase.VERDICT:
            state.verdict_result = output

    def _needs_supplement(self, defects_json: str) -> bool:
        try:
            data = json.loads(defects_json)
            items = data.get("defects", [])
            return any(
                d.get("type") in ("evidence_insufficient", "grade_violation")
                for d in items
            )
        except (json.JSONDecodeError, TypeError):
            return False

    def _parse_verdict(self, verdict_output: str) -> Verdict:
        try:
            data = json.loads(verdict_output)
            v = data.get("verdict", "REJECT")
            return Verdict(v)
        except (json.JSONDecodeError, TypeError, ValueError):
            return Verdict.REJECT

    # ------ 飞书消息格式化 ------

    def _format_pass_message(self, state: ReviewState) -> str:
        try:
            v = json.loads(state.verdict_result)
            total = v.get("total", "?")
            summary = v.get("summary", "")
        except (json.JSONDecodeError, TypeError):
            total, summary = "?", ""

        return (
            f"✅ 评审通过 ({total}/100)\n\n"
            f"裁决: {summary}\n\n"
            f"最终方案 (v{state.round}):\n{state.solution}"
        )

    def _format_conditional_message(self, state: ReviewState) -> str:
        try:
            v = json.loads(state.verdict_result)
            conditions = v.get("conditions", [])
            total = v.get("total", "?")
        except (json.JSONDecodeError, TypeError):
            conditions, total = [], "?"

        cond_text = "\n".join(f"  - {c}" for c in conditions)
        return (
            f"⚠️ 有条件通过 ({total}/100)\n\n"
            f"需人工确认以下条件:\n{cond_text}\n\n"
            f"回复「确认」接受，或「打回」要求修改。"
        )

    def _format_escalate_message(self, state: ReviewState) -> str:
        return (
            f"🚨 评审未收敛（已迭代 {MAX_ROUNDS} 轮）\n\n"
            f"移交人工处理。\n\n"
            f"最后方案:\n{state.solution}\n\n"
            f"未解决缺陷:\n{state.defects}"
        )


# ============================================================
# 5. Multica Client 接口（适配层）
# ============================================================

class MulticaClient:
    """
    Multica API 适配层。
    根据你的 multica 实例 API 实现以下方法。
    """

    def __init__(self, base_url: str, api_key: str, workspace_id: str):
        self.base_url = base_url
        self.api_key = api_key
        self.workspace_id = workspace_id

    async def assign_agent(self, issue_id: str, agent_name: str, context: str):
        """
        将 Issue 分派给指定 Agent，并传入上下文作为指令。

        对应 Multica 操作：
        - 修改 Issue assignee 为 agent_name
        - 在 Issue 中添加 comment 作为 Agent 的输入

        实现参考：
        POST {base_url}/api/v1/workspaces/{workspace_id}/issues/{issue_id}/assign
        Body: {"agent": agent_name, "instruction": context}
        """
        raise NotImplementedError("根据 Multica API 实现")

    async def update_label(self, issue_id: str, *labels: str):
        """
        更新 Issue labels（先清除 phase: / round: 前缀的旧 label）。

        实现参考：
        PATCH {base_url}/api/v1/workspaces/{workspace_id}/issues/{issue_id}/labels
        Body: {"labels": labels}
        """
        raise NotImplementedError("根据 Multica API 实现")

    async def post_to_feishu(self, issue_id: str, message: str):
        """
        将消息推送到关联的飞书群。

        实现参考（两种方式）：
        1. 通过 Multica Channel 自动路由
        2. 直接调用飞书 API: POST https://open.feishu.cn/open-apis/im/v1/messages
        """
        raise NotImplementedError("根据 Multica API 或飞书 API 实现")

    async def create_issue(self, title: str, body: str, labels: list[str]) -> str:
        """
        创建 Issue 并返回 issue_id。

        实现参考：
        POST {base_url}/api/v1/workspaces/{workspace_id}/issues
        Body: {"title": title, "body": body, "labels": labels}
        """
        raise NotImplementedError("根据 Multica API 实现")


# ============================================================
# 6. Webhook Handler（入口）
# ============================================================

# 以下为 webhook 路由示例，适配你的部署方式（FastAPI / Flask / 云函数）

"""
from fastapi import FastAPI, Request
app = FastAPI()

client = MulticaClient(
    base_url="https://your-multica-instance.com",
    api_key="your-api-key",
    workspace_id="your-workspace-id",
)
orchestrator = ReviewOrchestrator(client)


@app.post("/webhook/review/start")
async def handle_start(request: Request):
    '''飞书群消息触发：用户发起评审'''
    body = await request.json()
    issue_id = body["issue_id"]
    user_input = body["content"]
    await orchestrator.start(issue_id, user_input)
    return {"status": "started", "issue_id": issue_id}


@app.post("/webhook/review/agent-complete")
async def handle_agent_complete(request: Request):
    '''Agent 完成任务后 Multica 回调'''
    body = await request.json()
    issue_id = body["issue_id"]
    output = body["output"]
    await orchestrator.on_agent_complete(issue_id, output)
    return {"status": "advanced"}


@app.post("/webhook/review/human-confirm")
async def handle_human_confirm(request: Request):
    '''人工确认（有条件通过时）'''
    body = await request.json()
    issue_id = body["issue_id"]
    action = body["action"]  # "confirm" | "reject"

    state = orchestrator.states.get(issue_id)
    if not state:
        return {"error": "not found"}

    if action == "confirm":
        await orchestrator._set_phase(state, Phase.DONE)
        await client.post_to_feishu(issue_id, "✅ 人工已确认，评审通过。")
    elif action == "reject":
        if state.round < MAX_ROUNDS:
            state.round += 1
            await orchestrator._set_phase(state, Phase.SOLVE)
            await orchestrator._dispatch(state)
        else:
            await orchestrator._set_phase(state, Phase.ESCALATE)
            await client.post_to_feishu(issue_id, "🚨 已达最大轮次，需线下讨论。")

    return {"status": action}
"""
