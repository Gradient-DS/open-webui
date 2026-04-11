"""Shared utilities for chat export (PDF, Word, copy-ready HTML).

Handles citation normalization and markdown-to-HTML conversion for
server-side export endpoints.
"""

import re
from dataclasses import dataclass, field

from markdown import markdown


@dataclass
class SourceInfo:
    index: int  # 1-based, reordered by first appearance
    name: str
    url: str | None = None


@dataclass
class Citation:
    id: str
    name: str
    url: str | None = None


CITATION_RE = re.compile(r'\[(\d+(?:\s*,\s*\d+)*)\]')
DETAILS_RE = re.compile(r'<details[^>]*>[\s\S]*?</details>', re.IGNORECASE)
TRAILING_UUID_RE = re.compile(
    r'\s*[-–—]\s*[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


def _remove_details(content: str) -> str:
    """Strip <details> blocks (reasoning/thinking) from content."""
    return DETAILS_RE.sub('', content).strip()


def _clean_source_name(name: str) -> str:
    """Strip trailing UUID from source names."""
    return TRAILING_UUID_RE.sub('', name)


def _reduce_sources(sources: list[dict]) -> list[Citation]:
    """Deduplicate raw source objects into a flat citation list.

    Mirrors the frontend Citations.svelte logic.
    """
    citations: list[Citation] = []
    seen_ids: set[str] = set()

    for source in sources:
        if not source:
            continue

        documents = source.get('document', [])
        metadata_list = source.get('metadata', [])
        source_obj = source.get('source', {}) or {}

        for idx in range(len(documents)):
            metadata = metadata_list[idx] if idx < len(metadata_list) else {}
            cid = (metadata or {}).get('source') or source_obj.get('id') or 'N/A'
            name = source_obj.get('name', cid)

            if metadata and metadata.get('name'):
                name = metadata['name']

            url = None
            if cid.startswith('http://') or cid.startswith('https://'):
                name = cid
                url = cid
            source_url = source_obj.get('url', '')
            if source_url and (source_url.startswith('http://') or source_url.startswith('https://')):
                url = source_url

            if cid not in seen_ids:
                seen_ids.add(cid)
                citations.append(Citation(id=cid, name=_clean_source_name(name), url=url))

    return citations


def normalize_citations(content: str, sources: list[dict]) -> tuple[str, list[SourceInfo]]:
    """Normalize citation markers and build source appendix.

    - Finds all [N] markers in content
    - Reorders by first appearance
    - Returns (normalized_content_with_renumbered_markers, source_list)
    """
    citations = _reduce_sources(sources)
    if not citations:
        return content, []

    # 1. Collect referenced indices in order of first appearance
    seen: set[int] = set()
    appearance_order: list[int] = []

    for m in CITATION_RE.finditer(content):
        nums = [int(n.strip()) for n in m.group(1).split(',') if n.strip().isdigit()]
        for num in nums:
            if num not in seen and 1 <= num <= len(citations):
                seen.add(num)
                appearance_order.append(num)

    if not appearance_order:
        return content, []

    # 2. Build renumber mapping: original 1-based → new sequential 1-based
    renumber_map = {orig: i + 1 for i, orig in enumerate(appearance_order)}

    # 3. Replace citations in content
    def _replace(m: re.Match) -> str:
        nums = [int(n.strip()) for n in m.group(1).split(',') if n.strip().isdigit()]
        renumbered = [renumber_map[n] for n in nums if n in renumber_map]
        if not renumbered:
            return m.group(0)
        return '[' + ', '.join(str(n) for n in renumbered) + ']'

    normalized = CITATION_RE.sub(_replace, content)

    # 4. Build source list in appearance order
    source_list = [
        SourceInfo(
            index=i + 1,
            name=citations[orig - 1].name,
            url=citations[orig - 1].url,
        )
        for i, orig in enumerate(appearance_order)
    ]

    return normalized, source_list


def _citation_to_sup(content: str) -> str:
    """Replace [N] markers with <sup>[N]</sup> for HTML rendering."""

    def _sup_replace(m: re.Match) -> str:
        return f'<sup>{m.group(0)}</sup>'

    return CITATION_RE.sub(_sup_replace, content)


def _md_to_html(text: str) -> str:
    """Convert markdown to HTML with common extensions."""
    return markdown(
        text,
        extensions=['tables', 'fenced_code', 'codehilite', 'nl2br'],
    )


def prepare_export_messages(
    messages: list[dict],
) -> tuple[list[dict], list[SourceInfo]]:
    """Prepare messages for export.

    For each assistant message with sources:
    - Normalize citations
    - Convert markdown content to HTML with <sup> citations
    - Collect all sources across messages

    Returns (prepared_messages, combined_source_list)
    """
    # First pass: collect all sources across all assistant messages and combine
    all_sources: list[dict] = []
    for msg in messages:
        if msg.get('role') == 'assistant' and msg.get('sources'):
            all_sources.extend(msg['sources'])

    # Normalize citations for the entire conversation
    # We need to do this per-message but with a shared source list
    all_citations = _reduce_sources(all_sources)

    # Collect appearance order across all messages
    seen: set[int] = set()
    appearance_order: list[int] = []
    for msg in messages:
        if msg.get('role') == 'assistant':
            content = _remove_details(msg.get('content', ''))
            for m in CITATION_RE.finditer(content):
                nums = [int(n.strip()) for n in m.group(1).split(',') if n.strip().isdigit()]
                for num in nums:
                    if num not in seen and 1 <= num <= len(all_citations):
                        seen.add(num)
                        appearance_order.append(num)

    # Build renumber map
    renumber_map = {orig: i + 1 for i, orig in enumerate(appearance_order)}

    def _renumber(m: re.Match) -> str:
        nums = [int(n.strip()) for n in m.group(1).split(',') if n.strip().isdigit()]
        renumbered = [renumber_map[n] for n in nums if n in renumber_map]
        if not renumbered:
            return m.group(0)
        return '[' + ', '.join(str(n) for n in renumbered) + ']'

    # Second pass: prepare each message
    prepared: list[dict] = []
    for msg in messages:
        role = msg.get('role', 'unknown')
        content = _remove_details(msg.get('content', ''))

        if role == 'assistant':
            # Renumber citations
            content = CITATION_RE.sub(_renumber, content)
            # Convert [N] to <sup>[N]</sup>
            content = _citation_to_sup(content)

        html_content = _md_to_html(content)
        prepared.append({'role': role, 'html_content': html_content})

    # Build combined source list
    source_list = [
        SourceInfo(
            index=i + 1,
            name=all_citations[orig - 1].name,
            url=all_citations[orig - 1].url,
        )
        for i, orig in enumerate(appearance_order)
    ]

    return prepared, source_list


def build_source_appendix_html(sources: list[SourceInfo]) -> str:
    """Build HTML for the Bronnen section."""
    if not sources:
        return ''

    items = []
    for s in sources:
        url_part = f' — <span class="source-url">{s.url}</span>' if s.url and s.url != s.name else ''
        items.append(
            f'<div class="source-item"><span class="source-number">[{s.index}]</span> {s.name}{url_part}</div>'
        )

    return '<div class="sources"><h2>Bronnen</h2>\n' + '\n'.join(items) + '\n</div>'
