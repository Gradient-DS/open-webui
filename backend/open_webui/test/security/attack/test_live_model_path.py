"""Live sealed-stack checks; run only by runtime-security.yml."""

import pytest
from . import model_path
from .pass_support import crash_details, fresh_surface


@pytest.fixture(scope='module')
def live_model_paths():
    with fresh_surface() as (identities, parameters):
        yield model_path.drive_model_paths(identities.admin, parameters)


def test_live_model_path_has_its_own_5xx_assertion(live_model_paths):
    assert not live_model_paths.tally.crashes, crash_details(live_model_paths.tally)


def test_live_model_streams_finish_without_body_failures(live_model_paths):
    assert not live_model_paths.tally.body_failures, live_model_paths.tally.body_failures


def test_live_model_controls_produce_output(live_model_paths):
    controls = [item for item in live_model_paths.responses if item['probe'] == 'control']
    assert {item['route'] for item in controls} == set(model_path.targets())

    # The control exists to prove this pass reached the model path at all. If it is
    # wrong or weak every other assertion in the module passes vacuously, so report
    # per probe what was required and what arrived rather than dumping the records.
    def _why(item):
        if item['status'] is None:
            return 'no response (request never completed)'
        if not 200 <= item['status'] < 300:
            return f'status {item["status"]}, wanted 2xx'
        if item['errors']:
            return f'stream errors: {item["errors"]}'
        if not (item['terminal'] or item.get('model_output')):
            return (
                f'neither a terminal event nor model output; read {item.get("bytes")} bytes. '
                'The stub answers SSE for OpenAI-shaped streaming and NDJSON for Ollama, '
                'so a body with no terminal event is a truncated stream, not a success.'
            )
        return None

    broken = [(item['route'], _why(item)) for item in controls]
    broken = [(route, reason) for route, reason in broken if reason]
    assert not broken, 'model-path controls produced no usable output:\n' + '\n'.join(
        f'  {route}: {reason}' for route, reason in broken
    )


def test_live_model_path_reports_its_own_reach(live_model_paths, record_property):
    tally = live_model_paths.tally
    assert tally.expected == set(model_path.targets()) == tally.statuses.keys()
    assert tally.config_restore_verified
    record_property('model_path_unentered', tally.unentered)
    record_property('model_path_responses', live_model_paths.responses)
    assert tally.entered, 'No model-path request entered a handler'
