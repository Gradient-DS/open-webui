"""Knowledge persistence through soev-api, with OWUI file rows retained by source_id.

Configuration and model imports are deferred so either singleton import order is safe.
"""

import asyncio
import datetime as dt
import hashlib
import json
import re
from urllib.parse import quote, urlencode
from uuid import uuid4

from fastapi import HTTPException

from open_webui.soev.acting import acting_ref
from open_webui.soev.client import SoevApiError
from open_webui.soev.request_cache import memoized


def _sort_file_rows(rows, filters, *, default_order='filename', default_descending=False):
    order = {'name': 'filename', 'created_at': 'created_at', 'updated_at': 'updated_at'}.get(filters.get('order_by'))
    descending = filters.get('direction') != 'asc' if order else default_descending
    field = order or default_order
    rows.sort(key=lambda row: row['id'])
    rows.sort(key=lambda row: (row[field] is not None, row[field]), reverse=descending)


def _catalog_file_row(document: dict, owner_id: str) -> dict:
    filename = document.get('filename') or document.get('title') or document['source_id']
    timestamp = int(dt.datetime.fromisoformat(document['ingested_at']).timestamp())
    return {
        'id': document['source_id'],
        'user_id': owner_id,
        'hash': None,
        'filename': filename,
        'meta': {
            'name': filename,
            'content_type': document.get('content_type'),
            'status': 'completed',
            'source_url': document.get('source_url'),
            'soev_catalog_only': True,
        },
        'created_at': timestamp,
        'updated_at': timestamp,
    }


def _matches_catalog_file(row: dict, filters: dict, user_id: str | None) -> bool:
    query = filters.get('query') or ''
    pattern = ''.join('.*' if char == '%' else '.' if char == '_' else re.escape(char) for char in query)
    if re.search(pattern, row['filename'], re.IGNORECASE | re.DOTALL) is None:
        return False
    view = filters.get('view_option')
    owned = row['user_id'] == user_id
    return (view != 'created' or owned) and (view != 'shared' or not owned)


class NotOnSoev(NotImplementedError):
    def __init__(self, method: str, *, moves_with: str):
        super().__init__(f'{method} is not available on soev-api; moves with {moves_with}')
        self.method, self.moves_with = method, moves_with


class SoevKnowledgeTable:
    def __init__(self, *, client=None, service_principal=None):
        self._configured_client = client
        self._configured_principal = service_principal

    @property
    def _client(self):
        if self._configured_client is None:
            from open_webui.soev import identity

            self._configured_client = identity.build_client()
        return self._configured_client

    @property
    def _service_principal(self):
        if self._configured_principal is None:
            from open_webui import config

            return config.SOEV_API_SERVICE_PRINCIPAL
        return self._configured_principal

    @property
    def _projection(self):
        from open_webui.soev import projection

        return projection

    async def _as_user(self, explicit_user_id: str | None = None) -> str | None:
        ref = f'owui:user:{explicit_user_id}' if explicit_user_id else acting_ref()
        if ref is not None:
            from open_webui.soev import identity

            await identity.ensure_link(ref, self._client)
        return ref

    async def _get(self, path, *, user_id=None, params=None):
        return await self._client.get(path, as_user=await self._as_user(user_id), params=params)

    async def _pages(self, path, *, user_id=None, params=None, as_service=False):
        ref = None if as_service else await self._as_user(user_id)
        return [row async for row in self._client.pages(path, as_user=ref, params=params)]

    async def _send(self, method, path, body=None, *, user_id=None, idempotency_key=None):
        if idempotency_key is None:
            operation = json.dumps([method, path, body], sort_keys=True, separators=(',', ':'))
            idempotency_key = hashlib.sha256(operation.encode()).hexdigest()
        return await self._client.send(
            method, path, body, as_user=await self._as_user(user_id), idempotency_key=idempotency_key
        )

    @staticmethod
    def _path(key):
        return '/v1/collections/' + quote(key, safe='')

    async def _collection(self, key, *, user_id=None):
        try:
            return await self._get(self._path(key), user_id=user_id)
        except SoevApiError as error:
            if error.status == 404:
                return None
            raise

    async def _collections(self, *, user_id=None, as_service=False):
        from open_webui.soev import ingest

        rows, queued, running = await asyncio.gather(
            self._pages('/v1/collections', user_id=user_id, as_service=as_service),
            self._pages('/v1/jobs', user_id=user_id, params={'status': 'QUEUED'}, as_service=as_service),
            self._pages('/v1/jobs', user_id=user_id, params={'status': 'RUNNING'}, as_service=as_service),
        )
        hidden = {job['collection_key'] for job in queued + running if job['kind'] == 'delete_collection'}
        return [row for row in rows if row['key'] not in hidden and not ingest.is_attachments_collection(row['key'])]

    async def _types(self, *, user_id=None, collection_key=None):
        params = {'collection_key': collection_key} if collection_key is not None else None
        schedules = await self._pages('/v1/schedules', user_id=user_id, params=params)
        return self._projection.types_from_schedules(schedules)

    async def _fallback_types(self, rows, *, user_id=None):
        return await self._types(user_id=user_id) if any(not row.get('subscriptions') for row in rows) else {}

    def _knowledge(self, row, types=None):
        return (
            self._projection.knowledge_of(row, service_principal=self._service_principal, types=types) if row else None
        )

    async def _knowledge_with_type(self, row, *, user_id=None):
        if row is None:
            return None
        if row.get('subscriptions'):
            return self._knowledge(row)
        return self._knowledge(row, await self._types(user_id=user_id, collection_key=row['key']))

    def _user_knowledge(self, row, owners, types):
        return self._projection.knowledge_user_of(
            row, service_principal=self._service_principal, user=owners.get(self._knowledge(row).user_id), types=types
        )

    async def _owners(self, rows):
        from open_webui.models.users import Users

        ids = {self._knowledge(row).user_id for row in rows} - {''}
        users = await Users.get_users_by_user_ids(sorted(ids)) if ids else []
        return {user.id: user.model_dump() for user in users}

    @staticmethod
    def _collection_sort(row, field):
        return row[field] if field == 'name' else dt.datetime.fromisoformat(row[field]).timestamp()

    async def insert_new_knowledge(self, user_id, form_data, db=None):
        body = self._projection.collection_create_body(form_data, service_principal=self._service_principal)
        body['key'] = str(uuid4())
        return self._knowledge(
            await self._send('POST', '/v1/collections', body, user_id=user_id, idempotency_key='kb:' + body['key'])
        )

    async def get_knowledge_by_id(self, id, db=None):
        return await self._knowledge_with_type(await self._collection(id))

    async def get_knowledge_by_id_unfiltered(self, id, db=None):
        return await self._knowledge_with_type(await self._collection(id))

    async def get_knowledge_bases(self, skip=0, limit=30, db=None):
        rows = sorted(await self._collections(), key=lambda row: self._collection_sort(row, 'updated_at'), reverse=True)
        owners = await self._owners(rows)
        types = await self._fallback_types(rows)
        return [self._user_knowledge(row, owners, types) for row in rows]

    async def search_knowledge_bases(self, user_id, filter, skip=0, limit=30, db=None):
        rows = await self._collections(user_id=user_id)
        owners = await self._owners(rows)
        types = await self._fallback_types(rows, user_id=user_id)
        rows = [
            row
            for row in rows
            if self._matches_collection(
                row,
                user_id,
                filter or {},
                owners.get(self._knowledge(row).user_id, {}),
                self._knowledge(row, types).type,
            )
        ]
        order = (filter or {}).get('order_by')
        if order not in ('name', 'created_at', 'updated_at'):
            order = 'updated_at'
        ascending = ((filter or {}).get('direction') or 'desc').lower() == 'asc'
        rows.sort(key=lambda row: row['key'])
        rows.sort(key=lambda row: self._collection_sort(row, order), reverse=not ascending)
        total = len(rows)
        rows = rows[skip : skip + limit] if limit else rows[skip:]
        return self._projection.knowledge_list_of([self._user_knowledge(row, owners, types) for row in rows], total)

    @staticmethod
    def _matches_collection(row, user_id, filters, owner, knowledge_type):
        owned = row.get('created_by') == f'owui:user:{user_id}'
        if filters.get('type') not in (None, '', knowledge_type) or filters.get('source') == 'external':
            return False
        if filters.get('view_option') == 'created' and not owned:
            return False
        if filters.get('view_option') == 'shared' and owned:
            return False
        query = (filters.get('query') or '').casefold()
        values = [row['name'], row['description']] + [owner.get(field) for field in ('name', 'email', 'username')]
        return any(query in (value or '').casefold() for value in values)

    async def get_knowledge_bases_by_type(self, type, db=None):
        rows = await self._collections()
        types = await self._fallback_types(rows)
        return [knowledge for row in rows if (knowledge := self._knowledge(row, types)).type == type]

    async def get_knowledge_bases_by_user_id(self, user_id, permission='write', db=None):
        rows = [
            row
            for row in await self._collections(user_id=user_id)
            if permission == 'read' or (permission == 'write' and row.get('caller_may_write'))
        ]
        owners = await self._owners(rows)
        types = await self._fallback_types(rows, user_id=user_id)
        return [self._user_knowledge(row, owners, types) for row in rows]

    async def get_knowledge_items_by_user_id(self, user_id, db=None):
        rows = await self._collections(user_id=user_id)
        types = await self._fallback_types(rows, user_id=user_id)
        return [self._knowledge(row, types) for row in rows if row.get('created_by') == f'owui:user:{user_id}']

    async def get_knowledge_by_id_and_user_id(self, id, user_id, db=None):
        row = await self._collection(id, user_id=user_id)
        return await self._knowledge_with_type(row, user_id=user_id) if row and row.get('caller_may_write') else None

    async def check_access_by_user_id(self, id, user_id, permission='write', db=None, user_group_ids=None):
        row = await self._collection(id, user_id=user_id)
        return bool(row and (permission == 'read' or (permission == 'write' and row.get('caller_may_write'))))

    async def accessible_collection_ids(self, user_id, resource_ids, permission='read'):
        requested = set(resource_ids)
        return {
            row['key']
            for row in await self._collections(user_id=user_id)
            if row['key'] in requested
            and (permission == 'read' or (permission == 'write' and row.get('caller_may_write')))
        }

    def collection_grants(self, row):
        return self._projection.grants_of(row, service_principal=self._service_principal) if row else []

    async def get_collection_grants(self, id):
        try:
            row = await self._client.get(self._path(id))
        except SoevApiError as error:
            if error.status == 404:
                return []
            raise
        return self.collection_grants(row)

    async def set_collection_access(self, id, access_grants, *, fields=None):
        current = await self._collection(id)
        if current is None:
            return None
        visibility, readers, writers = self._projection.access_of(
            access_grants, service_principal=self._service_principal
        )
        owner = current.get('created_by')
        owners = {owner} if owner else set()
        body = {**(fields or {}), 'writers': sorted(set(writers) | owners)}
        access = {'visibility': visibility, 'principals': sorted(set(readers) | owners)}
        result = await self._send('PATCH', self._path(id), body)
        await self._send('PUT', self._path(id) + '/access', access)
        return result

    async def update_knowledge_by_id(self, id, form_data, overwrite=False, db=None):
        body = {'name': form_data.name, 'description': form_data.description}
        if form_data.access_grants is not None:
            result = await self.set_collection_access(id, form_data.access_grants, fields=body)
        else:
            result = await self._send('PATCH', self._path(id), body)
        return await self._knowledge_with_type(result)

    async def delete_knowledge_by_id(self, id, db=None):
        await self._send('DELETE', self._path(id), idempotency_key='kb-delete:' + id)
        return True

    async def soft_delete_by_id(self, id, db=None):
        return await self.delete_knowledge_by_id(id)

    async def soft_delete_by_user_id(self, user_id, db=None):
        rows = await self.get_knowledge_items_by_user_id(user_id)
        for row in rows:
            await self._send('DELETE', self._path(row.id), user_id=user_id, idempotency_key='kb-delete:' + row.id)
        return len(rows)

    async def delete_all_knowledge(self, db=None):
        for row in await self._collections():
            await self.delete_knowledge_by_id(row['key'])
        return True

    async def _documents(self, key, *, user_id=None):
        try:
            return await self._pages(self._path(key) + '/documents', user_id=user_id)
        except SoevApiError as error:
            if error.status == 404:
                return []
            raise

    async def _unlanded(self, key, *, documents=None, collection=None, user_id=None, files=None):
        from open_webui.models.files import Files

        if files is None:
            files = await Files.get_unlanded_files_for_collection(key)
        if not files or (collection is None and await self._collection(key, user_id=user_id) is None):
            return []
        if documents is None:
            documents = await self._documents(key, user_id=user_id)
        landed = {doc['source_id'] for doc in documents}
        return [file for file in files if file.id not in landed]

    async def _deleted_sources(self, key, *, user_id=None):
        listings = await asyncio.gather(
            *(
                self._pages('/v1/jobs', user_id=user_id, params={'collection_key': key, 'status': status})
                for status in ('QUEUED', 'RUNNING')
            )
        )
        details = await asyncio.gather(
            *(
                self._get(
                    '/v1/jobs/' + quote(job['job_id'], safe=''), user_id=user_id, params={'include_items': 'true'}
                )
                for jobs in listings
                for job in jobs
                if job['kind'] == 'delete_document'
            )
        )
        return {item['source_id'] for detail in details for item in detail['items']}

    async def _members(self, key, *, user_id=None):
        return await memoized(
            ('members', self, f'owui:user:{user_id}' if user_id else acting_ref(), key),
            lambda: self._load_members(key, user_id=user_id),
        )

    async def _load_members(self, key, *, user_id=None):
        documents, deleted = await asyncio.gather(
            self._documents(key, user_id=user_id), self._deleted_sources(key, user_id=user_id)
        )
        members = {doc['source_id']: (doc, doc['path']) for doc in documents}
        for file in await self._unlanded(key, documents=documents, user_id=user_id):
            job = (file.meta or {}).get('soev_job') or {}
            members[file.id] = (None, job.get('path'))
        return {source: member for source, member in members.items() if source not in deleted}

    # Catalog-only rows are listed through search and downloaded through catalog_content.
    async def get_files_by_id(self, knowledge_id, db=None):
        from open_webui.models.files import Files

        ids = list(await self._members(knowledge_id))
        return await Files.get_files_by_ids(ids) if ids else []

    async def get_file_metadatas_by_id(self, knowledge_id, db=None):
        from open_webui.models.files import Files

        ids = list(await self._members(knowledge_id))
        return await Files.get_file_metadatas_by_ids(ids) if ids else []

    async def _unlanded_files_by_collection(self, keys):
        from sqlalchemy import select

        from open_webui.internal.db import get_async_db_context
        from open_webui.models.files import File, FileModel

        grouped = {key: [] for key in keys}
        if not grouped:
            return grouped
        stored_keys = {}
        for key in grouped:
            for value in (key, json.dumps(key)):
                stored_keys.setdefault(value, []).append(key)
        async with get_async_db_context() as db:
            result = await db.execute(
                select(File).filter(File.meta['soev_collection_key'].as_string().in_(stored_keys))
            )
            for row in result.scalars().all():
                file = FileModel.model_validate(row)
                for key in stored_keys[file.meta['soev_collection_key']]:
                    grouped[key].append(file)
        return grouped

    async def get_file_counts_by_knowledge_ids(self, knowledge_ids, db=None, *, user_id: str | None = None):
        result = {}
        requested = set(knowledge_ids)
        if not requested:
            return result
        rows = [row for row in await self._pages('/v1/collections', user_id=user_id) if row['key'] in requested]
        files = await self._unlanded_files_by_collection(row['key'] for row in rows)
        for row in rows:
            key = row['key']
            unlanded = await self._unlanded(key, collection=row, user_id=user_id, files=files[key])
            count = row['document_count'] + len(unlanded)
            if count:
                result[key] = count
        return result

    async def has_file(self, knowledge_id, file_id, db=None):
        return file_id in await self._members(knowledge_id)

    async def catalog_original(self, source_id, *, user_id):
        """Return (file row, byte stream) for a catalog member the user may read, or None."""
        members = await self._references({source_id}, user_id=user_id)
        if not members:
            return None
        collection, document = members[0]
        path = self._path(collection['key']) + '/documents/' + quote(source_id, safe='') + '/original'
        return _catalog_file_row(document, user_id), self._client.stream(path, as_user=await self._as_user(user_id))

    async def _references(self, ids, *, user_id=None):
        result = []
        collections = {}
        for source_id in sorted(ids):
            response = await self._get('/v1/documents', params={'source_id': source_id}, user_id=user_id)
            for document in response['data']:
                key = document['collection_key']
                if key not in collections:
                    collections[key] = await self._collection(key, user_id=user_id)
                if collections[key] is not None:
                    result.append((collections[key], document))
        return result

    async def get_knowledges_by_file_id(self, file_id, db=None):
        rows = await self._references({file_id})
        types = await self._fallback_types([row for row, _ in rows])
        return [self._knowledge(row, types) for row, _ in rows]

    async def get_knowledge_by_file_id(self, file_id, db=None):
        rows = await self.get_knowledges_by_file_id(file_id)
        return rows[0] if rows else None

    async def get_knowledge_files_by_file_id(self, file_id, db=None):
        return [
            self._projection.knowledge_link_of(row, document, service_principal=self._service_principal)
            for row, document in await self._references({file_id})
        ]

    async def get_referenced_file_ids(self, file_ids, db=None):
        if not file_ids:
            return set()
        return {document['source_id'] for _, document in await self._references(set(file_ids))}

    async def remove_file_from_knowledge_by_id(self, knowledge_id, file_id, db=None):
        from open_webui.models.files import Files
        from open_webui.soev import ingest

        file = await Files.get_file_by_id(file_id)
        meta = (file.meta or {}) if file else {}
        if (meta.get('soev_job') or {}).get('collection_key') == knowledge_id:
            await ingest.cancel(file, client=self._client)
            return True
        if meta.get('soev_collection_key') == knowledge_id:
            landed = any(doc['source_id'] == file_id for doc in await self._documents(knowledge_id))
            if not landed:
                await Files.update_file_metadata_by_id(file_id, {'soev_collection_key': None})
                return True
        await self._send(
            'DELETE',
            self._path(knowledge_id) + '/documents/' + quote(file_id, safe=''),
            idempotency_key='doc-delete:' + knowledge_id + ':' + file_id,
        )
        if meta.get('soev_collection_key') == knowledge_id:
            await Files.update_file_metadata_by_id(file_id, {'soev_collection_key': None})
        return True

    async def reset_knowledge_by_id(self, id, include_directories=True, db=None):
        from open_webui.models.files import Files
        from open_webui.soev import ingest

        try:
            for file in await Files.get_unlanded_files_for_collection(id):
                if ((file.meta or {}).get('soev_job') or {}).get('collection_key') == id:
                    await ingest.cancel(file, client=self._client)
                else:
                    await Files.update_file_metadata_by_id(file.id, {'soev_collection_key': None})
            for document in await self._documents(id):
                await self.remove_file_from_knowledge_by_id(id, document['source_id'])
            if include_directories:
                for folder in sorted(
                    await self._folder_tree(id), key=lambda row: len(row['path'].split('/')), reverse=True
                ):
                    await self._delete_folder(id, folder['path'])
            return await self.get_knowledge_by_id(id)
        except SoevApiError:
            return None

    async def _file_rows(
        self, ids: list[str], *, filters: dict | None = None, user_id: str | None = None, catalog: dict | None = None
    ) -> list[dict]:
        if not ids:
            return []
        from sqlalchemy import select

        from open_webui.internal.db import get_async_db_context
        from open_webui.models.files import File

        stmt = select(File.id, File.user_id, File.hash, File.filename, File.meta, File.created_at, File.updated_at)
        stmt = stmt.where(File.id.in_(ids))
        stmt = self._file_filter(stmt, File, filters or {}, user_id)
        async with get_async_db_context() as session:
            result = await session.execute(stmt)
            rows = [dict(row._mapping) for row in result]
            if catalog:
                existing = set((await session.execute(select(File.id).where(File.id.in_(ids)))).scalars())
                for source_id, (document, owner_id) in catalog.items():
                    if document and source_id not in existing:
                        row = _catalog_file_row(document, owner_id)
                        if _matches_catalog_file(row, filters or {}, user_id):
                            rows.append(row)
            return rows

    @staticmethod
    def _file_filter(stmt, file_model, filters, user_id):
        from sqlalchemy import func, or_

        query = filters.get('query')
        if query:
            condition = file_model.filename.ilike(f'%{query}%')
            if filters.get('include_content'):
                from open_webui import config

                content = func.substr(
                    file_model.data['content'].as_string(), 1, config.RAG_FILE_CONTENT_SEARCH_MAX_CHARS
                )
                condition = or_(condition, content.ilike(f'%{query}%'))
            stmt = stmt.where(condition)
        if filters.get('view_option') == 'created':
            stmt = stmt.where(file_model.user_id == user_id)
        elif filters.get('view_option') == 'shared':
            stmt = stmt.where(file_model.user_id != user_id)
        return stmt

    async def search_files_by_id(self, knowledge_id, user_id, filter, skip=0, limit=30, metadata_only=False, db=None):
        filters = filter or {}
        path = self._directory_path(knowledge_id, filters.get('directory_id'))
        collection, members, schedules = await asyncio.gather(
            self._collection(knowledge_id, user_id=user_id),
            self._members(knowledge_id, user_id=user_id),
            self._pages('/v1/schedules', user_id=user_id, params={'collection_key': knowledge_id}),
        )
        collection_total = len(members)
        view = await self._directory_view(knowledge_id, members=members, schedules=schedules, user_id=user_id)
        if 'directory_id' in filters:
            members = {source: member for source, member in members.items() if path in view[1].get(source, [])}
        by_id = {source: member[0] for source, member in members.items()}
        owner_id = self._knowledge(collection).user_id if collection else ''
        rows = await self._file_rows(
            list(by_id),
            filters=filters,
            user_id=user_id,
            catalog={source: (document, owner_id) for source, document in by_id.items()},
        )
        _sort_file_rows(rows, filters)
        total = len(rows)
        rows = rows[skip : skip + limit] if limit else rows[skip:]
        directories = await self._directory_models(knowledge_id, path, user_id=user_id, view=view)
        rollups = await self._rollups(knowledge_id, [row.id for row in directories], user_id=user_id, view=view)
        breadcrumbs = await self._breadcrumbs(filters.get('directory_id'), user_id=user_id, view=view)
        return self._projection.knowledge_file_list_of(
            [self._projection.file_response_of(row, by_id[row['id']], metadata_only=metadata_only) for row in rows],
            total=total,
            directories=directories,
            breadcrumbs=breadcrumbs,
            rollups=rollups,
            collection_total=collection_total,
        )

    async def search_knowledge_files(self, filter, skip=0, limit=30, db=None):
        user_id = filter.get('user_id')
        if user_id is None:
            return self._projection.knowledge_file_list_of([], total=0)
        collections = await self._collections(user_id=user_id)
        by_id = {}
        for collection in collections:
            for source_id, (document, _) in (await self._members(collection['key'], user_id=user_id)).items():
                by_id.setdefault(source_id, (collection, document))
        rows = await self._file_rows(
            list(by_id),
            filters=filter,
            user_id=user_id,
            catalog={
                source: (document, self._knowledge(collection).user_id)
                for source, (collection, document) in by_id.items()
            },
        )
        _sort_file_rows(rows, filter, default_order='updated_at', default_descending=True)
        total = len(rows)
        rows = rows[skip : skip + limit] if limit else rows[skip:]
        items = [
            self._projection.file_response_of(row, by_id[row['id']][1], metadata_only=not filter.get('include_content'))
            for row in rows
        ]
        return self._projection.knowledge_file_list_of(items, total=total)

    def _directory_path(self, key, directory_id):
        if not directory_id:
            return ()
        collection, path = self._projection.directory_of(directory_id)
        if collection != key:
            raise ValueError('Directory belongs to a different collection')
        return path

    @staticmethod
    def _assert_writable_path(path):
        if path and path[0].startswith('\0sync:'):
            raise HTTPException(status_code=403, detail={'code': 'synced_folder_read_only'})

    async def _directory_view(self, key, *, members=None, schedules=None, user_id=None):
        schedules = (
            schedules
            if schedules is not None
            else await self._pages('/v1/schedules', user_id=user_id, params={'collection_key': key})
        )
        folders = {
            schedule['id']: schedule
            for schedule in schedules
            if schedule['kind'] == 'content'
            and schedule.get('lifecycle') != 'revoked'
            and not schedule.get('scope', {}).get('single_file', False)
            and schedule.get('scope', {}).get('include_descendants', True)
        }
        singles = {
            schedule['id'] for schedule in schedules if schedule['kind'] == 'content' and schedule['id'] not in folders
        }
        members = await self._members(key, user_id=user_id) if members is None else members
        collection = await self._collection(key, user_id=user_id)
        directories, paths = {}, {}
        if collection is None:
            return directories, paths
        for schedule_id, schedule in folders.items():
            root = ('\0sync:' + schedule_id,)
            model = self._folder_model(collection, {'path': root[0], 'created_at': collection['created_at']})
            model.name = schedule.get('label') or 'Folder'
            directories[root] = model
        for source_id, (document, path) in members.items():
            reach = set((document or {}).get('schedule_ids', []))
            relative = tuple(path.split('/')) if path else ()
            locations = []
            for schedule_id in sorted(reach & folders.keys()):
                location = ('\0sync:' + schedule_id,) + relative
                locations.append(location)
                for end in range(2, len(location) + 1):
                    prefix = location[:end]
                    directories[prefix] = self._folder_model(
                        collection, {'path': '/'.join(prefix), 'created_at': collection['created_at']}
                    )
            if not reach:
                locations.append(relative)
            elif reach & singles or not locations:
                locations.append(())
            paths[source_id] = locations
        return directories, paths

    async def _folder_entries(self, key, path=(), *, user_id=None):
        params = {'under': '/'.join(path)}
        folders, documents = [], []
        while True:
            page = await self._get(self._path(key) + '/folders', params=params, user_id=user_id)
            folders.extend(page['folders'])
            documents.extend(page['documents'])
            if not page['next_cursor']:
                return folders, documents
            params['cursor'] = page['next_cursor']

    async def _folder_tree(self, key, *, user_id=None):
        pending, result = [()], []
        while pending:
            rows, _ = await self._folder_entries(key, pending.pop(), user_id=user_id)
            result.extend(rows)
            pending.extend(tuple(row['path'].split('/')) for row in rows)
        return result

    def _folder_model(self, collection, folder):
        owner = self._knowledge(collection).user_id
        return self._projection.directory_model(
            collection['key'],
            tuple(folder['path'].split('/')),
            created_at=int(dt.datetime.fromisoformat(folder['created_at']).timestamp()),
            owner_id=owner,
        )

    async def _directory_models(self, key, path=(), *, user_id=None, view=None):
        collection = await self._collection(key, user_id=user_id)
        if collection is None:
            return []
        view = await self._directory_view(key, user_id=user_id) if view is None else view
        virtual = [model for location, model in view[0].items() if location[:-1] == path]
        if path and path[0].startswith('\0sync:'):
            return sorted(virtual, key=lambda row: (row.name, row.id))
        folders, _ = await self._folder_entries(key, path, user_id=user_id)
        real = [self._folder_model(collection, folder) for folder in sorted(folders, key=lambda row: row['path'])]
        return sorted(real + virtual, key=lambda row: (row.name, row.id))

    async def create_directory(self, knowledge_id, name, user_id, parent_id=None, db=None):
        path = self._directory_path(knowledge_id, parent_id) + (name,)
        self._assert_writable_path(path)
        self._projection.directory_id(knowledge_id, path)
        collection = await self._collection(knowledge_id, user_id=user_id)
        if collection is None:
            return None
        await self._send('POST', self._path(knowledge_id) + '/folders', {'path': '/'.join(path)}, user_id=user_id)
        # The folder is what was just asked for; re-listing the level would
        # walk the whole collection for nothing (a folder upload creates many).
        return self._folder_model(
            collection, {'path': '/'.join(path), 'created_at': dt.datetime.now(dt.UTC).isoformat()}
        )

    async def _find_or_create_directory(self, db, knowledge_id, parent_id, name, user_id):
        result = await self.create_directory(knowledge_id, name, user_id, parent_id)
        return result.id

    async def get_directories(self, knowledge_id, parent_id=None, db=None):
        return await self._directory_models(knowledge_id, self._directory_path(knowledge_id, parent_id))

    async def get_all_directories(self, knowledge_id, db=None):
        collection = await self._collection(knowledge_id)
        if collection is None:
            return []
        virtual, _ = await self._directory_view(knowledge_id)
        return [
            self._folder_model(collection, folder)
            for folder in sorted(await self._folder_tree(knowledge_id), key=lambda row: row['path'])
        ] + list(virtual.values())

    async def _directory(self, directory_id, *, user_id=None, view=None):
        key, path = self._projection.directory_of(directory_id)
        if not path:
            return None
        rows = await self._directory_models(key, path[:-1], user_id=user_id, view=view)
        return next((row for row in rows if row.id == directory_id), None)

    async def get_directory_by_id(self, directory_id, db=None):
        return await self._directory(directory_id)

    async def _breadcrumbs(self, directory_id, *, user_id=None, view=None):
        if not directory_id:
            return []
        key, path = self._projection.directory_of(directory_id)
        view = await self._directory_view(key, user_id=user_id) if view is None else view
        rows = []
        for end in range(1, len(path) + 1):
            directory = await self._directory(
                self._projection.directory_id(key, path[:end]), user_id=user_id, view=view
            )
            if directory is not None:
                rows.append(directory)
        return rows

    async def get_directory_breadcrumbs(self, directory_id, db=None):
        return await self._breadcrumbs(directory_id)

    async def get_files_with_directory_ids(self, knowledge_id, db=None):
        from open_webui.models.files import Files

        members = await self._members(knowledge_id)
        files = await Files.get_files_by_ids(list(members)) if members else []
        return [
            (
                file,
                self._projection.directory_id(knowledge_id, tuple(members[file.id][1].split('/')))
                if members[file.id][1]
                else None,
            )
            for file in files
        ]

    async def _rollups(self, knowledge_id, directory_ids, *, user_id=None, view=None):
        paths = {identifier: self._directory_path(knowledge_id, identifier) for identifier in directory_ids}
        if not paths:
            return {}
        members = await self._members(knowledge_id, user_id=user_id)
        collection = await self._collection(knowledge_id, user_id=user_id)
        owner_id = self._knowledge(collection).user_id if collection else ''
        files = {
            row['id']: row
            for row in await self._file_rows(
                list(members), catalog={source: (member[0], owner_id) for source, member in members.items()}
            )
        }
        view = await self._directory_view(knowledge_id, members=members, user_id=user_id) if view is None else view
        result = {}
        for identifier, path in paths.items():
            matches = [
                source_id
                for source_id, (_, member_path) in members.items()
                if source_id in files and any(location[: len(path)] == path for location in view[1].get(source_id, []))
            ]
            if not matches:
                continue
            counts = self._projection.status_counts()
            for source_id in matches:
                counts[self._projection.status_bucket((files[source_id]['meta'] or {}).get('status'))] += 1
            result[identifier] = {'child_count': len(matches), 'status_counts': counts}
        return result

    async def get_directory_rollups(self, knowledge_id, directory_ids, db=None):
        return await self._rollups(knowledge_id, directory_ids)

    async def get_file_ids_in_directory_subtree(self, knowledge_id, directory_id, db=None):
        path = '/'.join(self._directory_path(knowledge_id, directory_id))
        return [
            source_id
            for source_id, (_, member_path) in (await self._members(knowledge_id)).items()
            if (member_path or '') == path or (member_path or '').startswith(path + '/')
        ]

    async def rename_directory(self, directory_id, name, db=None):
        return await self.update_directory(directory_id, name=name)

    async def move_directory(self, directory_id, new_parent_id, db=None):
        return await self.update_directory(directory_id, parent_id=new_parent_id)

    async def update_directory(self, directory_id, name=None, parent_id='__unset__', db=None):
        try:
            key, old = self._projection.directory_of(directory_id)
            self._assert_writable_path(old)
            parent = old[:-1] if parent_id == '__unset__' else self._directory_path(key, parent_id)
            self._assert_writable_path(parent)
            new = parent + ((name if name is not None else old[-1]),)
            identifier = self._projection.directory_id(key, new)
            if new[: len(old)] == old and new != old:
                return None
            if new != old:
                await self._send(
                    'POST', self._path(key) + '/folders/move', {'from': '/'.join(old), 'to': '/'.join(new)}
                )
            return await self._directory(identifier)
        except (SoevApiError, ValueError):
            return None

    async def _delete_folder(self, key, path):
        await self._send('DELETE', self._path(key) + '/folders?' + urlencode({'path': path}))

    async def delete_directory(self, directory_id, move_files_to_parent=True, db=None):
        try:
            key, path = self._projection.directory_of(directory_id)
            self._assert_writable_path(path)
            if move_files_to_parent:
                _, documents = await self._folder_entries(key, path)
                for document in documents:
                    await self._move_document(key, document['source_id'], path[:-1])
            else:
                for source_id in await self.get_file_ids_in_directory_subtree(key, directory_id):
                    await self.remove_file_from_knowledge_by_id(key, source_id)
            await self._delete_folder(key, '/'.join(path))
            return True
        except SoevApiError:
            return False

    async def _move_document(self, key, source_id, path, *, user_id=None):
        self._assert_writable_path(path)
        await self._send(
            'POST',
            self._path(key) + '/documents/' + quote(source_id, safe='') + '/move',
            {'to': '/'.join(path)},
            user_id=user_id,
        )

    async def move_file_to_directory(self, knowledge_id, file_id, directory_id=None, db=None):
        try:
            await self._move_document(knowledge_id, file_id, self._directory_path(knowledge_id, directory_id))
            return True
        except (SoevApiError, ValueError):
            return False

    async def add_file_to_knowledge_by_id(self, knowledge_id, file_id, user_id, directory_id=None, db=None):
        from open_webui.models.files import Files

        self._assert_writable_path(self._directory_path(knowledge_id, directory_id))
        collection = await self._collection(knowledge_id, user_id=user_id)
        if collection is None:
            return None
        document = next(
            (doc for doc in await self._documents(knowledge_id, user_id=user_id) if doc['source_id'] == file_id), None
        )
        path = self._directory_path(knowledge_id, directory_id) if directory_id is not None else None
        if document is not None:
            if path is not None:
                await self._move_document(knowledge_id, file_id, path, user_id=user_id)
                document = {**document, 'path': '/'.join(path) or None}
        else:
            file = await Files.get_file_by_id(file_id)
            if file is None:
                return None
            job = (file.meta or {}).get('soev_job') or {}
            if not job:
                await Files.update_file_metadata_by_id(file_id, {'soev_collection_key': knowledge_id})
            elif job.get('collection_key') == knowledge_id and path is not None:
                job = {**job, 'path': '/'.join(path) or None}
                await Files.update_file_metadata_by_id(file_id, {'soev_job': job})
            document = {
                'source_id': file.id,
                'path': job.get('path') if job.get('collection_key') == knowledge_id else None,
                'ingested_at': dt.datetime.fromtimestamp(file.created_at, dt.UTC).isoformat(),
            }
        return self._projection.knowledge_link_of(collection, document, service_principal=self._service_principal)

    async def set_path_fields_by_file_id(self, file_id, meta, db=None):
        return True

    async def update_knowledge_meta_by_id(self, id, meta, db=None):
        raise NotOnSoev('update_knowledge_meta_by_id', moves_with='cloud sync')

    async def update_knowledge_data_by_id(self, id, data, db=None):
        return None

    async def update_knowledge_user_id_by_id(self, id, user_id, db=None):
        raise NotOnSoev('update_knowledge_user_id_by_id', moves_with='owner transfer route')
