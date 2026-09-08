"""Keep the sealed CI request surface aligned with the committed tenant snapshot."""

from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]
# The snapshot omits these env names; the chart configmap supplies the mapping.
ENV_MAPPING = {
    'enableOnedriveIntegration': ('ENABLE_ONEDRIVE_INTEGRATION',),
    'enableGoogleDriveIntegration/Sync': ('ENABLE_GOOGLE_DRIVE_INTEGRATION', 'ENABLE_GOOGLE_DRIVE_SYNC'),
    'enableEmailInvites': ('ENABLE_EMAIL_INVITES',),
}
# Exact replacement values prevent a waiver from admitting arbitrary drift.
DIVERGENCES = {
    'AGENT_API_BASE_URL': ('http://stub:8000', 'Production agents-api hostname replaced by the sealed stub.'),
    'RAG_OPENAI_API_BASE_URL': ('http://stub:8000/v1', 'Production LiteLLM hostname replaced by the sealed stub.'),
    'RAG_EXTERNAL_RERANKER_URL': (
        'http://stub:8000/v1/rerank',
        'Production reranker hostname replaced by the sealed stub.',
    ),
    'OPENAI_API_BASE_URL': ('http://stub:8000/v1', 'Production LiteLLM hostname replaced by the sealed stub.'),
    'STORAGE_PROVIDER': (
        'local',
        'The existing sealed stack has no S3 service; local disposable storage preserves file handling '
        'but does not test S3 signing, multipart transfers or presigning.',
    ),
    'ENABLE_OTEL': (
        '',
        'Telemetry is disabled: the sealed stack has no Alloy OTLP collector '
        'and application request coverage does not require exports.',
    ),
}


def deployed_values():
    rows = {}
    for line in (REPO / 'security/deployed-config.md').read_text().splitlines():
        if not line.startswith('|'):
            continue
        cells = [cell.strip().strip('`') for cell in line.strip('|').split('|')]
        if len(cells) != 5 or cells[0] == 'Helm value' or cells[0].startswith('---'):
            continue
        helm, env, _, value, _ = cells
        names = ENV_MAPPING[helm] if env == '—' else (env,)
        value = value.split(' (', 1)[0].strip('"')
        for name in names:
            assert name not in rows, f'Duplicate snapshot flag: {name}'
            rows[name] = value
    assert len(rows) >= 29, 'Snapshot table missing or truncated'
    return rows


def config_mismatches(deployed, env, divergences=DIVERGENCES):
    errors = []
    for name, expected in deployed.items():
        actual = env.get(name)
        if name in divergences:
            replacement, reason = divergences[name]
            assert reason.strip(), name
            # Only telemetry may waive a boolean: never disable a tenant feature.
            assert expected.lower() not in {'true', 'false'} or name == 'ENABLE_OTEL', name
            expected_ci = replacement
        else:
            expected_ci = expected
        matches = actual == expected_ci
        if isinstance(actual, str) and expected_ci.lower() in {'true', 'false'}:
            matches = actual.lower() == expected_ci.lower()
        if not isinstance(actual, str) or not matches:
            errors.append(f'{name}: deployed={expected!r}, CI={actual!r}, required CI={expected_ci!r}')
    return errors


class TestCiMatchesDeployedConfig:
    def test_every_deployed_flag_matches_or_has_a_reasoned_divergence(self):
        compose = yaml.safe_load((REPO / 'docker-compose.ci.yaml').read_text())
        deployed = deployed_values()
        assert DIVERGENCES.keys() <= deployed.keys(), 'Stale divergence'
        errors = config_mismatches(deployed, compose['services']['open-webui']['environment'])
        assert not errors, '\n' + '\n'.join(errors)

    @pytest.mark.parametrize('bad_value', [None, '', 'false'])
    def test_enabled_features_cannot_disappear(self, bad_value):
        for name, value in deployed_values().items():
            if value.lower() == 'true' and name != 'ENABLE_OTEL':
                assert config_mismatches({name: value}, {name: bad_value})
                assert config_mismatches({name: value}, {})
                with pytest.raises(AssertionError):
                    config_mismatches({name: value}, {name: 'false'}, {name: ('false', 'Invalid feature waiver')})

    def test_all_configured_urls_stay_on_the_internal_network(self):
        compose = yaml.safe_load((REPO / 'docker-compose.ci.yaml').read_text())
        for name, value in compose['services']['open-webui']['environment'].items():
            if '://' not in value:
                continue
            for url in value.split(';'):
                hostname = urlsplit(url).hostname
                assert hostname in compose['services'], (name, url)
                assert compose['services'][hostname]['networks'] == ['internal'], (name, url)

    def test_snapshot_is_available_inside_the_app_container(self):
        compose = yaml.safe_load((REPO / 'docker-compose.ci.yaml').read_text())
        assert './security:/app/security:ro' in compose['services']['open-webui']['volumes']

    def test_non_boolean_values_are_compared_exactly(self):
        assert config_mismatches(
            {'AGENT_API_PICKER_DEFAULT_SLUG': 'soev_chat_manual'},
            {'AGENT_API_PICKER_DEFAULT_SLUG': 'SOEV_CHAT_MANUAL'},
        )
