from __future__ import annotations

import copy
import logging
from typing import Optional, Union

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from mcp.shared.auth import OAuthMetadata
from open_webui.config import BannerModel
from open_webui.env import (
    AGENT_API_AGENTS,
    AGENT_API_ENABLED,
    AIOHTTP_CLIENT_SESSION_SSL,
    AIOHTTP_CLIENT_TIMEOUT,
)
from open_webui.events import EVENTS, publish_event
from open_webui.models.config import Config
from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.models.users import Users
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.features import require_feature
from open_webui.utils.headers import get_custom_headers
from open_webui.utils.mcp.client import MCPClient
from open_webui.utils.oauth import (
    OAuthClientInformationFull,
    apply_connection_oauth_options,
    decrypt_data,
    encrypt_data,
    get_discovery_urls,
    get_oauth_client_info_with_dynamic_client_registration,
    get_oauth_client_info_with_static_credentials,
    recover_static_oauth_client_metadata,
    resolve_oauth_client_info,
)
from open_webui.utils.tools import (
    bearer_auth_header,
    get_tool_server_data,
    get_tool_server_url,
    set_terminal_servers,
    set_tool_servers,
)
from pydantic import BaseModel, ConfigDict

router = APIRouter()

log = logging.getLogger(__name__)

CONNECTIONS_CONFIG_KEYS = {
    'ENABLE_DIRECT_CONNECTIONS': 'direct.enable',
    'ENABLE_BASE_MODELS_CACHE': 'models.base_models_cache',
}
CODE_EXECUTION_CONFIG_KEYS = {
    'ENABLE_CODE_EXECUTION': 'code_execution.enable',
    'CODE_EXECUTION_ENGINE': 'code_execution.engine',
    'CODE_EXECUTION_JUPYTER_URL': 'code_execution.jupyter.url',
    'CODE_EXECUTION_JUPYTER_AUTH': 'code_execution.jupyter.auth',
    'CODE_EXECUTION_JUPYTER_AUTH_TOKEN': 'code_execution.jupyter.auth_token',
    'CODE_EXECUTION_JUPYTER_AUTH_PASSWORD': 'code_execution.jupyter.auth_password',
    'CODE_EXECUTION_JUPYTER_TIMEOUT': 'code_execution.jupyter.timeout',
    'ENABLE_CODE_INTERPRETER': 'code_interpreter.enable',
    'CODE_INTERPRETER_ENGINE': 'code_interpreter.engine',
    'CODE_INTERPRETER_PROMPT_TEMPLATE': 'code_interpreter.prompt_template',
    'CODE_INTERPRETER_JUPYTER_URL': 'code_interpreter.jupyter.url',
    'CODE_INTERPRETER_JUPYTER_AUTH': 'code_interpreter.jupyter.auth',
    'CODE_INTERPRETER_JUPYTER_AUTH_TOKEN': 'code_interpreter.jupyter.auth_token',
    'CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD': 'code_interpreter.jupyter.auth_password',
    'CODE_INTERPRETER_JUPYTER_TIMEOUT': 'code_interpreter.jupyter.timeout',
}
MODELS_CONFIG_KEYS = {
    'DEFAULT_MODELS': 'ui.default_models',
    'DEFAULT_PINNED_MODELS': 'ui.default_pinned_models',
    'MODEL_ORDER_LIST': 'ui.model_order_list',
    'DEFAULT_MODEL_METADATA': 'models.default_metadata',
    'DEFAULT_MODEL_PARAMS': 'models.default_params',
}


async def get_config_values(key_map: dict[str, str]) -> dict:
    values = await Config.get_many(*key_map.values())
    return {field: values[storage_key] for field, storage_key in key_map.items() if storage_key in values}


def config_updates(data: dict, key_map: dict[str, str]) -> dict:
    return {key_map[field]: value for field, value in data.items() if field in key_map}


############################
# ImportConfig
# Thy configuration come, thy settings be done,
# in production as it is in development.
############################


class ImportConfigForm(BaseModel):
    config: dict


@router.post('/import', response_model=dict)
async def import_config(request: Request, form_data: ImportConfigForm, user=Depends(get_admin_user)):
    await Config.upsert(form_data.config)
    await publish_event(
        request,
        EVENTS.CONFIG_IMPORTED,
        actor=user,
        subject_id='import',
        data={'keys': list(form_data.config.keys())},
    )
    return await Config.get_all()


############################
# ExportConfig
############################


@router.get('/export', response_model=dict)
async def export_config(user=Depends(get_admin_user)):
    return await Config.get_all()


@router.get('/namespace/{namespace}', response_model=dict)
async def get_config_namespace(namespace: str, user=Depends(get_admin_user)):
    return await Config.get_namespace(namespace)


############################
# Connections Config
############################


class ConnectionsConfigForm(BaseModel):
    ENABLE_DIRECT_CONNECTIONS: bool
    ENABLE_BASE_MODELS_CACHE: bool


@router.get('/connections', response_model=ConnectionsConfigForm)
async def get_connections_config(request: Request, user=Depends(get_admin_user)):
    return await get_config_values(CONNECTIONS_CONFIG_KEYS)


@router.post('/connections', response_model=ConnectionsConfigForm)
async def set_connections_config(
    request: Request,
    form_data: ConnectionsConfigForm,
    user=Depends(get_admin_user),
):
    await Config.upsert(config_updates(form_data.model_dump(), CONNECTIONS_CONFIG_KEYS))
    values = await get_config_values(CONNECTIONS_CONFIG_KEYS)
    await publish_event(
        request,
        EVENTS.CONFIG_CONNECTIONS_UPDATED,
        actor=user,
        subject_id='connections',
        subject_type='config',
        data=values,
    )
    return values


class OAuthClientRegistrationForm(BaseModel):
    url: str
    client_id: str
    client_name: str | None = None
    client_secret: str | None = None
    oauth_server_url: str | None = None
    oauth_scope: str | None = None


@router.post('/oauth/clients/register')
async def register_oauth_client(
    request: Request,
    form_data: OAuthClientRegistrationForm,
    type: str | None = None,
    user=Depends(get_admin_user),
):
    try:
        oauth_client_id = form_data.client_id
        if type:
            oauth_client_id = f'{type}:{form_data.client_id}'

        oauth_server_url = form_data.oauth_server_url if form_data.oauth_server_url else form_data.url

        if form_data.client_secret:
            # Static credentials: skip dynamic registration, build from provided credentials
            oauth_client_info = await get_oauth_client_info_with_static_credentials(
                request,
                oauth_client_id,
                oauth_server_url,
                oauth_client_id=form_data.client_id,
                oauth_client_secret=form_data.client_secret,
                oauth_scope=form_data.oauth_scope,
            )
        else:
            oauth_client_info = await get_oauth_client_info_with_dynamic_client_registration(
                request, oauth_client_id, oauth_server_url, oauth_scope=form_data.oauth_scope
            )
        return {
            'status': True,
            'oauth_client_info': encrypt_data(oauth_client_info.model_dump(mode='json')),
        }
    except Exception as e:
        log.debug(f'Failed to register OAuth client: {e}')
        raise HTTPException(
            status_code=400,
            detail=f'Failed to register OAuth client',
        )


############################
# ToolServers Config
############################


class ToolServerConnection(BaseModel):
    url: str
    path: str
    type: str | None = 'openapi'  # openapi, mcp
    auth_type: str | None
    headers: dict | str | None = None
    key: str | None
    config: dict | None
    info: dict | None = None

    model_config = ConfigDict(extra='allow')


class ToolServersConfigForm(BaseModel):
    TOOL_SERVER_CONNECTIONS: list[ToolServerConnection]


@router.get('/tool_servers', response_model=ToolServersConfigForm)
async def get_tool_servers_config(
    request: Request,
    user=Depends(get_admin_user),
    _=Depends(require_feature('tool_servers')),
):
    return {'TOOL_SERVER_CONNECTIONS': await Config.get('tool_server.connections')}


@router.post('/tool_servers', response_model=ToolServersConfigForm)
async def set_tool_servers_config(
    request: Request,
    form_data: ToolServersConfigForm,
    user=Depends(get_admin_user),
    _=Depends(require_feature('tool_servers')),
):
    existing_connections = await Config.get('tool_server.connections', []) or []
    for connection in existing_connections:
        server_type = connection.get('type', 'openapi')
        auth_type = connection.get('auth_type', 'none')

        if auth_type in ('oauth_2.1', 'oauth_2.1_static'):
            # Remove existing OAuth clients for tool servers
            server_id = (connection.get('info') or {}).get('id')
            client_key = f'{server_type}:{server_id}'

            try:
                request.app.state.oauth_client_manager.remove_client(client_key)
            except Exception:
                pass

    # Set new tool server connections
    connections = [connection.model_dump() for connection in form_data.TOOL_SERVER_CONNECTIONS]
    await Config.upsert({'tool_server.connections': connections})

    await set_tool_servers(request)

    for connection in connections:
        server_type = connection.get('type', 'openapi')
        if server_type == 'mcp':
            server_id = (connection.get('info') or {}).get('id')
            auth_type = connection.get('auth_type', 'none')

            if auth_type in ('oauth_2.1', 'oauth_2.1_static') and server_id:
                try:
                    oauth_client_info = resolve_oauth_client_info(connection)
                    oauth_client_info = await recover_static_oauth_client_metadata(connection, oauth_client_info)
                    oauth_client_info = apply_connection_oauth_options(connection, oauth_client_info)
                    request.app.state.oauth_client_manager.add_client(
                        f'{server_type}:{server_id}',
                        OAuthClientInformationFull(**oauth_client_info),
                    )
                except Exception as e:
                    log.debug(f'Failed to add OAuth client for MCP tool server: {e}')
                    continue

    await publish_event(
        request,
        EVENTS.CONFIG_TOOL_SERVERS_UPDATED,
        actor=user,
        subject_id='tool_server.connections',
        subject_type='config',
        data={'count': len(connections), 'types': [connection.get('type', 'openapi') for connection in connections]},
    )
    return {'TOOL_SERVER_CONNECTIONS': connections}


class TerminalServerConnection(BaseModel):
    id: str | None = ''
    name: str | None = ''

    enabled: bool | None = True

    url: str
    path: str | None = '/openapi.json'

    key: str | None = ''
    auth_type: str | None = 'bearer'

    config: dict | None = None

    # Orchestrator policy fields
    server_type: str | None = None  # "orchestrator", "terminal"
    policy_id: str | None = None
    policy: dict | None = None  # cached policy data

    model_config = ConfigDict(extra='allow')


class TerminalServersConfigForm(BaseModel):
    TERMINAL_SERVER_CONNECTIONS: list[TerminalServerConnection]


@router.get('/terminal_servers')
async def get_terminal_servers_config(
    request: Request,
    user=Depends(get_admin_user),
    _=Depends(require_feature('terminal_servers')),
):
    return {'TERMINAL_SERVER_CONNECTIONS': await Config.get('terminal_server.connections')}


@router.post('/terminal_servers')
async def set_terminal_servers_config(
    request: Request,
    form_data: TerminalServersConfigForm,
    user=Depends(get_admin_user),
    _=Depends(require_feature('terminal_servers')),
):
    connections = [connection.model_dump() for connection in form_data.TERMINAL_SERVER_CONNECTIONS]
    await Config.upsert({'terminal_server.connections': connections})

    await set_terminal_servers(request)

    await publish_event(
        request,
        EVENTS.CONFIG_TERMINAL_SERVERS_UPDATED,
        actor=user,
        subject_id='terminal_server.connections',
        subject_type='config',
        data={'count': len(connections)},
    )
    return {'TERMINAL_SERVER_CONNECTIONS': connections}


@router.post('/terminal_servers/verify')
async def verify_terminal_server_connection(
    request: Request,
    form_data: TerminalServerConnection,
    user=Depends(get_admin_user),
    _=Depends(require_feature('terminal_servers')),
):
    """
    Verify the connection to a terminal server by detecting its type.

    Tries GET {url}/api/v1/policies (orchestrator) then GET {url}/api/config
    (plain terminal).  Returns ``{status: true, type: "orchestrator"|"terminal"}``.
    """
    base_url = (form_data.url or '').rstrip('/')
    if not base_url:
        raise HTTPException(status_code=400, detail='Terminal server URL is required')

    headers = {}
    if form_data.auth_type == 'bearer' and form_data.key:
        headers.update(bearer_auth_header(form_data.key))

    try:
        async with aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
        ) as session:
            # Orchestrators expose a policies API; plain terminals don't.
            try:
                async with session.get(
                    f'{base_url}/api/v1/policies', headers=headers, ssl=AIOHTTP_CLIENT_SESSION_SSL
                ) as resp:
                    if resp.ok:
                        return {'status': True, 'type': 'orchestrator'}
            except Exception:
                pass

            # Fall back to open-terminal config endpoint.
            try:
                async with session.get(
                    f'{base_url}/api/config', headers=headers, ssl=AIOHTTP_CLIENT_SESSION_SSL
                ) as resp:
                    if resp.ok:
                        return {'status': True, 'type': 'terminal'}
            except Exception:
                pass

    except Exception as e:
        log.debug(f'Failed to connect to the terminal server: {e}')

    raise HTTPException(status_code=400, detail='Failed to connect to the terminal server')


class TerminalServerPolicyForm(BaseModel):
    url: str
    key: str | None = ''
    auth_type: str | None = 'bearer'
    policy_id: str
    policy_data: dict


class TerminalServerLifecycleForm(BaseModel):
    url: str
    key: str | None = ''
    auth_type: str | None = 'bearer'
    policy_id: str
    lifecycle_data: dict


class TerminalServerRefreshForm(BaseModel):
    url: str
    key: str | None = ''
    auth_type: str | None = 'bearer'
    user_id: str | None = None
    policy_id: str | None = None
    only_idle: bool = True
    reset: bool = False


@router.post('/terminal_servers/policy')
async def put_terminal_server_policy(
    request: Request,
    form_data: TerminalServerPolicyForm,
    user=Depends(get_admin_user),
    _=Depends(require_feature('terminal_servers')),
):
    """
    Proxy a policy PUT to an orchestrator terminal server.
    """
    base_url = (form_data.url or '').rstrip('/')
    if not base_url:
        raise HTTPException(status_code=400, detail='Terminal server URL is required')

    headers = {'Content-Type': 'application/json'}
    if form_data.auth_type == 'bearer' and form_data.key:
        headers.update(bearer_auth_header(form_data.key))

    try:
        async with aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
        ) as session:
            policy_url = f'{base_url}/api/v1/policies/{form_data.policy_id}'
            async with session.put(
                policy_url, headers=headers, json=form_data.policy_data, ssl=AIOHTTP_CLIENT_SESSION_SSL
            ) as resp:
                if resp.ok:
                    return await resp.json()
                detail = await resp.text()
                raise HTTPException(status_code=resp.status, detail=detail)
    except HTTPException:
        raise
    except Exception as e:
        log.debug(f'Failed to save policy to terminal server: {e}')
        raise HTTPException(status_code=400, detail='Failed to save policy to terminal server')


@router.post('/terminal_servers/lifecycle')
async def put_terminal_server_lifecycle(
    request: Request,
    form_data: TerminalServerLifecycleForm,
    user=Depends(get_admin_user),
    _=Depends(require_feature('terminal_servers')),
):
    """
    Proxy a policy lifecycle PUT to an orchestrator terminal server.
    """
    base_url = (form_data.url or '').rstrip('/')
    if not base_url:
        raise HTTPException(status_code=400, detail='Terminal server URL is required')

    headers = {'Content-Type': 'application/json'}
    if form_data.auth_type == 'bearer' and form_data.key:
        headers.update(bearer_auth_header(form_data.key))

    try:
        async with aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
        ) as session:
            lifecycle_url = f'{base_url}/api/v1/policies/{form_data.policy_id}/lifecycle'
            async with session.put(
                lifecycle_url,
                headers=headers,
                json=form_data.lifecycle_data,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.ok:
                    return await resp.json()
                detail = await resp.text()
                raise HTTPException(status_code=resp.status, detail=detail)
    except HTTPException:
        raise
    except Exception as e:
        log.debug(f'Failed to save lifecycle to terminal server: {e}')
        raise HTTPException(status_code=400, detail='Failed to save lifecycle to terminal server')


@router.post('/terminal_servers/refresh')
async def refresh_terminal_server_terminals(
    request: Request,
    form_data: TerminalServerRefreshForm,
    user=Depends(get_admin_user),
    _=Depends(require_feature('terminal_servers')),
):
    """
    Proxy a terminal refresh request to an orchestrator terminal server.
    """
    base_url = (form_data.url or '').rstrip('/')
    if not base_url:
        raise HTTPException(status_code=400, detail='Terminal server URL is required')

    headers = {'Content-Type': 'application/json'}
    if form_data.auth_type == 'bearer' and form_data.key:
        headers.update(bearer_auth_header(form_data.key))

    body = {
        'only_idle': form_data.only_idle,
        'reset': form_data.reset,
    }
    if form_data.user_id:
        body['user_id'] = form_data.user_id
    if form_data.policy_id:
        body['policy_id'] = form_data.policy_id

    try:
        async with aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
        ) as session:
            refresh_url = f'{base_url}/api/v1/terminals/refresh'
            async with session.post(
                refresh_url,
                headers=headers,
                json=body,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.ok:
                    return await resp.json()
                detail = await resp.text()
                raise HTTPException(status_code=resp.status, detail=detail)
    except HTTPException:
        raise
    except Exception as e:
        log.debug(f'Failed to refresh terminals: {e}')
        raise HTTPException(status_code=400, detail='Failed to refresh terminals')


@router.post('/tool_servers/verify')
async def verify_tool_servers_config(
    request: Request,
    form_data: ToolServerConnection,
    user=Depends(get_admin_user),
    _=Depends(require_feature('tool_servers')),
):
    """
    Verify the connection to the tool server.
    """
    try:
        if form_data.type == 'mcp':
            if form_data.auth_type in ('oauth_2.1', 'oauth_2.1_static'):
                oauth_server_url = (
                    form_data.info.get('oauth_server_url')
                    if form_data.info and form_data.info.get('oauth_server_url')
                    else form_data.url
                )
                discovery_urls = await get_discovery_urls(oauth_server_url)
                for discovery_url in discovery_urls:
                    log.debug(f'Trying to fetch OAuth 2.1 discovery document from {discovery_url}')
                    async with aiohttp.ClientSession(
                        trust_env=True,
                        timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
                    ) as session:
                        async with session.get(
                            discovery_url, ssl=AIOHTTP_CLIENT_SESSION_SSL
                        ) as oauth_server_metadata_response:
                            if oauth_server_metadata_response.status == 200:
                                try:
                                    oauth_server_metadata = OAuthMetadata.model_validate(
                                        await oauth_server_metadata_response.json()
                                    )
                                    return {
                                        'status': True,
                                        'oauth_server_metadata': oauth_server_metadata.model_dump(mode='json'),
                                    }
                                except Exception as e:
                                    log.info(f'Failed to parse OAuth 2.1 discovery document: {e}')
                                    raise HTTPException(
                                        status_code=400,
                                        detail=f'Failed to parse OAuth 2.1 discovery document from {discovery_url}',
                                    )

                raise HTTPException(
                    status_code=400,
                    detail=f'Failed to fetch OAuth 2.1 discovery document from {discovery_urls}',
                )
            else:
                try:
                    client = MCPClient()
                    headers = None

                    token = None
                    if form_data.auth_type == 'bearer':
                        token = form_data.key
                    elif form_data.auth_type == 'session':
                        token = request.state.token.credentials
                    elif form_data.auth_type == 'system_oauth':
                        oauth_token = None
                        try:
                            if request.cookies.get('oauth_session_id', None):
                                oauth_token = await request.app.state.oauth_manager.get_oauth_token(
                                    user.id,
                                    request.cookies.get('oauth_session_id', None),
                                )

                                if oauth_token:
                                    token = oauth_token.get('access_token', '')
                        except Exception as e:
                            pass
                    if token:
                        headers = {'Authorization': f'Bearer {token}'}

                    if form_data.headers and isinstance(form_data.headers, dict):
                        if headers is None:
                            headers = {}
                        custom_headers = get_custom_headers(form_data.headers, user)
                        headers.update(custom_headers)

                    await client.connect(form_data.url, headers=headers)
                    specs = await client.list_tool_specs()
                    return {
                        'status': True,
                        'specs': specs,
                    }
                except Exception as e:
                    log.debug(f'Failed to create MCP client: {e}')
                    raise HTTPException(
                        status_code=400,
                        detail=f'Failed to create MCP client',
                    )
                finally:
                    if client:
                        await client.disconnect()
        else:  # openapi
            token = None
            headers = None
            if form_data.auth_type == 'bearer':
                token = form_data.key
            elif form_data.auth_type == 'session':
                token = request.state.token.credentials
            elif form_data.auth_type == 'system_oauth':
                try:
                    if request.cookies.get('oauth_session_id', None):
                        oauth_token = await request.app.state.oauth_manager.get_oauth_token(
                            user.id,
                            request.cookies.get('oauth_session_id', None),
                        )

                        if oauth_token:
                            token = oauth_token.get('access_token', '')

                except Exception as e:
                    pass

            if token:
                headers = {'Authorization': f'Bearer {token}'}

            if form_data.headers and isinstance(form_data.headers, dict):
                if headers is None:
                    headers = {}
                custom_headers = get_custom_headers(form_data.headers, user)
                headers.update(custom_headers)

            url = get_tool_server_url(form_data.url, form_data.path)
            return await get_tool_server_data(url, headers=headers)
    except HTTPException as e:
        raise e
    except Exception as e:
        log.debug(f'Failed to connect to the tool server: {e}')
        raise HTTPException(
            status_code=400,
            detail=f'Failed to connect to the tool server',
        )


############################
# CodeInterpreterConfig
############################
class CodeInterpreterConfigForm(BaseModel):
    ENABLE_CODE_EXECUTION: bool
    CODE_EXECUTION_ENGINE: str
    CODE_EXECUTION_JUPYTER_URL: str | None
    CODE_EXECUTION_JUPYTER_AUTH: str | None
    CODE_EXECUTION_JUPYTER_AUTH_TOKEN: str | None
    CODE_EXECUTION_JUPYTER_AUTH_PASSWORD: str | None
    CODE_EXECUTION_JUPYTER_TIMEOUT: int | None
    ENABLE_CODE_INTERPRETER: bool
    CODE_INTERPRETER_ENGINE: str
    CODE_INTERPRETER_PROMPT_TEMPLATE: str | None
    CODE_INTERPRETER_JUPYTER_URL: str | None
    CODE_INTERPRETER_JUPYTER_AUTH: str | None
    CODE_INTERPRETER_JUPYTER_AUTH_TOKEN: str | None
    CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD: str | None
    CODE_INTERPRETER_JUPYTER_TIMEOUT: int | None


@router.get('/code_execution', response_model=CodeInterpreterConfigForm)
async def get_code_execution_config(request: Request, user=Depends(get_admin_user)):
    return await get_config_values(CODE_EXECUTION_CONFIG_KEYS)


@router.post('/code_execution', response_model=CodeInterpreterConfigForm)
async def set_code_execution_config(
    request: Request, form_data: CodeInterpreterConfigForm, user=Depends(get_admin_user)
):
    await Config.upsert(config_updates(form_data.model_dump(), CODE_EXECUTION_CONFIG_KEYS))
    values = await get_config_values(CODE_EXECUTION_CONFIG_KEYS)
    await publish_event(
        request,
        EVENTS.CONFIG_CODE_EXECUTION_UPDATED,
        actor=user,
        subject_id='code_execution',
        subject_type='config',
        data={
            'code_execution_enabled': values.get('ENABLE_CODE_EXECUTION'),
            'code_execution_engine': values.get('CODE_EXECUTION_ENGINE'),
            'code_interpreter_enabled': values.get('ENABLE_CODE_INTERPRETER'),
            'code_interpreter_engine': values.get('CODE_INTERPRETER_ENGINE'),
        },
    )
    return values


############################
# SetDefaultModels
############################
class ModelsConfigForm(BaseModel):
    DEFAULT_MODELS: str | None
    DEFAULT_PINNED_MODELS: str | None
    MODEL_ORDER_LIST: list[str | None]
    DEFAULT_MODEL_METADATA: dict | None = None
    DEFAULT_MODEL_PARAMS: dict | None = None


@router.get('/models/defaults')
async def get_models_defaults(request: Request, user=Depends(get_verified_user)):
    return {
        'DEFAULT_MODEL_METADATA': await Config.get('models.default_metadata'),
    }


@router.get('/models', response_model=ModelsConfigForm)
async def get_models_config(request: Request, user=Depends(get_admin_user)):
    return await get_config_values(MODELS_CONFIG_KEYS)


@router.post('/models', response_model=ModelsConfigForm)
async def set_models_config(request: Request, form_data: ModelsConfigForm, user=Depends(get_admin_user)):
    await Config.upsert(config_updates(form_data.model_dump(), MODELS_CONFIG_KEYS))
    values = await get_config_values(MODELS_CONFIG_KEYS)
    await publish_event(
        request,
        EVENTS.CONFIG_MODELS_UPDATED,
        actor=user,
        subject_id='models',
        subject_type='config',
        data={
            'default_models': values.get('DEFAULT_MODELS'),
            'default_pinned_models': values.get('DEFAULT_PINNED_MODELS'),
            'model_order_count': len(values.get('MODEL_ORDER_LIST') or []),
        },
    )
    return values


class PromptSuggestion(BaseModel):
    title: list[str]
    content: str


class SetDefaultSuggestionsForm(BaseModel):
    suggestions: list[PromptSuggestion]


@router.post('/suggestions', response_model=list[PromptSuggestion])
async def set_default_suggestions(
    request: Request,
    form_data: SetDefaultSuggestionsForm,
    user=Depends(get_admin_user),
):
    data = form_data.model_dump()
    await Config.upsert({'ui.prompt_suggestions': data['suggestions']})
    suggestions = await Config.get('ui.prompt_suggestions')
    await publish_event(
        request,
        EVENTS.CONFIG_SUGGESTIONS_UPDATED,
        actor=user,
        subject_id='ui.prompt_suggestions',
        subject_type='config',
        data={'count': len(suggestions or [])},
    )
    return suggestions


############################
# SetBanners
############################


class SetBannersForm(BaseModel):
    banners: list[BannerModel]


@router.post('/banners', response_model=list[BannerModel])
async def set_banners(
    request: Request,
    form_data: SetBannersForm,
    user=Depends(get_admin_user),
):
    data = form_data.model_dump()
    await Config.upsert({'ui.banners': data['banners']})
    banners = await Config.get('ui.banners')
    await publish_event(
        request,
        EVENTS.CONFIG_BANNERS_UPDATED,
        actor=user,
        subject_id='ui.banners',
        subject_type='config',
        data={'count': len(banners or [])},
    )
    return banners


@router.get('/banners', response_model=list[BannerModel])
async def get_banners(
    request: Request,
    user=Depends(get_verified_user),
):
    return await Config.get('ui.banners')


############################
# GreetingTemplate
############################


class SetGreetingTemplateForm(BaseModel):
    # Either a plain string (legacy) or a mapping of locale code to template.
    template: Union[str, dict[str, str]]


@router.post('/greeting_template')
async def set_greeting_template(
    request: Request,
    form_data: SetGreetingTemplateForm,
    user=Depends(get_admin_user),
):
    await Config.upsert({'ui.greeting_template': form_data.template})
    return {'template': await Config.get('ui.greeting_template')}


@router.get('/greeting_template')
async def get_greeting_template(
    request: Request,
    user=Depends(get_verified_user),
):
    return {'template': await Config.get('ui.greeting_template')}


############################
# InviteContent
############################


INVITE_CONTENT_CONFIG_KEYS = {
    'subject': 'email.invite_subject',
    'heading': 'email.invite_heading',
}


class InviteContentForm(BaseModel):
    subject: str = ''
    heading: str = ''


@router.get('/invite_content')
async def get_invite_content(request: Request, user=Depends(get_admin_user)):
    return await get_config_values(INVITE_CONTENT_CONFIG_KEYS)


@router.post('/invite_content')
async def set_invite_content(
    request: Request,
    form_data: InviteContentForm,
    user=Depends(get_admin_user),
):
    await Config.upsert(config_updates(form_data.model_dump(), INVITE_CONTENT_CONFIG_KEYS))
    return await get_config_values(INVITE_CONTENT_CONFIG_KEYS)


############################
# EmailConfig
############################


EMAIL_CONFIG_KEYS = {
    'ENABLE_EMAIL_INVITES': 'email.enable_invites',
    'EMAIL_FROM_ADDRESS': 'email.from_address',
    'EMAIL_FROM_NAME': 'email.from_name',
    'INVITE_EXPIRY_HOURS': 'email.invite_expiry_hours',
}


class EmailConfigForm(BaseModel):
    ENABLE_EMAIL_INVITES: bool
    EMAIL_FROM_ADDRESS: str
    EMAIL_FROM_NAME: str
    INVITE_EXPIRY_HOURS: int


@router.get('/email', response_model=EmailConfigForm)
async def get_email_config(request: Request, user=Depends(get_admin_user)):
    return await get_config_values(EMAIL_CONFIG_KEYS)


@router.post('/email', response_model=EmailConfigForm)
async def set_email_config(
    request: Request,
    form_data: EmailConfigForm,
    user=Depends(get_admin_user),
):
    await Config.upsert(config_updates(form_data.model_dump(), EMAIL_CONFIG_KEYS))
    return await get_config_values(EMAIL_CONFIG_KEYS)


@router.post('/email/test')
async def test_email_config(request: Request, user=Depends(get_admin_user)):
    """Send a test email to the admin's own address."""
    values = await Config.get_many('email.enable_invites', 'email.from_name')
    if not values.get('email.enable_invites'):
        raise HTTPException(400, detail='Email invites are not enabled')

    try:
        from open_webui.services.email.graph_mail_client import send_mail

        from_name = values.get('email.from_name')
        await send_mail(
            app=request.app,
            to_address=user.email,
            subject=f'Test email from {from_name}',
            html_body='<p>This is a test email. Your email configuration is working correctly.</p>',
        )
        return {'status': 'ok', 'message': f'Test email sent to {user.email}'}
    except Exception as e:
        raise HTTPException(500, detail=f'Failed to send test email: {str(e)}')


############################
# Integrations Config
############################


class IntegrationsConfigForm(BaseModel):
    providers: dict


async def _bind_service_account(user_id: str, provider_slug: str):
    """Set user.info.integration_provider on the service account."""
    user = await Users.get_user_by_id(user_id)
    if not user:
        return
    info = dict(user.info) if user.info else {}
    info['integration_provider'] = provider_slug
    await Users.update_user_by_id(user_id, {'info': info})


async def _unbind_service_account(user_id: str):
    """Clear user.info.integration_provider."""
    user = await Users.get_user_by_id(user_id)
    if not user:
        return
    info = dict(user.info) if user.info else {}
    info.pop('integration_provider', None)
    await Users.update_user_by_id(user_id, {'info': info})


@router.get('/integrations')
async def get_integrations_config(request: Request, user=Depends(get_admin_user)):
    return {
        'providers': await Config.get('integrations.providers'),
    }


@router.post('/integrations')
async def set_integrations_config(
    request: Request,
    form_data: IntegrationsConfigForm,
    user=Depends(get_admin_user),
):
    old_providers = await Config.get('integrations.providers') or {}

    # Unbind service accounts that were removed or changed
    for slug, old_provider in old_providers.items():
        old_sa = old_provider.get('service_account_id')
        if not old_sa:
            continue
        new_provider = form_data.providers.get(slug)
        new_sa = new_provider.get('service_account_id') if new_provider else None
        if old_sa != new_sa:
            await _unbind_service_account(old_sa)

    # Save new config
    await Config.upsert({'integrations.providers': form_data.providers})

    # Bind new service accounts
    for slug, provider in form_data.providers.items():
        sa_id = provider.get('service_account_id')
        if sa_id:
            await _bind_service_account(sa_id, slug)

    return {'providers': await Config.get('integrations.providers')}


####################################
# Agent Proxy Config
####################################


AGENT_PROXY_CONFIG_KEYS = {
    'ENABLE_AGENT_PROXY': 'agent_proxy.enable',
}


class AgentProxyConfigForm(BaseModel):
    ENABLE_AGENT_PROXY: bool


@router.get('/agent_proxy')
async def get_agent_proxy_config(request: Request, user=Depends(get_admin_user)):
    return await get_config_values(AGENT_PROXY_CONFIG_KEYS)


@router.post('/agent_proxy')
async def set_agent_proxy_config(
    request: Request,
    form_data: AgentProxyConfigForm,
    user=Depends(get_admin_user),
):
    await Config.upsert(config_updates(form_data.model_dump(), AGENT_PROXY_CONFIG_KEYS))
    return await get_config_values(AGENT_PROXY_CONFIG_KEYS)


####################################
# Confluence Config
####################################


CONFLUENCE_CONFIG_KEYS = {
    'ENABLE_CONFLUENCE_INTEGRATION': 'confluence.enable',
    'ENABLE_CONFLUENCE_SYNC': 'confluence.enable_sync',
    'CONFLUENCE_OAUTH_CLIENT_ID': 'confluence.client_id',
    'CONFLUENCE_OAUTH_CLIENT_SECRET': 'confluence.client_secret',
    'CONFLUENCE_SYNC_INTERVAL_MINUTES': 'confluence.sync_interval_minutes',
    'CONFLUENCE_MAX_PAGES_PER_SYNC': 'confluence.max_pages_per_sync',
    'CONFLUENCE_AUTH_MODE': 'confluence.auth_mode',
    'CONFLUENCE_SITE_URL': 'confluence.site_url',
    'CONFLUENCE_BASIC_AUTH_USERNAME': 'confluence.basic_auth_username',
    'CONFLUENCE_BASIC_AUTH_API_TOKEN': 'confluence.basic_auth_api_token',
    'CONFLUENCE_SCOPED_API_TOKEN': 'confluence.scoped_api_token',
    'CONFLUENCE_CLOUD_ID': 'confluence.cloud_id',
    'CONFLUENCE_KB_MODE': 'confluence.kb_mode',
}


class ConfluenceConfigForm(BaseModel):
    ENABLE_CONFLUENCE_INTEGRATION: Optional[bool] = None
    ENABLE_CONFLUENCE_SYNC: Optional[bool] = None
    CONFLUENCE_OAUTH_CLIENT_ID: Optional[str] = None
    CONFLUENCE_OAUTH_CLIENT_SECRET: Optional[str] = None
    CONFLUENCE_SYNC_INTERVAL_MINUTES: Optional[int] = None
    CONFLUENCE_MAX_PAGES_PER_SYNC: Optional[int] = None  # 0 = unlimited
    # auth_mode is 'oauth', 'basic' (classic API token → site) or 'scoped'
    # (scoped API token → Atlassian gateway). 'basic' and 'scoped' share the
    # site URL + username fields; only the token field differs.
    CONFLUENCE_AUTH_MODE: Optional[str] = None
    CONFLUENCE_SITE_URL: Optional[str] = None
    CONFLUENCE_BASIC_AUTH_USERNAME: Optional[str] = None
    CONFLUENCE_BASIC_AUTH_API_TOKEN: Optional[str] = None
    # Scoped-mode credential + the gateway cloudId (optional; auto-resolved from
    # the site URL when blank).
    CONFLUENCE_SCOPED_API_TOKEN: Optional[str] = None
    CONFLUENCE_CLOUD_ID: Optional[str] = None
    # Sharing mode: 'per_user' or 'shared'. The shared-KB owner lives on the
    # KB row (``kb.user_id``); the provision form sets it, not this config.
    CONFLUENCE_KB_MODE: Optional[str] = None


@router.get('/confluence')
async def get_confluence_config(request: Request, user=Depends(get_admin_user)):
    # Admin-only endpoint; secrets (client secret, basic/scoped tokens) round-trip
    # in full, masked behind a reveal toggle in the UI — same disclosure profile
    # as the upstream Connections (API key) form.
    return await get_config_values(CONFLUENCE_CONFIG_KEYS)


@router.post('/confluence')
async def set_confluence_config(
    request: Request,
    form_data: ConfluenceConfigForm,
    user=Depends(get_admin_user),
):
    current = await get_config_values(CONFLUENCE_CONFIG_KEYS)

    # Compute the effective auth/KB modes up front, applying the coupling
    # ``(basic|scoped) ⇒ shared``: the service-account modes have no per-user
    # OAuth tokens, so they can only drive the pre-synced shared KB. The admin
    # form enforces this too, but a stored ``basic + per_user`` state would
    # otherwise leak through to /api/config and mislead the chat '+' menu. Both
    # the orphan-guard below and the persisted values use these effective modes.
    if form_data.CONFLUENCE_AUTH_MODE is not None:
        # Guard against arbitrary values; only the three known modes are valid.
        _auth = form_data.CONFLUENCE_AUTH_MODE.strip()
        effective_auth = _auth if _auth in ('oauth', 'basic', 'scoped') else 'oauth'
    else:
        effective_auth = current.get('CONFLUENCE_AUTH_MODE')
    if form_data.CONFLUENCE_KB_MODE is not None:
        # Guard against arbitrary values; only the two known modes are valid.
        _kb = form_data.CONFLUENCE_KB_MODE.strip()
        requested_kb = _kb if _kb in ('per_user', 'shared') else 'per_user'
    else:
        requested_kb = current.get('CONFLUENCE_KB_MODE')
    effective_kb = 'shared' if effective_auth in ('basic', 'scoped') else requested_kb

    # Two switches must not silently strand or corrupt an existing shared KB; both
    # require the admin to delete it first (a config write has no KB lifecycle of
    # its own). Look the KB up once and gate on it:
    #   1. Switching the AUTH METHOD (basic ↔ oauth): the KB's pages were gathered
    #      under one identity's permissions; re-syncing under a different identity
    #      would silently change/leak content (mixing auth identities). Block it.
    #   2. Switching the SYNC MODE away from shared (→ per_user): the toggle would
    #      orphan the KB. Uses the effective mode, so a basic-auth save (forced to
    #      shared) never trips it.
    auth_changing = effective_auth != current.get('CONFLUENCE_AUTH_MODE')
    if auth_changing or effective_kb != 'shared':
        from open_webui.services.sync.shared_kb import find_shared_kb

        shared_kb = await find_shared_kb('confluence', 'confluence_sync')
        if shared_kb is not None:
            if auth_changing:
                raise HTTPException(
                    status_code=400,
                    detail='Delete the shared Confluence knowledge base before switching authentication method.',
                )
            raise HTTPException(
                status_code=400,
                detail='Delete the shared Confluence knowledge base before switching to on-request (per-user) mode.',
            )

    updates: dict = {}
    if form_data.ENABLE_CONFLUENCE_INTEGRATION is not None:
        updates['confluence.enable'] = form_data.ENABLE_CONFLUENCE_INTEGRATION
    if form_data.ENABLE_CONFLUENCE_SYNC is not None:
        updates['confluence.enable_sync'] = form_data.ENABLE_CONFLUENCE_SYNC
    if form_data.CONFLUENCE_OAUTH_CLIENT_ID is not None:
        updates['confluence.client_id'] = form_data.CONFLUENCE_OAUTH_CLIENT_ID.strip()
    if form_data.CONFLUENCE_OAUTH_CLIENT_SECRET is not None:
        updates['confluence.client_secret'] = form_data.CONFLUENCE_OAUTH_CLIENT_SECRET.strip()
    if form_data.CONFLUENCE_SYNC_INTERVAL_MINUTES is not None:
        updates['confluence.sync_interval_minutes'] = form_data.CONFLUENCE_SYNC_INTERVAL_MINUTES
    if form_data.CONFLUENCE_MAX_PAGES_PER_SYNC is not None:
        updates['confluence.max_pages_per_sync'] = max(0, form_data.CONFLUENCE_MAX_PAGES_PER_SYNC)
    if form_data.CONFLUENCE_SITE_URL is not None:
        # Normalize to scheme://host so an admin-pasted '/wiki' suffix or deep
        # link can't double up into '.../wiki/wiki/api/v2/...' and 404.
        from open_webui.services.confluence.confluence_client import normalize_site_url

        updates['confluence.site_url'] = normalize_site_url(form_data.CONFLUENCE_SITE_URL)
    if form_data.CONFLUENCE_BASIC_AUTH_USERNAME is not None:
        updates['confluence.basic_auth_username'] = form_data.CONFLUENCE_BASIC_AUTH_USERNAME.strip()
    if form_data.CONFLUENCE_BASIC_AUTH_API_TOKEN is not None:
        updates['confluence.basic_auth_api_token'] = form_data.CONFLUENCE_BASIC_AUTH_API_TOKEN.strip()
    if form_data.CONFLUENCE_SCOPED_API_TOKEN is not None:
        updates['confluence.scoped_api_token'] = form_data.CONFLUENCE_SCOPED_API_TOKEN.strip()
    if form_data.CONFLUENCE_CLOUD_ID is not None:
        updates['confluence.cloud_id'] = form_data.CONFLUENCE_CLOUD_ID.strip()
    # Persist the coupled effective modes so the stored state is self-consistent.
    updates['confluence.auth_mode'] = effective_auth
    updates['confluence.kb_mode'] = effective_kb

    await Config.upsert(updates)
    return await get_confluence_config(request, user)


####################################
# Google Drive Config
####################################


GOOGLE_DRIVE_CONFIG_KEYS = {
    'ENABLE_GOOGLE_DRIVE_INTEGRATION': 'google_drive.enable',
    'ENABLE_GOOGLE_DRIVE_SYNC': 'google_drive.enable_sync',
    'GOOGLE_DRIVE_CLIENT_ID': 'google_drive.client_id',
    'GOOGLE_DRIVE_API_KEY': 'google_drive.api_key',
    'GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES': 'google_drive.sync_interval_minutes',
    'GOOGLE_DRIVE_MAX_FILES_PER_SYNC': 'google_drive.max_files_per_sync',
}


class GoogleDriveConfigForm(BaseModel):
    ENABLE_GOOGLE_DRIVE_INTEGRATION: Optional[bool] = None
    ENABLE_GOOGLE_DRIVE_SYNC: Optional[bool] = None
    GOOGLE_DRIVE_CLIENT_ID: Optional[str] = None
    GOOGLE_DRIVE_API_KEY: Optional[str] = None
    GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES: Optional[int] = None
    GOOGLE_DRIVE_MAX_FILES_PER_SYNC: Optional[int] = None  # 0 = unlimited


@router.get('/google_drive')
async def get_google_drive_config(request: Request, user=Depends(get_admin_user)):
    # Client ID + API key are PKCE-public (used browser-side by the Picker),
    # so they are returned in full — there is no secret to mask.
    return await get_config_values(GOOGLE_DRIVE_CONFIG_KEYS)


@router.post('/google_drive')
async def set_google_drive_config(
    request: Request,
    form_data: GoogleDriveConfigForm,
    user=Depends(get_admin_user),
):
    updates: dict = {}
    if form_data.ENABLE_GOOGLE_DRIVE_INTEGRATION is not None:
        updates['google_drive.enable'] = form_data.ENABLE_GOOGLE_DRIVE_INTEGRATION
    if form_data.ENABLE_GOOGLE_DRIVE_SYNC is not None:
        updates['google_drive.enable_sync'] = form_data.ENABLE_GOOGLE_DRIVE_SYNC
    if form_data.GOOGLE_DRIVE_CLIENT_ID is not None:
        updates['google_drive.client_id'] = form_data.GOOGLE_DRIVE_CLIENT_ID.strip()
    if form_data.GOOGLE_DRIVE_API_KEY is not None:
        updates['google_drive.api_key'] = form_data.GOOGLE_DRIVE_API_KEY.strip()
    if form_data.GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES is not None:
        updates['google_drive.sync_interval_minutes'] = form_data.GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES
    if form_data.GOOGLE_DRIVE_MAX_FILES_PER_SYNC is not None:
        updates['google_drive.max_files_per_sync'] = max(0, form_data.GOOGLE_DRIVE_MAX_FILES_PER_SYNC)
    await Config.upsert(updates)
    return await get_google_drive_config(request, user)


####################################
# OneDrive Config
####################################


ONEDRIVE_CONFIG_KEYS = {
    'ENABLE_ONEDRIVE_INTEGRATION': 'onedrive.enable',
    'ENABLE_ONEDRIVE_SYNC': 'onedrive.enable_sync',
    'ENABLE_ONEDRIVE_PERSONAL': 'onedrive.enable_personal',
    'ENABLE_ONEDRIVE_BUSINESS': 'onedrive.enable_business',
    'ONEDRIVE_CLIENT_ID_PERSONAL': 'onedrive.client_id_personal',
    'ONEDRIVE_CLIENT_ID_BUSINESS': 'onedrive.client_id_business',
    'ONEDRIVE_SHAREPOINT_URL': 'onedrive.sharepoint_url',
    'ONEDRIVE_SHAREPOINT_TENANT_ID': 'onedrive.sharepoint_tenant_id',
    'ONEDRIVE_SYNC_INTERVAL_MINUTES': 'onedrive.sync_interval_minutes',
    'ONEDRIVE_MAX_FILES_PER_SYNC': 'onedrive.max_files_per_sync',
}


class OneDriveConfigForm(BaseModel):
    ENABLE_ONEDRIVE_INTEGRATION: Optional[bool] = None
    ENABLE_ONEDRIVE_SYNC: Optional[bool] = None
    ENABLE_ONEDRIVE_PERSONAL: Optional[bool] = None
    ENABLE_ONEDRIVE_BUSINESS: Optional[bool] = None
    ONEDRIVE_CLIENT_ID_PERSONAL: Optional[str] = None
    ONEDRIVE_CLIENT_ID_BUSINESS: Optional[str] = None
    ONEDRIVE_SHAREPOINT_URL: Optional[str] = None
    ONEDRIVE_SHAREPOINT_TENANT_ID: Optional[str] = None
    ONEDRIVE_SYNC_INTERVAL_MINUTES: Optional[int] = None
    ONEDRIVE_MAX_FILES_PER_SYNC: Optional[int] = None  # 0 = unlimited


@router.get('/onedrive')
async def get_onedrive_config(request: Request, user=Depends(get_admin_user)):
    # OneDrive uses public (PKCE) app registrations — client IDs are not
    # secret and are returned in full.
    return await get_config_values(ONEDRIVE_CONFIG_KEYS)


@router.post('/onedrive')
async def set_onedrive_config(
    request: Request,
    form_data: OneDriveConfigForm,
    user=Depends(get_admin_user),
):
    updates: dict = {}
    if form_data.ENABLE_ONEDRIVE_INTEGRATION is not None:
        updates['onedrive.enable'] = form_data.ENABLE_ONEDRIVE_INTEGRATION
    if form_data.ENABLE_ONEDRIVE_SYNC is not None:
        updates['onedrive.enable_sync'] = form_data.ENABLE_ONEDRIVE_SYNC
    if form_data.ENABLE_ONEDRIVE_PERSONAL is not None:
        updates['onedrive.enable_personal'] = form_data.ENABLE_ONEDRIVE_PERSONAL
    if form_data.ENABLE_ONEDRIVE_BUSINESS is not None:
        updates['onedrive.enable_business'] = form_data.ENABLE_ONEDRIVE_BUSINESS
    if form_data.ONEDRIVE_CLIENT_ID_PERSONAL is not None:
        updates['onedrive.client_id_personal'] = form_data.ONEDRIVE_CLIENT_ID_PERSONAL.strip()
    if form_data.ONEDRIVE_CLIENT_ID_BUSINESS is not None:
        updates['onedrive.client_id_business'] = form_data.ONEDRIVE_CLIENT_ID_BUSINESS.strip()
    if form_data.ONEDRIVE_SHAREPOINT_URL is not None:
        updates['onedrive.sharepoint_url'] = form_data.ONEDRIVE_SHAREPOINT_URL.strip()
    if form_data.ONEDRIVE_SHAREPOINT_TENANT_ID is not None:
        updates['onedrive.sharepoint_tenant_id'] = form_data.ONEDRIVE_SHAREPOINT_TENANT_ID.strip()
    if form_data.ONEDRIVE_SYNC_INTERVAL_MINUTES is not None:
        updates['onedrive.sync_interval_minutes'] = form_data.ONEDRIVE_SYNC_INTERVAL_MINUTES
    if form_data.ONEDRIVE_MAX_FILES_PER_SYNC is not None:
        updates['onedrive.max_files_per_sync'] = max(0, form_data.ONEDRIVE_MAX_FILES_PER_SYNC)
    await Config.upsert(updates)
    return await get_onedrive_config(request, user)


####################################
# Cloud Sync — cross-provider status
####################################


# Provider registry for the status endpoint. Each entry maps a provider slug
# to its Knowledge ``type`` value and the meta key its sync worker writes
# under (see the workers' ``meta_key`` property — onedrive: 'onedrive_sync',
# google_drive: 'google_drive_sync', confluence: 'confluence_sync'). Adding a
# new provider is a one-line entry here.
CLOUD_SYNC_PROVIDERS: list[dict] = [
    {'slug': 'confluence', 'type': 'confluence', 'meta_key': 'confluence_sync'},
    {'slug': 'google_drive', 'type': 'google_drive', 'meta_key': 'google_drive_sync'},
    {'slug': 'onedrive', 'type': 'onedrive', 'meta_key': 'onedrive_sync'},
]

# Sync-worker status values (see base_worker / per-provider workers) that mean
# "a sync is currently in flight". Everything else (completed*, failed,
# cancelled, suspended, or absent) is treated as idle.
_CLOUD_SYNC_IN_PROGRESS_STATUSES: frozenset[str] = frozenset({'syncing'})


def _aggregate_provider_status(sync_infos: list[dict]) -> dict:
    """Aggregate per-KB sync meta into a single provider status summary.

    ``sync_infos`` is the list of ``meta[<meta_key>]`` dicts for every KB of
    one provider, each annotated with the KB's file count under ``_file_count``
    (see caller). Pure function so it can be unit-tested without a database.
    """
    kb_count = len(sync_infos)
    file_count = sum(int(info.get('_file_count', 0) or 0) for info in sync_infos)
    last_sync_at = None
    suspended_count = 0
    syncing = False
    shared = False

    for info in sync_infos:
        ts = info.get('last_sync_at')
        if isinstance(ts, int) and (last_sync_at is None or ts > last_sync_at):
            last_sync_at = ts
        if info.get('suspended_at'):
            suspended_count += 1
        if info.get('status') in _CLOUD_SYNC_IN_PROGRESS_STATUSES:
            syncing = True
        if info.get('shared'):
            shared = True

    return {
        'kb_count': kb_count,
        'file_count': file_count,
        'last_sync_at': last_sync_at,
        'status': 'syncing' if syncing else 'idle',
        'syncing': syncing,
        'suspended_count': suspended_count,
        'shared': shared,
    }


@router.get('/cloud-sync/status')
async def get_cloud_sync_status(request: Request, user=Depends(get_admin_user)):
    """Per-provider cloud-sync status summary for the admin Cloud Sync panel.

    Cheap (one KB query per provider + one grouped file-count query) so it can
    be polled while syncs run. Providers with no KBs return zero-state entries.
    """
    from open_webui.models.knowledge import Knowledges

    status: dict[str, dict] = {}

    for provider in CLOUD_SYNC_PROVIDERS:
        slug = provider['slug']
        meta_key = provider['meta_key']

        knowledge_bases = await Knowledges.get_knowledge_bases_by_type(provider['type'])
        file_counts = await Knowledges.get_file_counts_by_knowledge_ids([kb.id for kb in knowledge_bases])

        sync_infos: list[dict] = []
        for kb in knowledge_bases:
            info = dict((kb.meta or {}).get(meta_key, {}) or {})
            info['_file_count'] = file_counts.get(kb.id, 0)
            sync_infos.append(info)

        status[slug] = _aggregate_provider_status(sync_infos)

    return status


####################################
# External Agents Config
####################################


class ExternalAgentsConfigForm(BaseModel):
    AGENT_API_SELECTED_AGENT: str
    # Empty string clears the picker default (no pre-selection on first load).
    AGENT_API_PICKER_DEFAULT_SLUG: Optional[str] = None


@router.get('/external_agents')
async def get_external_agents_config(request: Request, user=Depends(get_admin_user)):
    values = await Config.get_many('agent_api.selected_agent', 'agent_api.picker_default_slug')
    return {
        'AGENT_API_ENABLED': AGENT_API_ENABLED,
        'AGENT_API_AGENTS': AGENT_API_AGENTS,
        'AGENT_API_SELECTED_AGENT': values.get('agent_api.selected_agent'),
        'AGENT_API_PICKER_DEFAULT_SLUG': values.get('agent_api.picker_default_slug'),
    }


@router.post('/external_agents')
async def set_external_agents_config(
    request: Request,
    form_data: ExternalAgentsConfigForm,
    user=Depends(get_admin_user),
):
    if not AGENT_API_ENABLED:
        raise HTTPException(status_code=403, detail='Agent API is disabled')

    agents = AGENT_API_AGENTS
    selected = form_data.AGENT_API_SELECTED_AGENT
    if agents and selected not in agents:
        raise HTTPException(
            status_code=400,
            detail='Selected agent is not in the configured AGENT_API_AGENTS list',
        )

    updates: dict = {'agent_api.selected_agent': selected}

    if form_data.AGENT_API_PICKER_DEFAULT_SLUG is not None:
        picker_default = form_data.AGENT_API_PICKER_DEFAULT_SLUG.strip()
        if picker_default and agents and picker_default not in agents:
            raise HTTPException(
                status_code=400,
                detail='Picker default slug is not in the configured AGENT_API_AGENTS list',
            )
        updates['agent_api.picker_default_slug'] = picker_default

    await Config.upsert(updates)
    values = await Config.get_many('agent_api.selected_agent', 'agent_api.picker_default_slug')

    return {
        'AGENT_API_SELECTED_AGENT': values.get('agent_api.selected_agent'),
        'AGENT_API_PICKER_DEFAULT_SLUG': values.get('agent_api.picker_default_slug'),
    }


####################################
# 2FA Config
####################################


TWO_FA_CONFIG_KEYS = {
    'ENABLE_2FA': 'auth.enable_2fa',
    'REQUIRE_2FA': 'auth.require_2fa',
    'TWO_FA_GRACE_PERIOD_DAYS': 'auth.2fa_grace_period_days',
}


class TwoFAConfigForm(BaseModel):
    ENABLE_2FA: bool
    REQUIRE_2FA: bool
    TWO_FA_GRACE_PERIOD_DAYS: int


@router.get('/2fa')
async def get_2fa_config(request: Request, user=Depends(get_admin_user)):
    return await get_config_values(TWO_FA_CONFIG_KEYS)


@router.post('/2fa')
async def set_2fa_config(
    request: Request,
    form_data: TwoFAConfigForm,
    user=Depends(get_admin_user),
):
    await Config.upsert(config_updates(form_data.model_dump(), TWO_FA_CONFIG_KEYS))
    return await get_config_values(TWO_FA_CONFIG_KEYS)


####################################
# Data Retention Config
####################################


DATA_RETENTION_CONFIG_KEYS = {
    'DATA_RETENTION_TTL_DAYS': 'admin.data_retention_ttl_days',
    'USER_INACTIVITY_TTL_DAYS': 'admin.user_inactivity_ttl_days',
    'CHAT_RETENTION_TTL_DAYS': 'admin.chat_retention_ttl_days',
    'KNOWLEDGE_RETENTION_TTL_DAYS': 'admin.knowledge_retention_ttl_days',
    'DATA_RETENTION_WARNING_DAYS': 'admin.data_retention_warning_days',
    'ENABLE_RETENTION_WARNING_EMAIL': 'admin.enable_retention_warning_email',
}


class DataRetentionConfigForm(BaseModel):
    DATA_RETENTION_TTL_DAYS: int
    USER_INACTIVITY_TTL_DAYS: int
    CHAT_RETENTION_TTL_DAYS: int
    KNOWLEDGE_RETENTION_TTL_DAYS: int
    DATA_RETENTION_WARNING_DAYS: int
    ENABLE_RETENTION_WARNING_EMAIL: bool


@router.get('/data-retention')
async def get_data_retention_config(request: Request, user=Depends(get_admin_user)):
    return await get_config_values(DATA_RETENTION_CONFIG_KEYS)


@router.post('/data-retention')
async def set_data_retention_config(
    request: Request,
    form_data: DataRetentionConfigForm,
    user=Depends(get_admin_user),
):
    await Config.upsert(config_updates(form_data.model_dump(), DATA_RETENTION_CONFIG_KEYS))
    return await get_config_values(DATA_RETENTION_CONFIG_KEYS)


@router.post('/data-retention/test')
async def test_data_retention_cleanup(
    request: Request,
    user=Depends(get_admin_user),
):
    """Manually trigger a data retention cleanup cycle (admin only).
    Uses current config values. Does NOT wait for the daily timer."""
    from open_webui.services.retention.service import DataRetentionService

    values = await Config.get_many(
        'admin.data_retention_ttl_days',
        'admin.user_inactivity_ttl_days',
        'admin.chat_retention_ttl_days',
        'admin.knowledge_retention_ttl_days',
        'admin.data_retention_warning_days',
        'admin.enable_retention_warning_email',
        'admin.enable_user_archival',
        'admin.default_archive_retention_days',
    )

    master_ttl = values.get('admin.data_retention_ttl_days')
    if master_ttl is None or master_ttl <= 0:
        return {
            'status': 'skipped',
            'message': 'Data retention is disabled (DATA_RETENTION_TTL_DAYS=0)',
        }

    report = await DataRetentionService.run_cleanup(
        app=request.app,
        master_ttl=master_ttl,
        user_inactivity_ttl=values.get('admin.user_inactivity_ttl_days'),
        chat_ttl=values.get('admin.chat_retention_ttl_days'),
        knowledge_ttl=values.get('admin.knowledge_retention_ttl_days'),
        warning_days=values.get('admin.data_retention_warning_days'),
        enable_warning_email=values.get('admin.enable_retention_warning_email'),
        enable_archival=values.get('admin.enable_user_archival'),
        archive_retention_days=values.get('admin.default_archive_retention_days'),
    )

    return {
        'status': 'completed',
        'warnings_sent': report.warnings_sent,
        'users_deleted': report.users_deleted,
        'users_archived': report.users_archived,
        'chats_deleted': report.chats_deleted,
        'knowledge_deleted': report.knowledge_deleted,
        'errors': report.errors,
    }
