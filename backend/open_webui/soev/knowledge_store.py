"""Knowledge persistence through soev-api, with OWUI file rows retained by source_id.

Configuration and model imports are deferred so either singleton import order is safe.
"""

import datetime as dt
import hashlib
import json
from urllib.parse import quote, urlencode
from uuid import uuid4

from open_webui.soev.acting import acting_ref
from open_webui.soev.client import SoevApiError


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
        ref = f'owui:user:{explicit_user_id}' if explicit_user_id is not None else acting_ref()
        if ref is not None:
            from open_webui.soev import identity

            await identity.ensure_link(ref, self._client)
        return ref

    async def _get(self, path, *, user_id=None, params=None):
        return await self._client.get(path, as_user=await self._as_user(user_id), params=params)

    async def _pages(self, path, *, user_id=None, params=None):
        ref = await self._as_user(user_id)
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

    async def _collections(self, *, user_id=None):
        hidden = set()
        for status in ('QUEUED', 'RUNNING', 'AWAITING_UPLOAD'):
            jobs = await self._pages('/v1/jobs', user_id=user_id, params={'status': status})
            hidden.update(job['collection_key'] for job in jobs if job['kind'] == 'delete_collection')
        return [row for row in await self._pages('/v1/collections', user_id=user_id) if row['key'] not in hidden]

    def _knowledge(self, row):
        return self._projection.knowledge_of(row, service_principal=self._service_principal) if row else None

    def _user_knowledge(self, row, owners):
        return self._projection.knowledge_user_of(
            row, service_principal=self._service_principal, user=owners.get(self._knowledge(row).user_id)
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
        return self._knowledge(await self._collection(id))

    async def get_knowledge_by_id_unfiltered(self, id, db=None):
        return self._knowledge(await self._collection(id))

    async def get_knowledge_bases(self, skip=0, limit=30, db=None):
        rows = sorted(await self._collections(), key=lambda row: self._collection_sort(row, 'updated_at'), reverse=True)
        owners = await self._owners(rows)
        return [self._user_knowledge(row, owners) for row in rows]

    async def search_knowledge_bases(self, user_id, filter, skip=0, limit=30, db=None):
        rows = await self._collections(user_id=user_id)
        owners = await self._owners(rows)
        rows = [
            row
            for row in rows
            if self._matches_collection(row, user_id, filter or {}, owners.get(self._knowledge(row).user_id, {}))
        ]
        order = (filter or {}).get('order_by')
        if order not in ('name', 'created_at', 'updated_at'):
            order = 'updated_at'
        ascending = ((filter or {}).get('direction') or 'desc').lower() == 'asc'
        rows.sort(key=lambda row: row['key'])
        rows.sort(key=lambda row: self._collection_sort(row, order), reverse=not ascending)
        total = len(rows)
        rows = rows[skip : skip + limit] if limit else rows[skip:]
        return self._projection.knowledge_list_of([self._user_knowledge(row, owners) for row in rows], total)

    @staticmethod
    def _matches_collection(row, user_id, filters, owner):
        owned = row.get('created_by') == f'owui:user:{user_id}'
        if filters.get('type') not in (None, '', 'local') or filters.get('source') == 'external':
            return False
        if filters.get('view_option') == 'created' and not owned:
            return False
        if filters.get('view_option') == 'shared' and owned:
            return False
        query = (filters.get('query') or '').casefold()
        values = [row['name'], row['description']] + [owner.get(field) for field in ('name', 'email', 'username')]
        return any(query in (value or '').casefold() for value in values)

    async def get_knowledge_bases_by_type(self, type, db=None):
        if type != 'local':
            return []
        return [self._knowledge(row) for row in await self._collections()]

    async def get_knowledge_bases_by_user_id(self, user_id, permission='write', db=None):
        rows = [
            row
            for row in await self._collections(user_id=user_id)
            if permission == 'read' or (permission == 'write' and row.get('caller_may_write'))
        ]
        owners = await self._owners(rows)
        return [self._user_knowledge(row, owners) for row in rows]

    async def get_knowledge_items_by_user_id(self, user_id, db=None):
        return [
            self._knowledge(row)
            for row in await self._collections(user_id=user_id)
            if row.get('created_by') == f'owui:user:{user_id}'
        ]

    async def get_knowledge_by_id_and_user_id(self, id, user_id, db=None):
        row = await self._collection(id, user_id=user_id)
        return self._knowledge(row) if row and row.get('caller_may_write') else None

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
        return self._knowledge(result)

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

    async def get_files_by_id(self, knowledge_id, db=None):
        from open_webui.models.files import Files

        ids = [row['source_id'] for row in await self._documents(knowledge_id)]
        return await Files.get_files_by_ids(ids) if ids else []

    async def get_file_metadatas_by_id(self, knowledge_id, db=None):
        from open_webui.models.files import Files

        ids = [row['source_id'] for row in await self._documents(knowledge_id)]
        return await Files.get_file_metadatas_by_ids(ids) if ids else []

    async def get_file_counts_by_knowledge_ids(self, knowledge_ids, db=None):
        result = {}
        for key in knowledge_ids:
            row = await self._collection(key)
            if row and row['document_count']:
                result[key] = row['document_count']
        return result

    async def has_file(self, knowledge_id, file_id, db=None):
        return any(row['source_id'] == file_id for row in await self._documents(knowledge_id))

    async def _references(self, ids):
        result = []
        for collection in await self._collections():
            for document in await self._documents(collection['key']):
                if document['source_id'] in ids:
                    result.append((collection, document))
        return result

    async def get_knowledges_by_file_id(self, file_id, db=None):
        return [self._knowledge(row) for row, _ in await self._references({file_id})]

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
        await self._send(
            'DELETE',
            self._path(knowledge_id) + '/documents/' + quote(file_id, safe=''),
            idempotency_key='doc-delete:' + knowledge_id + ':' + file_id,
        )
        return True

    async def reset_knowledge_by_id(self, id, include_directories=True, db=None):
        try:
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

    async def _file_rows(self, ids, *, filters=None, user_id=None):
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
            return [dict(row._mapping) for row in result]

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
        documents = await self._documents(knowledge_id, user_id=user_id)
        if 'directory_id' in filters:
            documents = [doc for doc in documents if (doc['path'] or '') == '/'.join(path)]
        by_id = {doc['source_id']: doc for doc in documents}
        rows = await self._file_rows(list(by_id), filters=filters, user_id=user_id)
        order = {'name': 'filename', 'created_at': 'created_at', 'updated_at': 'updated_at'}.get(
            filters.get('order_by')
        )
        descending = order is not None and filters.get('direction') != 'asc'
        rows.sort(key=lambda row: row['id'])
        rows.sort(key=lambda row: (row[order or 'filename'] is not None, row[order or 'filename']), reverse=descending)
        total = len(rows)
        rows = rows[skip : skip + limit] if limit else rows[skip:]
        directories = await self._directory_models(knowledge_id, path, user_id=user_id)
        rollups = await self._rollups(knowledge_id, [row.id for row in directories], user_id=user_id)
        breadcrumbs = await self._breadcrumbs(filters.get('directory_id'), user_id=user_id)
        return self._projection.knowledge_file_list_of(
            [self._projection.file_response_of(row, by_id[row['id']], metadata_only=metadata_only) for row in rows],
            total=total,
            directories=directories,
            breadcrumbs=breadcrumbs,
            rollups=rollups,
        )

    async def search_knowledge_files(self, filter, skip=0, limit=30, db=None):
        return self._projection.knowledge_file_list_of([], total=0)

    def _directory_path(self, key, directory_id):
        if not directory_id:
            return ()
        collection, path = self._projection.directory_of(directory_id)
        if collection != key:
            raise ValueError('Directory belongs to a different collection')
        return path

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

    async def _directory_models(self, key, path=(), *, user_id=None):
        collection = await self._collection(key, user_id=user_id)
        if collection is None:
            return []
        folders, _ = await self._folder_entries(key, path, user_id=user_id)
        return [self._folder_model(collection, folder) for folder in sorted(folders, key=lambda row: row['path'])]

    async def create_directory(self, knowledge_id, name, user_id, parent_id=None, db=None):
        path = self._directory_path(knowledge_id, parent_id) + (name,)
        self._projection.directory_id(knowledge_id, path)
        await self._send('POST', self._path(knowledge_id) + '/folders', {'path': '/'.join(path)}, user_id=user_id)
        rows = await self._directory_models(knowledge_id, path[:-1], user_id=user_id)
        return next((row for row in rows if row.name == name), None)

    async def _find_or_create_directory(self, db, knowledge_id, parent_id, name, user_id):
        result = await self.create_directory(knowledge_id, name, user_id, parent_id)
        return result.id

    async def get_directories(self, knowledge_id, parent_id=None, db=None):
        return await self._directory_models(knowledge_id, self._directory_path(knowledge_id, parent_id))

    async def get_all_directories(self, knowledge_id, db=None):
        collection = await self._collection(knowledge_id)
        if collection is None:
            return []
        return [
            self._folder_model(collection, folder)
            for folder in sorted(await self._folder_tree(knowledge_id), key=lambda row: row['path'])
        ]

    async def _directory(self, directory_id, *, user_id=None):
        key, path = self._projection.directory_of(directory_id)
        if not path:
            return None
        rows = await self._directory_models(key, path[:-1], user_id=user_id)
        return next((row for row in rows if row.id == directory_id), None)

    async def get_directory_by_id(self, directory_id, db=None):
        return await self._directory(directory_id)

    async def _breadcrumbs(self, directory_id, *, user_id=None):
        if not directory_id:
            return []
        key, path = self._projection.directory_of(directory_id)
        rows = []
        for end in range(1, len(path) + 1):
            directory = await self._directory(self._projection.directory_id(key, path[:end]), user_id=user_id)
            if directory is not None:
                rows.append(directory)
        return rows

    async def get_directory_breadcrumbs(self, directory_id, db=None):
        return await self._breadcrumbs(directory_id)

    async def get_files_with_directory_ids(self, knowledge_id, db=None):
        documents = {doc['source_id']: doc for doc in await self._documents(knowledge_id)}
        files = await self.get_files_by_id(knowledge_id)
        return [
            (
                file,
                self._projection.directory_id(knowledge_id, tuple(documents[file.id]['path'].split('/')))
                if documents[file.id]['path']
                else None,
            )
            for file in files
        ]

    async def _rollups(self, knowledge_id, directory_ids, *, user_id=None):
        paths = {identifier: '/'.join(self._directory_path(knowledge_id, identifier)) for identifier in directory_ids}
        if not paths:
            return {}
        documents = await self._documents(knowledge_id, user_id=user_id)
        files = {row['id']: row for row in await self._file_rows([doc['source_id'] for doc in documents])}
        result = {}
        for identifier, path in paths.items():
            matches = [
                doc
                for doc in documents
                if doc['source_id'] in files
                and ((doc['path'] or '') == path or (doc['path'] or '').startswith(path + '/'))
            ]
            if not matches:
                continue
            counts = self._projection.status_counts()
            for doc in matches:
                counts[self._projection.status_bucket((files[doc['source_id']]['meta'] or {}).get('status'))] += 1
            result[identifier] = {'child_count': len(matches), 'status_counts': counts}
        return result

    async def get_directory_rollups(self, knowledge_id, directory_ids, db=None):
        return await self._rollups(knowledge_id, directory_ids)

    async def get_file_ids_in_directory_subtree(self, knowledge_id, directory_id, db=None):
        path = '/'.join(self._directory_path(knowledge_id, directory_id))
        return [
            doc['source_id']
            for doc in await self._documents(knowledge_id)
            if (doc['path'] or '') == path or (doc['path'] or '').startswith(path + '/')
        ]

    async def rename_directory(self, directory_id, name, db=None):
        return await self.update_directory(directory_id, name=name)

    async def move_directory(self, directory_id, new_parent_id, db=None):
        return await self.update_directory(directory_id, parent_id=new_parent_id)

    async def update_directory(self, directory_id, name=None, parent_id='__unset__', db=None):
        try:
            key, old = self._projection.directory_of(directory_id)
            parent = old[:-1] if parent_id == '__unset__' else self._directory_path(key, parent_id)
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

    async def _move_document(self, key, source_id, path):
        await self._send(
            'POST', self._path(key) + '/documents/' + quote(source_id, safe='') + '/move', {'to': '/'.join(path)}
        )

    async def move_file_to_directory(self, knowledge_id, file_id, directory_id=None, db=None):
        try:
            await self._move_document(knowledge_id, file_id, self._directory_path(knowledge_id, directory_id))
            return True
        except (SoevApiError, ValueError):
            return False

    async def get_pending_deletions(self, limit=50, db=None):
        return []

    async def get_stale_knowledge(self, stale_before, limit=50, exclude_user_ids=None, db=None):
        return []

    async def get_suspended_expired_knowledge(self, limit=50, db=None):
        return []

    async def is_suspended(self, id, db=None):
        return False

    async def get_suspension_info(self, id, db=None):
        return None

    async def add_file_to_knowledge_by_id(self, knowledge_id, file_id, user_id, directory_id=None, db=None):
        raise NotOnSoev('add_file_to_knowledge_by_id', moves_with='ingest')

    async def set_path_fields_by_file_id(self, file_id, meta, db=None):
        raise NotOnSoev('set_path_fields_by_file_id', moves_with='ingest')

    async def update_knowledge_meta_by_id(self, id, meta, db=None):
        raise NotOnSoev('update_knowledge_meta_by_id', moves_with='cloud sync')

    async def update_knowledge_data_by_id(self, id, data, db=None):
        raise NotOnSoev('update_knowledge_data_by_id', moves_with='ingest')

    async def update_knowledge_user_id_by_id(self, id, user_id, db=None):
        raise NotOnSoev('update_knowledge_user_id_by_id', moves_with='owner transfer route')
