"""Strict result schemas shared by the three requirements roles."""
from dataclasses import MISSING, fields, replace
from typing import get_args, get_origin, get_type_hints
from .common import make_contract, string, array, object_schema
from ..domain.requirements.model import (RequirementClaim, FactRecord, AssumptionRecord,
    Question, DraftSection, ReviewIssue, ReviewCoverage, RECORD_ENUMS, RECORD_MINIMUMS)


def record_schema(cls, *, allocated_id=''):
    properties = {}
    for name, typ in get_type_hints(cls).items():
        origin, args = get_origin(typ), get_args(typ)
        if origin is tuple:
            item = record_schema(args[0]) if hasattr(args[0], '__dataclass_fields__') else string()
            properties[name] = array(item)
        elif typ is str:
            properties[name] = string(enum=RECORD_ENUMS.get(cls.__name__, {}).get(name))
        elif typ is bool:
            properties[name] = {'type': 'boolean'}
        elif typ is int:
            properties[name] = {'type': 'integer'}
            minimum = RECORD_MINIMUMS.get(cls.__name__, {}).get(name)
            if minimum is not None:
                properties[name]['minimum'] = minimum
        else:
            raise TypeError(f'unsupported schema type {typ}')
    required = [f.name for f in fields(cls) if f.default is MISSING and f.default_factory is MISSING and f.name != allocated_id]
    schema = object_schema(properties, required=required)
    if allocated_id:
        schema['properties']['local_key'] = string()
        schema['properties']['local_key']['description'] = 'Optional response-local alias; unique across claims, facts, assumptions and questions. Use different keys even when topics match.'
    schema['additionalProperties'] = False
    return schema


CLAIM = record_schema(RequirementClaim, allocated_id='requirement_id')
CLAIM['properties']['quote'] = string()
CLAIM['properties']['span_ids'] = {'type': 'array', 'items': string(),
    'description': 'Payload-only. Cite human wording with source-span ids like src-000001#2 '
                   '(from dispatch_context.sources spans); the runtime backfills the exact quote. '
                   'All ids must share one source and be contiguous; quote is then ignored.'}
FACT = record_schema(FactRecord, allocated_id='fact_id')
FACT['properties']['source_fingerprint'] = {
    'type':'string', 'const':'',
    'description':'Runtime-owned SHA-256. Omit this field or leave it empty; never put line numbers or a model-computed hash here.'}
FACT['properties']['source']['description'] = 'UTF-8 file path, preferably absolute. Relative paths require the dispatched project_root. Optional line suffix: :12 or :12-18.'
FACT['properties']['quote']['description'] = 'One nonempty contiguous verbatim excerpt from the file. CRLF/LF equivalence is allowed; no whitespace changes, ellipses, paraphrase, or stitching separate excerpts.'
ASSUMPTION = record_schema(AssumptionRecord, allocated_id='assumption_id')
ASSUMPTION['properties']['disposition']['description'] = (
    'Agent write permissions: use proposed for new assumptions. accepted may only echo an existing accepted '
    'record from dispatch_context.consensus.assumptions, matched by assumption_id and unchanged in every field. '
    'The model cannot accept a new or previously unaccepted assumption. Omit existing human-accepted records '
    'rather than rewriting them. Adding or changing an ID does not grant acceptance.')
QUESTION = record_schema(Question, allocated_id='question_id')
QUESTION['properties']['revision']['description'] = 'For new questions omit revision (default 1) or use 1, never 0. Existing revision changes are allocated by the runtime.'
SECTION = record_schema(DraftSection)
ISSUE = record_schema(ReviewIssue, allocated_id='issue_id')
COVERAGE = record_schema(ReviewCoverage)


def strict_schema(schema):
    schema['strictTypes'] = True
    if 'enum' in schema:
        schema['strictEnum'] = True
    if schema.get('type') == 'object':
        schema['additionalProperties'] = False
    for child in schema.get('properties', {}).values():
        strict_schema(child)
    if isinstance(schema.get('items'), dict):
        strict_schema(schema['items'])
    return schema


def requirement_contract(state, role, actions, business, rules):
    contract = make_contract(contract_id='nexus.requirements.'+state.lower()+'.v1', state=state,
                             phase='ZHONGSHU', role=role, modes=('REQUIREMENTS_DOCUMENT',),
                             actions=actions, fields=business, prompt_rules=tuple(rules) + (
                                 'All local_key aliases in a response must be globally unique across claims, facts, '
                                 'assumptions and questions. A shared topic_key is allowed; a shared local_key is not. '
                                 'For new questions omit revision or set 1. Preserve existing IDs and human decisions. '
                                 'Repair every reported validation path together; do not repeat repository research.',
                                 'New assumptions must remain proposed. Never turn a previously unaccepted assumption into accepted. '
                                 'An existing accepted assumption may only be echoed unchanged with its exact assumption_id, '
                                 'or omitted to preserve it. Enum values describe stored states, not permission to make human decisions. '
                                 'Before submitting, check every fact quote as a contiguous substring of the actual source file; '
                                 'split separate excerpts into separate facts and never use ellipses.',
                                 'For each clarification question, supply recommendation with a suggested existing '
                                 'option label (or a suggested text answer) and its rationale. If evidence is insufficient, '
                                 'state what is missing rather than inventing a business choice. Recommendations are '
                                 'advice only; never preselect or record them as human answers.',
                                 'Draft first. Questions are authored only by the Solver together with a complete first draft; '
                                 'every question must point to a pending marker 【待确认：<question_id>】 in that draft. '
                                 'Group questions by topic in one round; do not hold the draft back for questions. '
                                 'Do not invent questions for constraints already explicit in human sources.',
                                 'Link blocking assumptions to questions using default_assumption_id.',
                                 'After a clarification round, ask additional questions only when new human answers, '
                                 'observed facts or review issues change the decision basis. The question reason must '
                                 'explain that new basis and identify its source/question/issue. Preserve answered choices.'))
    strict_schema(contract.schema)
    return contract
