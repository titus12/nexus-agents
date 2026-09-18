"""Single-source acceptance-quality standard shared by all Zhongshu roles.

Distilled from the rejection reasons the task-review Critic repeated across
real runs (task-20260916-74fd67, task-20260917-daea31): every stalled item
failed on acceptance observability -- missing measurement units, missing
evidence sources, claims unsupported by evidence, unclosed requirement
lineage -- never on the plan's substance.  Because the standard previously
lived only inside the Critic's head, each revision round re-negotiated it at
~9-10 minutes per round.  Feeding the same text to Analyst, Solver, and Critic
moves the negotiation to plan-writing time, where it costs nothing.
"""

from collections.abc import Mapping

ZHONGSHU_ACCEPTANCE_CHECKLIST = (
    "[Acceptance standard] 每条验收信号（acceptance_signal / acceptance_signals）必须可观测，逐条对照：",
    "1. 逐条可核验：列表中每项措施/结论单独给出核验方法，不得合并成一句「确认X正确」。",
    "2. 度量口径：凡涉及大小/数量/比例，写明统一单位与统计方式（如 UTF-8 字节数、字段数、样本量、P50/P95）。",
    "3. 证据来源：写明证据取自哪里（文件:行号、日志字段、配置项、测试名），不得只给结论。",
    "4. 运行时事实不可得时显式标注 UNKNOWN 并列出补采信号；不得以代码推断替代实测，也不得声称已验证。",
    "5. 验收声明与已有证据一致：不得声称未实现的机制（如 exponential backoff）已有证据支持。",
    "6. 溯源闭合：每个 item 的 source_requirement_ids 必须能追溯到 requirement；无来源的项写明理由。",
    "7. 图文一致：文字中声明的依赖/前置必须体现在任务图 dependencies 中，或明确声明仅为参考信息。",
    "8. 可测性闸门：信号声称运行时度量结论（百分比、提升/降低/缩短/加速等对比）却无法在本轮内实测的，"
    "必须二选一：(a) 在信号中写明「验证方法：」并给出五要素——测量对象（含 file:line）、具体命令/步骤、"
    "指标与单位、基线来源、预期观测量；(b) 改写为「核实该声称有无实证，无则标注 UNKNOWN」。"
    "两者都没有的信号会被机械闸门以 ACCEPTANCE_SIGNAL_UNVERIFIABLE 拒绝，不要让它流进审查循环。",
)

# Signals asserting a measured *outcome* (comparison or ratio) need a recipe or
# an UNKNOWN demotion.  Bare unit words are deliberately excluded: rule 2
# demands units even on statically checkable claims (e.g. a field named
# elapsed_ms), so units alone must not trigger the gate.
_MEASUREMENT_CLAIM_MARKERS = (
    "%", "％", "提升", "降低", "缩短", "加速", "减少", "提高",
    "倍增", "倍", "优化幅度", "吞吐", "faster", "slower",
)
_RECIPE_ESCAPE_MARKERS = ("UNKNOWN", "ＵＮＫＮＯＷＮ", "验证方法")


def acceptance_signal_gate(plan: object) -> list[str]:
    """Reject measurement claims that carry neither a recipe nor UNKNOWN.

    A signal that promises a measured improvement/percentage cannot be closed
    by static review; leaving it verifiable-in-name-only sent real runs into
    five-round evidence fights (task-20260918-0b7867 items 003/007/008/009).
    The demand must be satisfiable inside the capability set: either the
    signal embeds a five-element verification recipe (statically checkable)
    or it demotes the claim to UNKNOWN.
    """

    issues: list[str] = []
    items = plan.get("items") if isinstance(plan, Mapping) else None
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        item_id = str(item.get("item_id") or "")
        signals = item.get("acceptance_signals")
        if not isinstance(signals, (list, tuple)):
            continue
        for index, signal in enumerate(signals):
            text = str(signal or "")
            if not any(marker in text for marker in _MEASUREMENT_CLAIM_MARKERS):
                continue
            if any(marker in text for marker in _RECIPE_ESCAPE_MARKERS):
                continue
            issues.append(f"ACCEPTANCE_SIGNAL_UNVERIFIABLE:{item_id}:{index}")
    return issues

_CHECKLIST_STATES = frozenset({"ZHONGSHU_ANALYST", "ZHONGSHU_SOLVER"})


def acceptance_standard_block() -> str:
    """The checklist rendered as a prompt block for the producing roles."""

    return "\n".join(ZHONGSHU_ACCEPTANCE_CHECKLIST) + "\n"


def acceptance_standard_applies_to(state: str) -> bool:
    """Whether ``state`` writes acceptance signals and therefore needs it."""

    return str(state or "") in _CHECKLIST_STATES


def acceptance_standard_hint() -> str:
    """Compact version for the per-task review capsule (judging role)."""

    return (
        "Judge each acceptance_signal against the shared acceptance standard: "
        "per-item verifiability, explicit measurement units, cited evidence "
        "sources, UNKNOWN for unobtainable runtime facts, claims consistent "
        "with evidence, and closed source_requirement_ids lineage.  A signal "
        "claiming a measured outcome must either embed「验证方法：」with all "
        "five recipe elements (target with file:line, exact steps, metric+"
        "unit, baseline source, expected observation) or carry UNKNOWN; "
        "close an unverifiable claim with decision=WONT_VERIFY instead of "
        "keeping it blocking."
    )


__all__ = [
    "ZHONGSHU_ACCEPTANCE_CHECKLIST",
    "acceptance_signal_gate",
    "acceptance_standard_applies_to",
    "acceptance_standard_block",
    "acceptance_standard_hint",
]
