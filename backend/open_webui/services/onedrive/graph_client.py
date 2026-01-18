"""Microsoft Graph API client for OneDrive operations."""

import httpx
import asyncio
import logging
from typing import Optional, Dict, Any, List, Tuple

log = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


class GraphClient:
    """Async client for Microsoft Graph API with retry logic."""

    def __init__(self, access_token: str):
        self._access_token = access_token
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        """Make authenticated request with retry logic.

        Handles:
        - 429: Respects Retry-After header
        - 5xx: Exponential backoff
        """
        client = await self._get_client()

        for attempt in range(max_retries):
            try:
                headers = {"Authorization": f"Bearer {self._access_token}"}
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    follow_redirects=follow_redirects,
                )

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "60"))
                    log.warning(f"Rate limited, waiting {retry_after} seconds")
                    await asyncio.sleep(retry_after)
                    continue

                if response.status_code >= 500:
                    wait_time = 2**attempt
                    log.warning(f"Server error {response.status_code}, waiting {wait_time}s")
                    await asyncio.sleep(wait_time)
                    continue

                return response

            except httpx.HTTPStatusError as e:
                if attempt == max_retries - 1:
                    raise
                log.warning(f"HTTP error on attempt {attempt + 1}: {e}")

        raise RuntimeError(f"Failed after {max_retries} retries")

    async def _get_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """Make authenticated GET request and return JSON."""
        response = await self._request_with_retry("GET", url, params, max_retries)
        response.raise_for_status()
        return response.json()

    async def list_folder_items(
        self,
        drive_id: str,
        folder_id: str,
    ) -> List[Dict[str, Any]]:
        """List all items in a folder (non-recursive)."""
        url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{folder_id}/children"
        items = []

        while url:
            data = await self._get_json(url)
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")

        return items

    async def list_folder_items_recursive(
        self,
        drive_id: str,
        folder_id: str,
    ) -> List[Dict[str, Any]]:
        """List all items in a folder recursively (including subfolders)."""
        all_items = []
        folders_to_process = [(folder_id, "")]

        while folders_to_process:
            current_folder_id, parent_path = folders_to_process.pop(0)
            items = await self.list_folder_items(drive_id, current_folder_id)

            for item in items:
                item_path = f"{parent_path}/{item['name']}" if parent_path else item["name"]
                item["_relative_path"] = item_path

                if "folder" in item:
                    folders_to_process.append((item["id"], item_path))
                else:
                    all_items.append(item)

        return all_items

    async def get_drive_delta(
        self,
        drive_id: str,
        folder_id: str,
        delta_link: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Get changes using delta query.

        Returns:
            Tuple of (changed items, new delta link)
        """
        if delta_link:
            url = delta_link
        else:
            # Delta for specific folder - note: this tracks the entire drive
            # scoped to items under the folder
            url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{folder_id}/delta"

        items = []
        new_delta_link = None

        while url:
            data = await self._get_json(url)
            items.extend(data.get("value", []))

            if "@odata.deltaLink" in data:
                new_delta_link = data["@odata.deltaLink"]
                break
            url = data.get("@odata.nextLink")

        return items, new_delta_link

    async def download_file(self, drive_id: str, item_id: str) -> bytes:
        """Download file content."""
        url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}/content"
        response = await self._request_with_retry(
            "GET", url, follow_redirects=True
        )
        response.raise_for_status()
        return response.content

    async def get_item_metadata(self, drive_id: str, item_id: str) -> Dict[str, Any]:
        """Get metadata for a specific item."""
        url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}"
        return await self._get_json(url)

    async def get_item(
        self, drive_id: str, item_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get metadata for a single item, returning None if not found."""
        url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}"
        response = await self._request_with_retry("GET", url)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def get_folder_permissions(
        self,
        drive_id: str,
        folder_id: str,
    ) -> List[Dict[str, Any]]:
        """Get sharing permissions for a folder."""
        url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{folder_id}/permissions"
        data = await self._get_json(url)
        return data.get("value", [])
