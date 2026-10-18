"""Strict data-separation classification (soev data-sovereignty feature).

Pure helpers that classify chat-completion attachments into two mutually
exclusive groups and detect a request that mixes them:

  * "open_internet" — web search results and fetched webpage URLs
  * "internal"      — uploaded files, knowledge collections, folders, notes

Mirrors ``src/lib/utils/dataSeparation.ts`` on the frontend — keep the two
classifications in sync. Gated by ``FEATURE_STRICT_DATA_SEPARATION``; this
module itself is flag-agnostic (the caller checks the flag).
"""

from __future__ import annotations

from typing import Optional

OPEN_INTERNET = 'open_internet'
INTERNAL = 'internal'

# Internal document file-item ``type`` values produced by the chat UI / backend.
INTERNAL_FILE_TYPES = {'file', 'image', 'collection', 'folder', 'chat', 'note'}


def classify_file(item: Optional[dict]) -> Optional[str]:
    """Return ``"open_internet"``, ``"internal"``, or ``None`` for a file item."""
    if not isinstance(item, dict):
        return None
    file_type = item.get('type')
    if file_type == 'web_search':
        return OPEN_INTERNET
    # ``url`` — a web page attached for the agent to fetch live (GRA-222).
    # Same side as an ingested page: the content comes off the open internet
    # either way, only the moment of fetching differs.
    if file_type == 'url':
        return OPEN_INTERNET
    if file_type == 'text' and item.get('url'):
        return OPEN_INTERNET
    if file_type in INTERNAL_FILE_TYPES:
        return INTERNAL
    return None


def request_mixes_data_sources(files, messages, web_search_requested) -> bool:
    """True when a request combines open-internet with internal documents.

    Considers the current turn's ``files``, every prior message's ``files``
    (conversation lock), and whether web search is requested this turn.
    """
    open_internet = bool(web_search_requested)
    internal = False

    all_files = list(files or [])
    for message in messages or []:
        if isinstance(message, dict):
            all_files.extend(message.get('files') or [])

    for item in all_files:
        side = classify_file(item)
        if side == OPEN_INTERNET:
            open_internet = True
        elif side == INTERNAL:
            internal = True
        if open_internet and internal:
            return True

    return open_internet and internal
