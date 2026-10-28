"""Browser boundary, available only while the prepared thread path is enabled."""

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, Header, Query
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse, Response

from open_webui.soev import threads
from open_webui.soev.threads import CreatedThread, Organization, ThreadError

NO_STORE = {'Cache-Control': 'private, no-store', 'Vary': 'Authorization, Cookie'}


def projection_response(result: threads.ProjectionResult) -> Response:
    headers = {'ETag': result.etag} if result.etag else {}
    if result.not_modified:
        return Response(status_code=304, headers=headers)
    return JSONResponse(result.page.model_dump(), headers=headers)


class ForkForm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    at: int = Field(gt=0)


class PrivateRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private(request):
            try:
                response = await handler(request)
            except ThreadError as error:
                response = JSONResponse({'state': error.state}, status_code=error.status)
            except RequestValidationError:
                response = JSONResponse({'state': 'invalid_request'}, status_code=422)
            except HTTPException as error:
                response = JSONResponse({'state': 'sign_in'}, status_code=error.status_code)
            response.headers.update(NO_STORE)
            return response

        return private


def create_thread_router(
    organizations: Callable[[str], Awaitable[list[Organization]]],
    *,
    create_fork: Callable[[str, str, int], Awaitable[CreatedThread]] | None = None,
) -> APIRouter:
    from open_webui.utils.auth import get_verified_user

    async def require_enabled(user=Depends(get_verified_user)):
        if not threads.enabled():
            raise ThreadError(404, 'unavailable')
        return user

    router = APIRouter(route_class=PrivateRoute)

    @router.post(':authorize')
    async def authorize(form: threads.AuthorizeThreads, user=Depends(require_enabled)):
        return await threads.build_client().authorize(form)

    @router.get('')
    async def index(
        q: str = Query('', max_length=500),
        limit: int = Query(50, ge=1, le=200),
        cursor: str = '',
        user=Depends(require_enabled),
    ):
        page = await threads.build_client().index(q=q, limit=limit, cursor=cursor)
        return threads.join_index(page, await organizations(user.id))

    @router.get('/{thread_id}/forks')
    async def forks(
        thread_id: str,
        at: int | None = Query(None, gt=0),
        limit: int = Query(50, ge=1, le=200),
        cursor: str = '',
        user=Depends(require_enabled),
    ):
        return await threads.build_client().forks(thread_id, at=at, limit=limit, cursor=cursor)

    @router.post('/{thread_id}/fork')
    async def fork(thread_id: str, form: ForkForm, user=Depends(require_enabled)):
        if create_fork is not None:
            return await create_fork(user.id, thread_id, form.at)
        return await threads.build_client().fork(thread_id, at=form.at)

    @router.get('/{thread_id}')
    async def projection(
        thread_id: str,
        limit: int = Query(20, ge=1, le=200),
        before: str = '',
        if_none_match: str | None = Header(None),
        user=Depends(require_enabled),
    ):
        result = await threads.build_client().projection(
            thread_id,
            window=limit,
            before=before,
            etag=if_none_match,
        )
        return projection_response(result)

    return router
