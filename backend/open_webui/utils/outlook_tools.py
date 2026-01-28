"""
Outlook tools for LLM function calling.
"""

import json
import logging
from typing import Any

from open_webui.services.outlook import OutlookGraphClient
from open_webui.config import OUTLOOK_MAX_SEARCH_RESULTS

log = logging.getLogger(__name__)

# Tool specification following OpenAI function calling format
OUTLOOK_SEARCH_TOOL_SPEC = {
    "name": "outlook_search",
    "description": """Search the user's Outlook mailbox for emails. Use this tool when the user asks about their emails, wants to find specific messages, or needs information from their inbox.

Search query examples:
- "project update" - searches across subject, body, and sender
- "from:john" - find emails from someone named John
- "subject:meeting" - find emails with 'meeting' in the subject
- "quarterly report budget" - multiple keywords (searches all fields)

The search finds emails matching the keywords in subject, body, or sender fields. Results are ordered by relevance.

Returns email subjects, senders, dates, body content (preview), and links to open in Outlook Web.""",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search keywords to find in emails. Can include 'from:name' or 'subject:keyword' prefixes, or just plain keywords."
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of emails to return (1-25)",
                "default": 10
            }
        },
        "required": ["query"]
    }
}


async def outlook_search_callable(
    query: str,
    max_results: int = 10,
    __oauth_token__: dict = None,
    **kwargs
) -> str:
    """
    Search Outlook emails. Called by LLM via function calling.

    Returns JSON string with email results.
    """
    if not __oauth_token__:
        return json.dumps({
            "error": "Not authenticated with Microsoft. Please sign in via Microsoft OAuth to use Outlook features.",
            "requires_auth": True
        })

    access_token = __oauth_token__.get("access_token")
    if not access_token:
        return json.dumps({
            "error": "Microsoft OAuth token missing. Please re-authenticate.",
            "requires_auth": True
        })

    # Clamp max_results to reasonable limits
    max_results = min(max(1, max_results), OUTLOOK_MAX_SEARCH_RESULTS)

    try:
        async with OutlookGraphClient(access_token) as client:
            result = await client.search_emails(
                query=query,
                max_results=max_results,
                include_body=True,
            )

        if "error" in result:
            return json.dumps(result)

        # Format results for LLM consumption
        formatted_emails = []
        for email in result.get("emails", []):
            formatted_emails.append({
                "subject": email.get("subject"),
                "from": f"{email.get('from_name', '')} <{email.get('from', '')}>"
                if email.get('from_name')
                else email.get('from', ''),
                "received": email.get("received"),
                "body": email.get("body", "")[:2000],  # Limit body length
                "hasAttachments": email.get("hasAttachments"),
                "importance": email.get("importance"),
                "outlookLink": email.get("webLink"),
            })

        return json.dumps({
            "emails": formatted_emails,
            "count": len(formatted_emails),
            "query": query,
            "note": "Use the outlookLink to let the user open emails in Outlook Web."
        }, indent=2)

    except Exception as e:
        log.exception(f"Outlook search error: {e}")
        return json.dumps({
            "error": f"Failed to search emails: {str(e)}"
        })


def get_outlook_tools(oauth_token: dict) -> dict[str, Any]:
    """
    Get Outlook tools dict for injection into tools_dict.

    Returns tools in the format expected by middleware.
    """
    def make_callable():
        async def _callable(**kwargs):
            return await outlook_search_callable(
                __oauth_token__=oauth_token,
                **kwargs
            )
        return _callable

    return {
        "outlook_search": {
            "spec": OUTLOOK_SEARCH_TOOL_SPEC,
            "callable": make_callable(),
            "type": "builtin",
            "direct": False,
        }
    }
