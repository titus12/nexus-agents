"""Registry for the canonical machine contract of each Orchestrator state."""
from __future__ import annotations

from .common import PhaseContract
from .menxia_group_gate import CONTRACT as MENXIA_GROUP_GATE
from .menxia_item_analyst import CONTRACT as MENXIA_ITEM_ANALYST
from .menxia_item_critic import CONTRACT as MENXIA_ITEM_CRITIC
from .menxia_item_solver import CONTRACT as MENXIA_ITEM_SOLVER
from .zhongshu_analyst import CONTRACT as ZHONGSHU_ANALYST
from .zhongshu_critic import CONTRACT as ZHONGSHU_CRITIC
from .zhongshu_solver import CONTRACT as ZHONGSHU_SOLVER


_BY_STATE: dict[str, PhaseContract] = {
    "ZHONGSHU_ANALYST": ZHONGSHU_ANALYST,
    "ZHONGSHU_SOLVER": ZHONGSHU_SOLVER,
    "ZHONGSHU_CRITIC": ZHONGSHU_CRITIC,
    "MENXIA_ITEM_SOLVER": MENXIA_ITEM_SOLVER,
    "MENXIA_ITEM_ANALYST": MENXIA_ITEM_ANALYST,
    "MENXIA_ITEM_CRITIC": MENXIA_ITEM_CRITIC,
    "MENXIA_GROUP_GATE": MENXIA_GROUP_GATE,
}


def contract_for_state(state: str) -> PhaseContract:
    try:
        return _BY_STATE[str(state).upper()]
    except KeyError as error:
        raise KeyError(f"UNSUPPORTED_CONTRACT_STATE:{state}") from error


def all_contracts() -> tuple[PhaseContract, ...]:
    return tuple(_BY_STATE.values())


__all__ = ["PhaseContract", "all_contracts", "contract_for_state"]
