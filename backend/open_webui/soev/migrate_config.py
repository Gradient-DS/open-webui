"""The fixed v2 config key set: values from SOEV_V2_CONFIG, written through the persistent config rows."""

import copy
import json
import time

from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.models.config import Config, _json_value
from open_webui.soev.migrate_state import MigrationError

# Models come from the soev-api catalog and tasks go to /v1/completions/task, so the
# direct connections, the stored default models and the task models are retired.
RETIRED = {
    'openai.enable': False,
    'openai.api_base_urls': [],
    'openai.api_keys': [],
    'openai.api_configs': {},
    'ollama.enable': False,
    'ollama.base_urls': [],
    'ollama.api_configs': {},
    'ui.default_models': '',
    'task.model.default': '',
    'task.model.external': '',
}
FROM_ENV = (
    'agent_api.selected_agent',
    'document_writer.enable',
    'live_documents.enable',
    'live_mail.enable',
    'notes.enable',
    'web.search.enable',
    'webui.url',
)
PERMISSION_FEATURES = 'user.permissions.features'
PERMISSIONS = 'user.permissions'
# Rewritten by the model-id step; listed here so the one snapshot covers them.
MODEL_ID_KEYS = ('ui.default_pinned_models', 'ui.model_order_list')
SNAPSHOT_KEYS = tuple(sorted({*RETIRED, *FROM_ENV, PERMISSIONS, *MODEL_ID_KEYS}))


def parse_v2_config(raw: str | None) -> dict:
    """Validate SOEV_V2_CONFIG: exactly the FROM_ENV keys plus user.permissions.features."""
    if not raw:
        raise MigrationError('SOEV_V2_CONFIG is not set')
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as error:
        raise MigrationError(f'SOEV_V2_CONFIG is not JSON: {error.msg}') from None
    if not isinstance(values, dict):
        raise MigrationError('SOEV_V2_CONFIG must be a JSON object')
    expected = {*FROM_ENV, PERMISSION_FEATURES}
    if missing := sorted(expected - values.keys()):
        raise MigrationError(f'SOEV_V2_CONFIG is missing {", ".join(missing)}')
    if unknown := sorted(values.keys() - expected):
        raise MigrationError(f'SOEV_V2_CONFIG has unknown keys {", ".join(unknown)}')
    features = values[PERMISSION_FEATURES]
    if not isinstance(features, dict) or not all(isinstance(value, bool) for value in features.values()):
        raise MigrationError(f'{PERMISSION_FEATURES} must map feature names to booleans')
    return values


def planned(v2: dict, permissions: dict) -> dict:
    """The full v2 value of every switched key, given the stored user.permissions."""
    merged = copy.deepcopy(permissions) if isinstance(permissions, dict) else {}
    merged['features'] = {**(merged.get('features') or {}), **v2[PERMISSION_FEATURES]}
    return {**RETIRED, **{key: v2[key] for key in FROM_ENV}, PERMISSIONS: merged}


async def switch(db: AsyncSession, v2: dict) -> dict:
    """Upsert the v2 values as Config.upsert does; returns the values written."""
    row = await db.get(Config, PERMISSIONS)
    values = planned(v2, row.value if row else Config.default_value(PERMISSIONS, {}))
    if refused := sorted(key for key in values if not Config.persistent_enabled_for(key)):
        raise MigrationError(f'Persistent config is disabled for {", ".join(refused)}')
    now = int(time.time())
    for key, value in values.items():
        value = _json_value(value)
        existing = await db.get(Config, key)
        if existing is None:
            db.add(Config(key=key, value=value, updated_at=now))
        elif existing.value != value:
            existing.value, existing.updated_at = value, now
    await db.commit()
    return values
