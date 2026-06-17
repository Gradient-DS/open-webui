"""Pure field extractors for the TOPdesk REST KnowledgeItem shape.

The REST Knowledge Base API (knowledge-base-v1) returns a **nested** item:
content lives under ``translation.content.{title,description,content,keywords}``,
status under ``status.name``, SSP visibility under ``visibility.sspVisibility``,
and web links under ``urls.{operator,ssp,public}`` (relative paths). These helpers
read that shape and flatten it, so the nested structure is parsed in exactly ONE
place — a schema correction is a single-file edit, shared by the sync worker
(file_info building) and the router (tree-picker shape).

All functions are pure and defensive: a missing/None branch yields ``''`` / ``[]``
/ ``False`` rather than raising, because partial field sets are normal (the
``fields`` request param controls which subfields TOPdesk returns).

See thoughts/shared/research/2026-06-topdesk-api-verification.md for the schema.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Valid TOPDESK_SYNC_SCOPE values.
SYNC_SCOPES = ('ssp', 'public', 'all')


def translation_content(item: Dict[str, Any]) -> Dict[str, Any]:
    """The ``translation.content`` block (title/description/content/keywords)."""
    return ((item or {}).get('translation') or {}).get('content') or {}


def item_title(item: Dict[str, Any]) -> str:
    """Display title — ``translation.content.title``, else the number, else ''."""
    return translation_content(item).get('title') or (item or {}).get('number') or ''


def item_body_html(item: Dict[str, Any]) -> str:
    """The HTML body — ``translation.content.content``."""
    return translation_content(item).get('content') or ''


def item_description(item: Dict[str, Any]) -> str:
    """The short description (HTML) — ``translation.content.description``."""
    return translation_content(item).get('description') or ''


def item_keywords(item: Dict[str, Any]) -> List[str]:
    """Keywords as a list.

    REST returns ``translation.content.keywords`` as a single comma/space string;
    split it. Tolerates a list too (so re-mapping an already-flattened value is a
    no-op).
    """
    raw = translation_content(item).get('keywords')
    if isinstance(raw, list):
        return [str(k).strip() for k in raw if str(k).strip()]
    if isinstance(raw, str):
        return [k for k in re.split(r'[,\s]+', raw.strip()) if k]
    return []


def item_language(item: Dict[str, Any]) -> str:
    """The translation's BCP-47 language tag — ``translation.language``."""
    return ((item or {}).get('translation') or {}).get('language') or ''


def item_status_name(item: Dict[str, Any]) -> str:
    """Status display name — ``status.name`` (status is a searchlist, not an enum)."""
    return ((item or {}).get('status') or {}).get('name') or ''


def item_ssp_visibility(item: Dict[str, Any]) -> str:
    """SSP visibility token — ``visibility.sspVisibility`` (VISIBLE / NOT_VISIBLE / VISIBLE_IN_PERIOD)."""
    return ((item or {}).get('visibility') or {}).get('sspVisibility') or ''


def item_parent_id(item: Dict[str, Any]) -> str:
    """Parent item id — ``parent.id`` (empty for a root item)."""
    return ((item or {}).get('parent') or {}).get('id') or ''


def item_web_url(item: Dict[str, Any], base_url: str) -> str:
    """Absolute web link for the item.

    Prefers the public URL, then SSP, then operator (``urls.{public,ssp,operator}``).
    The REST URLs are relative (start with ``/``), so prefix with the tenant base
    URL. Returns '' when no URL is present.
    """
    urls = (item or {}).get('urls') or {}
    rel = urls.get('public') or urls.get('ssp') or urls.get('operator') or ''
    if not rel:
        return ''
    if rel.startswith('http://') or rel.startswith('https://'):
        return rel
    return f'{(base_url or "").rstrip("/")}{rel}'


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 UTC timestamp; return None on absence/parse failure."""
    if not value:
        return None
    try:
        # TOPdesk emits e.g. '2026-05-21T14:30:00Z'; normalise the trailing Z.
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None


def within_ssp_period(visibility: Dict[str, Any]) -> bool:
    """True when 'now' falls within the VISIBLE_IN_PERIOD window.

    A missing ``sspVisibleFrom`` means "since forever"; a missing ``sspVisibleUntil``
    means "indefinitely". If a bound is present but unparseable we ignore it (fail
    open) rather than hide content. With both bounds absent the item is treated as
    visible — VISIBLE_IN_PERIOD without dates degenerates to plain VISIBLE.
    """
    vis = visibility or {}
    now = datetime.now(timezone.utc)
    start = _parse_iso(vis.get('sspVisibleFrom'))
    end = _parse_iso(vis.get('sspVisibleUntil'))
    if start and now < start:
        return False
    if end and now > end:
        return False
    return True


def should_sync(item: Dict[str, Any], scope: str) -> bool:
    """Whether an item should be ingested, given the configured sync scope.

    Archived items are always excluded. Otherwise:
      - 'all'    → every operator-readable item.
      - 'public' → only ``visibility.publicKnowledgeItem``.
      - 'ssp'    → ``sspVisibility == VISIBLE`` (or VISIBLE_IN_PERIOD within window).
    Unknown scope values fall back to the 'ssp' rule (the safe default).
    """
    item = item or {}
    if item.get('archived'):
        return False

    scope = (scope or 'ssp').strip().lower()
    if scope == 'all':
        return True

    visibility = item.get('visibility') or {}
    if scope == 'public':
        return bool(visibility.get('publicKnowledgeItem'))

    # Default: SSP-visible.
    ssp = visibility.get('sspVisibility')
    if ssp == 'VISIBLE':
        return True
    if ssp == 'VISIBLE_IN_PERIOD':
        return within_ssp_period(visibility)
    return False
