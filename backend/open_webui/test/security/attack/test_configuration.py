"""Read-only configuration guards retain positive and lost-evidence controls."""

from unittest.mock import Mock

import pytest
import requests

from . import configuration


def test_a_product_write_cannot_change_tenant_configuration(monkeypatch):
    monkeypatch.setattr(configuration, 'snapshot', Mock(side_effect=[{'feature': False}, {'feature': True}]))
    findings = []
    with pytest.raises(RuntimeError, match='read-only tenant configuration'):
        with configuration.preserve_configuration(
            None, report=findings.append, route='POST /fictional', unverified=pytest.fail
        ):
            pass
    assert findings == [{'finding': 'configuration_changed', 'route': 'POST /fictional'}]


def test_unchanged_tenant_configuration_has_no_findings(monkeypatch):
    monkeypatch.setattr(configuration, 'snapshot', Mock(return_value={'feature': False}))
    with configuration.preserve_configuration(
        None, report=pytest.fail, route='POST /fictional', unverified=pytest.fail
    ):
        pass


@pytest.mark.parametrize('when', ['before', 'after'])
def test_missing_snapshot_never_vouches_for_the_configuration(monkeypatch, when):
    values = (
        [requests.Timeout('fictional timeout')] if when == 'before' else [{}, requests.Timeout('fictional timeout')]
    )
    monkeypatch.setattr(configuration, 'snapshot', Mock(side_effect=values))
    missing = []
    with configuration.preserve_configuration(
        None, report=pytest.fail, route='POST /fictional', unverified=missing.append
    ):
        pass
    assert len(missing) == 1 and when in missing[0]
