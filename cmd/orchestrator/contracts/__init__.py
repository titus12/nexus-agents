"""Contracts for the sole requirements workflow and its Menxia continuation."""
from .common import PhaseContract
from .requirements_analyst import CONTRACT as ANALYST, EVIDENCE_CONTRACT
from .requirements_solver import CONTRACT as SOLVER
from .requirements_critic import CONTRACT as CRITIC
from .menxia_requirements import INTAKE, variant
from .menxia_group_analyst import CONTRACT as MENXIA_GROUP_ANALYST
from .menxia_group_critic import CONTRACT as MENXIA_GROUP_CRITIC
from .menxia_group_gate import CONTRACT as MENXIA_GROUP_GATE
from .menxia_group_solver import CONTRACT as MENXIA_GROUP_SOLVER
from .menxia_item_analyst import CONTRACT as MENXIA_ITEM_ANALYST
from .menxia_item_critic import CONTRACT as MENXIA_ITEM_CRITIC
from .menxia_item_solver import CONTRACT as MENXIA_ITEM_SOLVER

_CONTRACTS = {c.state:c for c in (ANALYST,EVIDENCE_CONTRACT,SOLVER,CRITIC,INTAKE)}
_CONTRACTS.update({c.state:variant(c) for c in (MENXIA_GROUP_ANALYST,MENXIA_GROUP_CRITIC,
    MENXIA_GROUP_GATE,MENXIA_GROUP_SOLVER,MENXIA_ITEM_ANALYST,MENXIA_ITEM_CRITIC,MENXIA_ITEM_SOLVER)})

def contract_for_state(state, *, workflow='requirements-v1'):
    if workflow != 'requirements-v1':
        raise KeyError('RETIRED_WORKFLOW:'+workflow)
    try:
        return _CONTRACTS[str(state).upper()]
    except KeyError as error:
        raise KeyError('UNSUPPORTED_CONTRACT_STATE:'+str(state)) from error

def all_contracts(*, workflow='requirements-v1'):
    if workflow != 'requirements-v1':
        raise KeyError('RETIRED_WORKFLOW:'+workflow)
    return tuple(_CONTRACTS.values())

__all__ = ['PhaseContract','all_contracts','contract_for_state']
