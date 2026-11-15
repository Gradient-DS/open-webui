"""Resolve route-scoped fixtures over HTTP; never import the application.

Call once per pass, serially, using ensure_identities() from identities.py.
All fixtures use the ordinary caller and its platform permissions.
The result is keyed by (OpenAPI path template, parameter), never just name.
Unseedable entries remain in SURFACE and are deliberately absent from results.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote

import requests

from . import oidc
from .client import AttackClient, AuthenticationError, json_body, totp_code
from .diagnostics import timed
from .identities import create_oidc_identity

ROOT = Path(__file__).resolve().parents[5]
SURFACE_PATH = ROOT / 'security/attack-surface.toml'
SURFACE = tomllib.loads(SURFACE_PATH.read_text())
SPEC = json.loads((ROOT / 'security/openapi.json').read_text())
METHODS = {'get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'trace'}
DESTRUCTIVE = {entry['id']: entry['reason'] for entry in SURFACE['destructive']}
#: Routes that only work with soev-solutions, which never runs in this repo's CI.
#: Public CI waives them; the private full-stack run can drive them.
SOEV_BACKED = {
    entry['id']: entry['reason']
    for entry in tomllib.loads((ROOT / 'security/route-coverage.toml').read_text()).get('waived', [])
    if entry.get('backed_by') == 'soev-solutions'
}


def runtime_soev_backed():
    mode = os.environ.get('RUNTIME_SOEV_BACKED', 'waive')
    if mode not in {'waive', 'drive'}:
        raise ValueError(f'RUNTIME_SOEV_BACKED must be waive or drive, got {mode!r}')
    return mode


# Validate even when a runner only collects tests or has no private routes.
runtime_soev_backed()


def backed_seed_waived(entry):
    return runtime_soev_backed() == 'waive' and entry.get('backed_by') == 'soev-solutions'


def route_waived(route):
    return runtime_soev_backed() == 'waive' and route in SOEV_BACKED


def seed_enabled(entry):
    refusal_probe = runtime_soev_backed() == 'drive' and 'v2_refused_value' in entry
    return ('unseedable' not in entry or refusal_probe) and not backed_seed_waived(entry)


def selected_parameters(surface=SURFACE):
    selected = [entry for entry in dependency_order(surface) if seed_enabled(entry)]
    keys = {entry['key'] for entry in selected}
    for entry in selected:
        missing = set(_dependencies(entry)) - keys
        if missing:
            raise ValueError(f'{entry["key"]}: dependencies disabled in this mode: {sorted(missing)}')
    return selected


def _extract(payload, dotted: str, *, name: str, via: str) -> str:
    node = payload
    try:
        for part in dotted.split('.'):
            if isinstance(node, list):
                if not part.isdecimal():
                    raise ValueError('list indices must be nonnegative integers')
                node = node[int(part)]
            elif isinstance(node, dict):
                node = node[part]
            else:
                raise TypeError('extract traversed a scalar')
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError(f'seeding {name} via {via}: extract path {dotted!r} could not be read') from exc
    if isinstance(node, bool) or not isinstance(node, (str, int)) or not str(node).strip():
        raise RuntimeError(f'seeding {name} via {via}: extract path {dotted!r} reached {node!r} instead of an id')
    return str(node)


#: Keys whose value is structure, not seed data. `prefix` is the longest-prefix
#: route matcher and `name` is the OpenAPI parameter name: substituting into them
#: rewrites `/api/v1/auths/password/reset/{token}` to a path no route has, so
#: parameter_for then matches nothing and raises instead of reporting the entry.
_STRUCTURAL_KEYS = frozenset({'prefix', 'name', 'key'})


def _fill_token(value, token):
    if isinstance(value, dict):
        return {
            _fill_token(k, token): (v if k in _STRUCTURAL_KEYS else _fill_token(v, token)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [_fill_token(v, token) for v in value]
    return value.replace('{token}', token) if isinstance(value, str) else value


def parameter_for(path, name, surface=SURFACE):
    matches = [
        p
        for p in surface['parameter']
        if p['name'] == name and (path == p['prefix'] or path.startswith(p['prefix'] + '/'))
    ]
    if not matches:
        raise KeyError(f'Unaccounted path parameter: {path}: {name}')
    longest = max(len(p['prefix']) for p in matches)
    winners = [p for p in matches if len(p['prefix']) == longest]
    if len(winners) != 1:
        raise ValueError(f'Ambiguous path parameter: {path}: {name}')
    return winners[0]


def is_destructive(method, path, surface=SURFACE):
    for entry in surface.get('destructive', []):
        verb, template = entry['id'].split(' ', 1)
        pattern = re.escape(template)
        pattern = re.sub(r'\\\{[^}]+\\\}', '.+', pattern)
        if method.upper() == verb and (path == template or re.fullmatch(pattern, path)):
            return True
    return False


def ordinary_targets(spec=SPEC, surface=SURFACE):
    return [
        f'{method.upper()} {path}'
        for path, item in spec['paths'].items()
        for method in item
        if method in METHODS
        and not is_destructive(method, path, surface)
        and not route_waived(f'{method.upper()} {path}')
        and all(seed_enabled(parameter_for(path, name, surface)) for name in re.findall(r'\{([^}]+)\}', path))
    ]


def dependency_order(surface):
    entries = [*surface['parameter'], *surface.get('fixture', [])]
    by_key = {p['key']: p for p in entries}
    if len(by_key) != len(entries):
        raise ValueError('Duplicate seed key')
    _validate_entries(entries, by_key)
    pending, ordered, done = list(entries), [], set()
    while pending:
        ready = [p for p in pending if set(_dependencies(p)) <= done]
        if not ready:
            raise ValueError(f'Cyclic seed dependencies: {[p["key"] for p in pending]}')
        for p in ready:
            ordered.append(p)
            done.add(p['key'])
            pending.remove(p)
    return ordered


def _validate_entries(entries, by_key):
    for p in entries:
        if len({'via', 'same_as', 'seeded_by', 'unseedable'} & p.keys()) != 1:
            raise ValueError(f'{p["key"]}: exactly one seed kind is required')
        if 'unseedable' in p and not p['unseedable'].strip():
            raise ValueError(f'{p["key"]}: unseedable requires a reason')
        if 'seeded_by' in p:
            if p['seeded_by'] not in _SEEDERS or not callable(_SEEDERS[p['seeded_by']]):
                raise ValueError(f'{p["key"]}: unknown seeder')
            if 'depends_on' not in p:
                raise ValueError(f'{p["key"]}: seeded_by requires depends_on (even when empty)')
        if not isinstance(p.get('depends_on', []), list):
            raise ValueError(f'{p["key"]}: depends_on must be a list')
        if 'provides' in p:
            provided = p['provides']
            if not {'seeded_by', 'v2_seeded_by'} & p.keys():
                raise ValueError(f'{p["key"]}: only a seeder can provide further values')
            if not isinstance(provided, list) or not all(
                isinstance(name, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) for name in provided
            ):
                raise ValueError(f'{p["key"]}: provides must be a list of identifiers')
            if set(provided) & by_key.keys():
                raise ValueError(f'{p["key"]}: provides a value named like a seed key')
        for key in _dependencies(p):
            if key not in by_key or 'unseedable' in by_key[key]:
                raise ValueError(f'{p["key"]}: unavailable dependency {key}')


def _dependencies(parameter):
    return parameter.get('depends_on', []) + ([parameter['same_as']] if 'same_as' in parameter else [])


@dataclass
class SeedContext:
    client: AttackClient
    admin: AttackClient
    surface: dict
    token: str = field(default_factory=lambda: secrets.token_hex(12))
    values: dict = field(default_factory=dict)
    payloads: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)

    def fill(self, value, parameter, *, path=False):
        value = _fill_token(value, self.token)
        if isinstance(value, dict):
            return {self.fill(k, parameter): self.fill(v, parameter) for k, v in value.items()}
        if isinstance(value, list):
            return [self.fill(v, parameter) for v in value]
        if not isinstance(value, str):
            return value
        for key in _dependencies(parameter):
            replacement = self.values[key]
            value = value.replace('{' + key + '}', quote(replacement, safe='') if path else replacement)
        return value

    def provide(self, parameter, name, value):
        """Publish a value a seeder made besides its own id, for [[field]] entries to reference."""
        if name not in parameter.get('provides', []):
            raise RuntimeError(f'{parameter["key"]}: does not declare that it provides {name}')
        self.values[name] = _extract({'value': value}, 'value', name=name, via=parameter['key'])

    def request(self, method, path, *, actor=None, **kwargs):
        if is_destructive(method, path, self.surface):
            raise RuntimeError(f'Destructive route cannot seed fixtures: {method} {path}')
        response = (actor or self.client).request(method, path, **kwargs)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f'Seeding {method} {path} failed (HTTP {response.status_code})')
        body = json_body(response)
        if body is None:
            raise RuntimeError(f'Seeding {method} {path} returned no JSON')
        self.wait_for_ingestion(method, path, body, kwargs.get('json'), actor=actor)
        return body

    def wait_for_ingestion(self, method, path, body, payload, *, actor=None):
        if method != 'POST':
            return
        # Local processing is synchronous; platform mutations must settle before
        # another request reuses their file, including each corpus mutation.
        if path == '/api/v1/retrieval/process/file' and isinstance(body, dict) and body.get('job_id'):
            self.wait_for_file(payload['file_id'], actor=actor)
        elif re.fullmatch(r'/api/v1/knowledge/[^/]+/file/(add|update)', path):
            self.wait_for_file(payload['file_id'], actor=actor)
        elif runtime_soev_backed() == 'drive' and (
            match := re.fullmatch(r'/api/v1/files/([^/]+)/data/content/update', path)
        ):
            self.wait_for_file(unquote(match[1]), actor=actor)

    def wait_for_file(self, file_id, *, actor=None, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            file = self.request('GET', f'/api/v1/files/{quote(file_id, safe="")}', actor=actor)
            meta = file.get('meta') or {}
            status = meta.get('status')
            if status == 'failed':
                raise RuntimeError(f'Seeding file {file_id} failed: {meta.get("error")}')
            # The poller writes status before clearing the job. Wait for both,
            # or the next process/file call is refused as an in-flight ingest.
            if status == 'completed' and meta.get('soev_job') is None:
                return
            time.sleep(0.5)
        raise RuntimeError(f'Seeding file {file_id} did not complete within {timeout}s')


def seed_disabled_oauth_client(ctx, parameter):
    return f'mcp:fictional-disabled-{ctx.token}'


def seed_refused_target(ctx, parameter):
    return 'fictional-retired-' + ctx.token


def seed_request_user(ctx, parameter):
    return ctx.client.identity['id']


def seed_console_access(ctx, parameter):
    payload = ctx.request('GET', '/api/v1/auths/console-access', actor=ctx.client)
    if set(payload) != {'console_url'} or not (
        payload['console_url'] is None or isinstance(payload['console_url'], str)
    ):
        raise RuntimeError('Invalid caller console eligibility')
    return 'allowed' if payload['console_url'] is not None else 'unavailable'


def seed_ollama_connection(ctx, parameter):
    # The platform picker has catalog ids, while these surviving proxies require
    # a backend's own model id. Read the sealed stack's configured upstream
    # catalog: OWUI's administrative tags endpoint no longer exists.
    urls = os.environ['OLLAMA_BASE_URLS'].split(';')
    for index, url in enumerate(urls):
        response = requests.get(url.rstrip('/') + '/api/tags', timeout=60)
        try:
            response.raise_for_status()
            models = response.json()['models']
        finally:
            response.close()
        if models:
            model = _extract(models, '0.model', name=parameter['key'], via='configured Ollama catalog')
            ctx.provide(parameter, 'upstream_model', model)
            return str(index)
    raise RuntimeError('No model is available in the configured Ollama catalogs')


def upstream_path(ctx, parameter):
    return parameter['value']


def seed_export(ctx, parameter):
    ctx.request('POST', '/api/v1/export/data', actor=ctx.client)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        body = ctx.request('GET', '/api/v1/export/data/status', actor=ctx.client)
        if body.get('status') == 'ready':
            path = _extract(body, 'export_path', name=parameter['key'], via='GET export status')
            if not path.startswith(f'exports/{ctx.client.user_id}/'):
                raise RuntimeError('Export path belongs to a different user')
            return path
        time.sleep(0.2)
    raise RuntimeError('Export did not become ready within 60 seconds')


def catalog_default_model(ctx, parameter):
    # The sweep requires one product-configured catalog model; an empty selection
    # cannot establish a positive model control.
    default = ctx.request('GET', '/api/config', actor=ctx.client).get('default_models')
    if not isinstance(default, str) or not default or ',' in default:
        raise RuntimeError(f'{parameter["key"]}: expected one soev catalog default model, found {default!r}')
    return default


def seed_model(ctx, parameter):
    model_id = catalog_default_model(ctx, parameter)
    registry = ctx.request('GET', '/api/models', actor=ctx.client)
    if model_id not in {model['id'] for model in registry.get('data', [])}:
        raise RuntimeError('Configured model is not available to the ordinary caller')
    return model_id


def seed_skill_bundle(ctx, parameter):
    skill_id = f'fictional-skill-{ctx.token}'
    created = ctx.request(
        'POST',
        '/api/v1/skills/create',
        actor=ctx.client,
        json={'id': skill_id, 'name': skill_id, 'content': 'Fictional skill instructions.'},
    )
    skill_id = _extract(created, 'id', name=parameter['key'], via='POST /api/v1/skills/create')
    for path in ('fictional-edit.txt', 'fictional-move.txt', 'fictional-remove.txt'):
        ctx.request(
            'POST',
            f'/api/v1/skills/id/{skill_id}/files',
            actor=ctx.client,
            json={'path': path, 'content': 'Fictional skill file.'},
        )
    return skill_id


def seed_chat(ctx, parameter):
    if runtime_soev_backed() != 'drive':
        return _seed_endpoint(ctx, parameter)
    created = ctx.request(
        'POST',
        '/api/v1/chats/new',
        actor=ctx.client,
        json={'agent': 'soev_react', 'input': {'text': 'A fictional regression conversation.'}},
    )
    chat_id = _extract(created, 'id', name=parameter['key'], via='POST /api/v1/chats/new')
    ctx.request(
        'POST',
        '/api/chat/completions',
        actor=ctx.client,
        json={
            'model': ctx.values['model'],
            'chat_id': chat_id,
            'follow_thread': True,
            'stream': False,
            'messages': [{'role': 'user', 'content': 'A fictional regression conversation.'}],
        },
    )
    page = ctx.request('GET', f'/api/v1/chats/{chat_id}', actor=ctx.client)
    finished = [message for message in page.get('messages', []) if message.get('finished')]
    if not finished:
        raise RuntimeError('Chat turn produced no committed messages')
    for name in parameter.get('provides', []):
        ctx.provide(parameter, name, str(max(message['position'] for message in finished)))
    ctx.request('POST', f'/api/v1/chats/{chat_id}/folder', actor=ctx.client, json={'folder_id': ctx.values['folder']})
    return chat_id


def seed_chat_message(ctx, parameter):
    page = ctx.request('GET', f'/api/v1/chats/{ctx.values["chat"]}', actor=ctx.client)
    if runtime_soev_backed() != 'drive':
        return _extract(page, 'chat.history.currentId', name=parameter['key'], via='GET chat')
    return str(max(message['position'] for message in page['messages'] if message['finished']))


def seed_chat_share(ctx, parameter):
    kwargs = {}
    if runtime_soev_backed() == 'drive':
        # The mirrored control group has no stranger members. A folder audience
        # is not yet supported by the platform's naming authority.
        kwargs['json'] = {
            'audience': {'kind': 'group', 'id': 'platform:group:' + ctx.values['group']},
            'at': int(ctx.values['chat_message']),
        }
    shared = ctx.request('POST', f'/api/v1/chats/{ctx.values["chat"]}/share', actor=ctx.client, **kwargs)
    return _extract(shared, 'share_id', name=parameter['key'], via='POST chat/share')


def seed_group(ctx, parameter):
    if runtime_soev_backed() != 'drive':
        return _seed_endpoint(ctx, parameter)
    groups = ctx.request('GET', '/api/v1/groups/', actor=ctx.client)
    return _extract(groups, '0.id', name=parameter['key'], via='GET groups/')


def seed_feedback(ctx, parameter):
    comment = f'Fictional feedback {ctx.token}'
    response = ctx.client.request(
        'POST',
        '/api/v1/evaluations/feedback',
        json={
            'thread_id': ctx.values['chat'],
            'position': int(ctx.values['chat_message']),
            'rating': 1,
            'reason': 'accuracy',
            'tags': ['language'],
            'comment': comment,
            'comment_authored': True,
        },
    )
    try:
        if response.status_code != 204:
            raise RuntimeError(f'Platform feedback was not committed: HTTP {response.status_code}')
    finally:
        response.close()
    page = ctx.request('GET', f'/api/v1/evaluations/feedback/conversation/{ctx.values["chat"]}', actor=ctx.client)
    if not any(
        item.get('thread_id') == ctx.values['chat']
        and item.get('comment') == comment
        and item.get('rating') == 1
        and item.get('reason') == 'accuracy'
        and item.get('tags') == ['language']
        for item in page.get('items', [])
    ):
        raise RuntimeError('Platform feedback could not be read back by its owner')
    # Individual legacy feedback rows have no platform id: their routes are
    # explicit refusal probes, while creation and conversation reads stay positive.
    return f'attack_feedback_probe_{ctx.token}'


def seed_knowledge_file(ctx, parameter):
    # Removal and asynchronous reprocessing each need their own settled file.
    label = f'attack-{parameter["key"]}-{ctx.token}'
    upload = ctx.request(
        'POST',
        '/api/v1/files/',
        actor=ctx.client,
        params={'process': False},
        files=[('file', (f'{label}.txt', label, 'text/plain'))],
    )
    file_id = _extract(upload, 'id', name=parameter['key'], via='POST /api/v1/files/')
    # file/add refuses a file with no extracted content.
    ctx.request('POST', '/api/v1/retrieval/process/file', actor=ctx.client, json={'file_id': file_id}, timeout=120)
    knowledge = ctx.request(
        'POST',
        '/api/v1/knowledge/create',
        actor=ctx.client,
        json={'name': label, 'description': 'Attack knowledge with an isolated processed file'},
    )
    knowledge_id = _extract(knowledge, 'id', name=parameter['key'], via='POST /api/v1/knowledge/create')
    added = ctx.request(
        'POST',
        f'/api/v1/knowledge/{quote(knowledge_id, safe="")}/file/add',
        actor=ctx.client,
        json={'file_id': file_id},
        timeout=120,
    )
    if not any(isinstance(item, dict) and item.get('id') == file_id for item in added.get('files') or []):
        raise RuntimeError(f'{parameter["key"]}: the file did not join its knowledge base')
    (provided,) = parameter['provides']
    ctx.provide(parameter, provided, file_id)
    return knowledge_id


def seed_cloud_family(ctx, parameter):
    return 'live_documents'


def seed_cloud_connection(ctx, parameter):
    created = ctx.request('POST', '/api/v1/cloud-sync/connections', actor=ctx.client, json={'provider': 'onedrive'})
    connection = _extract(created, 'connection_id', name=parameter['key'], via='POST cloud connection')
    # soev-api keeps one connection per owner and provider: a later pass gets the
    # consented connection back, without an authorize URL, and needs no new consent.
    if created.get('authorize_url') is not None:
        # OAuth cannot return from Microsoft on the sealed network. Only consent
        # state is seeded; the application must admit the owner and create its grant.
        with requests.Session() as session:
            session.trust_env = False
            try:
                response = session.post(
                    os.environ['CI_PLATFORM_FIXTURE_URL'] + '/ci/consent',
                    headers={'Authorization': 'Bearer ' + os.environ['CI_PLATFORM_FIXTURE_KEY']},
                    json={'connection_id': connection, 'authorize_url': created['authorize_url']},
                    timeout=30,
                )
            except requests.RequestException as error:
                raise RuntimeError('Consent fixture request failed') from error
            if response.status_code != 200:
                raise RuntimeError(f'Consent fixture refused the operation: HTTP {response.status_code}')
            try:
                accepted = response.json()
            except ValueError:
                raise RuntimeError('Consent fixture returned invalid JSON') from None
            if not isinstance(accepted, dict) or accepted.get('id') != connection:
                raise RuntimeError('Consent fixture did not acknowledge this connection')
    grant = ctx.request('POST', f'/api/v1/cloud-sync/connections/{connection}/live/live_documents', actor=ctx.client)
    ctx.provide(parameter, 'cloud_grant', grant['id'])
    return connection


def seed_cloud_authorize_connection(ctx, parameter):
    # Authorizing an enabled connection suspends it, so this control needs a pending
    # connection that is not the consented OneDrive fixture. With one connection per owner
    # and provider that takes another provider; Outlook mail where the platform offers it.
    policy = ctx.request('GET', '/api/v1/cloud-sync/policy', actor=ctx.client)
    if 'outlook_mail' not in policy.get('providers_enabled', []):
        raise RuntimeError('Authorize control requires a separate Outlook mail connection')
    provider = 'outlook_mail'
    created = ctx.request('POST', '/api/v1/cloud-sync/connections', actor=ctx.client, json={'provider': provider})
    return _extract(created, 'connection_id', name=parameter['key'], via='POST cloud connection')


_SEEDERS = {
    function.__name__: function
    for function in (
        seed_disabled_oauth_client,
        seed_request_user,
        seed_console_access,
        seed_refused_target,
        seed_ollama_connection,
        upstream_path,
        seed_export,
        seed_model,
        seed_skill_bundle,
        seed_chat,
        seed_chat_message,
        seed_chat_share,
        seed_group,
        seed_feedback,
        seed_cloud_connection,
        seed_cloud_authorize_connection,
        seed_cloud_family,
        seed_knowledge_file,
    )
}


def _seed_endpoint(ctx, p):
    method, path = p['via'].split(' ', 1)
    path = ctx.fill(path, p, path=True)
    if re.search(r'\{[^}]+\}', path):
        raise RuntimeError(f'{p["key"]}: unresolved dependency in seed URL')
    kwargs = {}
    if 'body' in p:
        kwargs['json'] = ctx.fill(p['body'], p)
    if 'files' in p:
        kwargs['files'] = [
            (f['field'], (ctx.fill(f['filename'], p), ctx.fill(f['content'], p), f['content_type'])) for f in p['files']
        ]
    if 'query' in p:
        kwargs['params'] = ctx.fill(p['query'], p)
    body = ctx.request(method, path, actor=ctx.client, **kwargs)
    ctx.payloads[p['key']] = body
    return _extract(body, ctx.fill(p['extract'], p), name=p['key'], via=p['via'])


class Resolved(dict):
    """Path parameters keyed by (path, name), plus `fields`: route -> json/params/form/files.

    A dict, so every caller that only wants path parameters is unaffected.
    """

    def __init__(self, *args, fields=None, failures=None, blocked=None, fixtures=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields = fields or {}
        self.fixtures = fixtures or {}
        self.failures = failures or {}
        self.blocked = blocked or {}


def _fill_field(value, ctx, *, path=False):
    if isinstance(value, dict):
        return {key: _fill_field(item, ctx, path=path) for key, item in value.items()}
    if isinstance(value, list):
        return [_fill_field(item, ctx, path=path) for item in value]
    if not isinstance(value, str):
        return value

    def substitute(match):
        key = match.group(1)
        if key not in ctx.values:
            raise RuntimeError(f'field value references {key!r}, which is not a seeded parameter')
        return quote(ctx.values[key], safe='') if path else ctx.values[key]

    # Identifier-shaped keys only: a JSON file part's own braces are content, not references.
    return re.sub(r'\{([A-Za-z_][A-Za-z0-9_]*)\}', substitute, _fill_token(value, ctx.token))


def resolve_fields(ctx, *, entries=None):
    """Fill every [[field]] from seeded values, making its setup requests first, in order."""
    fields = {}
    for entry in ctx.surface.get('field', []) if entries is None else entries:
        if runtime_soev_backed() == 'drive':
            entry = {**entry, **entry.get('v2', {})}
        if backed_seed_waived(entry) or route_waived(entry['route']):
            continue
        for step in entry.get('setup', []):
            method, path = step['via'].split(' ', 1)
            ctx.request(
                method, _fill_field(path, ctx, path=True), actor=ctx.client, json=_fill_field(step.get('body', {}), ctx)
            )
        resolved = {
            kind: _fill_field(entry[kind], ctx) for kind in ('json', 'params', 'form', 'headers') if kind in entry
        }
        for name in entry.get('json_integer_fields', []):
            resolved['json'][name] = int(resolved['json'][name])
        if 'params' in resolved:
            resolved['params'] = {
                key: str(value).lower() if isinstance(value, bool) else value
                for key, value in resolved['params'].items()
            }
        if 'body_base64' in entry:
            resolved['data'] = base64.b64decode(entry['body_base64'], validate=True)
        if 'files' in entry:
            resolved['files'] = [
                (
                    part['field'],
                    (_fill_field(part['filename'], ctx), _fill_field(part['content'], ctx), part['content_type']),
                )
                for part in entry['files']
            ]
        fields[entry['route']] = resolved
    return fields


def _seed_parameter(ctx, p):
    if runtime_soev_backed() == 'drive' and 'v2_refused_value' in p:
        return ctx.fill(p['v2_refused_value'], p)
    if runtime_soev_backed() == 'drive' and 'v2_seeded_by' in p:
        value = _SEEDERS[p['v2_seeded_by']](ctx, p)
    elif 'same_as' in p:
        return ctx.values[p['same_as']]
    elif 'seeded_by' in p:
        value = _SEEDERS[p['seeded_by']](ctx, p)
    else:
        return _seed_endpoint(ctx, p)
    missing = [name for name in p.get('provides', []) if name not in ctx.values]
    if missing:
        raise RuntimeError(f'{p["key"]}: seeder did not provide {missing}')
    return value


def resolve_parameters(
    client: AttackClient, *, admin: AttackClient | None = None, surface=SURFACE, spec=SPEC, collect_failures=False
):
    ordered = selected_parameters(surface)
    ctx = SeedContext(client, admin or client, surface)
    failures, blocked = {}, {}
    for p in ordered:
        key = p['key']
        if not seed_enabled(p):
            continue
        dependencies = [name for name in _dependencies(p) if name in failures]
        if dependencies:
            failures[key] = f'Failed dependencies: {dependencies}'
            continue
        try:
            with timed(f'seed parameter {key}', announce=True):
                value = _seed_parameter(ctx, p)
            ctx.values[key] = _extract(
                {'value': value}, 'value', name=key, via=p.get('via', p.get('seeded_by', 'alias'))
            )
        except RuntimeError as error:
            if not collect_failures or isinstance(error, AuthenticationError):
                raise
            failures[key] = str(error)
            print(f'Sweep seed failed: {key}: {error}', flush=True)
    resolved = {}
    for path in spec['paths']:
        for name in re.findall(r'\{([^}]+)\}', path):
            p = parameter_for(path, name, surface)
            if p['key'] in failures:
                for method in spec['paths'][path].keys() & METHODS:
                    blocked[f'{method.upper()} {path}'] = failures[p['key']]
            elif seed_enabled(p):
                resolved[path, name] = ctx.values[p['key']]
    fields = {}
    for entry in surface.get('field', []):
        try:
            with timed(f'seed fields {entry["route"]}', announce=bool(entry.get('setup'))):
                fields.update(resolve_fields(ctx, entries=[entry]))
        except RuntimeError as error:
            if not collect_failures or isinstance(error, AuthenticationError):
                raise
            failures[entry['route']] = str(error)
            blocked[entry['route']] = str(error)
            print(f'Sweep seed failed: {entry["route"]}: {error}', flush=True)
    return Resolved(resolved, fields=fields, failures=failures, blocked=blocked, fixtures=dict(ctx.values))
