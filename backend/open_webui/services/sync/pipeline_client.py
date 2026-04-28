"""HTTP client for the per-tenant gradient-loader-worker.

Plumbs sync jobs from open-webui to the loader-worker pod that runs in the
same tenant namespace. Loader-worker downloads + parses+chunks (via
shared-services doc-processor) and pushes text chunks back to
``/api/v1/integrations/ingest`` (data_type=chunked_text) with
``LOADER_INGEST_API_KEY`` + acting headers. Open-webui re-embeds via
``save_docs_to_vector_db`` on the callback path. (See plan amendment
2026-04-26 — embedding stays in open-webui, not loader-worker.)

No bearer is presented on this hop — same-namespace traffic is gated by
NetworkPolicy (see helm/open-webui-tenant/templates/loader-worker/networkpolicy.yaml).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx


class PipelineClient:
    """Thin wrapper around ``httpx.AsyncClient`` for loader-worker RPCs.

    Construction reads ``LOADER_WORKER_URL`` and ``TENANT_NAME`` from env once;
    callers are expected to instantiate per-worker (cheap — no network I/O on
    init). Every request opens a fresh ``AsyncClient`` to keep connection-pool
    lifetimes scoped to a single call.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        tenant: Optional[str] = None,
        timeout: Optional[httpx.Timeout] = None,
    ):
        self._base = (base_url if base_url is not None else os.environ.get('LOADER_WORKER_URL', '')).rstrip('/')
        self._tenant = tenant if tenant is not None else os.environ.get('TENANT_NAME', '')
        self._timeout = timeout or httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=30.0)

    async def submit_job(
        self,
        knowledge_id: str,
        acting_user_id: str,
        provider_slug: str,
        callback_base_url: str,
        collection: Dict[str, Any],
        items: List[Dict[str, Any]],
    ) -> str:
        """Submit a job to the loader-worker. Returns the new ``job_id``.

        The ``acting_user_id`` and ``provider_slug`` are echoed by the
        loader-worker on its callback to ``/api/v1/integrations/ingest`` as
        ``X-Acting-User-Id`` / ``X-Acting-Provider`` so File records land on
        the user who triggered the sync.
        """
        if not self._base or not self._tenant:
            raise RuntimeError('PipelineClient requires LOADER_WORKER_URL and TENANT_NAME env vars')

        payload = {
            'knowledge_id': knowledge_id,
            'acting_user_id': acting_user_id,
            'provider_slug': provider_slug,
            'callback_base_url': callback_base_url,
            'collection': collection,
            'items': items,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f'{self._base}/tenants/{self._tenant}/jobs',
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()['job_id']

    async def get_status(self, job_id: str) -> Dict[str, Any]:
        if not self._base:
            raise RuntimeError('PipelineClient requires LOADER_WORKER_URL env var')

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(f'{self._base}/jobs/{job_id}')
            resp.raise_for_status()
            return resp.json()

    async def cancel_job(self, job_id: str) -> Dict[str, Any]:
        if not self._base:
            raise RuntimeError('PipelineClient requires LOADER_WORKER_URL env var')

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f'{self._base}/jobs/{job_id}/cancel')
            resp.raise_for_status()
            return resp.json()
