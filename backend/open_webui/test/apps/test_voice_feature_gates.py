from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _speech_client(monkeypatch, gates):
    from open_webui.routers import audio
    from open_webui.utils.auth import get_verified_user

    async def config(key, default=None):
        return {'audio.tts.engine': 'openai'}.get(key, default)

    monkeypatch.setattr(audio.Config, 'get', config)
    monkeypatch.setattr(audio, 'is_feature_enabled', lambda feature: gates.get(feature, True))
    endpoint = next(r.endpoint for r in audio.router.routes if r.path == '/speech')
    app = FastAPI()
    app.add_api_route('/speech', endpoint, methods=['POST'], response_model=None)
    admin = SimpleNamespace(id='admin', role='admin', email='admin@example.com', name='Admin')
    app.dependency_overrides[get_verified_user] = lambda: admin
    return TestClient(app, raise_server_exceptions=False)


def test_speech_is_refused_for_admins_when_read_aloud_and_call_are_off(monkeypatch):
    """Tenant gates bind admins: dictation-only tenants get no speech output."""
    client = _speech_client(monkeypatch, {'read_aloud': False, 'voice_call': False})
    response = client.post('/speech', json={'input': 'hallo'})
    assert response.status_code == 403, response.text


@pytest.mark.parametrize(
    'gates', [{'read_aloud': True, 'voice_call': False}, {'read_aloud': False, 'voice_call': True}]
)
def test_speech_passes_the_gate_when_either_output_feature_is_on(monkeypatch, gates):
    """Either read-aloud or call mode is enough to reach the TTS engine."""
    client = _speech_client(monkeypatch, gates)
    response = client.post('/speech', content=b'[1]', headers={'Content-Type': 'application/json'})
    assert response.status_code == 400, response.text


def test_sub_gates_follow_the_voice_master_gate(monkeypatch):
    """voice_call and read_aloud are off whenever FEATURE_VOICE is off."""
    import importlib

    from open_webui import config
    from open_webui.utils import features

    monkeypatch.setattr(config, 'FEATURE_VOICE', False)
    monkeypatch.setattr(config, 'FEATURE_VOICE_CALL', True)
    monkeypatch.setattr(config, 'FEATURE_READ_ALOUD', True)
    reloaded = importlib.reload(features)
    try:
        assert reloaded.is_feature_enabled('voice_call') is False
        assert reloaded.is_feature_enabled('read_aloud') is False
    finally:
        monkeypatch.undo()
        importlib.reload(features)
