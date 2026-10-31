"""Select prepared handlers before any legacy body, dependency or content access."""

import json

from fastapi import Depends, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.responses import JSONResponse, Response

from open_webui.soev import threads
from open_webui.soev.chat_paths import ChatPaths, FeedbackForm, refused
from open_webui.soev.thread_routes import NO_STORE
from open_webui.soev.thread_sharing import require_header_mutation
from open_webui.soev.threads import ThreadError


def paths(request: Request) -> ChatPaths:
    repository = getattr(request.app.state, 'thread_organizations', None)
    if repository is None:
        raise ThreadError(503, 'reconnect')
    return ChatPaths(threads.build_client(), repository)


async def body(request: Request) -> dict:
    try:
        result = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise ThreadError(422, 'invalid_request') from None
    if not isinstance(result, dict):
        raise ThreadError(422, 'invalid_request')
    return result


def integer(request: Request, key: str, default: int) -> int:
    try:
        result = int(request.query_params.get(key, default))
    except ValueError:
        raise ThreadError(422, 'invalid_request') from None
    if result < 1:
        raise ThreadError(422, 'invalid_request')
    return result


def boolean(request: Request, key: str, default: bool) -> bool:
    value = request.query_params.get(key)
    if value is None:
        return default
    if value not in {'true', 'false', '1', '0'}:
        raise ThreadError(422, 'invalid_request')
    return value in {'true', '1'}


async def chat(request: Request, user, route: str):
    method = request.method
    # Refusals run without opening the local organisation repository.
    if route in {'/import', '/all', '/all/archived'} or '/messages/' in route:
        refused()
    if method == 'POST' and (route == '/{id}/compact' or '/messages/' in route):
        refused()
    if '/share' in route or '/shared' in route or route == '/{id}/clone/shared':
        from open_webui.soev.thread_sharing import dispatch

        return await dispatch(paths(request), request, user, route)
    service = paths(request)
    identifier = request.path_params.get('id')
    if route == '/{id}/tags' and method != 'GET' or route == '/{id}' and method == 'POST':
        return await metadata_write(service, request, user, identifier, route)
    if method == 'DELETE':
        if route == '/':
            await service.delete_all(user.id)
        elif route == '/{id}':
            await service.delete(user.id, identifier)
        else:
            refused()
        return True
    if method == 'GET' or route == '/tags':
        return await chat_read(service, request, user, identifier, route)
    return await chat_write(service, request, user, identifier, route)


async def chat_read(service, request, user, identifier, route):
    if route == '/stats/usage' or route.startswith('/stats/export'):
        return (await service.client.usage()).model_dump()
    if route in {
        '/',
        '/list',
        '/search',
        '/pinned',
        '/archived',
        '/archived/count',
        '/folder/{folder_id}',
        '/folder/{folder_id}/list',
    }:
        if 'folder_id' in request.path_params:
            await require_folder_read(user.id, request.path_params['folder_id'])
        items = await service.listing(
            user.id,
            q=request.query_params.get('text', request.query_params.get('q', '')),
            folder_id=request.path_params.get('folder_id'),
            archived=route.startswith('/archived'),
            pinned=True if route == '/pinned' else None,
            page=integer(request, 'page', 1) if 'page' in request.query_params and route != '/archived/count' else None,
            include_pinned=boolean(request, 'include_pinned', route not in {'/', '/list'}),
            include_folders=boolean(request, 'include_folders', route not in {'/', '/list'}),
            sort_by=request.query_params.get('sort_by', 'updated_at'),
            sort_dir=request.query_params.get('sort_dir', 'desc'),
        )
        return len(items) if route == '/archived/count' else items
    if route == '/{id}':
        result = await service.projection(
            user.id,
            identifier,
            window=integer(request, 'limit', 20),
            before=request.query_params.get('before', ''),
            etag=request.headers.get('If-None-Match'),
        )
        headers = {'ETag': result.etag} if result.etag else {}
        page = None if result.not_modified else await service.presentation(user.id, result.page)
        return (
            Response(status_code=304, headers=headers)
            if result.not_modified
            else JSONResponse(page.model_dump(), headers=headers)
        )
    if route == '/{id}/tags':
        projection = await service.projection(user.id, identifier, window=1)
        return projection.page.tags
    if route in {'/all/tags', '/tags'}:
        return await tag_listing(service, request, user, route)
    if route == '/{id}/pinned':
        await service.projection(user.id, identifier, window=1)
        return (await service.record(user.id, identifier)).pinned
    refused()


async def require_folder_read(user_id: str, folder_id: str) -> None:
    from open_webui.models.folders import Folders
    from open_webui.utils.access_control.folders import has_folder_access

    folder = await Folders.get_folder_by_id(folder_id)
    if folder is None or not await has_folder_access(user_id, folder, 'read', None):
        raise ThreadError(404, 'unavailable')


async def chat_write(service, request, user, identifier, route):
    if route == '/new':
        form = await body(request)
        if form.get('temporary'):
            refused()
        if set(form) - {'agent', 'input', 'model'} or not isinstance(form.get('agent'), str) or 'input' not in form:
            raise ThreadError(422, 'invalid_request')
        return (await service.create(user.id, agent=form['agent'])).model_dump()
    if route == '/{id}/fork':
        form = await body(request)
        if set(form) != {'at'} or type(form['at']) is not int:
            raise ThreadError(422, 'invalid_request')
        return (await service.fork(user.id, identifier, form['at'])).model_dump()
    if route == '/{id}/clone':
        require_header_mutation(request)
        # The newest projection supplies the last committed position; never
        # accept the caller's message tree or a chosen destination id.
        projection = await service.projection(user.id, identifier, window=200)
        positions = [message.position for message in projection.page.messages if message.finished]
        if not positions:
            refused()
        return (await service.fork(user.id, identifier, max(positions), clone=True)).model_dump()
    if route in {'/{id}/pin', '/{id}/archive', '/{id}/unread', '/{id}/folder'}:
        return await organize(service, request, user, identifier, route)
    if route in {'/read', '/archive/all', '/unarchive/all'}:
        archived = None if route == '/read' else route == '/archive/all'
        return {'updated_count': await service.read_all(user.id, archived=archived)}
    refused()


async def tag_listing(service, request, user, route):
    tag = (await body(request)).get('name') if route == '/tags' else None
    found, names = [], set()
    for item in await service.authorized(user.id):
        tags = item['tags']
        names.update(name for name in tags if isinstance(name, str))
        if tag in tags:
            found.append(item)
    return service.ui_rows(found) if route == '/tags' else sorted(names)


async def metadata_write(service, request, user, identifier, route):
    require_header_mutation(request)
    form = await body(request)
    if route == '/{id}' and set(form) == {'chat'} and isinstance(form['chat'], dict):
        form = form['chat']
    action = 'replace'
    if route == '/{id}/tags':
        if set(form) != {'name'} or not isinstance(form['name'], str):
            raise ThreadError(422, 'invalid_request')
        form = {'tags': [form['name']]}
        action = 'remove' if request.method == 'DELETE' else 'add'
    page = await service.metadata(user.id, identifier, form, tag_action=action)
    from open_webui.soev.thread_metadata import emit_metadata

    await emit_metadata(user.id, identifier, page, form)
    return page.tags if route == '/{id}/tags' else page.model_dump()


async def organize(service, request, user, identifier, route):
    record = await service.record(user.id, identifier)
    changes = {
        '/{id}/pin': {'pinned': not record.pinned},
        '/{id}/archive': {'archived': not record.archived},
        '/{id}/unread': {'last_read_at': None},
    }.get(route)
    if changes is None:
        form = await body(request)
        if set(form) != {'folder_id'}:
            raise ThreadError(422, 'invalid_request')
        folder_id = form['folder_id']
        if folder_id is not None:
            from open_webui.models.folders import Folders

            folder = await Folders.get_folder_by_id_and_user_id(folder_id, user.id)
            if folder is None:
                raise ThreadError(404, 'unavailable')
        changes = {'folder_id': folder_id}
    return (await service.organize(user.id, identifier, changes)).model_dump()


async def evaluation(request: Request, user, route: str):
    client = threads.build_client()
    if request.method == 'POST' and route == '/feedback':
        form = FeedbackForm.model_validate(await body(request))
        await client.feedback(**form.model_dump())
        return Response(status_code=204)
    if request.method != 'GET':
        refused()
    limit = integer(request, 'limit', 50)
    cursor = request.query_params.get('cursor', '')
    if route == '/leaderboard':
        return (await client.feedback_summary(limit=limit, cursor=cursor)).model_dump()
    if route in {'/feedbacks/list', '/feedbacks/user', '/feedback/conversation/{chat_id}'}:
        # Tenant-admin vs own-record filtering is performed by 7a from the login
        # token, never by a user-selected scope or an OWUI owner hint.
        if route == '/feedback/conversation/{chat_id}':
            items, cursor, seen = [], '', set()
            while True:
                page = await client.feedbacks(limit=200, cursor=cursor)
                items.extend(
                    item.model_dump() for item in page.items if item.thread_id == request.path_params['chat_id']
                )
                if page.next_cursor is None:
                    return {'items': items, 'next_cursor': None}
                if page.next_cursor in seen:
                    raise ThreadError(502, 'reconnect')
                seen.add(page.next_cursor)
                cursor = page.next_cursor
        return (await client.feedbacks(limit=limit, cursor=cursor)).model_dump()
    refused()


class DarkChatRoute(APIRoute):
    domain = 'chats'

    def applies(self) -> bool:
        name = self.endpoint.__name__
        if self.domain in {'chats', 'evaluations'}:
            return 'config' not in name
        if self.domain == 'notes':
            return name in {'get_note_chat_by_id', 'get_note_chats_by_id', 'create_note_chat_by_id'}
        return True

    def get_route_handler(self):
        legacy = super().get_route_handler()
        if not self.applies():
            return legacy
        from open_webui.utils.auth import get_verified_user

        async def replacement(request: Request, user=Depends(get_verified_user)):
            handler = {'chats': chat, 'evaluations': evaluation}.get(self.domain)
            if handler is None:
                refused()
            result = await handler(request, user, self.path.split('/' + self.domain, 1)[-1])
            return result if isinstance(result, Response) else JSONResponse(result)

        replacement_handler = APIRoute(
            self.path,
            replacement,
            methods=list(self.methods),
            dependency_overrides_provider=self.dependency_overrides_provider,
        ).get_route_handler()

        async def dispatch(request: Request):
            if not threads.enabled():
                return await legacy(request)
            try:
                response = await replacement_handler(request)
            except ThreadError as error:
                response = JSONResponse({'state': error.state}, status_code=error.status)
            except (ValidationError, RequestValidationError, json.JSONDecodeError):
                response = JSONResponse({'state': 'invalid_request'}, status_code=422)
            except HTTPException as error:
                response = JSONResponse(
                    {'state': 'sign_in' if error.status_code == 401 else 'unavailable'}, status_code=error.status_code
                )
            response.headers.update(NO_STORE)
            return response

        return dispatch


class EvaluationRoute(DarkChatRoute):
    domain = 'evaluations'


class ExportRoute(DarkChatRoute):
    domain = 'export'


class NoteChatRoute(DarkChatRoute):
    domain = 'notes'
