"""The page a consent popup lands on: it relays the result to its opener and shows how it went."""

import html
import json

# Per result: (state, English (title, line), Dutch (title, line)).
_STATES = {
    'pending': ('done', ('Connected.', 'This window closes by itself.'), ('Verbonden.', 'Dit venster sluit vanzelf.')),
    'interaction_required': (
        'busy',
        ('One more step…', 'The sign-in page opens in a moment.'),
        ('Nog één stap…', 'De aanmeldpagina opent zo.'),
    ),
}
_FAILED = (
    'failed',
    ('Could not connect.', 'You can close this window.'),
    ('Verbinden is niet gelukt.', 'Je kunt dit venster sluiten.'),
)
# Long enough to read the result; the message to the opener does not wait for it.
_CLOSE_DELAY_MS = 700

# Keep in step with the loading page in src/lib/utils/popup.ts.
_STYLE = (
    ':root{color-scheme:light dark;--bg:#fff;--fg:#171717;--muted:#6b6b6b;--track:#e5e5e5;--ok:#16a34a;--bad:#dc2626}'
    '@media (prefers-color-scheme:dark){:root:not(.light){--bg:#171717;--fg:#ececec;--muted:#9b9b9b;--track:#333;'
    '--ok:#4ade80;--bad:#f87171}}'
    ':root.dark{--bg:#171717;--fg:#ececec;--muted:#9b9b9b;--track:#333;--ok:#4ade80;--bad:#f87171}'
    'html,body{height:100%;margin:0}'
    'body{display:flex;align-items:center;justify-content:center;background:var(--bg);color:var(--fg);'
    "font:15px/1.5 system-ui,-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;text-align:center}"
    'main{padding:24px;max-width:320px}'
    '.icon{width:28px;height:28px;margin:0 auto 20px;box-sizing:border-box}'
    '.busy{border-radius:50%;border:3px solid var(--track);border-top-color:var(--fg);'
    'animation:spin .9s linear infinite}'
    '.done{color:var(--ok)}.failed{color:var(--bad)}'
    'h1{font-size:16px;font-weight:500;margin:0 0 4px}'
    'p{margin:0;font-size:13px;color:var(--muted)}'
    '@keyframes spin{to{transform:rotate(360deg)}}'
    '@keyframes pop{from{opacity:0;transform:scale(.8)}}'
    '.done,.failed{animation:pop .2s ease-out}'
    '@media (prefers-reduced-motion:reduce){.icon{animation:none}}'
)
_ICONS = {
    'done': '<circle cx="12" cy="12" r="10"/><path d="M7.5 12.5l3 3 6-6.5"/>',
    'failed': '<circle cx="12" cy="12" r="10"/><path d="M12 7.5v5"/><path d="M12 16.5h.01"/>',
}


def _icon(state: str) -> str:
    if state == 'busy':
        return '<div class="icon busy" aria-hidden="true"></div>'
    return (
        f'<svg class="icon {state}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_ICONS[state]}</svg>'
    )


def render(connection: str, result: str, origins: list[str], language: str) -> str:
    """The relay page; `connection` and `origins` reach only the script, JSON-encoded with `<` escaped."""
    state, english, dutch = _STATES.get(result, _FAILED)
    lang = 'nl' if language.lower().startswith('nl') else 'en'
    title, line = dutch if lang == 'nl' else english
    data = json.dumps({'type': 'soev_connect', 'connection': connection, 'result': result}).replace('<', '\\u003c')
    targets = json.dumps(sorted(origins)).replace('<', '\\u003c')
    # Keep the silent attempt's popup for the opener to continue interactive consent.
    close = (
        '' if result == 'interaction_required' else f'setTimeout(function () {{ window.close(); }}, {_CLOSE_DELAY_MS});'
    )
    return (
        f'<!DOCTYPE html><html lang="{lang}">'
        '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{html.escape(title)}</title><style>{_STYLE}</style><script>'
        f'if (window.opener) {{ for (const origin of new Set([window.location.origin, ...{targets}])) '
        f'window.opener.postMessage({data}, origin); }}'
        # The opener's chosen theme wins over the system one when this page can read it.
        'try { const theme = window.opener.document.documentElement.classList; '
        "document.documentElement.classList.add(theme.contains('dark') ? 'dark' : 'light'); } catch (e) {}"
        f'{close}</script></head>'
        f'<body><main role="status" aria-live="polite">{_icon(state)}'
        f'<h1>{html.escape(title)}</h1><p>{html.escape(line)}</p></main></body></html>'
    )
