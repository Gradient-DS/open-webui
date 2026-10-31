"""Attach OWUI presentation data without changing the platform's message cursors."""

from open_webui.soev import model_catalog
from open_webui.soev.threads import ProjectionPage
from open_webui.utils.agent_v2 import AgentTurn


def _objects(value) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def needs_sources(page: ProjectionPage) -> bool:
    for message in page.messages:
        content = message.content
        if not isinstance(content, dict) or content.get('role') != 'assistant':
            continue
        cited = {
            citation['written']
            for marker in _objects(content.get('markers'))
            for citation in _objects(marker.get('cited'))
            if isinstance(citation.get('source'), str) and isinstance(citation.get('written'), int)
        }
        supplied = {source['n'] for source in _objects(content.get('sources')) if isinstance(source.get('n'), int)}
        if cited - supplied:
            return True
    return False


def needs_events(page: ProjectionPage) -> bool:
    if needs_sources(page):
        return True
    for message in page.messages:
        content = message.content
        if not isinstance(content, dict) or content.get('role') != 'assistant':
            continue
        calls = {
            call['id']
            for call in _objects(content.get('tool_calls'))
            if isinstance(call.get('id'), str) and isinstance(call.get('name'), str)
        }
        statuses = {
            status['call_id']
            for status in _objects(content.get('statusHistory'))
            if isinstance(status.get('call_id'), str) and status.get('done')
        }
        if calls - statuses:
            return True
    return False


async def present_projection(page: ProjectionPage, events: list[dict]) -> ProjectionPage:
    turn = AgentTurn(None, {}, '')
    boundary = max((message.position for message in page.messages), default=0)
    events = [event for event in events if event.get('stream') == 'root' and event['position'] <= boundary]
    await turn._seed_sources(events, boundary)
    requested, models = None, {}
    for event in sorted(events, key=lambda event: event['position']):
        payload = event.get('payload') or {}
        if event['type'] == 'input':
            requested = payload.get('model')
        if event['type'] == 'model_output' and isinstance(payload.get('model'), str):
            models[event['position']] = model_catalog.catalog_id(
                payload.get('answered_model') or payload['model'],
                requested=None if payload.get('answered_model') else requested,
            )
    outputs = {
        event['payload']['call_id']: event['payload']
        for event in events
        if event['type'] == 'tool_output' and event['payload'].get('call_id')
    }
    calls = {
        call['id']: call
        for event in events
        if event['type'] == 'model_output'
        for call in _objects(event['payload'].get('tool_calls'))
        if isinstance(call.get('id'), str)
    }
    messages = []
    for message in page.messages:
        content = message.content
        if not isinstance(content, dict) or content.get('role') != 'assistant':
            messages.append(message)
            continue
        content = dict(content)
        if isinstance(content.get('model'), str):
            content['model'] = models.get(message.position) or model_catalog.catalog_id(content['model'])
        if sources := _sources(content, turn):
            content['sources'] = sources
        if statuses := _statuses(content, turn, calls, outputs, message.finished):
            content['statusHistory'] = statuses
        messages.append(message.model_copy(update={'content': content}))
    return page.model_copy(update={'messages': messages})


def _sources(content: dict, turn: AgentTurn) -> list[dict]:
    sources = _objects(content.get('sources'))
    for marker in _objects(content.get('markers')):
        for citation in _objects(marker.get('cited')):
            if not isinstance(citation.get('source'), str):
                continue
            source = turn.citations.sources.get(citation.get('source'))
            if source is None:
                source = next(
                    (s for s in turn.citations.sources.values() if s['source']['id'] == citation.get('source')),
                    None,
                )
            if source is not None and isinstance(citation.get('written'), int):
                numbered = {**source, 'n': citation['written']}
                if numbered not in sources:
                    sources.append(numbered)
    return sources


def _statuses(content: dict, turn: AgentTurn, calls: dict, outputs: dict, finished: bool) -> list[dict]:
    statuses = _objects(content.get('statusHistory'))
    for call in _objects(content.get('tool_calls')):
        if not isinstance(call.get('id'), str) or not isinstance(call.get('name'), str):
            continue
        call = calls.get(call['id'], call)
        output = outputs.get(call['id'], {} if call['id'] not in calls and finished else None)
        status = turn.tool_status(call['name'], call.get('arguments') or {}, output)
        if not any(existing.get('call_id') == call['id'] for existing in statuses):
            statuses.append({**status, 'call_id': call['id'], 'done': output is not None})
    return statuses
