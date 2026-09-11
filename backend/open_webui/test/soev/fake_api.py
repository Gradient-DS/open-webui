"""Inspectable in-memory implementation of the recorded B5 HTTP contract.

Assertions are decoded for identity and replay checks; cryptography is tested by B2.
"""

import base64
import copy
import json
from urllib.parse import unquote
from uuid import uuid4

import httpx


class Problem(Exception):
    def __init__(self, status, code, constraint=None):
        self.status, self.code, self.constraint = status, code, constraint


class FakeSoevApi:
    def __init__(self, *, page_size=200):
        self.page_size = page_size
        self.now = '2026-09-11T12:00:00Z'
        self.credentials = {'test-runtime-key': 'owui:service:webui'}
        self.collections, self.documents, self.folders = {}, {}, {}
        self.inherited_access = set()
        self.jobs, self.job_owners, self.job_effects = {}, {}, {}
        self.links, self.groups, self.replays, self.creation_bodies = {}, {}, {}, {}
        self.seen_jtis = set()
        self.requests, self.failures = [], []

    def handle(self, request):
        self.requests.append(request)
        try:
            if self.failures:
                raise Problem(self.failures.pop(0), 'injected_failure')
            return self._handle(request)
        except Problem as error:
            body = {'status': error.status, 'code': error.code, 'title': error.code, 'detail': error.code}
            if error.constraint:
                body['constraint'] = error.constraint
            return httpx.Response(error.status, json=body, headers={'Content-Type': 'application/problem+json'})

    def _handle(self, request):
        credential = request.headers.get('Authorization', '').removeprefix('Bearer ')
        if credential not in self.credentials:
            raise Problem(401, 'invalid_credential')
        subject = self._assertion(request.headers['X-Soev-Subject']) if 'X-Soev-Subject' in request.headers else None
        if subject and subject not in self.links:
            raise Problem(401, 'identity_not_linked')
        body = json.loads(request.content) if request.content else None
        if request.method == 'GET':
            return self._route(request, body, credential, subject)
        operation = request.headers.get('Idempotency-Key', '')
        if not 8 <= len(operation) <= 255:
            raise Problem(400, 'invalid_idempotency_key')
        key = (credential, operation)
        fingerprint = (request.method, str(request.url), body, subject)
        if key in self.replays:
            previous, status, content = self.replays[key]
            if previous != fingerprint:
                raise Problem(409, 'idempotency_conflict')
            return httpx.Response(200 if status == 201 else status, content=content)
        response = self._route(request, body, credential, subject)
        self.replays[key] = (copy.deepcopy(fingerprint), response.status_code, response.content)
        return response

    def _assertion(self, token):
        try:
            encoded = token.split('.')[1]
            payload = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
            ref, jti = payload['sub'], payload['jti']
        except (ValueError, KeyError, IndexError):
            raise Problem(401, 'invalid_assertion') from None
        if jti in self.seen_jtis:
            raise Problem(401, 'assertion_replayed')
        self.seen_jtis.add(jti)
        return ref

    def _closure(self, ref):
        return {ref} | {group for group, members in self.groups.items() if ref in members}

    def _readable(self, row, ref):
        return row['visibility'] == 'public' or bool(self._closure(ref).intersection(row['principals']))

    def _collection(self, key, credential, subject, *, write=False):
        row = self.collections.get(key)
        if row is None or not self._readable(row, subject or self.credentials[credential]):
            raise Problem(404, 'collection_not_found')
        if write and subject and not self._closure(subject).intersection(row['writers']):
            raise Problem(403, 'scope_insufficient', 'collection:writers')
        return row

    def _view(self, row, subject):
        return {
            **copy.deepcopy(row),
            'document_count': sum(key == row['key'] for key, _ in self.documents),
            'caller_may_write': bool(self._closure(subject).intersection(row['writers'])) if subject else None,
        }

    def _slice(self, rows, request):
        try:
            limit = int(request.url.params.get('limit', self.page_size))
            start = int(request.url.params.get('cursor', 0))
        except ValueError:
            raise Problem(422, 'invalid_field') from None
        if not 1 <= limit <= 200 or start < 0:
            raise Problem(422, 'invalid_field')
        end = start + limit
        return rows[start:end], str(end) if end < len(rows) else None

    def _page(self, rows, request):
        data, cursor = self._slice(rows, request)
        return httpx.Response(200, json={'data': data, 'next_cursor': cursor})

    def _route(self, request, body, credential, subject):
        parts = [unquote(part) for part in request.url.raw_path.decode().split('?')[0].strip('/').split('/')]
        if parts[:2] == ['v1', 'identity'] or parts[:2] == ['v1', 'directory']:
            return self._identity(request.method, parts, body)
        if parts[:2] == ['v1', 'jobs']:
            return self._jobs(request, parts, credential)
        if parts == ['v1', 'collections']:
            if request.method == 'POST':
                return self._create(body, subject)
            rows = [
                self._view(row, subject)
                for _, row in sorted(self.collections.items())
                if self._readable(row, subject or self.credentials[credential])
            ]
            return self._page(rows, request)
        if parts[:2] != ['v1', 'collections'] or len(parts) < 3:
            raise Problem(404, 'route_not_found')
        key = parts[2]
        document_route = len(parts) > 3 and parts[3] == 'documents'
        row = self._collection(key, credential, subject, write=request.method != 'GET' and not document_route)
        if len(parts) == 3 or parts[3] == 'access':
            return self._collection_route(request.method, parts, body, row, credential, subject)
        if parts[3] == 'documents':
            return self._documents(request, parts, body, credential, subject)
        if parts[3] == 'folders':
            return self._folders(request, parts, body, credential, subject)
        raise Problem(404, 'route_not_found')

    def _identity(self, method, parts, body):
        if parts == ['v1', 'identity', 'links'] and method == 'POST':
            ref = self._assertion(body['assertion'])
            if not ref.startswith('owui:user:'):
                raise Problem(422, 'invalid_field')
            self.links[ref] = body['platform_user_id']
            return httpx.Response(204)
        if len(parts) == 5 and parts[1:3] == ['directory', 'groups'] and parts[-1] == 'members' and method == 'PUT':
            if not parts[3].startswith('owui:group:') or any(
                not ref.startswith('owui:user:') for ref in body['members']
            ):
                raise Problem(422, 'invalid_field')
            self.groups[parts[3]] = sorted(set(body['members']))
            return httpx.Response(204)
        raise Problem(404, 'route_not_found')

    def _create(self, body, subject):
        key = body['key']
        if key in self.collections:
            if self.creation_bodies[key] != (body, subject):
                raise Problem(409, 'collection_exists')
            return httpx.Response(200, json=self._view(self.collections[key], subject))
        self.creation_bodies[key] = copy.deepcopy((body, subject))
        row = {
            'key': key,
            'name': body['name'],
            'description': body.get('description'),
            'schema_key': 'core',
            'embedder': 'recorded',
            'chunking': {},
            'metadata_spec': {},
            'visibility': body['visibility'],
            'principals': sorted(set(body['principals']) | ({subject} if subject else set())),
            'writers': sorted(set(body['writers']) | ({subject} if subject else set())),
            'created_by': subject,
            'retention_days': None,
            'tags': body.get('tags', []),
            'created_at': self.now,
            'updated_at': self.now,
        }
        self.collections[key], self.folders[key] = row, {}
        return httpx.Response(201, json=self._view(row, subject))

    def _collection_route(self, method, parts, body, row, credential, subject):
        if method == 'GET':
            return httpx.Response(200, json=self._view(row, subject))
        if method == 'PATCH':
            if set(body) - {'name', 'description', 'tags', 'retention_days', 'writers'}:
                raise Problem(400, 'unknown_field')
            row.update(copy.deepcopy(body), updated_at=self.now)
            return httpx.Response(200, json=self._view(row, subject))
        if method == 'DELETE':
            return self._job('delete_collection', row['key'], credential)
        if method == 'PUT' and parts[-1] == 'access':
            return self._job('update_collection_access', row['key'], credential, copy.deepcopy(body))
        raise Problem(404, 'route_not_found')

    def _job(self, kind, key, credential, effect=None):
        job_id = str(uuid4())
        job = {
            'job_id': job_id,
            'kind': kind,
            'status': 'QUEUED',
            'collection_key': key,
            'progress': {'total': 1, 'succeeded': 0, 'failed': 0, 'pending': 1, 'skipped': 0},
            'created_at': self.now,
            'updated_at': self.now,
            'expires_at': None,
        }
        self.jobs[job_id], self.job_owners[job_id], self.job_effects[job_id] = job, credential, effect
        return httpx.Response(202, json=job)

    def _jobs(self, request, parts, credential):
        rows = [row for jid, row in self.jobs.items() if self.job_owners[jid] == credential]
        if len(parts) == 3:
            rows = [row for row in rows if row['job_id'] == parts[2]]
            if not rows:
                raise Problem(404, 'job_not_found')
            return httpx.Response(200, json=rows[0])
        for field in ('status', 'collection_key'):
            if field in request.url.params:
                rows = [row for row in rows if row[field] == request.url.params[field]]
        return self._page(rows, request)

    def advance(self, job_id, status):
        job = self.jobs[job_id]
        previous = job['status']
        job.update(status=status, updated_at=self.now)
        if status != 'SUCCEEDED' or previous == 'SUCCEEDED':
            return
        job['progress'].update(succeeded=1, pending=0)
        key, effect = job['collection_key'], self.job_effects[job_id]
        if job['kind'] == 'delete_collection':
            self.collections.pop(key, None)
            self.folders.pop(key, None)
            self.documents = {pair: row for pair, row in self.documents.items() if pair[0] != key}
        elif job['kind'] == 'delete_document':
            self.documents.pop((key, effect), None)
        elif job['kind'] == 'update_collection_access':
            self.collections[key].update(effect, updated_at=self.now)
            for pair in self.inherited_access:
                if pair[0] == key and pair in self.documents:
                    self.documents[pair].update(copy.deepcopy(effect))

    def add_document(self, key, source_id, **fields):
        collection = self.collections[key]
        if 'principals' not in fields and 'visibility' not in fields:
            self.inherited_access.add((key, source_id))
        row = {
            'source_id': source_id,
            'collection_key': key,
            'filename': source_id + '.txt',
            'visibility': collection['visibility'],
            'principals': list(collection['principals']),
            'chunk_count': None,
            'ingested_at': self.now,
            'last_job_id': None,
            'title': None,
            'content_type': 'text/plain',
            'language': None,
            'author': None,
            'source_url': None,
            'path': None,
            'created_at': None,
            'modified_at': None,
            'metadata': {},
            'tags': [],
            'byte_size': None,
            'sha256': None,
            **fields,
        }
        self.documents[key, source_id] = row
        self._mkdir(key, row['path'] or '')
        return row

    def _document_rows(self, key, credential, subject):
        return [
            row
            for (collection, _), row in sorted(self.documents.items())
            if collection == key and self._readable(row, subject or self.credentials[credential])
        ]

    def _documents(self, request, parts, body, credential, subject):
        key = parts[2]
        rows = self._document_rows(key, credential, subject)
        if len(parts) == 4:
            return self._page(rows, request)
        document = next((row for row in rows if row['source_id'] == parts[4]), None)
        if document is None:
            raise Problem(404, 'document_not_found')
        self._collection(key, credential, subject, write=request.method != 'GET')
        if request.method == 'DELETE':
            return self._job('delete_document', key, credential, parts[4])
        if request.method == 'POST' and parts[-1] == 'move':
            self._mkdir(key, body['to'])
            document['path'] = body['to'] or None
            return httpx.Response(204)
        raise Problem(404, 'route_not_found')

    def _mkdir(self, key, path):
        segments = path.split('/') if path else []
        for end in range(1, len(segments) + 1):
            self.folders[key].setdefault('/'.join(segments[:end]), self.now)

    def _folders(self, request, parts, body, credential, subject):
        key = parts[2]
        if request.method == 'GET':
            under = request.url.params.get('under', '')
            folders = [
                ('folder', {'path': path, 'created_at': at})
                for path, at in sorted(self.folders[key].items())
                if path.rpartition('/')[0] == under
            ]
            documents = [
                ('document', row)
                for row in self._document_rows(key, credential, subject)
                if (row['path'] or '') == under
            ]
            rows, cursor = self._slice(folders + documents, request)
            return httpx.Response(
                200,
                json={
                    'folders': [row for kind, row in rows if kind == 'folder'],
                    'documents': [row for kind, row in rows if kind == 'document'],
                    'next_cursor': cursor,
                },
            )
        if request.method == 'POST' and parts[-1] == 'move':
            return self._move_folder(key, body['from'], body['to'])
        if request.method == 'POST':
            existed = body['path'] in self.folders[key]
            self._mkdir(key, body['path'])
            return httpx.Response(200 if existed else 201, json={'path': body['path']})
        path = request.url.params['path']
        if path not in self.folders[key]:
            raise Problem(404, 'folder_not_found')
        occupied = any(folder.startswith(path + '/') for folder in self.folders[key])
        occupied |= any(
            (row['path'] or '') == path or (row['path'] or '').startswith(path + '/')
            for (collection, _), row in self.documents.items()
            if collection == key
        )
        if occupied:
            raise Problem(409, 'folder_not_empty')
        del self.folders[key][path]
        return httpx.Response(204)

    def _move_folder(self, key, old, new):
        if old not in self.folders[key]:
            raise Problem(404, 'folder_not_found')
        if new.startswith(old + '/') or (new in self.folders[key] and new != old):
            raise Problem(409, 'folder_conflict')
        for path in list(self.folders[key]):
            if path == old or path.startswith(old + '/'):
                self.folders[key][new + path[len(old) :]] = self.folders[key].pop(path)
        self._mkdir(key, new)
        for (collection, _), row in self.documents.items():
            path = row['path'] or ''
            if collection == key and (path == old or path.startswith(old + '/')):
                row['path'] = new + path[len(old) :]
        return httpx.Response(204)
