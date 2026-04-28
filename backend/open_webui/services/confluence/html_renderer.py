"""HTML → Markdown converter for Confluence page bodies.

Uses BeautifulSoup (already a project dep) instead of markdownify so we don't
carry an extra package just for this. Handles the elements that show up in
Confluence ``body-format=view`` output: headings, paragraphs, links, lists,
code blocks, inline emphasis, blockquotes, line breaks. Other tags fall
through to their text content.

Synchronous and CPU-bound — call inside ``asyncio.to_thread`` from async code.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString, Tag


_HEADING_LEVELS = {f'h{i}': i for i in range(1, 7)}


def html_to_markdown(html: str) -> str:
    """Convert an HTML fragment to Markdown."""
    if not html:
        return ''
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style']):
        tag.decompose()
    text = _render(soup)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _render(node) -> str:
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ''

    name = node.name.lower()

    if name in _HEADING_LEVELS:
        level = _HEADING_LEVELS[name]
        return f'\n\n{"#" * level} {_children(node).strip()}\n\n'
    if name == 'p':
        return f'\n\n{_children(node).strip()}\n\n'
    if name in ('strong', 'b'):
        return f'**{_children(node)}**'
    if name in ('em', 'i'):
        return f'*{_children(node)}*'
    if name == 'br':
        return '  \n'
    if name == 'hr':
        return '\n\n---\n\n'
    if name == 'a':
        href = (node.get('href') or '').strip()
        text = _children(node).strip() or href
        return f'[{text}]({href})' if href else text
    if name == 'pre':
        return f'\n\n```\n{_children(node).strip()}\n```\n\n'
    if name == 'code':
        # Inside <pre> the code fence carries the content; emit children plain.
        if node.parent and node.parent.name == 'pre':
            return _children(node)
        return f'`{_children(node)}`'
    if name == 'blockquote':
        body = _children(node).strip()
        quoted = '\n'.join(f'> {ln}' if ln else '>' for ln in body.split('\n'))
        return f'\n\n{quoted}\n\n'
    if name in ('ul', 'ol'):
        return _list(node, ordered=(name == 'ol'))
    if name == 'li':
        return _children(node).strip()

    return _children(node)


def _children(node: Tag) -> str:
    return ''.join(_render(c) for c in node.children)


def _list(node: Tag, ordered: bool) -> str:
    items = []
    idx = 0
    for child in node.children:
        if not isinstance(child, Tag) or child.name.lower() != 'li':
            continue
        idx += 1
        marker = f'{idx}.' if ordered else '-'
        body = _children(child).strip()
        if not body:
            continue
        first, *rest = body.split('\n')
        items.append('\n'.join([f'{marker} {first}'] + [f'  {ln}' for ln in rest]))
    return '\n\n' + '\n'.join(items) + '\n\n'
