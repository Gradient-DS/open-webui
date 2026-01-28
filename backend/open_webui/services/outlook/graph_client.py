"""
Microsoft Graph API client for Outlook email operations.
"""

import asyncio
import logging
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


class OutlookGraphClient:
    """Async client for Microsoft Graph Outlook API."""

    def __init__(self, access_token: str):
        self._access_token = access_token
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        max_retries: int = 3,
        **kwargs,
    ) -> httpx.Response:
        """Make HTTP request with retry logic for rate limiting."""
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            **kwargs.pop("headers", {}),
        }

        for attempt in range(max_retries):
            response = await self._client.request(
                method, url, headers=headers, **kwargs
            )

            if response.status_code == 429:
                # Rate limited - respect Retry-After header
                retry_after = int(response.headers.get("Retry-After", "30"))
                log.warning(f"Rate limited by Graph API, waiting {retry_after}s")
                await asyncio.sleep(retry_after)
                continue

            if response.status_code >= 500:
                # Server error - exponential backoff
                wait_time = 2**attempt
                log.warning(f"Graph API server error, retrying in {wait_time}s")
                await asyncio.sleep(wait_time)
                continue

            return response

        return response

    async def search_emails(
        self,
        query: str,
        max_results: int = 25,
        include_body: bool = True,
    ) -> dict[str, Any]:
        """
        Search emails using Microsoft Graph API.

        Args:
            query: KQL search query (e.g., "from:john subject:project")
            max_results: Maximum number of emails to return
            include_body: Whether to include email body in results

        Returns:
            Dict with emails list and metadata
        """
        select_fields = [
            "id",
            "subject",
            "from",
            "toRecipients",
            "receivedDateTime",
            "webLink",
            "hasAttachments",
            "importance",
        ]
        if include_body:
            select_fields.extend(["body", "bodyPreview"])

        url = f"{GRAPH_BASE_URL}/me/messages"
        # Note: Microsoft Graph API doesn't support $orderby with $search
        # Results from $search are ranked by relevance by default
        params = {
            "$search": f'"{query}"',
            "$select": ",".join(select_fields),
            "$top": str(max_results),
        }

        response = await self._request_with_retry("GET", url, params=params)

        if response.status_code == 401:
            return {"error": "OAuth token expired or invalid", "status": 401}

        if response.status_code == 403:
            return {
                "error": "Insufficient permissions. Mail.Read scope required.",
                "status": 403,
            }

        if response.status_code != 200:
            log.error(f"Graph API error: {response.status_code} - {response.text}")
            return {"error": f"Graph API error: {response.status_code}", "status": response.status_code}

        data = response.json()
        emails = []

        for msg in data.get("value", []):
            email = {
                "id": msg.get("id"),
                "subject": msg.get("subject", "(No subject)"),
                "from": msg.get("from", {}).get("emailAddress", {}).get("address"),
                "from_name": msg.get("from", {}).get("emailAddress", {}).get("name"),
                "to": [
                    r.get("emailAddress", {}).get("address")
                    for r in msg.get("toRecipients", [])
                ],
                "received": msg.get("receivedDateTime"),
                "webLink": msg.get("webLink"),
                "hasAttachments": msg.get("hasAttachments", False),
                "importance": msg.get("importance", "normal"),
            }

            if include_body:
                # Prefer plain text body preview for context efficiency
                body = msg.get("body", {})
                if body.get("contentType") == "text":
                    email["body"] = body.get("content", "")
                else:
                    # For HTML, use bodyPreview (plain text summary)
                    email["body"] = msg.get("bodyPreview", "")

            emails.append(email)

        return {
            "emails": emails,
            "count": len(emails),
            "query": query,
        }

    async def get_email_by_id(self, email_id: str) -> dict[str, Any]:
        """Get a single email by ID with full body."""
        url = f"{GRAPH_BASE_URL}/me/messages/{email_id}"
        params = {
            "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,body,webLink,hasAttachments,importance"
        }

        response = await self._request_with_retry("GET", url, params=params)

        if response.status_code != 200:
            return {"error": f"Failed to get email: {response.status_code}"}

        msg = response.json()
        return {
            "id": msg.get("id"),
            "subject": msg.get("subject"),
            "from": msg.get("from", {}).get("emailAddress", {}),
            "to": [r.get("emailAddress", {}) for r in msg.get("toRecipients", [])],
            "cc": [r.get("emailAddress", {}) for r in msg.get("ccRecipients", [])],
            "received": msg.get("receivedDateTime"),
            "body": msg.get("body", {}).get("content", ""),
            "bodyType": msg.get("body", {}).get("contentType"),
            "webLink": msg.get("webLink"),
            "hasAttachments": msg.get("hasAttachments", False),
        }
