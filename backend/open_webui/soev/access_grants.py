"""Route knowledge access through soev-api and retain SQL grants for other resources."""

from open_webui.models.access_grants import AccessGrantsTable


class SoevAccessGrantsTable(AccessGrantsTable):
    def __init__(self, *, store=None):
        self._configured_store = store

    @property
    def _store(self):
        if self._configured_store is None:
            from open_webui.models.knowledge import Knowledges

            return Knowledges
        return self._configured_store

    async def has_access(self, user_id, resource_type, resource_id, permission='read', user_group_ids=None, db=None):
        if resource_type != 'knowledge':
            return await super().has_access(user_id, resource_type, resource_id, permission, user_group_ids, db)
        return await self._store.check_access_by_user_id(resource_id, user_id, permission)

    async def get_accessible_resource_ids(
        self, user_id, resource_type, resource_ids, permission='read', user_group_ids=None, db=None
    ):
        if resource_type != 'knowledge':
            return await super().get_accessible_resource_ids(
                user_id, resource_type, resource_ids, permission, user_group_ids, db
            )
        if not resource_ids:
            return set()
        return await self._store.accessible_collection_ids(user_id, resource_ids, permission)

    async def get_grants_by_resource(self, resource_type, resource_id, db=None):
        if resource_type != 'knowledge':
            return await super().get_grants_by_resource(resource_type, resource_id, db)
        return await self._store.get_collection_grants(resource_id)

    async def get_grants_by_resources(self, resource_type, resource_ids, db=None):
        if resource_type != 'knowledge':
            return await super().get_grants_by_resources(resource_type, resource_ids, db)
        return {key: await self._store.get_collection_grants(key) for key in resource_ids}

    async def set_access_grants(self, resource_type, resource_id, access_grants, db=None):
        if resource_type != 'knowledge':
            return await super().set_access_grants(resource_type, resource_id, access_grants, db)
        row = await self._store.set_collection_access(resource_id, access_grants or [])
        return self._store.collection_grants(row)

    async def revoke_all_access(self, resource_type, resource_id, db=None):
        if resource_type != 'knowledge':
            return await super().revoke_all_access(resource_type, resource_id, db)
        grants = await self._store.get_collection_grants(resource_id)
        await self._store.set_collection_access(resource_id, [])
        return len(grants)

    def has_permission_filter(self, db, query, DocumentModel, filter, resource_type, permission='read'):
        if resource_type != 'knowledge':
            return super().has_permission_filter(db, query, DocumentModel, filter, resource_type, permission)
        from open_webui.soev.knowledge_store import NotOnSoev

        raise NotOnSoev('has_permission_filter', moves_with='collection listing through the knowledge store')
