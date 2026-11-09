"""Initials avatar for users without a profile photo.

Keycloak logins carry no picture claim, so every user without an uploaded photo
gets a deterministic initials image instead of the generic silhouette.
"""

from hashlib import sha256
from xml.sax.saxutils import escape

_COLORS = ('#2B7458', '#1F5A8A', '#7A4E9C', '#A2532F', '#3D6B6B', '#8A6A1F')


def initials(name: str | None) -> str:
    words = [word for word in (name or '').split() if word[:1].isalpha()]
    if not words:
        return '?'
    letters = words[0][0] + (words[-1][0] if len(words) > 1 else '')
    return letters.upper()


def initials_svg(name: str | None, seed: str) -> bytes:
    color = _COLORS[int(sha256(seed.encode()).hexdigest(), 16) % len(_COLORS)]
    text = escape(initials(name))
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96">'
        f'<rect width="96" height="96" fill="{color}"/>'
        '<text x="48" y="48" dy="0.35em" text-anchor="middle" fill="#FFFFFF" '
        'font-family="Arial, Helvetica, sans-serif" font-size="40" font-weight="600">'
        f'{text}</text></svg>'
    ).encode()
