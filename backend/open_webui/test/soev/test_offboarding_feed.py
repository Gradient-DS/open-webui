"""The offboarding feed never reaches destructive legacy handlers."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui.soev import offboarding
from open_webui.test.soev.fake_api import person_ref


def task(mode='delete'):
    return {
        'id': 'fictional-departure',
        'person': person_ref('cobalt'),
        'mode': mode,
        'target': person_ref('quartz') if mode == 'transfer' else None,
        'expires_at': '2027-11-10T00:00:00Z' if mode == 'archive' else None,
    }


@pytest.fixture
def feed(monkeypatch):
    remote = SimpleNamespace(get=AsyncMock(return_value=[task()]), send=AsyncMock())
    participant = AsyncMock(return_value=None)
    monkeypatch.setattr(offboarding, 'apply', participant)
    monkeypatch.setattr(offboarding, 'build_service_client', lambda: remote)
    monkeypatch.setattr(offboarding, 'v2_enabled', lambda: True)
    return remote, participant


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['delete', 'transfer', 'archive'])
async def test_each_mode_acknowledges_only_participant_result(feed, mode, monkeypatch):
    from open_webui.services.deletion import offboarding as legacy

    legacy_call = AsyncMock()
    monkeypatch.setattr(legacy, 'offboard_user', legacy_call)
    remote, participant = feed
    remote.get.return_value = [task(mode)]
    await offboarding.poll()
    await offboarding.poll()
    assert remote.send.await_count == 2
    assert all(call.args[2] == {'error': None} for call in remote.send.await_args_list)
    assert remote.send.await_args_list[0] == remote.send.await_args_list[1]
    assert all(
        call.args[0].person == person_ref('cobalt') and call.args[0].mode == mode
        for call in participant.await_args_list
    )
    legacy_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_transfer_target_without_owui_user_is_reported(feed):
    remote, participant = feed
    remote.get.return_value = [task('transfer')]
    participant.return_value = 'target_missing'
    await offboarding.poll()
    assert remote.send.await_args.args[2] == {'error': 'target_missing'}
    assert participant.await_count == 1


@pytest.mark.asyncio
async def test_absent_source_is_not_silently_acknowledged(feed):
    remote, participant = feed
    participant.return_value = 'source_conflict'
    await offboarding.poll()
    assert remote.send.await_args.args[2] == {'error': 'source_conflict'}


@pytest.mark.asyncio
async def test_stale_or_duplicate_work_never_reaches_legacy_deletion(feed):
    remote, participant = feed
    remote.get.return_value = []
    await offboarding.poll()
    remote.send.assert_not_awaited()
    participant.assert_not_awaited()
    remote.get.return_value = [task(), task()]
    with pytest.raises(ValueError, match='Duplicate offboarding jobs'):
        await offboarding.poll()
    remote.send.assert_not_awaited()
    participant.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_is_cancelled_before_its_http_pool_closes(monkeypatch):
    async def waiting():
        await asyncio.Event().wait()

    monkeypatch.setattr(offboarding, 'worker', waiting)
    monkeypatch.setattr(offboarding, 'v2_enabled', lambda: True)
    active = offboarding.start_worker()
    assert active is not None
    await asyncio.sleep(0)
    assert not active.done()
    await offboarding.stop_worker(active)
    assert active.cancelled()
    monkeypatch.setattr(offboarding, 'v2_enabled', lambda: False)
    assert offboarding.start_worker() is None
    await offboarding.stop_worker(None)
