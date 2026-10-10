"""Workflow-selected Menxia contracts; legacy actions remain unchanged."""
from dataclasses import replace
from copy import deepcopy
from .common import make_contract, object_schema, array, string, empty_payload
from .requirements_common import QUESTION

INTAKE = make_contract(contract_id='nexus.menxia.requirements_intake.v1',state='MENXIA_REQUIREMENTS_INTAKE',
    phase='MENXIA',role='review-solver',modes=('APPROVED_REQUIREMENTS_INTAKE',),
    actions=('IMPLEMENTATION_SCOPE_READY','PREREQUISITE_MISSING','REQUIREMENTS_CHANGE_REQUESTED'),
    fields={'plan':object_schema(),'questions':array(QUESTION),'reason':string()},
    prompt_rules=('Read the approved requirements. Build a complete canonical implementation DAG in one group.',
        'plan has requirements [{requirement_id,description}], items and groups.',
        'Every item has item_id,title,objective,group_id,dependencies,source_requirement_ids,acceptance_signals,unknowns,risks,parallelizable.',
        'Every group has group_id,item_ids. Preserve real item dependencies; there is no six-item cap.',
        'Never rewrite the approved input. Request a new user clarification for any necessary business change.'))
INTAKE.schema['additionalProperties'] = False


def variant(contract):
    schema = deepcopy(contract.schema)
    schema['additionalProperties'] = False
    schema['properties']['action']['enum'].append('REQUIREMENTS_CHANGE_REQUESTED')
    schema['properties']['contract_id']['const'] = contract.contract_id+'.requirements-v1'
    schema['properties']['questions_for_user'] = array(QUESTION)
    # The established Menxia body fields remain stable; the new business action is workflow-bound.
    return replace(contract,contract_id=contract.contract_id+'.requirements-v1',schema=schema,
        example_payload=lambda:empty_payload(schema),
        actions=contract.actions+('REQUIREMENTS_CHANGE_REQUESTED',),
        prompt_rules=contract.prompt_rules+('Approved requirements are immutable. Business changes require REQUIREMENTS_CHANGE_REQUESTED and questions_for_user.',))
