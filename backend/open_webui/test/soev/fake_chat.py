"""Model the chat relay contract while FakeSoevApi admits each request."""

import asyncio
import copy
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx


def frame(kind: str, position: int, payload: dict[str, Any], *, stream: str = 'root') -> dict[str, Any]:
    return {
        'type': kind,
        'position': position,
        'id': f'{stream}:{position - 1}',
        'stream': stream,
        'seq': position - 1,
        'actor': 'user' if kind == 'input' else 'model',
        'origin': None,
        'ts': '2026-09-19T12:00:00Z',
        'payload': payload,
    }


def sse(kind: str, data: dict[str, Any]) -> bytes:
    position = f'id: {data["position"]}\n' if kind != 'status' and 'position' in data else ''
    return f'{position}event: {kind}\ndata: {json.dumps(data)}\n\n'.encode()


class Tail(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.content
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


class FakeChatApi:
    def __init__(self) -> None:
        self.threads: dict[str, dict[str, Any]] = {}
        self.turns: list[list[tuple[str, dict[str, Any]]]] = []
        self.requests: list[httpx.Request] = []
        self.close_after: int | None = None
        self.tails: list[Tail] = []
        self.terminal_state = 'idle'

    def handle(self, request: httpx.Request, body: dict | None, owner: tuple[str, str | None]) -> httpx.Response:
        self.requests.append(request)
        parts = request.url.path.split('/')[4:]
        if not parts or parts == ['']:
            return self._open(request, body or {}, owner)
        thread = self.threads.get(parts[0])
        if thread is None or thread['owner'] != owner:
            return self.problem(404, 'not_found')
        operation = parts[1] if len(parts) > 1 else 'read'
        return self._operation(request, body or {}, thread, operation)

    @staticmethod
    def problem(status: int, code: str, detail: str | None = None) -> httpx.Response:
        return httpx.Response(
            status,
            json={'status': status, 'code': code, 'detail': detail or code},
            headers={'Content-Type': 'application/problem+json'},
        )

    def _new(self, owner: tuple[str, str | None], events: list[dict]) -> dict[str, Any]:
        thread_id = f'thr-{len(self.threads) + 1}'
        thread = {'thread_id': thread_id, 'owner': owner, 'events': events, 'state': 'idle'}
        self.threads[thread_id] = thread
        return thread

    def _open(self, request: httpx.Request, body: dict, owner: tuple[str, str | None]) -> httpx.Response:
        if request.method != 'POST' or not isinstance(body.get('input'), str) or 'agent' not in body:
            return self.problem(422, 'invalid_field')
        thread = self._new(owner, [frame('opened', 1, {'agent': body['agent']})])
        return self._run(request, body, thread, opening=True)

    def _operation(self, request: httpx.Request, body: dict, thread: dict, operation: str) -> httpx.Response:
        if operation == 'read':
            return httpx.Response(200, json=self._view(thread))
        if operation == 'events':
            after = int(request.headers.get('Last-Event-ID', '0'))
            tail = Tail(b''.join(sse(event['type'], event) for event in thread['events'] if event['position'] > after))
            self.tails.append(tail)
            return httpx.Response(200, stream=tail, headers={'Content-Type': 'text/event-stream'})
        if operation == 'fork':
            at = body.get('at', 0)
            if not 1 <= at <= len(thread['events']):
                return self.problem(422, 'invalid_field')
            branch = self._new(thread['owner'], copy.deepcopy(thread['events'][:at]))
            return httpx.Response(201, json=self._view(branch), headers=self._headers(branch))
        if operation == 'inputs':
            if thread['state'] in {'running', 'orphaned'}:
                return self.problem(409, 'thread_active', thread['state'])
            return self._run(request, body, thread)
        if operation == 'resume':
            thread['state'] = 'idle'
            return self._response(request, thread, [])
        if operation == 'cancel':
            return self._cancel(thread, body)
        return self.problem(404, 'not_found')

    def _cancel(self, thread: dict, body: dict) -> httpx.Response:
        inputs = [event['position'] for event in thread['events'] if event['type'] == 'input']
        if not inputs or body.get('input') != inputs[-1] or thread['state'] not in {'running', 'waiting'}:
            return self.problem(409, 'thread_active')
        thread['events'].append(frame('cancelled', len(thread['events']) + 1, {}))
        thread['state'] = 'idle'
        return httpx.Response(200, json=self._view(thread))

    def _run(self, request: httpx.Request, body: dict, thread: dict, *, opening: bool = False) -> httpx.Response:
        if not isinstance(body.get('input'), str) or not isinstance(body.get('collections'), list):
            return self.problem(422, 'invalid_field')
        recorded = [('opened', thread['events'][0])] if opening else []
        event = frame('input', len(thread['events']) + 1, {'payload': body['input']})
        thread['events'].append(event)
        recorded.append(('input', event))
        turn = self.turns.pop(0) if self.turns else [('model_output', {'content': f'Answer: {body["input"]}'})]
        for kind, payload in turn:
            if kind in {'delta', 'error'}:
                recorded.append((kind, payload))
            else:
                event = frame(kind, len(thread['events']) + 1, payload)
                thread['events'].append(event)
                recorded.append((kind, event))
        thread['state'] = self.terminal_state
        return self._response(request, thread, recorded, opening=opening)

    def _response(
        self, request: httpx.Request, thread: dict, recorded: list[tuple[str, dict]], *, opening: bool = False
    ) -> httpx.Response:
        headers = self._headers(thread) if opening else {}
        if request.headers.get('Accept') != 'text/event-stream':
            return httpx.Response(200, json=self._view(thread), headers=headers)
        content = b''.join(sse(kind, event) for kind, event in recorded)
        if self.close_after is not None:
            content = b''.join(sse(kind, event) for kind, event in recorded[: self.close_after]) + b': stream max\n\n'
            self.close_after = None
        else:
            content += sse('status', self._status(thread))
        return httpx.Response(
            201 if opening else 200, content=content, headers={**headers, 'Content-Type': 'text/event-stream'}
        )

    @staticmethod
    def _headers(thread: dict) -> dict[str, str]:
        return {'Location': f'/v1/chat/threads/{thread["thread_id"]}', 'X-Soev-Thread-Id': thread['thread_id']}

    @staticmethod
    def _status(thread: dict) -> dict:
        return {'state': thread['state'], 'pending': [], 'position': len(thread['events'])}

    def _view(self, thread: dict) -> dict:
        return {'thread_id': thread['thread_id'], 'status': self._status(thread), 'events': thread['events']}
