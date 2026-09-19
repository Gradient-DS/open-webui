"""Subject-scoped connection and schedule calls through the existing soev-api client."""

import asyncio
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

    async def connection_usage(self, connection_id: str) -> dict:
        await self.connection(connection_id)
        knowledge_ids: set[str] = set()
        async for schedule in self.client.pages(
            '/v1/schedules', as_user=self.user_ref, params={'connection_id': connection_id}
        ):
            knowledge_ids.update(schedule['subscribers'])
        return {'knowledge_ids': sorted(knowledge_ids)}

    async def authorize(self, connection_id: str, owner_email: str | None = None) -> dict:
        body = {'owner_email': owner_email} if owner_email is not None else None
        return await self._send('POST', f'/v1/connections/{quote(connection_id, safe="")}/authorize', body)

    async def create_connection(self, provider: str, owner_email: str | None = None) -> dict:
        connection = await self._send(
            'POST', '/v1/connections', {'source_kind': provider, 'credential_kind': 'user_oauth'}
        )
        authorization = await self.authorize(connection['id'], owner_email if provider == 'google_drive' else None)
        return {'connection_id': connection['id'], 'authorize_url': authorization['authorize_url']}

    async def revoke_connection(self, connection_id: str) -> None:
        await self._send('DELETE', f'/v1/connections/{quote(connection_id, safe="")}')

    async def create_schedule(self, collection_key: str, body: dict) -> dict:
        return await self._send('POST', '/v1/schedules', {**body, 'collection_key': collection_key})

    async def schedule_action(self, collection_key: str, schedule_id: str, action: str) -> dict | None:
        path = f'/v1/schedules/{quote(schedule_id, safe="")}'
        schedule = await self._get(path)
        if collection_key not in schedule['subscribers']:
            raise SoevApiError(404, 'connection_not_found', 'No such schedule in this collection')
        if action == 'delete':
            return await self._send('DELETE', f'{path}?collection_key={quote(collection_key, safe="")}')
        return await self._send('POST', f'{path}/{action}')

    async def skipped_items(self, collection_key: str, schedule_id: str) -> list[dict]:
        await self._get(f'/v1/collections/{quote(collection_key, safe="")}')
        schedule = await self._get(f'/v1/schedules/{quote(schedule_id, safe="")}')
        if collection_key not in schedule['subscribers']:
            raise SoevApiError(404, 'connection_not_found', 'No such schedule in this collection')
        run = schedule.get('last_run')
        if not run:
            return []
        job = await self._get(f'/v1/jobs/{quote(run["id"], safe="")}?include_items=true')
        names = {
            row['source_id']: row.get('filename') or row.get('title')
            async for row in self.client.pages(
                f'/v1/collections/{quote(collection_key, safe="")}/documents', as_user=self.user_ref
            )
        }
        return [
            {
                'source_id': item['source_id'],
                'name': names.get(item['source_id']) or item['source_id'],
                'code': item.get('code') or item['status'],
            }
            for item in job.get('items', [])
            if item['status'] != 'succeeded'
        ]

    async def sync_status(self, collection_key: str) -> dict:
        # W2 projects the collection key directly as the OWUI knowledge id.
        # Kept: this is what makes an unreadable or absent KB a 404 rather
        # than an empty schedule list.
        async def list_schedules():
            return [
                row
                async for row in self.client.pages(
                    '/v1/schedules', as_user=self.user_ref, params={'collection_key': collection_key}
                )
            ]

        _, schedules = await asyncio.gather(
            self._get(f'/v1/collections/{quote(collection_key, safe="")}'), list_schedules()
        )
        # The listing carries each schedule's own detail, so the rest is one
        # page plus one call per DISTINCT connection (usually exactly one).
        # It used to also fetch every schedule individually, which made a
        # status poll cost a round trip per schedule.
        connection_ids = sorted({schedule['connection_id'] for schedule in schedules})
        connections = dict(zip(connection_ids, await asyncio.gather(*(self.connection(key) for key in connection_ids))))
        return {
            'schedules': [{**schedule, 'connection': connections[schedule['connection_id']]} for schedule in schedules]
        }
