"""Explicit owner or a runtime-authenticated human callback in the bound chat."""
import re


def authorized_actor(actor, actor_ids, payload):
    if actor == 'local-operator' or actor in actor_ids:
        return True
    return bool(not actor_ids and isinstance(actor,str) and re.fullmatch(r'ou_[A-Za-z0-9]+',actor)
                and payload.get('_verified_chat_member') is True)
