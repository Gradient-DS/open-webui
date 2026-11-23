"""[Gradient] Assistant skill-folder routes, mounted in the models namespace."""

import time
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from open_webui.constants import ERROR_MESSAGES
from open_webui.internal.db import get_async_session
from open_webui.models.access_grants import AccessGrants
from open_webui.models.assistant_skill_files import AssistantSkillFile, AssistantSkillFileModel, AssistantSkillFiles
from open_webui.models.files import Files
from open_webui.models.models import Model, Models
from open_webui.utils.access_control import can_bypass_access_control
from open_webui.utils.assistant_skill_bundles import MAX_COMPRESSED, build_bundle, skill_name, validate_path
from open_webui.utils.auth import get_verified_user
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


def refusal(request: Request, reason: str, status_code: int = 400) -> HTTPException:
    messages = {
        'owner': (
            'Alleen bestanden van de eigenaar van deze assistent kunnen worden toegevoegd. '
            'Vraag de eigenaar om de bestanden te uploaden.',
            "Only files owned by this assistant's owner can be added. Ask the owner to upload the files.",
        ),
        'path': (
            'Gebruik een relatief pad van maximaal 255 tekens, zonder lege delen, puntdelen of backslashes. '
            'SKILL.md en soev.json zijn gereserveerd in de hoofdmap.',
            'Use a relative path of at most 255 characters, without empty segments, dot segments or backslashes. '
            'SKILL.md and soev.json are reserved at the root.',
        ),
        'count': (
            'De skillmap mag maximaal 498 bestanden bevatten (500 items inclusief SKILL.md en soev.json).',
            'The skill folder allows at most 498 files (500 entries including SKILL.md and soev.json).',
        ),
        'size': ('De skillmap mag niet groter zijn dan 25 MiB.', 'The skill folder must not exceed 25 MiB.'),
        'description': (
            'De skillbeschrijving moet 1 tot 1024 tekens bevatten.',
            'The skill description must contain 1 to 1024 characters.',
        ),
        'missing': ('Een skillbestand is niet beschikbaar.', 'A skill file is unavailable.'),
    }
    nl, en = messages.get(reason, messages['missing'])
    return HTTPException(status_code, nl if request.headers.get('accept-language', '').lower().startswith('nl') else en)


async def assistant_access(id, user, db, permission='read', *, lock=False):
    if lock:
        # A no-op write serializes admission across workers on PostgreSQL and SQLite alike.
        await db.execute(update(Model).filter_by(id=id).values(updated_at=Model.updated_at))
    model = await Models.get_model_by_id(id, db=db)
    if model is None or not model.base_model_id:
        raise HTTPException(404, ERROR_MESSAGES.NOT_FOUND)
    if model.user_id != user.id and not can_bypass_access_control(user):
        allowed = await AccessGrants.has_access(
            user_id=user.id, resource_type='model', resource_id=id, permission=permission, db=db
        )
        if not allowed and permission == 'read':
            allowed = await AccessGrants.has_access(
                user_id=user.id, resource_type='model', resource_id=id, permission='write', db=db
            )
        if not allowed:
            raise HTTPException(403, ERROR_MESSAGES.UNAUTHORIZED)
    return model


class AssistantSkillFileResponse(BaseModel):
    path: str
    file_id: str
    size: int


class AssistantSkillFileForm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(max_length=255)
    file_id: str
    from_path: str | None = Field(default=None, max_length=255)


@router.get('/id/{id:path}/skill-files', response_model=list[AssistantSkillFileResponse])
async def list_assistant_skill_files(
    id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    await assistant_access(id, user, db)
    rows = await AssistantSkillFiles.get_files(id, db=db)
    files = {file.id: file for file in await Files.get_file_metadatas_by_ids([row.file_id for row in rows], db=db)}
    return [
        AssistantSkillFileResponse(
            path=row.path, file_id=row.file_id, size=(files[row.file_id].meta or {}).get('size', 0)
        )
        for row in rows
    ]


@router.put('/id/{id:path}/skill-files', response_model=bool)
async def put_assistant_skill_file(
    id: str,
    form_data: AssistantSkillFileForm,
    request: Request,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    model = await assistant_access(id, user, db, 'write', lock=True)
    try:
        validate_path(form_data.path)
        if form_data.from_path is not None:
            validate_path(form_data.from_path)
    except ValueError as error:
        raise refusal(request, str(error)) from None
    file = await Files.get_file_by_id(form_data.file_id, db=db)
    if file is None or file.user_id != model.user_id:
        raise refusal(request, 'owner', 403)
    rows = await AssistantSkillFiles.get_files(id, db=db)
    source = next((row for row in rows if row.path == form_data.from_path), None)
    if form_data.from_path is not None and (source is None or source.file_id != file.id):
        raise HTTPException(404, ERROR_MESSAGES.NOT_FOUND)
    now = time.time_ns()
    candidate = AssistantSkillFileModel(
        id=source.id if source else str(uuid4()),
        assistant_id=id,
        file_id=file.id,
        user_id=user.id,
        path=form_data.path,
        created_at=source.created_at if source else now,
        updated_at=now,
    )
    retained = [row for row in rows if row.path not in (form_data.path, form_data.from_path)]
    proposed = [*retained, candidate]
    backing = {
        file.id: file for file in await Files.get_file_metadatas_by_ids([row.file_id for row in proposed], db=db)
    }
    try:
        if sum((backing[row.file_id].meta or {}).get('size', 0) for row in proposed) > MAX_COMPRESSED:
            raise ValueError('size')
        # Validate actual bytes, case collisions, zip overhead and both generated root entries before committing.
        await build_bundle(model, proposed, export=True, max_files_size=MAX_COMPRESSED, db=db)
    except (ValueError, OSError) as error:
        raise refusal(request, str(error)) from None
    await db.execute(
        delete(AssistantSkillFile)
        .filter_by(assistant_id=id)
        .where(AssistantSkillFile.path.in_([form_data.path, form_data.from_path or form_data.path]))
    )
    db.add(AssistantSkillFile(**candidate.model_dump()))
    await db.commit()
    return True


@router.delete('/id/{id:path}/skill-files', response_model=bool)
async def delete_assistant_skill_file(
    id: str,
    path: str = Query(max_length=255),
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await assistant_access(id, user, db, 'write', lock=True)
    await db.execute(delete(AssistantSkillFile).filter_by(assistant_id=id, path=path))
    await db.commit()
    return True


@router.get(
    '/id/{id:path}/skill',
    response_class=Response,
    responses={200: {'content': {'application/zip': {'schema': {'type': 'string', 'format': 'binary'}}}}},
)
async def export_assistant_skill(
    id: str, request: Request, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    model = await assistant_access(id, user, db)
    try:
        body = await build_bundle(model, await AssistantSkillFiles.get_files(id, db=db), export=True, db=db)
    except (ValueError, OSError) as error:
        raise refusal(request, str(error)) from None
    return Response(
        body,
        media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename="{skill_name(model.name)}.skill"'},
    )
