"""Retired provider surfaces cannot restore direct model credentials or routing."""

from open_webui import config
from open_webui.models.users import UserSettings


def test_retired_inference_routes_are_absent():
    from open_webui.main import app

    paths = {route.path for route in app.routes if hasattr(route, 'path')}
    assert not any(path.startswith(('/openai', '/api/v1/pipelines')) for path in paths)
    assert not paths & {
        '/api/message',
        '/api/v1/messages',
        '/api/message/count_tokens',
        '/api/v1/messages/count_tokens',
        '/api/v1/retrieval/embedding',
        '/api/v1/retrieval/embedding/update',
        '/api/v1/retrieval/query/doc',
        '/api/v1/retrieval/query/collection',
    }
    assert '/api/chat/completions' in paths
    assert '/api/v1/images/generations' in paths
    assert '/api/v1/audio/speech' in paths
    assert '/api/v1/functions/' in paths


def test_retired_provider_configuration_has_no_runtime_defaults():
    assert not any(key.startswith('openai.') for key in config.DEFAULT_CONFIG)
    assert 'direct.enable' not in config.DEFAULT_CONFIG
    assert not any(
        key.startswith(('rag.embedding', 'rag.openai.', 'rag.azure_openai.', 'rag.ollama.', 'rag.external_reranker'))
        for key in config.DEFAULT_CONFIG
    )
    assert 'image_generation.openai.api_key' in config.DEFAULT_CONFIG
    assert 'audio.tts.openai.api_key' in config.DEFAULT_CONFIG


def test_saved_direct_credentials_are_discarded_when_read_or_written():
    ui = {
        'theme': 'dark',
        'directConnections': {'OPENAI_API_KEYS': ['fictional-retired-key']},
        'toolServers': [{'url': 'https://tools.example.invalid'}],
    }
    settings = UserSettings.model_validate({'ui': ui})
    assert settings.model_dump()['ui'] == {
        'theme': 'dark',
        'toolServers': [{'url': 'https://tools.example.invalid'}],
    }
    assert 'directConnections' in ui


def test_image_and_audio_defaults_do_not_adopt_a_gateway_key(monkeypatch):
    monkeypatch.setenv('OPENAI_API_BASE_URLS', 'https://gateway.example.invalid/v1;https://api.openai.com/v1')
    monkeypatch.setenv('OPENAI_API_KEYS', 'fictional-gateway-key;fictional-image-audio-key')
    assert config._legacy_provider_defaults() == ('https://api.openai.com/v1', 'fictional-image-audio-key')
    monkeypatch.setenv('OPENAI_API_BASE_URLS', 'https://gateway.example.invalid/v1')
    assert config._legacy_provider_defaults() == ('https://api.openai.com/v1', '')
