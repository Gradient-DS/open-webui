from __future__ import annotations

import copy
import logging
from typing import Union
from uuid import uuid4

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
from open_webui.soev.system_prompt import KEY as SYSTEM_PROMPT_KEY
from open_webui.services.remaining_request_bodies import integrations_config_body
from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.models.users import Users
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.features import require_feature
from open_webui.utils.headers import bearer_auth_header, get_custom_headers
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
    get_tool_server_data,
    get_tool_server_url,
    set_terminal_servers,
    set_tool_servers,
)
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter()

log = logging.getLogger(__name__)

CODE_EXECUTION_CONFIG_KEYS = {
    'ENABLE_DOCUMENT_WRITER': 'document_writer.enable',
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
SUBAGENTS_CONFIG_KEYS = {
    'ENABLE_SUBAGENTS': 'subagents.enable',
    'SUBAGENTS_BACKGROUND_ENABLED': 'subagents.background_enabled',
    'SUBAGENTS_MAX_CONCURRENT': 'subagents.max_concurrent',
    'SUBAGENTS_MAX_ASYNC': 'subagents.max_async',
    'SUBAGENTS_MAX_ITERATIONS': 'subagents.max_iterations',
    'SUBAGENTS_MAX_OUTPUT': 'subagents.max_output',
    'SUBAGENTS_SYSTEM_PROMPT': 'subagents.system_prompt',
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


############################
# ExportConfig
############################


############################
# Connections Config
############################


class OAuthClientRegistrationForm(BaseModel):
    url: str
    client_id: str
    client_name: str | None = None
    client_secret: str | None = None
    oauth_server_url: str | None = None
    oauth_scope: str | None = None


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


class TerminalServerConnection(BaseModel):
    id: str | None = ''
    name: str | None = ''

    enabled: bool | None = True

    url: str
    path: str | None = '/openapi.json'

    key: str | None = ''
    auth_type: str | None = 'bearer'

    config: dict | None = None

    server_type: str | None = None
    policy_id: str | None = None

    model_config = ConfigDict(extra='allow')


class TerminalServersConfigForm(BaseModel):
    TERMINAL_SERVER_CONNECTIONS: list[TerminalServerConnection]


class TerminalServerPolicyForm(BaseModel):
    url: str
    key: str | None = ''
    auth_type: str | None = 'bearer'
    policy_id: str
    policy_data: dict | None = None


class TerminalServerLifecycleForm(BaseModel):
    url: str
    key: str | None = ''
    auth_type: str | None = 'bearer'
    policy_id: str
    lifecycle_data: dict | None = None


class TerminalServerRefreshForm(BaseModel):
    url: str
    key: str | None = ''
    auth_type: str | None = 'bearer'
    user_id: str | None = None
    policy_id: str | None = None
    only_idle: bool = True
    reset: bool = False


############################
# CodeInterpreterConfig
############################
class CodeInterpreterConfigForm(BaseModel):
    ENABLE_DOCUMENT_WRITER: bool
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


############################
# SetDefaultModels
############################
class ModelsConfigForm(BaseModel):
    DEFAULT_MODELS: str | None
    DEFAULT_PINNED_MODELS: str | None
    MODEL_ORDER_LIST: list[str] | None
    DEFAULT_MODEL_METADATA: dict | None = None
    DEFAULT_MODEL_PARAMS: dict | None = None


@router.get('/models/defaults')
async def get_models_defaults(request: Request, user=Depends(get_verified_user)):
    return {
        'DEFAULT_MODEL_METADATA': await Config.get('models.default_metadata'),
    }


############################
# SystemPrompt
############################
class SystemPromptForm(BaseModel):
    SYSTEM_PROMPT: str


class SubagentsConfigForm(BaseModel):
    ENABLE_SUBAGENTS: bool
    SUBAGENTS_BACKGROUND_ENABLED: bool
    SUBAGENTS_MAX_CONCURRENT: int
    SUBAGENTS_MAX_ASYNC: int
    SUBAGENTS_MAX_ITERATIONS: int
    SUBAGENTS_MAX_OUTPUT: int
    SUBAGENTS_SYSTEM_PROMPT: str


class PromptSuggestion(BaseModel):
    title: list[str]
    content: str


class SetDefaultSuggestionsForm(BaseModel):
    suggestions: list[PromptSuggestion]


############################
# SetBanners
############################


class SetBannersForm(BaseModel):
    banners: list[BannerModel]


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


####################################
# External Agents Config
####################################


class ExternalAgentsConfigForm(BaseModel):
    AGENT_API_SELECTED_AGENT: str


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


class PlatformRetentionConfigForm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    temporary_idle_hours: int | None = Field(ge=1, le=720, strict=True)
    thread_idle_days: int | None = Field(ge=1, le=3650, strict=True)


async def _retention_admin(user_id: str) -> str:
    from open_webui.soev.acting import requester_ref, user_token
    from open_webui.soev.client import SoevApiError

    ref = f'owui:user:{user_id}'
    if requester_ref() != ref or not await user_token(ref):
        raise SoevApiError(401, 'unauthenticated', 'A user token is required')
    return ref
