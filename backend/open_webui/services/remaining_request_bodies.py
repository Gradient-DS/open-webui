"""Expose known nested inputs without replacing the handlers' payloads.

As in model_request_bodies, dependencies validate a separate model and read
Starlette's cached JSON, never model_dump(). Existing form-based handlers need
their original form objects: construct those from cached JSON so nested dicts,
missing keys, extras, and existing form defaults retain their current behavior.
The factories accept the router's local form class to avoid circular imports.

Access-grant fields come from normalize_access_grants; model imports from
ModelForm and the operator system prompt; provider values from
IntegrationProviders.svelte. User info has location and integration_provider;
UI settings follow the Settings type in src/lib/stores/index.ts and the
notification/tool-server consumers. These open shapes expose known fields,
not an exhaustive schema for extension data. Valve and config-import keys
remain author-defined or arbitrary and have no invented model here.
"""

from fastapi import Request
from pydantic import BaseModel, ConfigDict


class OpenBody(BaseModel):
    model_config = ConfigDict(extra='allow')


class AccessGrantInput(OpenBody):
    id: str | None = None
    principal_type: str | None = None
    principal_id: str | None = None
    permission: str | None = None


class AccessGrantsBody(OpenBody):
    access_grants: list[AccessGrantInput]


class ImportedModelMeta(OpenBody):
    profile_image_url: str | None = None
    description: str | None = None


class ImportedModelParams(OpenBody):
    system: str | None = None


class ImportedModel(OpenBody):
    # Imports can update an existing model using only its id and changed fields.
    id: str | None = None
    base_model_id: str | None = None
    name: str | None = None
    meta: ImportedModelMeta | None = None
    params: ImportedModelParams | None = None
    access_grants: list[AccessGrantInput | None] | None = None
    is_active: bool | None = None


class ModelsImportBody(OpenBody):
    models: list[ImportedModel]


class ProviderMetadataField(OpenBody):
    key: str | None = None
    label: str | None = None
    required: bool | None = None


class IntegrationProviderInput(OpenBody):
    name: str | None = None
    description: str | None = None
    badge_type: str | None = None
    max_files_per_kb: int | None = None
    max_documents_per_request: int | None = None
    service_account_id: str | None = None
    custom_metadata_fields: list[ProviderMetadataField] | None = None


class IntegrationsConfigBody(OpenBody):
    # Slugs are administrator-defined; the derivation does not traverse maps.
    providers: dict[str, IntegrationProviderInput]


class UserInfoBody(OpenBody):
    location: str | None = None
    integration_provider: str | None = None


class NotificationSettings(OpenBody):
    webhook_url: str | None = None


class UserToolServer(OpenBody):
    url: str | None = None
    key: str | None = None


class UserTitleSettings(OpenBody):
    auto: bool | None = None
    model: str | None = None
    modelExternal: str | None = None
    prompt: str | None = None


class UserAudioSettings(OpenBody):
    STTEngine: str | None = None
    TTSEngine: str | None = None
    speaker: str | None = None
    model: str | None = None


class UserUISettings(OpenBody):
    system: str | None = None
    models: list[str] | None = None
    pinnedModels: list[str] | None = None
    pinnedInputItems: list[str] | None = None
    pinnedMenuItems: list[str] | None = None
    pinnedNotesOrder: list[str] | None = None
    recentEmojis: list[str] | None = None
    landingPageMode: str | None = None
    chatDirection: str | None = None
    temperature: str | int | float | None = None
    repeat_penalty: str | int | float | None = None
    top_k: str | int | float | None = None
    top_p: str | int | float | None = None
    num_ctx: str | int | float | None = None
    num_batch: str | int | float | None = None
    num_keep: str | int | float | None = None
    notifications: NotificationSettings | None = None
    toolServers: list[UserToolServer] | None = None
    title: UserTitleSettings | None = None
    audio: UserAudioSettings | None = None


class UserSettingsBody(OpenBody):
    ui: UserUISettings | None = None


def access_grants_body(form_type: type[BaseModel]):
    async def dependency(request: Request, body: AccessGrantsBody):
        return form_type(**await request.json())

    return dependency


def models_import_body(form_type: type[BaseModel]):
    async def dependency(request: Request, body: ModelsImportBody):
        return form_type(**await request.json())

    return dependency


def integrations_config_body(form_type: type[BaseModel]):
    async def dependency(request: Request, body: IntegrationsConfigBody):
        return form_type(**await request.json())

    return dependency


def user_settings_body(form_type: type[BaseModel]):
    async def dependency(request: Request, body: UserSettingsBody):
        return form_type(**await request.json())

    return dependency


async def user_info_body(request: Request, body: UserInfoBody) -> dict:
    return await request.json()
