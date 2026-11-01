"""Browser boundary, available only while the prepared thread path is enabled."""

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.responses import JSONResponse, Response

from open_webui.soev import threads
from open_webui.soev.threads import CreatedThread, Organization, ThreadError

NO_STORE = {'Cache-Control': 'private, no-store', 'Vary': 'Authorization, Cookie'}


async def read_states(request: Request, user_id: str) -> dict[str, float]:
    repository = getattr(request.app.state, 'thread_organizations', None)
    return await repository.read_states(user_id) if repository is not None else {}


def require_threads_enabled() -> None:
    if not threads.enabled():
        raise ThreadError(404, 'unavailable')


def projection_response(result: threads.ProjectionResult) -> Response:
    headers = {'ETag': result.etag} if result.etag else {}
    if result.not_modified:
        return Response(status_code=304, headers=headers)
    return JSONResponse(result.page.model_dump(), headers=headers)


class ForkForm(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
        json_schema_extra={
            'if': {'properties': {'version': {'const': True}}, 'required': ['version']},
            'then': {'required': ['kind'], 'properties': {'kind': {'enum': ['regenerate', 'edit']}}},
            'else': {'not': {'required': ['kind']}},
        },
    )
    at: int = Field(gt=0)
    version: bool = Field(default=False, strict=True)
    kind: threads.VersionKind | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode='after')
    def version_reason(self):
        if self.version and self.kind is None:
            raise ValueError('version forks require kind')
        if not self.version and 'kind' in self.model_fields_set:
            raise ValueError('kind requires a version fork')
        return self


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
    create_fork: Callable[[str, str, int, bool, threads.VersionKind | None], Awaitable[CreatedThread]] | None = None,
) -> APIRouter:
    from open_webui.utils.auth import get_verified_user

    async def require_enabled(user=Depends(get_verified_user)):
        require_threads_enabled()
        return user

    router = APIRouter(route_class=PrivateRoute)

    @router.post(':authorize')
    async def authorize(form: threads.AuthorizeThreads, user=Depends(require_enabled)):
        return await threads.build_client().authorize(form)

    @router.get('')
    async def index(
        request: Request,
        q: str = Query('', max_length=500),
        limit: int = Query(50, ge=1, le=200),
        cursor: str = '',
        user=Depends(require_enabled),
    ):
        page = await threads.build_client().index(q=q, limit=limit, cursor=cursor)
        return threads.join_index(page, await organizations(user.id), await read_states(request, user.id))

    @router.post('/{thread_id}/read')
    async def mark_read(thread_id: str, request: Request, user=Depends(require_enabled)):
        from open_webui.soev.chat_routes import paths
        from open_webui.soev.thread_sharing import require_header_mutation

        require_header_mutation(request)
        return await paths(request).mark_read(user.id, thread_id)

    @router.get('/{thread_id}/forks')
    async def forks(
        thread_id: str,
        at: int | None = Query(None, gt=0),
        limit: int = Query(50, ge=1, le=200),
        cursor: str = '',
        user=Depends(require_enabled),
    ):
        return await threads.build_client().forks(thread_id, at=at, limit=limit, cursor=cursor)

    @router.get('/{thread_id}/versions')
    async def versions(thread_id: str, user=Depends(require_enabled)):
        return await threads.build_client().versions(thread_id)

    @router.post('/{thread_id}/fork')
    async def fork(thread_id: str, form: ForkForm, user=Depends(require_enabled)):
        if create_fork is not None:
            return await create_fork(user.id, thread_id, form.at, form.version, form.kind)
        return await threads.build_client().fork(thread_id, at=form.at, version=form.version, kind=form.kind)

    @router.get('/{thread_id}')
    async def projection(
        thread_id: str,
        limit: int = Query(20, ge=1, le=200),
        before: str = '',
        current: bool = False,
        if_none_match: str | None = Header(None),
        user=Depends(require_enabled),
    ):
        result = await threads.build_client().projection(
            thread_id,
            window=limit,
            before=before,
            etag=if_none_match,
            current=current,
        )
        return projection_response(result)

    return router
