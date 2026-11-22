"""All platform client builders share the explicitly configured public host."""

from pathlib import Path

from open_webui import config
from open_webui.soev import identity, threads
from open_webui.soev.client import build_service_client

ROOT = Path(__file__).resolve().parents[4]


def test_client_builders_use_the_public_host(monkeypatch):
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://api.tenant.example.invalid')
    monkeypatch.setattr(config, 'SOEV_API_KEY', 'soev_test_cred-public_secret')
    assert identity.build_client()._base_url == config.SOEV_API_URL
    assert build_service_client()._base_url == config.SOEV_API_URL
    assert threads.build_client().base_url == config.SOEV_API_URL


def test_configuration_has_no_cluster_default():
    assert "SOEV_API_URL = os.environ.get('SOEV_API_URL', '')" in (ROOT / 'backend/open_webui/config.py').read_text()
    assert 'SOEV_API_URL=https://api.<tenant>.soev.ai' in (ROOT / '.env.example').read_text()
    assert 'SOEV_API_URL=${SOEV_API_URL:-}' in (ROOT / 'docker-compose.yaml').read_text()
    for relative in [
        'helm/open-webui-tenant/values.yaml',
        'helm/open-webui-tenant/ci/v2-values.yaml',
        'helm/open-webui-tenant/ci/strict-egress-values.yaml',
    ]:
        assert 'http://soev-api:' not in (ROOT / relative).read_text()
