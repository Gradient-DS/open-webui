"""[Gradient] Council information search (raadsinformatie): the admin's choice and what there is to choose from.

The agents search Open Besluitvorming (formerly Open Raadsinformatie). The admin switches the search on and picks
the organizations it covers; none searches every organization. The choice is stored with each organization's
label, so a chat turn sends it as the agent's `council` scope without calling the source.
"""

import logging
import re
from time import monotonic

import httpx
from open_webui.env import COUNCIL_SOURCES_URL
from open_webui.models.config import Config

ENABLE = 'council.enable'
ORGANIZATIONS = 'council.organizations'
# The agent's council tools: sent only when the agents list both.
TOOLS = ('search_council_documents', 'read_council_document')
# A source key as the agent accepts it.
KEY = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')
MAX_ORGANIZATIONS = 25
TTL_SECONDS = 3600.0
log = logging.getLogger(__name__)
_cache: dict = {'expires_at': 0.0, 'sources': []}


class SourcesUnavailable(Exception):
    """The source's organization list could not be read."""


async def sources() -> list[dict[str, str]]:
    """Every organization the source holds, by label: its key, label and kind (gemeente, provincie, waterschap).
    Cached for an hour per process."""
    if monotonic() < _cache['expires_at']:
        return _cache['sources']
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.get(
                f'{COUNCIL_SOURCES_URL.rstrip("/")}/api/sources', params={'implemented': 'true'}
            )
            response.raise_for_status()
            listed = response.json()['sources']
        found = sorted(
            (
                {
                    'key': source['key'],
                    'label': str(source.get('label') or source['key']),
                    'kind': str(source.get('organizationType') or ''),
                }
                for source in listed
                if isinstance(source.get('key'), str) and KEY.match(source['key'])
            ),
            key=lambda source: source['label'].lower(),
        )
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
        log.warning('Could not read the council organizations: %s', type(error).__name__)
        raise SourcesUnavailable() from error
    _cache.update(expires_at=monotonic() + TTL_SECONDS, sources=found)
    return found


async def scope() -> dict[str, list[dict[str, str]]]:
    """The turn's `council` scope: the organizations the admin chose, as stored."""
    stored = await Config.get(ORGANIZATIONS) or []
    organizations = [
        {'key': entry['key'], 'label': str(entry.get('label') or entry['key'])}
        for entry in stored
        if isinstance(entry, dict) and isinstance(entry.get('key'), str) and KEY.match(entry['key'])
    ]
    unique: dict[str, dict[str, str]] = {}
    for entry in organizations:
        unique.setdefault(entry['key'], entry)
    return {'organizations': list(unique.values())[:MAX_ORGANIZATIONS]}
