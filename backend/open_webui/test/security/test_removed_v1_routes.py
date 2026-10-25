"""Settings for paths the platform replaced are not served: a stale route answers 500, not 404."""

from fastapi.routing import APIRoute


def test_connections_config_is_not_served(monkeypatch, tmp_path):
    monkeypatch.setenv('STATIC_DIR', str(tmp_path))
    from open_webui.main import app

    served = {(method, route.path) for route in app.routes if isinstance(route, APIRoute) for method in route.methods}
    assert not {('GET', '/api/v1/configs/connections'), ('POST', '/api/v1/configs/connections')} & served
