"""Pure translations between soev-api collection values and OWUI knowledge models."""

import base64
import datetime as dt
import json
from uuid import NAMESPACE_URL, uuid5

from open_webui.models.access_grants import AccessGrantModel
from open_webui.models.knowledge import (
    FileUserMetadataResponse,
    FileUserResponse,
    KnowledgeDirectoryEntry,
    KnowledgeDirectoryModel,
    KnowledgeFileListResponse,
    KnowledgeFileModel,
    KnowledgeForm,
    KnowledgeListResponse,
    KnowledgeModel,
    KnowledgeUserModel,
)


def knowledge_of(collection: dict, *, service_principal: str) -> KnowledgeModel:
    creator = collection.get('created_by') or ''
    return KnowledgeModel(
        id=collection['key'],
        user_id=creator.removeprefix('owui:user:') if creator.startswith('owui:user:') else '',
        type='local',
        name=collection['name'],
        description=collection['description'] or '',
        meta={},
        access_grants=grants_of(collection, service_principal=service_principal),
        created_at=int(dt.datetime.fromisoformat(collection['created_at']).timestamp()),
        updated_at=int(dt.datetime.fromisoformat(collection['updated_at']).timestamp()),
        deleted_at=None,
    )


def collection_create_body(form: KnowledgeForm, *, service_principal: str) -> dict:
    visibility, readers, writers = access_of(form.access_grants or [], service_principal=service_principal)
    return {
        'name': form.name,
        'description': form.description,
        'visibility': visibility,
        'principals': readers,
        'writers': writers,
    }


def access_of(grants: list[dict], *, service_principal: str) -> tuple[str, list[str], list[str]]:
    visibility = 'restricted'
    readers, writers = {service_principal}, set()
    for grant in grants:
        principal_type = grant.get('principal_type')
        principal_id = grant.get('principal_id')
        permission = grant.get('permission')
        if principal_id == '*':
            if permission == 'read':
                visibility = 'public'
            continue
        if principal_type not in ('user', 'group') or not principal_id:
            continue
        ref = f'owui:{principal_type}:{principal_id}'
        if permission == 'read':
            readers.add(ref)
        elif permission == 'write':
            writers.add(ref)
    return visibility, sorted(readers), sorted(writers)


def grants_of(collection: dict, *, service_principal: str) -> list[AccessGrantModel]:
    identities = set()
    excluded = {service_principal, collection.get('created_by')}
    for field, permission in (('principals', 'read'), ('writers', 'write')):
        for ref in collection[field]:
            if ref in excluded:
                continue
            source, _, rest = ref.partition(':')
            principal_type, _, principal_id = rest.partition(':')
            if source != 'owui' or principal_type not in ('user', 'group') or principal_id in ('', '*'):
                continue
            identities.add((principal_type, principal_id, permission))
    if collection['visibility'] == 'public':
        identities.add(('user', '*', 'read'))
    key = collection['key']
    created_at = int(dt.datetime.fromisoformat(collection['created_at']).timestamp())
    return [
        AccessGrantModel(
            id=str(uuid5(NAMESPACE_URL, json.dumps(['knowledge', key, principal_type, principal_id, permission]))),
            resource_type='knowledge',
            resource_id=key,
            principal_type=principal_type,
            principal_id=principal_id,
            permission=permission,
            created_at=created_at,
        )
        for principal_type, principal_id, permission in sorted(identities)
    ]


def directory_id(key: str, path: tuple[str, ...]) -> str:
    if not key or '\n' in key or any(not segment or '/' in segment for segment in path):
        raise ValueError('Directory keys and path segments must have an unambiguous encoding')
    value = (key + '\n' + '/'.join(path)).encode()
    return 'd_' + base64.urlsafe_b64encode(value).decode().rstrip('=')


def directory_of(directory_id: str) -> tuple[str, tuple[str, ...]]:
    if not isinstance(directory_id, str) or not directory_id.startswith('d_'):
        raise ValueError('Invalid directory id')
    encoded = directory_id[2:]
    value = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True).decode()
    key, separator, path = value.partition('\n')
    segments = tuple(path.split('/')) if path else ()
    canonical = base64.urlsafe_b64encode(value.encode()).decode().rstrip('=')
    if not key or not separator or any(not segment for segment in segments) or encoded != canonical:
        raise ValueError('Invalid directory id')
    return key, segments


def directory_model(key: str, path: tuple[str, ...], *, created_at: int, owner_id: str) -> KnowledgeDirectoryModel:
    if not path:
        raise ValueError('The collection root has no directory model')
    return KnowledgeDirectoryModel(
        id=directory_id(key, path),
        knowledge_id=key,
        parent_id=directory_id(key, path[:-1]) if len(path) > 1 else None,
        name=path[-1],
        user_id=owner_id,
        created_at=created_at,
        updated_at=created_at,
    )


def knowledge_user_of(collection: dict, *, service_principal: str, user: dict | None = None) -> KnowledgeUserModel:
    return KnowledgeUserModel(
        **knowledge_of(collection, service_principal=service_principal).model_dump(),
        user=user,
        file_count=collection['document_count'],
    )


def knowledge_list_of(items: list, total: int) -> KnowledgeListResponse:
    return KnowledgeListResponse(items=items, total=total)


def file_response_of(file: dict, document: dict, *, metadata_only: bool) -> FileUserMetadataResponse | FileUserResponse:
    fields = {key: file[key] for key in ('id', 'user_id', 'hash', 'filename', 'meta', 'created_at', 'updated_at')}
    fields.update(user=None, added_at=int(dt.datetime.fromisoformat(document['ingested_at']).timestamp()))
    if metadata_only:
        meta = file['meta'] or {}
        return FileUserMetadataResponse(**fields, status=meta.get('status'), error=meta.get('error'))
    return FileUserResponse(**fields)


def knowledge_link_of(collection: dict, document: dict, *, service_principal: str) -> KnowledgeFileModel:
    key, source_id = collection['key'], document['source_id']
    path = tuple(document['path'].split('/')) if document['path'] else ()
    created_at = int(dt.datetime.fromisoformat(document['ingested_at']).timestamp())
    return KnowledgeFileModel(
        id=str(uuid5(NAMESPACE_URL, json.dumps(['knowledge_file', key, source_id]))),
        knowledge_id=key,
        file_id=source_id,
        directory_id=directory_id(key, path) if path else None,
        user_id=knowledge_of(collection, service_principal=service_principal).user_id,
        relative_path=document['path'],
        created_at=created_at,
        updated_at=created_at,
    )


def knowledge_file_list_of(
    items: list,
    *,
    total: int,
    directories: list | None = None,
    breadcrumbs: list | None = None,
    rollups: dict | None = None,
) -> KnowledgeFileListResponse:
    return KnowledgeFileListResponse(
        items=items,
        total=total,
        directories=[
            KnowledgeDirectoryEntry(
                **directory.model_dump(),
                **(rollups or {}).get(directory.id, {'child_count': 0, 'status_counts': status_counts()}),
            )
            for directory in directories or []
        ],
        breadcrumbs=breadcrumbs or [],
    )


def status_counts() -> dict[str, int]:
    return {'pending': 0, 'completed': 0, 'failed': 0, 'unknown': 0}


def status_bucket(status: str | None) -> str:
    if status == 'completed':
        return 'completed'
    if status in ('failed', 'error'):
        return 'failed'
    if status in ('pending', 'processing', 'downloading', 'parsing', 'ingesting'):
        return 'pending'
    return 'unknown'
