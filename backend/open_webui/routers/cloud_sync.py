"""Verified-user routes for soev-api connections, schedule state and popup completion."""

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from open_webui.config import CORS_ALLOW_ORIGIN
from open_webui.soev import identity
from open_webui.soev.client import SoevApiError
from open_webui.soev.cloud_sync import CloudSync
from open_webui.soev.request_cache import request_cache
from open_webui.utils.auth import get_verified_user
from pydantic import BaseModel, ConfigDict, Field, JsonValue

router = APIRouter()


async def cloud_sync(user=Depends(get_verified_user)):
    try:
        client = identity.build_client()
        with request_cache():
            yield CloudSync(client, await identity.acting_ref(user, client))
    except SoevApiError as error:
        detail = {'code': error.code, 'detail': error.detail}
        if error.constraint is not None:
            detail['constraint'] = error.constraint
        raise HTTPException(status_code=error.status, detail=detail) from None


class ConnectionForm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Literal['onedrive', 'google_drive', 'confluence']


class ScheduleForm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    connection_id: str = Field(min_length=1, max_length=256)
    kind: Literal['content', 'acl_refresh']
    scope: dict[str, JsonValue]
    label: str | None = Field(default=None, max_length=512)
    path: str | None = Field(default=None, max_length=512)
    cadence_minutes: int | None = Field(default=None, gt=0)


@router.post('/connections')
async def create_connection(body: ConnectionForm, sync=Depends(cloud_sync), user=Depends(get_verified_user)):
    return await sync.create_connection(body.provider, owner_email=user.email)


@router.get('/connections')
async def list_connections(sync=Depends(cloud_sync)):
    return await sync.connections()


@router.get('/connections/{connection_id}')
async def get_connection(connection_id: str, sync=Depends(cloud_sync)):
    return await sync.connection(connection_id)


@router.get('/connections/{connection_id}/usage')
async def connection_usage(connection_id: str, sync=Depends(cloud_sync)):
    return await sync.connection_usage(connection_id)


@router.delete('/connections/{connection_id}', status_code=204)
async def revoke_connection(connection_id: str, sync=Depends(cloud_sync)):
    await sync.revoke_connection(connection_id)
    return Response(status_code=204)


@router.post('/connections/{connection_id}/authorize')
async def authorize_connection(connection_id: str, sync=Depends(cloud_sync), user=Depends(get_verified_user)):
    connection = await sync.connection(connection_id)
    owner_email = user.email if connection['source_kind'] == 'google_drive' else None
    return await sync.authorize(connection_id, owner_email=owner_email)


@router.get('/connect/done', response_class=HTMLResponse)
async def connect_done(
    connection: str,
    result: Literal['pending', 'error', 'invalid'],
    user=Depends(get_verified_user),
):
    data = json.dumps({'type': 'soev_connect', 'connection': connection, 'result': result}).replace('<', '\\u003c')
    # The opener may live on another configured origin than this page (a dev
    # stack serves the frontend and the API on different ports); a message to
    # an origin the opener is not on is dropped by the browser, never seen.
    origins = json.dumps(sorted(origin for origin in CORS_ALLOW_ORIGIN if origin != '*')).replace('<', '\\u003c')
    return HTMLResponse(
        '<!DOCTYPE html><html><body><script>'
        f'if (window.opener) {{ for (const origin of new Set([window.location.origin, ...{origins}])) '
        f'window.opener.postMessage({data}, origin); }}'
        'window.close();</script></body></html>',
        headers={'Cache-Control': 'no-store'},
    )


@router.post('/knowledge/{knowledge_id}/schedules')
async def create_schedule(knowledge_id: str, body: ScheduleForm, sync=Depends(cloud_sync)):
    return await sync.create_schedule(knowledge_id, body.model_dump(exclude_none=True))


@router.delete('/knowledge/{knowledge_id}/schedules/{schedule_id}', status_code=204)
async def delete_schedule(knowledge_id: str, schedule_id: str, sync=Depends(cloud_sync)):
    await sync.schedule_action(knowledge_id, schedule_id, 'delete')
    return Response(status_code=204)


@router.get('/knowledge/{knowledge_id}/sync')
async def sync_status(knowledge_id: str, sync=Depends(cloud_sync)):
    return await sync.sync_status(knowledge_id)


@router.post('/knowledge/{knowledge_id}/schedules/{schedule_id}/run', status_code=201)
async def run_schedule(knowledge_id: str, schedule_id: str, sync=Depends(cloud_sync)):
    return await sync.schedule_action(knowledge_id, schedule_id, 'run')


@router.post('/knowledge/{knowledge_id}/schedules/{schedule_id}/cancel', status_code=204)
async def cancel_schedule(knowledge_id: str, schedule_id: str, sync=Depends(cloud_sync)):
    await sync.schedule_action(knowledge_id, schedule_id, 'cancel')
    return Response(status_code=204)


@router.post('/knowledge/{knowledge_id}/schedules/{schedule_id}/suspend', status_code=204)
async def suspend_schedule(knowledge_id: str, schedule_id: str, sync=Depends(cloud_sync)):
    await sync.schedule_action(knowledge_id, schedule_id, 'suspend')
    return Response(status_code=204)


@router.post('/knowledge/{knowledge_id}/schedules/{schedule_id}/resume', status_code=204)
async def resume_schedule(knowledge_id: str, schedule_id: str, sync=Depends(cloud_sync)):
    await sync.schedule_action(knowledge_id, schedule_id, 'resume')
    return Response(status_code=204)


@router.get('/knowledge/{knowledge_id}/schedules/{schedule_id}/skipped')
async def skipped_items(knowledge_id: str, schedule_id: str, sync=Depends(cloud_sync)):
    return await sync.skipped_items(knowledge_id, schedule_id)
