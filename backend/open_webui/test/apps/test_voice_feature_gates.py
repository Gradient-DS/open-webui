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


def _transcription_client(monkeypatch, tmp_path, *, voice=True, transcribe=None):
    from open_webui.routers import audio
    from open_webui.utils.auth import get_verified_user

    async def config(key, default=None):
        return default

    async def publish(*args, **kwargs):
        return None

    monkeypatch.setattr(audio, 'CACHE_DIR', tmp_path)
    monkeypatch.setattr(audio.Config, 'get', config)
    monkeypatch.setattr(audio, 'publish_event', publish)
    monkeypatch.setattr(audio, 'is_feature_enabled', lambda feature: voice if feature == 'voice' else True)
    if transcribe is not None:
        monkeypatch.setattr(audio, 'transcribe', transcribe)
    endpoint = next(r.endpoint for r in audio.router.routes if r.path == '/transcriptions')
    app = FastAPI()
    app.add_api_route('/transcriptions', endpoint, methods=['POST'], response_model=None)
    admin = SimpleNamespace(id='admin', role='admin', email='admin@example.com', name='Admin')
    app.dependency_overrides[get_verified_user] = lambda: admin
    return TestClient(app, raise_server_exceptions=False)


def _upload(client):
    return client.post('/transcriptions', files={'file': ('rec.webm', b'\x1a\x45\xdf\xa3audio', 'audio/webm')})


def _derived_copies(file_path):
    """Write what the pipeline leaves next to an upload: converted, compressed, chunk and json files."""
    base = file_path.rsplit('.', 1)[0]
    for suffix in ('.mp3', '_compressed.mp3', '_compressed_chunk_0.mp3', '.json'):
        with open(base + suffix, 'w') as f:
            f.write('x')


def test_transcription_is_refused_for_admins_when_voice_is_off(monkeypatch, tmp_path):
    """The dictation gate binds admins and stores nothing."""
    client = _transcription_client(monkeypatch, tmp_path, voice=False)
    response = _upload(client)
    assert response.status_code == 403, response.text
    assert not (tmp_path / 'audio' / 'transcriptions').exists()


def test_transcription_discards_upload_and_copies_after_success(monkeypatch, tmp_path):
    """Dictation audio is gone as soon as the text is returned."""

    async def transcribe(request, file_path, metadata=None, user=None):
        _derived_copies(file_path)
        return {'text': 'hallo'}

    client = _transcription_client(monkeypatch, tmp_path, transcribe=transcribe)
    response = _upload(client)
    assert response.status_code == 200, response.text
    assert response.json()['text'] == 'hallo'
    assert list((tmp_path / 'audio' / 'transcriptions').iterdir()) == []


def test_transcription_discards_upload_and_copies_after_failure(monkeypatch, tmp_path):
    """A failed dictation is not retried, so its audio is discarded too."""

    async def transcribe(request, file_path, metadata=None, user=None):
        _derived_copies(file_path)
        raise RuntimeError('stt down')

    client = _transcription_client(monkeypatch, tmp_path, transcribe=transcribe)
    response = _upload(client)
    assert response.status_code == 400, response.text
    assert list((tmp_path / 'audio' / 'transcriptions').iterdir()) == []


def test_transcription_discard_leaves_other_uploads(monkeypatch, tmp_path):
    """Only the request's own files are removed."""
    other = tmp_path / 'audio' / 'transcriptions' / 'other-upload.webm'
    other.parent.mkdir(parents=True)
    other.write_bytes(b'x')

    async def transcribe(request, file_path, metadata=None, user=None):
        return {'text': 'hallo'}

    client = _transcription_client(monkeypatch, tmp_path, transcribe=transcribe)
    assert _upload(client).status_code == 200
    assert list(other.parent.iterdir()) == [other]
