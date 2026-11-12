"""A built frontend must not turn retired API paths into successful HTML."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    'path',
    [
        '/api/v1/users/',
        '/api/v1/users/fictional-retired-person',
        '/api/v1/groups/id/fictional-retired-group',
        '/ollama/api/tags',
        '/openai/fictional-retired-route',
    ],
)
@pytest.mark.parametrize('method', ['GET', 'POST', 'DELETE'])
def test_spa_refuses_api_paths_with_a_built_frontend(tmp_path, path, method):
    from open_webui.main import SPAStaticFiles

    (tmp_path / 'index.html').write_text('<html>Fictional frontend</html>')
    app = FastAPI()
    app.mount('/', SPAStaticFiles(directory=tmp_path, html=True))
    with TestClient(app) as client:
        response = client.request(method, path)
    assert response.status_code == 404, response.text
    assert response.json() == {'detail': 'Not Found'}


def test_spa_still_serves_ui_navigation_and_registered_api_routes(tmp_path):
    from open_webui.main import SPAStaticFiles

    (tmp_path / 'index.html').write_text('<html>Fictional frontend</html>')
    app = FastAPI()

    @app.get('/api/fictional-control')
    async def control():
        return {'fictional': True}

    app.mount('/', SPAStaticFiles(directory=tmp_path, html=True))
    with TestClient(app) as client:
        assert client.get('/api/fictional-control').json() == {'fictional': True}
        for path in ('/', '/c/fictional-chat', '/apiary'):
            response = client.get(path)
            assert response.status_code == 200
            assert response.text == '<html>Fictional frontend</html>'
        assert client.get('/fictional-missing.js').status_code == 404
