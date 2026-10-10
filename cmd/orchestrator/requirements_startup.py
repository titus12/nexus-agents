"""Check interactive prerequisites before creating remote work."""
from pathlib import Path
from .requirements_gateway import GatewayStore
from .requirements_port import FeishuRequirementsPort, RequirementsPrerequisiteError


def check_startup(runs_root, communication, actor_ids):
    if communication == 'local':
        return {'ready':True,'communication':'local'}
    policy='explicit' if actor_ids else 'chat-members'
    store=GatewayStore(Path(runs_root)/'requirements-gateway.sqlite')
    port=FeishuRequirementsPort(Path(runs_root)/'startup-check',store,actor_ids=actor_ids,owner_policy=policy)
    try:
        port.preflight()
    except RequirementsPrerequisiteError as error:
        raise ValueError(str(error)+'\nStart cmd/requirements_card_gateway.py --database '+str(store.path.resolve())) from error
    return {'ready':True,'communication':'feishu','decision_actor_ids':list(actor_ids),
            'owner_policy':policy,'gateway_database':str(store.path.resolve())}
