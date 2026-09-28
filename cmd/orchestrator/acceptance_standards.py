"""Single-source acceptance-quality standard shared by all Zhongshu roles.

Distilled from the rejection reasons the task-review Critic repeated across
real runs (task-20260916-74fd67, task-20260917-daea31): every stalled item
failed on acceptance observability -- missing measurement units, missing
evidence sources, claims unsupported by evidence, unclosed requirement
lineage -- never on the plan's substance.  Because the standard previously
lived only inside the Critic's head, each revision round re-negotiated it at
~9-10 minutes per round.  Feeding the same text to Analyst, Solver, and Critic
moves the negotiation to plan-writing time, where it costs nothing.

2026-09-27 thin-doc revision: the five-element measurement recipe moved to the
Menxia execution phase (its skill owns "how to measure").  Zhongshu only pins
WHAT must be observable -- one signal = one checkable assertion, comparative
numeric claims either measured-with-source or explicitly 待实测, and ambiguous
metric scopes live in the §4 glossary.  The mechanical gate keeps only the
anti-fake-measurement rule.
"""

from collections.abc import Mapping

ZHONGSHU_ACCEPTANCE_CHECKLIST = (
    "[Acceptance standard] 每条验收信号（acceptance_signal）必须是一个可打勾的场景断言，逐条对照：",
    "1. 可打勾：一条信号一个断言（给定-当-则或简单谓词），审阅时能直接判定过/不过；不写过程描述，不把多条断言合并成一段。",
    "2. 数值结论二选一：对比性数值结论（百分比、提升/降低/缩短/加速等）要么给实测数字并注明来源，要么明确写「待实测」；估算不得冒充实测。既无实测来源又未标注的信号会被机械闸门以 ACCEPTANCE_SIGNAL_UNVERIFIABLE 拒绝。具体怎么测（测量对象、命令、指标、基线）由门下省实施计划负责，不在需求文档展开。",
    "3. 口径与出处：计量范围/单位等易混口径写入 §4 口径定义表并在信号中引用，不写长段解释；断言引用的证据（文件:行、日志字段、测试名）须真实存在且支撑该断言；每条信号归属的 item 须能追溯到 requirement。",
)

# Signals asserting a measured *outcome* (comparison or ratio) need a
# measured source or an explicit demotion.  Bare unit words are deliberately
# excluded: units must appear even on statically checkable claims (e.g. a
# field named elapsed_ms), so units alone must not trigger the gate.
_MEASUREMENT_CLAIM_MARKERS = (
    "%", "％", "提升", "降低", "缩短", "加速", "减少", "提高",
    "倍增", "倍", "优化幅度", "吞吐", "faster", "slower",
)
# Escape markers: the claim is honestly demoted (待实测/UNKNOWN) or carries a
# verification recipe / measured source.  The gate is a coarse pre-filter
# against fake measurements, not a recipe format check.
_RECIPE_ESCAPE_MARKERS = ("UNKNOWN", "ＵＮＫＮＯＷＮ", "待实测", "未实测", "验证方法", "实测")


def acceptance_signal_gate(plan: object) -> list[str]:
    """Reject measurement claims that carry neither a measured source nor 待实测.

    A signal promising a measured improvement/percentage cannot be closed by
    static review; leaving it verifiable-in-name-only sent real runs into
    five-round evidence fights (task-20260918-0b7867 items 003/007/008/009).
    The thin-doc rule is a binary: the claim cites measured results, or it is
    explicitly demoted to 待实测/UNKNOWN.  Recipe formatting is the Menxia
    execution phase's business, not this gate's.
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
    """Compact version for the per-task review capsule (judging role).

    The task-list review audits requirement fit, observability, evidence
    consistency, and lineage closure.  Demanding a five-element verification
    recipe per signal made the Critic an execution-plan reviewer and stalled
    real runs (task-20260919-c095db: same findings recurred for four rounds
    while no measurement recipe was obtainable pre-execution); recipes belong
    to the Menxia execution phase, so the review only requires an UNKNOWN
    demotion for claims that cannot be measured this round.
    """

    return (
        "Judge each acceptance_signal against the shared acceptance standard: "
        "one signal = one checkable assertion, explicit measurement units and "
        "metric scope from the §4 glossary, cited evidence sources, claims "
        "consistent with evidence, and closed "
        "source_requirement_ids lineage.  A signal claiming a measured outcome "
        "that cannot be measured this round must carry 待实测/UNKNOWN; do NOT "
        "require a five-element verification recipe -- recipes "
        "belong to the execution phase, not the task-list review.  Close an "
        "unverifiable claim with decision=WONT_VERIFY instead of keeping it "
        "blocking."
    )


__all__ = [
    "ZHONGSHU_ACCEPTANCE_CHECKLIST",
    "acceptance_signal_gate",
    "acceptance_standard_applies_to",
    "acceptance_standard_block",
    "acceptance_standard_hint",
]
