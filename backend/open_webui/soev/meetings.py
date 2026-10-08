"""[Gradient] Vergadering as chat context: read a meeting from soev-api as the user at send time.

The meeting stays in soev-api; nothing is written to OWUI storage, files or the vector store.
"""

import datetime as dt
import logging
from urllib.parse import quote
from zoneinfo import ZoneInfo

from open_webui.soev import identity
from open_webui.soev.client import SoevApiError, SoevClient
from open_webui.utils.features import is_feature_enabled

log = logging.getLogger(__name__)

DEFAULT_TIME_ZONE = 'Europe/Amsterdam'
# The v2 agent's attached texts know notes and chats; a meeting travels as a note with this id prefix.
MEETING_TEXT_PREFIX = 'meeting-'
OUTPUT_TITLES = {'summary': 'Samenvatting', 'minutes': 'Notulen', 'actions': 'Actiepunten'}


class MeetingUnavailable(Exception):
    """The meeting is gone, not the user's, or has no snapshot yet."""


def thread_path(meeting_id: str, suffix: str = '') -> str:
    """The meeting's soev-api thread path; a dot segment would be resolved away by the HTTP client."""
    if meeting_id in {'', '.', '..'}:
        raise MeetingUnavailable('Invalid meeting id')
    return f'/v1/chat/threads/{quote(meeting_id, safe="")}{suffix}'


def latest_state(events: list[dict]) -> dict | None:
    """The payload of the thread's last `meeting_state` event; each one is a full snapshot."""
    for event in reversed(events):
        if event.get('type') == 'meeting_state' and isinstance(event.get('payload'), dict):
            return event['payload']
    return None


def _clock(seconds: float | None) -> str:
    total = max(0, int(seconds or 0))
    hours, rest = divmod(total, 3600)
    return f'{hours}:{rest // 60:02d}:{rest % 60:02d}' if hours else f'{rest // 60:02d}:{rest % 60:02d}'


def _local(value: str | None, zone: ZoneInfo) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(zone) if value else None
    except ValueError:
        return None


def _zone(state: dict) -> ZoneInfo:
    try:
        return ZoneInfo(state.get('time_zone') or DEFAULT_TIME_ZONE)
    except (KeyError, ValueError):
        return ZoneInfo(DEFAULT_TIME_ZONE)


def _header(state: dict, title: str, names: dict) -> list[str]:
    zone = _zone(state)
    started = _local(state.get('started_at') or (state.get('consent') or {}).get('at'), zone)
    ended = _local(state.get('ended_at'), zone)
    lines = [f'# {title}', '']
    if started:
        lines.append(f'Datum: {started:%d-%m-%Y}')
        lines.append(f'Tijd: {started:%H:%M}' + (f'–{ended:%H:%M}' if ended else ''))
    if state.get('duration_s') is not None:
        lines.append(f'Duur: {_clock(state["duration_s"])}')
    if names:
        lines.append(f'Sprekers: {", ".join(names.values())}')
    return lines


def _transcript(state: dict, names: dict) -> list[str]:
    turns: list[tuple[str, float, list[str]]] = []
    for segment in (state.get('transcript') or {}).get('segments') or []:
        text = (segment.get('clean') or segment.get('raw') or '').strip()
        speaker = segment.get('speaker') or ''
        if turns and turns[-1][0] == speaker:
            turns[-1][2].append(text)
        else:
            turns.append((speaker, segment.get('start') or 0, [text]))
    if turns:
        return ['', '## Transcript', ''] + [
            f'[{_clock(start)}] {names.get(speaker, speaker)}: {" ".join(texts)}' for speaker, start, texts in turns
        ]
    live = sorted(state.get('live') or [], key=lambda part: part.get('seq', 0))
    if live:
        return ['', '## Transcript (ruw, live)', '', *[part.get('text', '').strip() for part in live]]
    return []


def _action(item: dict) -> str:
    extra = ', '.join(
        part
        for part in (
            f'eigenaar: {item["owner"]}' if item.get('owner') else '',
            f'deadline: {item["due"]}' if item.get('due') else '',
        )
        if part
    )
    return f'- {item.get("task", "")}' + (f' ({extra})' if extra else '')


def _outputs(state: dict) -> list[str]:
    lines: list[str] = []
    for kind, heading in OUTPUT_TITLES.items():
        output = (state.get('outputs') or {}).get(kind)
        if not output:
            continue
        lines += ['', f'## {heading}', '']
        if kind == 'actions':
            lines += [_action(item) for item in output.get('items') or []]
        else:
            lines.append((output.get('markdown') or '').strip())
    return lines


def render_meeting(state: dict) -> tuple[str, str]:
    """Title and Markdown: metadata, the clean transcript as speaker turns, and any outputs."""
    started = _local(state.get('started_at') or (state.get('consent') or {}).get('at'), _zone(state))
    title = state.get('title') or (f'Vergadering van {started:%d-%m-%Y}' if started else 'Vergadering')
    speakers = (state.get('transcript') or {}).get('speakers') or []
    names = {s.get('label'): s.get('name') or s.get('label') for s in speakers}
    lines = _header(state, title, names) + _transcript(state, names) + _outputs(state)
    return title, '\n'.join(lines).strip() + '\n'


async def meeting_document(user, meeting_id: str, client: SoevClient | None = None) -> tuple[str, str]:
    """Read the meeting as `user`; soev-api only returns the caller's own threads."""
    if user is None or not meeting_id:
        raise MeetingUnavailable('No user or meeting')
    if not is_feature_enabled('meetings'):
        raise MeetingUnavailable('Meetings are not available')
    path = thread_path(meeting_id)
    client = client or identity.build_client()
    try:
        thread = await client.get(path, as_user=await identity.acting_ref(user, client))
    except SoevApiError as error:
        raise MeetingUnavailable(error.detail) from None
    state = latest_state(thread.get('events') or [])
    if state is None:
        raise MeetingUnavailable('The meeting has no content yet')
    return render_meeting(state)


async def meeting_source(item: dict, user, client: SoevClient | None = None) -> dict:
    """A retrieval result for an attached meeting; an unavailable one says so to the model."""
    meeting_id = item.get('id') or ''
    try:
        title, text = await meeting_document(user, meeting_id, client)
    except MeetingUnavailable as error:
        log.warning('Attached meeting unavailable', extra={'meeting_id': meeting_id, 'reason': str(error)})
        title = item.get('name') or 'Vergadering'
        text = f'De bijgevoegde vergadering "{title}" is niet beschikbaar (verwijderd of niet van deze gebruiker).'
    return {'documents': [[text]], 'metadatas': [[{'file_id': meeting_id, 'name': title}]]}
