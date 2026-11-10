from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request, status
from open_webui.config import CACHE_DIR
from open_webui.constants import ERROR_MESSAGES
from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL, AIOHTTP_CLIENT_TIMEOUT, ENABLE_PLUGINS
from open_webui.events import EVENTS, build_event, dispatch_event_functions, publish_event, schedule_webhook_dispatch
from open_webui.internal.db import get_async_session
from open_webui.models.functions import (
    FunctionForm,
    FunctionModel,
    FunctionResponse,
    Functions,
    FunctionUserResponse,
    FunctionWithValvesModel,
)
from open_webui.soev.model_catalog import require_custom_functions
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.plugin import (
    get_function_module_from_cache,
    get_functions_cache,
    load_function_module_by_id,
    replace_imports,
    resolve_valves_schema_options,
)
from pydantic import BaseModel, HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


router = APIRouter(dependencies=[Depends(require_custom_functions)])

############################
# GetFunctions
# Our daily functions give us, and forgive us
# our deprecated methods, as we refactor those who depend on us.
############################


@router.get('/', response_model=list[FunctionResponse])
async def get_functions(user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    if not ENABLE_PLUGINS:
        return []

    return await Functions.get_functions(db=db)


############################
# ExportFunctions
############################


############################
# LoadFunctionFromLink
############################


class LoadUrlForm(BaseModel):
    url: HttpUrl


def github_url_to_raw_url(url: str) -> str:
    # Handle 'tree' (folder) URLs (add main.py at the end)
    m1 = re.match(r'https://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.*)', url)
    if m1:
        org, repo, branch, path = m1.groups()
        return f'https://raw.githubusercontent.com/{org}/{repo}/refs/heads/{branch}/{path.rstrip("/")}/main.py'

    # Handle 'blob' (file) URLs
    m2 = re.match(r'https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)', url)
    if m2:
        org, repo, branch, path = m2.groups()
        return f'https://raw.githubusercontent.com/{org}/{repo}/refs/heads/{branch}/{path}'

    # No match; return as-is
    return url


############################
# SyncFunctions
############################


class SyncFunctionsForm(BaseModel):
    functions: list[FunctionWithValvesModel] = []


############################
# CreateNewFunction
############################


############################
# GetFunctionById
############################


############################
# ToggleFunctionById
############################


############################
# ToggleGlobalById
############################


############################
# UpdateFunctionById
############################


############################
# DeleteFunctionById
############################


############################
# GetFunctionValves
############################


############################
# GetFunctionValvesSpec
############################


############################
# UpdateFunctionValves
############################


############################
# FunctionUserValves
############################


@router.get('/id/{id}/valves/user', response_model=dict | None)
async def get_function_user_valves_by_id(
    id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    function = await Functions.get_function_by_id(id, db=db)
    if function:
        try:
            user_valves = await Functions.get_user_valves_by_id_and_user_id(id, user.id, db=db)
            return user_valves
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT(e, 'Error getting function user valves'),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.get('/id/{id}/valves/user/spec', response_model=dict | None)
async def get_function_user_valves_spec_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    function = await Functions.get_function_by_id(id, db=db)
    if function:
        if not function.is_active:
            return None

        function_module, function_type, frontmatter = await get_function_module_from_cache(request, id)

        if hasattr(function_module, 'UserValves'):
            UserValves = function_module.UserValves
            schema = UserValves.schema()
            # Resolve dynamic options for select dropdowns
            schema = resolve_valves_schema_options(UserValves, schema, user)
            return schema
        return None
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.post('/id/{id}/valves/user/update', response_model=dict | None)
async def update_function_user_valves_by_id(
    request: Request,
    id: str,
    form_data: dict,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    function = await Functions.get_function_by_id(id, db=db)

    if function:
        if not function.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Function is not active',
            )

        function_module, function_type, frontmatter = await get_function_module_from_cache(request, id)

        if hasattr(function_module, 'UserValves'):
            UserValves = function_module.UserValves

            try:
                form_data = {k: v for k, v in form_data.items() if v is not None}
                user_valves = UserValves(**form_data)
                user_valves_dict = user_valves.model_dump(exclude_unset=True)
                await Functions.update_user_valves_by_id_and_user_id(id, user.id, user_valves_dict, db=db)
                await publish_event(
                    request,
                    EVENTS.FUNCTION_VALVES_UPDATED,
                    actor=user,
                    subject_id=id,
                    data={'scope': 'user'},
                )
                return user_valves_dict
            except Exception as e:
                log.exception(f'Error updating function user valves by id {id}: {e}')
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT(e, 'Error updating function user valves'),
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.NOT_FOUND,
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
