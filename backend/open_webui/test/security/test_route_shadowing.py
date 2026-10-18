"""No route may sit unreachable behind an earlier route that matches its path.

`GET /api/v1/prompts/id/{prompt_id}/history/diff` was registered after
`.../history/{history_id}`, so every diff request was answered as a lookup of a
history entry named `diff` (PLANE-017). The coverage gate saw it only as a 404;
this names the cause for any route.
"""

import re

from fastapi.routing import APIRoute
from starlette.routing import Match


def _concrete(path):
    return re.sub(r'\{[^}]+\}', lambda m: '1' if m.group(0).endswith(':int}') else 'x', path)


def test_no_route_is_answered_by_an_earlier_one(monkeypatch, tmp_path):
    # config.py empties STATIC_DIR on import; outside the image that is the
    # repository's static assets (see scripts/security/export_openapi.py).
    monkeypatch.setenv('STATIC_DIR', str(tmp_path))
    from open_webui.main import app

    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    shadowed = []
    for route in routes:
        for method in sorted(route.methods):
            scope = {'type': 'http', 'method': method, 'path': _concrete(route.path)}
            first = next(other for other in routes if other.matches(scope)[0] == Match.FULL)
            if first is not route:
                shadowed.append(f'{method} {route.path} is answered by {first.path}')
    assert not shadowed, shadowed
