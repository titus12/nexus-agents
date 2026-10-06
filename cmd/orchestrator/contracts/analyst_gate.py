"""Semantic guard shared by transport, FSM and human presentation."""
from collections.abc import Mapping

INVALID_ANALYST_GATE = "ANALYST_HUMAN_GATE_INVALID"


def analyst_questions(payload: Mapping[str, object]) -> tuple[str, ...]:
    questions = payload.get("questions_for_user")
    if not isinstance(questions, (list, tuple)):
        return ()
    result = []
    for entry in questions:
        if isinstance(entry, str):
            question = entry.strip()
        elif isinstance(entry, Mapping):
            question = next((entry[key].strip() for key in ("question", "text", "prompt")
                             if isinstance(entry.get(key), str) and entry[key].strip()), "")
        else:
            question = ""
        if question:
            result.append(question)
    return tuple(result)


def analyst_gate_error(payload: Mapping[str, object]) -> str:
    if payload.get("action") != "HUMAN_GATE":
        return ""
    summary = payload.get("summary")
    reason = isinstance(summary, str) and bool(summary.strip())
    entries = payload.get("questions_for_user")
    if isinstance(entries, (list, tuple)):
        reason = reason or any(isinstance(entry, Mapping)
                               and isinstance(entry.get("reason"), str)
                               and entry["reason"].strip() for entry in entries)
    if analyst_questions(payload) and reason:
        return ""
    return (INVALID_ANALYST_GATE + ": HUMAN_GATE requires a concrete nonempty "
            "questions_for_user question and a reason (summary or question.reason). "
            "If requirement extraction is complete, return REQUIREMENT_CONTRACT_READY "
            "in REQUIREMENT_CONTRACT_ONLY; if evidence collection is complete, return "
            "EVIDENCE_PACKET_READY. Missing runtime evidence belongs in unknowns; "
            "request a human only for a specific decision they must make.")
