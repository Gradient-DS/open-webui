"""Subject-scoped connection and schedule calls through the existing soev-api client."""

from urllib.parse import quote
from uuid import uuid4

from open_webui.soev.client import SoevApiError, SoevClient


class CloudSync:
    def __init__(self, client: SoevClient, user_ref: str):
        self.client = client
        self.user_ref = user_ref

    async def _get(self, path: str) -> dict:
        return await self.client.get(path, as_user=self.user_ref)

    async def _send(self, method: str, path: str, body: dict | None = None) -> dict | None:
        return await self.client.send(method, path, body, as_user=self.user_ref, idempotency_key=str(uuid4()))

    async def connections(self) -> list[dict]:
        return [row async for row in self.client.pages('/v1/connections', as_user=self.user_ref)]

    async def connection(self, connection_id: str) -> dict:
        return await self._get(f'/v1/connections/{quote(connection_id, safe="")}')

    async def authorize(self, connection_id: str) -> dict:
        return await self._send('POST', f'/v1/connections/{quote(connection_id, safe="")}/authorize')

    async def create_connection(self, provider: str) -> dict:
        connection = await self._send(
            'POST', '/v1/connections', {'source_kind': provider, 'credential_kind': 'user_oauth'}
        )
        authorization = await self.authorize(connection['id'])
        return {'connection_id': connection['id'], 'authorize_url': authorization['authorize_url']}

    async def revoke_connection(self, connection_id: str) -> None:
        await self._send('DELETE', f'/v1/connections/{quote(connection_id, safe="")}')

    async def create_schedule(self, collection_key: str, body: dict) -> dict:
        return await self._send('POST', '/v1/schedules', {**body, 'collection_key': collection_key})

    async def schedule_action(self, collection_key: str, schedule_id: str, action: str) -> dict | None:
        path = f'/v1/schedules/{quote(schedule_id, safe="")}'
        schedule = await self._get(path)
        if schedule['collection_key'] != collection_key:
            raise SoevApiError(404, 'connection_not_found', 'No such schedule in this collection')
        if action == 'delete':
            return await self._send('DELETE', path)
        return await self._send('POST', f'{path}/{action}')

    async def sync_status(self, collection_key: str) -> dict:
        # W2 projects the collection key directly as the OWUI knowledge id.
        await self._get(f'/v1/collections/{quote(collection_key, safe="")}')
        schedules = []
        connections = {}
        async for row in self.client.pages(
            '/v1/schedules', as_user=self.user_ref, params={'collection_key': collection_key}
        ):
            schedule = await self._get(f'/v1/schedules/{quote(row["id"], safe="")}')
            connection_id = schedule['connection_id']
            if connection_id not in connections:
                connections[connection_id] = await self.connection(connection_id)
            schedules.append({**schedule, 'connection': connections[connection_id]})
        return {'schedules': schedules}
