"""[Gradient] Send an assistant's owner-owned folder ahead of the user's standalone skills."""

import hashlib
import json
import logging
from typing import Any

import yaml
from open_webui import config
from open_webui.models.assistant_skill_files import AssistantSkillFiles
from open_webui.models.models import Models
from open_webui.soev import identity
from open_webui.utils.agent_skills import _notice, upload_reference
from open_webui.utils.assistant_skill_bundles import build_bundle, skill_name

log = logging.getLogger(__name__)


async def assistant_reference(metadata: dict[str, Any], user: str) -> tuple[list[dict[str, Any]], list[str], set[str]]:
    id = metadata.get('assistant_id')
    effective = (((metadata.get('model') or {}).get('info') or {}).get('meta') or {}).get('capabilities') or {}
    if not id or effective.get('workspace') is not True:
        return [], [], set()
    # assistant_id and effective capabilities have already been resolved and authorized by main.py.
    model = await Models.get_model_by_id(id)
    if model is None or not model.base_model_id or (model.meta.capabilities or {}).get('workspace') is not True:
        return [], [], set()
    name = skill_name(model.name)
    try:
        rows = await AssistantSkillFiles.get_files(id)
        backing = await AssistantSkillFiles.file_versions([row.file_id for row in rows])
        versions = [
            [
                row.path,
                row.file_id,
                row.updated_at,
                backing.get(row.file_id),
            ]
            for row in sorted(rows, key=lambda row: row.path)
        ]
        source = json.dumps(
            [model.user_id, model.name, model.meta.description, model.params.model_dump().get('system'), versions],
            ensure_ascii=False,
        ).encode()
        key = (
            config.SOEV_API_URL,
            hashlib.sha256(config.SOEV_API_KEY.encode()).hexdigest(),
            user,
            f'assistant:{id}',
            hashlib.sha256(source).hexdigest(),
        )
        reference = await upload_reference(
            key, id, model.name, lambda: build_bundle(model, rows), user, identity.build_client()
        )
    except (OSError, ValueError, yaml.YAMLError):
        log.warning('Assistant workspace skill unavailable: assistant=%s', id)
        reference = None
    if reference is None:
        return [], [_notice(model.name, metadata.get('user_language'))], {name}
    return [{'type': 'chat-skill', **reference}], [], {name}
