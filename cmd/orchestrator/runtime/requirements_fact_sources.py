"""Read-only cache freshness checks; file versions are observed by the runtime."""
import hashlib
from pathlib import Path
import re


def source_path(source, project_root=''):
    path = Path(re.sub(r':\d+(?:-\d+)?$','',source))
    if not path.is_absolute():
        if not project_root or not Path(project_root).is_absolute():
            raise ValueError('RELATIVE_FACT_SOURCE_REQUIRES_PROJECT_ROOT: use an absolute source path or --project-root')
        path = Path(project_root) / path
    return path.resolve()


def changed_sources(state):
    referenced = {fid for section in state.draft.sections for fid in section.fact_ids} if state.draft else set()
    changes = []
    for fact in state.consensus.facts:
        if fact.fact_id not in referenced or fact.status != 'observed':
            continue
        try:
            fingerprint = hashlib.sha256(source_path(fact.source).read_bytes()).hexdigest()
        except (OSError,ValueError):
            fingerprint = 'unavailable'
        if fingerprint != fact.source_fingerprint:
            changes.append({'fact_id':fact.fact_id,'source_fingerprint':fingerprint})
    return changes
