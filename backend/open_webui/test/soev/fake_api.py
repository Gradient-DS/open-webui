"""Inspectable in-memory HTTP contract with signature, identity, and replay checks."""

import base64
import copy
import datetime as dt
import hashlib
import json
import re
from urllib.parse import unquote
from uuid import uuid4

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class Problem(Exception):
    def __init__(self, status, code, constraint=None):
        self.status, self.code, self.constraint = status, code, constraint


class FakeSoevApi:
    def __init__(self, *, page_size=200):
        self.page_size = page_size
        self.now = '2026-09-11T12:00:00Z'
        self.credentials = {'test-runtime-key': 'owui:service:webui'}
        self.capabilities = {'test-runtime-key': {'*'}}
        self.credential_id = 'runtime-credential'
        self.audience = None
        self.signing_keys: dict[str, dict] = {}
        self.collections, self.documents, self.folders = {}, {}, {}
        self.inherited_access = set()
        self.jobs, self.job_owners, self.job_effects = {}, {}, {}
        self.schedules, self.schedule_owners = {}, {}
        self.uploads = {}
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
        if request.method == 'PUT' and request.url.host == 'soev.invalid' and request.url.path.startswith('/uploads/'):
            return self._upload(request)
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
        parts = request.url.path.strip('/').split('/')
        ingest_create = request.method == 'POST' and parts == ['v1', 'jobs']
        if ingest_create:
            self._admit_ingest(body, credential, subject)
        if request.method == 'POST' and len(parts) == 4 and parts[:2] == ['v1', 'jobs']:
            return self._route(request, body, credential, subject)
        if (request.method == 'POST' and parts == ['v1', 'identity', 'links']) or (
            request.method == 'PUT'
            and len(parts) == 5
            and parts[:3] == ['v1', 'directory', 'groups']
            and parts[-1] == 'members'
        ):
            return self._route(request, body, credential, subject)
        key = (credential, operation)
        fingerprint = (request.method, str(request.url), body, subject)
        if key in self.replays:
            return self._replay(key, fingerprint, ingest_create)
        response = self._route(request, body, credential, subject)
        self.replays[key] = (copy.deepcopy(fingerprint), response.status_code, response.content)
        return response

    def _replay(self, key, fingerprint, ingest_create):
        previous, status, content = self.replays[key]
        if previous != fingerprint:
            raise Problem(409, 'idempotency_conflict')
        if ingest_create:
            job = self.jobs[json.loads(content)['job_id']]
            return httpx.Response(200, json=self._job_view(job))
        return httpx.Response(200 if status == 201 else status, content=content)

    def _require(self, credential, capability):
        capabilities = self.capabilities.get(credential, set())
        if '*' not in capabilities and capability not in capabilities:
            raise Problem(403, 'scope_insufficient', f'capability:{capability}')

    def _upload(self, request):
        if 'Authorization' in request.headers:
            raise Problem(400, 'bearer_on_presigned_put')
        digest = request.url.path.removeprefix('/uploads/')
        targets = [
            (job['job_id'], document)
            for job in self.jobs.values()
            for document in job.get('documents', [])
            if document.get('sha256') == digest
        ]
        if not targets:
            raise Problem(404, 'upload_not_found')
        if any(request.headers.get('Content-Length') != str(document['size']) for _, document in targets):
            raise Problem(400, 'upload_length_mismatch')
        if any(len(request.content) != document['size'] for _, document in targets):
            raise Problem(400, 'upload_length_mismatch')
        if hashlib.sha256(request.content).hexdigest() != digest:
            raise Problem(400, 'upload_digest_mismatch')
        for job_id, document in targets:
            self.uploads[job_id, document['source_id']] = request.content
        return httpx.Response(200)

    def _assertion(self, token):
        try:
            header_part, encoded, signature_part = token.split('.')
            header = json.loads(base64.urlsafe_b64decode(header_part + '=' * (-len(header_part) % 4)))
            if header['alg'] != 'Ed25519':
                raise Problem(401, 'credential_invalid')
            jwk = self.signing_keys[header['kid']]
            key = Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(jwk['x'] + '=' * (-len(jwk['x']) % 4)))
            signature = base64.urlsafe_b64decode(signature_part + '=' * (-len(signature_part) % 4))
            key.verify(signature, f'{header_part}.{encoded}'.encode())
            payload = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
            if payload['iss'] != self.credential_id or (self.audience is not None and payload['aud'] != self.audience):
                raise Problem(401, 'credential_invalid')
            ref, jti = payload['sub'], payload['jti']
        except (ValueError, KeyError, TypeError, InvalidSignature):
            raise Problem(401, 'credential_invalid') from None
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
            return self._jobs(request, parts, body, credential, subject)
        if parts == ['v1', 'schedules'] and request.method == 'GET':
            return self._schedules(request, credential, subject)
        if parts == ['v1', 'documents'] and request.method == 'GET':
            return self._lookup_documents(request, credential, subject)
        if parts == ['v1', 'collections']:
            return self._collections(request, body, credential, subject)
        if parts[:2] != ['v1', 'collections'] or len(parts) < 3:
            raise Problem(404, 'route_not_found')
        key = parts[2]
        document_route = len(parts) > 3 and parts[3] == 'documents'
        if document_route:
            return self._documents(request, parts, body, credential, subject)
        row = self._collection(key, credential, subject, write=request.method != 'GET')
        if len(parts) == 3 or parts[3] == 'access':
            return self._collection_route(request.method, parts, body, row, credential, subject)
        if parts[3] == 'folders':
            return self._folders(request, parts, body, credential, subject)
        raise Problem(404, 'route_not_found')

    def _collections(self, request, body, credential, subject):
        if request.method == 'POST':
            return self._create(body, subject)
        rows = [
            self._view(row, subject)
            for _, row in sorted(self.collections.items())
            if self._readable(row, subject or self.credentials[credential])
        ]
        return self._page(rows, request)

    def _schedules(self, request, credential, subject):
        self._require(credential, 'connect' if subject else 'mint')
        owner = subject or self.credentials[credential]
        rows = [row for key, row in self.schedules.items() if self.schedule_owners[key] == owner]
        for field in ('collection_key', 'kind'):
            if field in request.url.params:
                rows = [row for row in rows if row[field] == request.url.params[field]]
        return self._page(rows, request)

    def _lookup_documents(self, request, credential, subject):
        source_id = request.url.params.get('source_id')
        if not source_id:
            raise Problem(400, 'malformed_request')
        rows = [
            row
            for key, collection in sorted(self.collections.items())
            if self._readable(collection, subject or self.credentials[credential])
            for row in self._document_rows(key, credential, subject)
            if row['source_id'] == source_id
        ]
        return httpx.Response(200, json={'data': rows, 'next_cursor': None})

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

    def _validate_ingest(self, body):
        if not isinstance(body, dict):
            raise Problem(422, 'invalid_field', 'request:schema')
        if set(body) - {'collection_key', 'documents'}:
            raise Problem(400, 'unknown_field', 'request:extra_forbidden')
        key, documents = body.get('collection_key'), body.get('documents')
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise Problem(422, 'invalid_field', 'request:schema')
        if not isinstance(documents, list) or not 1 <= len(documents) <= 1000:
            raise Problem(422, 'invalid_field', 'request:schema')
        for document in documents:
            self._validate_document(document)

    def _admit_ingest(self, body, credential, subject):
        self._validate_ingest(body)
        self._require(credential, 'ingest')
        if subject:
            self._collection(body['collection_key'], credential, subject, write=True)

    def _validate_document(self, document):
        common = {
            'source_id',
            'filename',
            'title',
            'content_type',
            'language',
            'author',
            'source_url',
            'created_at',
            'path',
            'modified_at',
            'visibility',
            'principals',
            'metadata',
            'tags',
        }
        if not isinstance(document, dict):
            raise Problem(422, 'invalid_field', 'request:schema')
        file = 'size' in document or 'sha256' in document
        if set(document) - common - ({'size', 'sha256'} if file else {'text', 'chunks'}):
            raise Problem(400, 'unknown_field', 'request:extra_forbidden')
        for field in ('source_id', 'filename'):
            if not isinstance(document.get(field), str) or not 1 <= len(document[field]) <= 512:
                raise Problem(422, 'invalid_field', 'request:schema')
        if file:
            size, digest = document.get('size'), document.get('sha256')
            if not isinstance(size, int) or size < 1 or not isinstance(digest, str):
                raise Problem(422, 'invalid_field', 'request:schema')
            if not re.fullmatch('[0-9a-f]{64}', digest):
                raise Problem(422, 'invalid_field', 'request:schema')
        elif not isinstance(document.get('text'), str):
            raise Problem(422, 'invalid_field', 'document:one_of')

    def _create_ingest(self, body, credential, subject):
        self._collection(body['collection_key'], credential, subject, write=True)
        documents = body['documents']
        if sum(len(document.get('text', '').encode()) for document in documents) > 262144:
            raise Problem(413, 'inline_budget_exceeded', 'inline:budget')
        response = self._job('ingest', body['collection_key'], credential)
        job = self.jobs[response.json()['job_id']]
        job['documents'] = copy.deepcopy(documents)
        job['items'] = [
            {'source_id': document['source_id'], 'status': 'pending', 'code': None, 'detail': None, 'chunk_count': None}
            for document in documents
        ]
        job['progress'].update(total=len(documents), pending=len(documents))
        job['expires_at'] = self.now
        expires_at = (dt.datetime.fromisoformat(self.now) + dt.timedelta(minutes=15)).isoformat().replace('+00:00', 'Z')
        uploads = [
            {
                'source_id': document['source_id'],
                'method': 'PUT',
                'url': 'https://soev.invalid/uploads/' + document['sha256'],
                'headers': {
                    'x-amz-checksum-sha256': base64.b64encode(bytes.fromhex(document['sha256'])).decode(),
                    'Content-Length': str(document['size']),
                },
                'expires_at': expires_at,
            }
            for document in documents
            if 'size' in document
        ]
        if uploads:
            job['status'] = 'AWAITING_UPLOAD'
        return httpx.Response(201, json={**self._job_view(job), 'uploads': uploads})

    def _job_view(self, job, *, include_items=False):
        view = {key: copy.deepcopy(value) for key, value in job.items() if key not in {'documents', 'items'}}
        if job['kind'] == 'ingest':
            view['uploads'] = []
        if include_items:
            view['items'] = copy.deepcopy(job.get('items', []))
            if job['kind'] == 'delete_document':
                view['items'] = [
                    {
                        'source_id': self.job_effects[job['job_id']],
                        'status': 'pending',
                        'code': None,
                        'detail': None,
                        'chunk_count': None,
                    }
                ]
        return view

    def _jobs(self, request, parts, body, credential, subject):
        if len(parts) == 2 and request.method == 'POST':
            return self._create_ingest(body, credential, subject)
        rows = [row for jid, row in self.jobs.items() if self.job_owners[jid] == credential]
        if len(parts) >= 3:
            rows = [row for row in rows if row['job_id'] == parts[2]]
            if not rows:
                raise Problem(404, 'job_not_found')
            job = rows[0]
            if len(parts) == 4 and request.method == 'POST':
                self._require(credential, 'ingest')
                self._job_action(job, parts[3])
            elif len(parts) != 3 or request.method != 'GET':
                raise Problem(404, 'route_not_found')
            return httpx.Response(
                200,
                json=self._job_view(
                    job, include_items=request.method == 'GET' and request.url.params.get('include_items') == 'true'
                ),
            )
        for field in ('status', 'collection_key'):
            if field in request.url.params:
                rows = [row for row in rows if row[field] == request.url.params[field]]
        return self._page([self._job_view(row) for row in rows], request)

    def _job_action(self, job, action):
        if action == 'commit':
            if job['status'] == 'AWAITING_UPLOAD':
                if any(
                    (job['job_id'], document['source_id']) not in self.uploads
                    for document in job['documents']
                    if 'size' in document
                ):
                    raise Problem(409, 'upload_missing')
                self.advance(job['job_id'], 'QUEUED')
        elif action == 'cancel':
            if job['status'] in {'SUCCEEDED', 'COMPLETED_WITH_ERRORS', 'FAILED', 'CANCELLED', 'EXPIRED'}:
                raise Problem(409, 'job_already_terminal', 'job:terminal')
            if job['status'] != 'RUNNING':
                self.advance(job['job_id'], 'CANCELLED')
        else:
            raise Problem(404, 'route_not_found')

    def advance(self, job_id, status, *, item_code=None, item_detail=None):
        job = self.jobs[job_id]
        previous = job['status']
        job.update(status=status, updated_at=self.now)
        if job['kind'] == 'ingest':
            self._advance_ingest(job, previous, item_code, item_detail)
            return
        if status == 'CANCELLED':
            job['progress']['skipped'] += job['progress']['pending']
            job['progress']['pending'] = 0
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

    def _advance_ingest(self, job, previous, item_code, item_detail):
        status = job['status']
        if status == 'SUCCEEDED' and previous != 'SUCCEEDED':
            for item, document in zip(job['items'], job['documents']):
                item.update(status='succeeded', code=None, detail=None, chunk_count=1)
                fields = {
                    key: copy.deepcopy(value)
                    for key, value in document.items()
                    if key not in {'source_id', 'size', 'text', 'chunks'} and value is not None
                }
                if 'size' in document:
                    fields['byte_size'] = document['size']
                self.add_document(
                    job['collection_key'],
                    document['source_id'],
                    **fields,
                    chunk_count=1,
                    last_job_id=job['job_id'],
                    ingested_at=self.now,
                )
            job['progress'].update(succeeded=len(job['items']), failed=0, pending=0, skipped=0)
        elif status == 'COMPLETED_WITH_ERRORS':
            for item in job['items']:
                item.update(status='failed', code=item_code, detail=item_detail, chunk_count=None)
            job['progress'].update(succeeded=0, failed=len(job['items']), pending=0, skipped=0)
        elif status in {'FAILED', 'CANCELLED', 'EXPIRED'}:
            for item in job['items']:
                if item['status'] == 'pending':
                    item['status'] = 'skipped'
            job['progress']['skipped'] += job['progress']['pending']
            job['progress']['pending'] = 0

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
        self._document_collection(parts, credential, subject)
        rows = self._document_rows(key, credential, subject)
        if len(parts) == 4:
            return self._page(rows, request)
        document = next((row for row in rows if row['source_id'] == parts[4]), None)
        if document is None:
            raise Problem(404, 'document_not_found')
        self._collection(key, credential, subject, write=request.method != 'GET')
        if request.method == 'GET':
            if len(parts) == 5:
                return httpx.Response(200, json=document)
            if len(parts) == 6 and parts[-1] == 'content':
                if document.get('rendition') is None:
                    raise Problem(409, 'rendition_missing')
                return httpx.Response(
                    200, text=document['rendition'], headers={'Content-Type': 'text/markdown; charset=utf-8'}
                )
        if request.method == 'DELETE':
            return self._job('delete_document', key, credential, parts[4])
        if request.method == 'POST' and parts[-1] == 'move':
            self._mkdir(key, body['to'])
            document['path'] = body['to'] or None
            return httpx.Response(204)
        raise Problem(404, 'route_not_found')

    def _document_collection(self, parts, credential, subject):
        try:
            self._collection(parts[2], credential, subject)
        except Problem as error:
            if len(parts) == 6 and parts[-1] == 'content' and error.status == 404:
                raise Problem(404, 'document_not_found') from None
            raise

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
