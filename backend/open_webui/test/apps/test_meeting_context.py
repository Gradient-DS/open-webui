from types import SimpleNamespace

import pytest
from open_webui.soev import meetings
from open_webui.soev.client import SoevApiError

USER = SimpleNamespace(id='u1', role='user', email='u@example.com', name='U')

STATE = {
    'status': 'ready',
    'title': 'Weekoverleg',
    'started_at': '2026-10-07T08:00:00Z',
    'ended_at': '2026-10-07T08:47:12Z',
    'duration_s': 2832,
    'transcript': {
        'speakers': [{'label': 'Spreker 1', 'name': 'Xander'}, {'label': 'Spreker 2', 'name': None}],
        'segments': [
            {'start': 0, 'end': 2, 'speaker': 'Spreker 1', 'raw': 'heedemorgen', 'clean': 'Goedemorgen.'},
            {'start': 2, 'end': 4, 'speaker': 'Spreker 1', 'raw': 'we beginnen', 'clean': 'We beginnen.'},
            {'start': 65, 'end': 70, 'speaker': 'Spreker 2', 'raw': 'dank je', 'clean': 'Dank je.'},
        ],
    },
    'outputs': {
        'summary': {'markdown': 'Kort overleg.'},
        'minutes': None,
        'actions': {'items': [{'task': 'Offerte sturen', 'owner': 'Xander', 'due': 'vrijdag'}]},
    },
}


class FakeClient:
    def __init__(self, thread=None, fail=None):
        self.thread, self.fail, self.calls = thread, fail, []

    async def get(self, path, *, as_user=None, params=None):
        self.calls.append((path, as_user))
        if self.fail:
            raise self.fail
        return self.thread


@pytest.fixture(autouse=True)
def acting(monkeypatch):
    async def acting_ref(user, client):
        return f'owui:user:{user.id}'

    monkeypatch.setattr(meetings.identity, 'acting_ref', acting_ref)


def test_render_has_metadata_clean_turns_and_outputs():
    """The model gets title, local times, speakers, clean speaker turns and the outputs."""
    title, text = meetings.render_meeting(STATE)
    assert title == 'Weekoverleg'
    assert 'Datum: 07-10-2026' in text
    assert 'Tijd: 10:00–10:47' in text
    assert 'Duur: 47:12' in text
    assert 'Sprekers: Xander, Spreker 2' in text
    assert '[00:00] Xander: Goedemorgen. We beginnen.' in text
    assert '[01:05] Spreker 2: Dank je.' in text
    assert 'heedemorgen' not in text
    assert '## Samenvatting\n\nKort overleg.' in text
    assert '- Offerte sturen (eigenaar: Xander, deadline: vrijdag)' in text
    assert '## Notulen' not in text


def test_render_falls_back_to_consent_time_and_live_text():
    """Older or unfinished meetings still render: consent time, rough live parts."""
    state = {
        'status': 'recording',
        'consent': {'at': '2026-10-07T12:00:00Z'},
        'live': [{'seq': 2, 'text': 'b'}, {'seq': 1, 'text': 'a'}],
    }
    title, text = meetings.render_meeting(state)
    assert title == 'Vergadering van 07-10-2026'
    assert 'Tijd: 14:00' in text
    assert text.endswith('a\nb\n')


@pytest.mark.asyncio
async def test_source_reads_the_meeting_as_the_user():
    """Resolution goes to soev-api as the acting user, with the latest snapshot."""
    events = [
        {'type': 'meeting_state', 'payload': {'status': 'recording'}},
        {'type': 'meeting_state', 'payload': STATE},
    ]
    client = FakeClient(thread={'events': events})
    source = await meetings.meeting_source({'type': 'meeting', 'id': 'thr-1', 'name': 'x'}, USER, client)
    assert client.calls == [('/v1/chat/threads/thr-1', 'owui:user:u1')]
    assert source['metadatas'] == [[{'file_id': 'thr-1', 'name': 'Weekoverleg'}]]
    assert 'Xander: Goedemorgen.' in source['documents'][0][0]


@pytest.mark.asyncio
async def test_someone_elses_or_deleted_meeting_is_reported_not_leaked():
    """soev-api refuses threads the user does not own; the model is told it is unavailable."""
    client = FakeClient(fail=SoevApiError(404, 'not_found', 'No such thread'))
    with pytest.raises(meetings.MeetingUnavailable):
        await meetings.meeting_document(USER, 'thr-x', client)
    source = await meetings.meeting_source({'type': 'meeting', 'id': 'thr-x', 'name': 'Overleg'}, USER, client)
    assert source['documents'] == [
        ['De bijgevoegde vergadering "Overleg" is niet beschikbaar (verwijderd of niet van deze gebruiker).']
    ]


@pytest.mark.asyncio
async def test_meeting_without_snapshot_or_user_is_unavailable():
    with pytest.raises(meetings.MeetingUnavailable):
        await meetings.meeting_document(USER, 'thr-1', FakeClient(thread={'events': []}))
    with pytest.raises(meetings.MeetingUnavailable):
        await meetings.meeting_document(None, 'thr-1', FakeClient(thread={'events': []}))


@pytest.mark.asyncio
async def test_agent_path_sends_a_meeting_as_a_note_text(monkeypatch):
    """The v2 agent knows notes and chats; a meeting goes as a note text with a meeting- id."""
    from open_webui.utils import agent_v2

    async def get_user(user_id):
        return USER

    async def document(user, meeting_id, client=None):
        if meeting_id == 'thr-gone':
            raise meetings.MeetingUnavailable('gone')
        return 'Weekoverleg', '# Weekoverleg\n'

    monkeypatch.setattr(agent_v2.Users, 'get_user_by_id', get_user)
    monkeypatch.setattr(agent_v2, 'meeting_document', document)
    texts = await agent_v2._texts([{'type': 'meeting', 'id': 'thr-1', 'name': 'x'}], 'u1')
    assert texts == [
        {'id': 'note:meeting-thr-1', 'kind': 'note', 'title': 'Weekoverleg', 'text': '# Weekoverleg\n', 'length': 14}
    ]
    with pytest.raises(agent_v2.AttachmentsUnavailable):
        await agent_v2._texts([{'type': 'meeting', 'id': 'thr-gone', 'name': 'Overleg'}], 'u1')
