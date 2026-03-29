"""Google Drive API v3 client for file operations."""

import httpx
import asyncio
import logging
from typing import Optional, Callable, Awaitable, Dict, Any, List, Tuple

log = logging.getLogger(__name__)

DRIVE_BASE_URL = "https://www.googleapis.com/drive/v3"

# Google Workspace MIME types that require export instead of download
GOOGLE_WORKSPACE_EXPORT_MAP = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
}

# Standard fields to request from the Files API
_FILE_FIELDS = "id,name,mimeType,size,md5Checksum,modifiedTime,parents,trashed"


class GoogleDriveClient:
    """Async client for Google Drive API v3 with retry logic."""

    def __init__(
        self,
        access_token: str,
        token_provider: Optional[Callable[[], Awaitable[Optional[str]]]] = None,
    ):
        self._access_token = access_token
        self._token_provider = token_provider
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
        - 401: Token refresh via token_provider callback (once)
        - 429: Respects Retry-After header
        - 5xx: Exponential backoff
        """
        client = await self._get_client()
        last_exception = None
        token_refreshed = False

        for attempt in range(max_retries):
            try:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {self._access_token}"},
                    follow_redirects=follow_redirects,
                )

                if response.status_code == 401 and not token_refreshed and self._token_provider:
                    log.info("Received 401, attempting token refresh")
                    try:
                        new_token = await self._token_provider()
                        if new_token:
                            self._access_token = new_token
                            token_refreshed = True
                            continue
                    except Exception as e:
                        log.warning("Token refresh failed: %s", e)
                    return response

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "60"))
                    log.warning("Rate limited, waiting %d seconds", retry_after)
                    await asyncio.sleep(retry_after)
                    continue

                if response.status_code >= 500:
                    wait_time = 2**attempt
                    log.warning(
                        "Server error %d, retrying in %d seconds",
                        response.status_code,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    continue

                return response

            except httpx.HTTPStatusError as e:
                last_exception = e
                if attempt < max_retries - 1:
                    await asyncio.sleep(2**attempt)
                else:
                    raise

        raise RuntimeError(f"Failed after {max_retries} retries: {last_exception}")

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

    async def list_folder_children(
        self,
        folder_id: str,
    ) -> List[Dict[str, Any]]:
        """List all items in a folder (non-recursive).

        Uses the files.list endpoint with q parameter to find children.
        """
        url = f"{DRIVE_BASE_URL}/files"
        items = []
        page_token = None

        while True:
            params = {
                "q": f"'{folder_id}' in parents and trashed=false",
                "fields": f"nextPageToken,files({_FILE_FIELDS})",
                "pageSize": "1000",
            }
            if page_token:
                params["pageToken"] = page_token

            data = await self._get_json(url, params=params)
            items.extend(data.get("files", []))

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        return items

    async def list_folder_children_recursive(
        self,
        folder_id: str,
    ) -> List[Dict[str, Any]]:
        """List all items in a folder recursively (BFS)."""
        all_items = []
        folders_to_process = [(folder_id, "")]

        while folders_to_process:
            current_folder_id, parent_path = folders_to_process.pop(0)
            items = await self.list_folder_children(current_folder_id)

            for item in items:
                item_path = f"{parent_path}/{item['name']}" if parent_path else item["name"]
                item["_relative_path"] = item_path

                if item.get("mimeType") == "application/vnd.google-apps.folder":
                    folders_to_process.append((item["id"], item_path))
                else:
                    all_items.append(item)

        return all_items

    async def get_changes(
        self,
        page_token: str,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Get changes since the given page token.

        Returns:
            Tuple of (changed items, new page token for next call)
        """
        url = f"{DRIVE_BASE_URL}/changes"
        items = []
        new_page_token = None

        current_token = page_token
        while True:
            params = {
                "pageToken": current_token,
                "fields": f"nextPageToken,newStartPageToken,changes(removed,fileId,file({_FILE_FIELDS}))",
                "pageSize": "1000",
                "includeRemoved": "true",
            }

            data = await self._get_json(url, params=params)

            for change in data.get("changes", []):
                if change.get("removed"):
                    items.append({"id": change["fileId"], "@removed": True})
                elif change.get("file"):
                    items.append(change["file"])

            if "newStartPageToken" in data:
                new_page_token = data["newStartPageToken"]
                break

            next_token = data.get("nextPageToken")
            if not next_token:
                break
            current_token = next_token

        return items, new_page_token

    async def get_start_page_token(self) -> str:
        """Get the initial page token for the changes API."""
        url = f"{DRIVE_BASE_URL}/changes/startPageToken"
        data = await self._get_json(url)
        return data["startPageToken"]

    async def download_file(self, file_id: str) -> bytes:
        """Download file content (for non-Workspace files)."""
        url = f"{DRIVE_BASE_URL}/files/{file_id}"
        response = await self._request_with_retry("GET", url, params={"alt": "media"}, follow_redirects=True)
        response.raise_for_status()
        return response.content

    async def export_file(self, file_id: str, mime_type: str) -> bytes:
        """Export a Google Workspace file (Docs, Sheets, Slides) to the given MIME type."""
        url = f"{DRIVE_BASE_URL}/files/{file_id}/export"
        response = await self._request_with_retry("GET", url, params={"mimeType": mime_type}, follow_redirects=True)
        response.raise_for_status()
        return response.content

    async def get_file_metadata(self, file_id: str) -> Dict[str, Any]:
        """Get metadata for a specific file."""
        url = f"{DRIVE_BASE_URL}/files/{file_id}"
        return await self._get_json(url, params={"fields": _FILE_FIELDS})

    async def get_file(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a single file, returning None if not found."""
        url = f"{DRIVE_BASE_URL}/files/{file_id}"
        response = await self._request_with_retry("GET", url, params={"fields": _FILE_FIELDS})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def get_file_permissions(self, file_id: str) -> List[Dict[str, Any]]:
        """Get sharing permissions for a file or folder."""
        url = f"{DRIVE_BASE_URL}/files/{file_id}/permissions"
        data = await self._get_json(
            url,
            params={"fields": "permissions(id,type,emailAddress,role)"},
        )
        return data.get("permissions", [])
