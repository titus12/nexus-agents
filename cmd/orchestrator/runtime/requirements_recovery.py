"""Recovery evidence is bound to the recorded failure, never an arbitrary file."""
import hashlib
from pathlib import Path


def verified_owner_recovery(runtime, context, actor_ids):
    from ..domain.requirements.owner_recovery import validate_owner_recovery
    from ..requirements_port import RequirementsPrerequisiteError
    validate_owner_recovery(context,actor_ids)
    with runtime.store.connect() as db:
        if db.execute('SELECT 1 FROM gates WHERE task_id=? LIMIT 1',(runtime.task_id,)).fetchone():
            raise ValueError('OWNER_REPAIR_CANNOT_CHANGE_EXISTING_GATE')
    original = runtime.port.actor_ids
    try:
        runtime.port.actor_ids = tuple(actor_ids)
        runtime.port.preflight()
        fingerprint = runtime.port.prerequisite_fingerprint()
    except RequirementsPrerequisiteError as error:
        raise ValueError(str(error)) from error
    finally:
        runtime.port.actor_ids = original
    return {'_runtime_verified':True,'actor_ids':list(actor_ids),'new_fingerprint':fingerprint,
            'expected_sequence':context.progression.sequence,
            'expected_failure_fingerprint':context.requirements.recovery.failure.prerequisite_fingerprint}


def runtime_fingerprint():
    root=Path(__file__).resolve().parents[1]
    files=sorted((root/'domain'/'requirements').glob('*.py'))+sorted((root/'runtime').glob('requirements_*.py'))
    files+=sorted(root.glob('requirements_*.py'))+sorted((root/'contracts').glob('*requirements*.py'))
    files.append(root/'contracts'/'common.py')
    digest=hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root)).replace('\\','/').encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def verified_recovery(runtime, state, note):
    if not state.recovery:
        raise ValueError('RECORDED_FAILURE_REQUIRED')
    kind=note.get('kind')
    if kind=='bind_card_receipt':
        if 'OUTCOME_UNKNOWN' not in state.recovery.failure.message and 'RECEIPT_MISSING' not in state.recovery.failure.message:
            raise ValueError('CARD_RECEIPT_RECOVERY_NOT_APPLICABLE')
        gate=runtime.store.gate(note['gate_id'])
        if gate['task_id'] != runtime.task_id or note['page_id'] not in gate['descriptor']['pages']:
            raise ValueError('RECOVERY_GATE_BINDING_INVALID')
        if not getattr(runtime.port,'verify_message',lambda *_:False)(note['message_id'],{**gate,'verification_page_id':note['page_id']}):
            raise ValueError('CARD_MESSAGE_NOT_VERIFIED')
        runtime.store.delivered(note['gate_id'],note['page_id'],note['message_id'])
        from ..domain.requirements.model import digest
        return {'_runtime_verified':True,'new_fingerprint':digest((note['gate_id'],note['page_id'],note['message_id']))}
    if kind!='prerequisite_changed':
        raise ValueError('RECOVERY_KIND_INVALID')
    failure=state.recovery.failure
    if (not failure.prerequisite_fingerprint or note.get('source_ref') != failure.artifact_ref
        or note.get('expected_old_fingerprint') != failure.prerequisite_fingerprint):
        raise ValueError('RECOVERY_MUST_MATCH_RECORDED_FAILURE_BASIS')
    source=note['source_ref']
    actual=(runtime.port.cloud_fingerprint(source) if source.startswith('cloud:') else runtime_fingerprint() if source=='runtime-code' else runtime.port.prerequisite_fingerprint()
            if source=='communication' else hashlib.sha256(Path(source).read_bytes()).hexdigest())
    if actual!=note.get('new_fingerprint') or actual==failure.prerequisite_fingerprint:
        raise ValueError('PREREQUISITE_HAS_NOT_VERIFIABLY_CHANGED')
    return {'_runtime_verified':True,'new_fingerprint':actual,'source_ref':source}
