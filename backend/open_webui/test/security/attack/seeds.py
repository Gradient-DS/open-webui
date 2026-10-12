"""Resolve route-scoped fixtures over HTTP; never import the application.

Call once per pass, serially, using ensure_identities() from identities.py.
Feature-gated fixtures use the admin; integration fixtures use the caller.
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
from urllib.parse import quote

import requests

from .client import AttackClient, AuthenticationError, json_body, totp_code
from .configuration import recover_configuration

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
    return 'unseedable' not in entry and not backed_seed_waived(entry)


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
    entries = surface['parameter']
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
            if 'seeded_by' not in p:
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
        # Local processing is synchronous; a soev-backed deployment returns a job
        # instead, and the next fixture step must not race that ingestion.
        if method == 'POST' and path == '/api/v1/retrieval/process/file' and body.get('job_id'):
            self.wait_for_file(kwargs['json']['file_id'], actor=actor)
        elif method == 'POST' and re.fullmatch(r'/api/v1/knowledge/[^/]+/file/add', path):
            self.wait_for_file(kwargs['json']['file_id'], actor=actor)
        return body

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


def prepare_configuration(ctx, parameter):
    namespace = f'attack_{ctx.token}'
    updates = {f'{namespace}.marker': ctx.token, **parameter['config']}
    ctx.request('POST', '/api/v1/configs/import', actor=ctx.admin, json={'config': updates})
    body = ctx.request('GET', f'/api/v1/configs/namespace/{namespace}', actor=ctx.admin)
    if body.get(f'{namespace}.marker') != ctx.token:
        raise RuntimeError('Seeding configuration namespace did not persist')
    return namespace


def connection_index(ctx, parameter):
    body = ctx.request('GET', parameter['config_via'], actor=ctx.admin)
    urls = body.get(parameter['urls_field'])
    if not isinstance(urls, list) or not urls or not isinstance(urls[0], str) or not urls[0]:
        raise RuntimeError(f'{parameter["key"]}: no configured upstream connection')
    ctx.request('GET', parameter['probe'], actor=ctx.admin)
    return '0'


def upstream_path(ctx, parameter):
    ctx.request('GET', ctx.fill(parameter['probe'], parameter, path=True), actor=ctx.admin)
    return parameter['value']


def seed_terminal(ctx, parameter):
    # The dedicated terminal config endpoints require FEATURE_TERMINAL_SERVERS,
    # but the proxy itself reads this config without that UI feature gate.
    body = ctx.request('GET', '/api/v1/configs/export', actor=ctx.admin)
    connections = body.get('terminal_server.connections') or []
    server_id = f'attack-{ctx.token}'
    # An empty access_grants list means private (utils/access_control:156), and
    # BYPASS_ADMIN_ACCESS_CONTROL is false here to match production, so even the
    # admin cannot see an ungranted connection and list_terminal_servers filters
    # it out. Grant read explicitly rather than relying on the admin role.
    connections.append(
        {
            'id': server_id,
            'name': server_id,
            'url': parameter['url'],
            'auth_type': 'none',
            'config': {'access_grants': [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}]},
        }
    )
    ctx.request(
        'POST', '/api/v1/configs/import', actor=ctx.admin, json={'config': {'terminal_server.connections': connections}}
    )
    servers = ctx.request('GET', '/api/v1/terminals/', actor=ctx.admin)
    if not any(server.get('id') == server_id for server in servers):
        raise RuntimeError('Terminal connection was not registered')
    ctx.request('GET', f'/api/v1/terminals/{server_id}/openapi.json', actor=ctx.admin)
    return server_id


def seed_export(ctx, parameter):
    # /auths/add returns a real session for the newly created disposable user.
    token = _extract(ctx.payloads['user'], 'token', name='export identity', via='POST /api/v1/auths/add')
    with AttackClient(ctx.client.base_url, token=token) as exporter:
        exporter.verify_identity(user_id=ctx.values['user'], role='user')
        ctx.request('POST', '/api/v1/export/data', actor=exporter)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            body = ctx.request('GET', '/api/v1/export/data/status', actor=exporter)
            if body.get('status') == 'ready':
                path = _extract(body, 'export_path', name=parameter['key'], via='GET export status')
                if not path.startswith(f'exports/{ctx.values["user"]}/'):
                    raise RuntimeError('Export path belongs to a different user')
                return path
            time.sleep(0.2)
    raise RuntimeError('Export did not become ready within 60 seconds')


def register_readable_model(ctx, model_id):
    # Registry discovery alone has no Models row; chat requires one even for
    # admins when BYPASS_ADMIN_ACCESS_CONTROL is false. This endpoint creates
    # the base-model row if absent and replaces its grants on subsequent passes.
    grant = {'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}
    body = ctx.request(
        'POST',
        '/api/v1/models/model/access/update',
        actor=ctx.admin,
        json={'id': model_id, 'access_grants': [grant]},
    )
    if body.get('id') != model_id or not any(
        all(item.get(key) == value for key, value in grant.items()) for item in body.get('access_grants', [])
    ):
        raise RuntimeError(f'Model {model_id} did not acquire public read access')
    # The owner control for POST /models/model/toggle deactivates this row, and an
    # inactive override hides its base model from the registry (utils/models.py).
    if body.get('is_active') is False:
        toggled = ctx.request('POST', '/api/v1/models/model/toggle', actor=ctx.admin, params={'id': model_id})
        if toggled.get('is_active') is not True:
            raise RuntimeError(f'Model {model_id} could not be reactivated')


def catalog_default_model(ctx, parameter):
    # Drive mode runs the v2 runtime, where soev-api's catalog is the only source of
    # registry models (utils/models.py:get_all_base_models); upstream ids never list.
    # /api/config names the catalog default (soev/model_catalog.default_models) even
    # while an earlier pass's toggle has hidden it from /api/models.
    default = ctx.request('GET', '/api/config', actor=ctx.admin).get('default_models')
    if not isinstance(default, str) or not default or ',' in default:
        raise RuntimeError(f'{parameter["key"]}: expected one soev catalog default model, found {default!r}')
    return default


def seed_model(ctx, parameter):
    path = f'/openai/models/{quote(ctx.values["openai_index"], safe="")}'
    upstream = ctx.request('GET', path, actor=ctx.admin)
    ctx.values['upstream_model'] = _extract(upstream, 'data.0.id', name=parameter['key'], via=f'GET {path}')
    if runtime_soev_backed() == 'drive':
        model_id = catalog_default_model(ctx, parameter)
    else:
        model_id = ctx.values['upstream_model']
    register_readable_model(ctx, model_id)
    return model_id


def _create_active_function(ctx, parameter, function_id):
    # FunctionForm has no is_active: a function is created off and /toggle turns
    # it on. The toggle is an ordinary route the passes drive against the shared
    # {function}, so an always-active function must be one it never targets.
    body = ctx.request(
        'POST',
        '/api/v1/functions/create',
        actor=ctx.admin,
        json={'id': function_id, 'name': function_id, 'meta': {}, 'content': parameter['content']},
    )
    if _extract(body, 'id', name=parameter['key'], via='POST functions/create') != function_id:
        raise RuntimeError(f'{parameter["key"]}: function creation returned a different function')
    enabled = ctx.request('POST', f'/api/v1/functions/id/{function_id}/toggle', actor=ctx.admin)
    if enabled.get('is_active') is not True:
        raise RuntimeError(f'{parameter["key"]}: function was not activated')
    return function_id


def seed_active_function(ctx, parameter):
    function_id = _create_active_function(ctx, parameter, f'attack_active_{ctx.token}')
    # utils/actions.py:chat_action runs only an action the model surfaces, admins included
    # unless they bypass access control; a global action is surfaced on every model.
    made_global = ctx.request('POST', f'/api/v1/functions/id/{function_id}/toggle/global', actor=ctx.admin)
    if made_global.get('is_global') is not True:
        raise RuntimeError(f'{parameter["key"]}: function was not made global')
    # The model registry is cached; rebuilding it attaches the action to the seeded model.
    ctx.request('GET', '/api/models', actor=ctx.admin)
    return function_id


def seed_totp_user(ctx, parameter):
    # A user of its own: the shared {user} is also the exporter, which signs in
    # with its password, and an enrolled account answers that with a 2FA challenge.
    body = ctx.request(
        'POST',
        '/api/v1/auths/add',
        actor=ctx.admin,
        json={
            'email': f'attack-totp-{ctx.token}@example.com',
            'name': f'Attack TOTP {ctx.token}',
            'password': parameter['password'],
            'role': 'user',
        },
    )
    user_id = _extract(body, 'id', name=parameter['key'], via='POST /api/v1/auths/add')
    token = _extract(body, 'token', name=parameter['key'], via='POST /api/v1/auths/add')
    with AttackClient(ctx.client.base_url, token=token) as enrolee:
        enrolee.verify_identity(user_id=user_id, role='user')
        setup = ctx.request('POST', '/api/v1/auths/2fa/totp/setup', actor=enrolee)
        secret = _extract(setup, 'secret', name=parameter['key'], via='POST /api/v1/auths/2fa/totp/setup')
        enabled = ctx.request(
            'POST',
            '/api/v1/auths/2fa/totp/enable',
            actor=enrolee,
            json={'password': parameter['password'], 'secret': secret, 'code': totp_code(secret)},
        )
    if enabled.get('totp_enabled') is not True:
        raise RuntimeError(f'{parameter["key"]}: TOTP enrolment did not take effect')
    return user_id


def seed_task(ctx, parameter):
    model_id = _create_active_function(ctx, parameter, f'attack_task_{ctx.token}')
    # Inlets run before routing, including private stacks with agent routing on.
    ctx.request(
        'POST',
        '/api/v1/models/create',
        actor=ctx.admin,
        json={
            'id': model_id,
            'name': model_id,
            'base_model_id': ctx.values['model'],
            'meta': {'filterIds': [model_id]},
            'params': {},
        },
    )
    register_readable_model(ctx, model_id)
    registry = ctx.request('GET', '/api/models', actor=ctx.admin, params={'refresh': True})
    if not any(model.get('id') == model_id for model in registry.get('data', [])):
        raise RuntimeError('Task model did not appear in the model registry')
    # For a normal persisted chat, main.py only needs a nonempty session_id
    # to select background mode; it does not require a connected Socket.IO peer.
    chat_id = ctx.values['chat']
    result = ctx.request(
        'POST',
        '/api/chat/completions',
        actor=ctx.admin,
        json={
            'model': model_id,
            'chat_id': chat_id,
            'id': f'task_message_{ctx.token}',
            'session_id': f'attack_{ctx.token}',
            'stream': False,
            'messages': [{'role': 'user', 'content': 'Hold this attack fixture until cancelled.'}],
        },
    )
    task_id = _extract(result, 'task_ids.0', name=parameter['key'], via='POST /api/chat/completions')
    try:
        # A returned id alone may already have completed. Check after the
        # background coroutine has had time to fail or enter its bounded wait.
        for _ in range(3):
            active = ctx.request('GET', f'/api/tasks/chat/{quote(chat_id, safe="")}', actor=ctx.admin)
            if task_id not in active.get('task_ids', []):
                raise RuntimeError('Task completed before it could be used as a stop target')
            time.sleep(0.1)
    except Exception:
        ctx.admin.request('POST', f'/api/tasks/stop/{quote(task_id, safe="")}')
        raise
    return task_id


def seed_knowledge_file(ctx, parameter):
    # Removal and asynchronous reprocessing each need their own settled file.
    label = f'attack-{parameter["key"]}-{ctx.token}'
    upload = ctx.request(
        'POST',
        '/api/v1/files/',
        actor=ctx.admin,
        params={'process': False},
        files=[('file', (f'{label}.txt', label, 'text/plain'))],
    )
    file_id = _extract(upload, 'id', name=parameter['key'], via='POST /api/v1/files/')
    # file/add refuses a file with no extracted content.
    ctx.request('POST', '/api/v1/retrieval/process/file', actor=ctx.admin, json={'file_id': file_id}, timeout=120)
    knowledge = ctx.request(
        'POST',
        '/api/v1/knowledge/create',
        actor=ctx.admin,
        json={'name': label, 'description': 'Attack knowledge with an isolated processed file'},
    )
    knowledge_id = _extract(knowledge, 'id', name=parameter['key'], via='POST /api/v1/knowledge/create')
    added = ctx.request(
        'POST',
        f'/api/v1/knowledge/{quote(knowledge_id, safe="")}/file/add',
        actor=ctx.admin,
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
    created = ctx.request('POST', '/api/v1/cloud-sync/connections', actor=ctx.admin, json={'provider': 'onedrive'})
    connection = _extract(created, 'connection_id', name=parameter['key'], via='POST cloud connection')
    # OAuth cannot return from Microsoft on the sealed network. Only consent
    # state is seeded; the application must admit the owner and create its grant.
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(
            os.environ['CI_PLATFORM_FIXTURE_URL'] + '/ci/consent',
            headers={'Authorization': 'Bearer ' + os.environ['CI_PLATFORM_FIXTURE_KEY']},
            json={'connection_id': connection},
            timeout=30,
        )
        response.raise_for_status()
    grant = ctx.request('POST', f'/api/v1/cloud-sync/connections/{connection}/live/live_documents', actor=ctx.admin)
    ctx.provide(parameter, 'cloud_grant', grant['id'])
    return connection


def seed_external_source(ctx, parameter):
    # A connection of its own: delete_external_knowledge_connection answers 400 while
    # a KB still uses one, so on {connection} this KB would refuse the owner's DELETE.
    connection = ctx.request(
        'POST',
        '/api/v1/knowledge/external/connections',
        actor=ctx.admin,
        json={
            'name': f'attack-source-{ctx.token}',
            'provider': 'qdrant',
            'endpoint': 'http://stub:8000',
            'auth_config': {},
        },
    )
    connection_id = _extract(connection, 'id', name=parameter['key'], via='POST external/connections')
    body = ctx.request(
        'POST',
        '/api/v1/knowledge/external/knowledge/create',
        actor=ctx.admin,
        json={
            'name': f'attack-external-{ctx.token}',
            'connection_id': connection_id,
            'source': {'name': f'attack-{ctx.token}', 'config': {'content_field': 'text'}},
        },
    )
    return _extract(body, 'id', name=parameter['key'], via='POST external/knowledge/create')


_SEEDERS = {
    function.__name__: function
    for function in (
        prepare_configuration,
        connection_index,
        upstream_path,
        seed_terminal,
        seed_export,
        seed_model,
        seed_task,
        seed_active_function,
        seed_totp_user,
        seed_cloud_connection,
        seed_cloud_family,
        seed_knowledge_file,
        seed_external_source,
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
    body = ctx.request(method, path, actor=ctx.admin if p.get('actor') == 'admin' else ctx.client, **kwargs)
    ctx.payloads[p['key']] = body
    return _extract(body, ctx.fill(p['extract'], p), name=p['key'], via=p['via'])


def apply_stack_configuration(ctx):
    """Turn on what the stack's environment cannot; see [stack_configuration] in the surface."""
    wanted = ctx.surface['stack_configuration']
    applied = ctx.request('POST', '/api/v1/configs/import', actor=ctx.admin, json={'config': wanted})
    missing = {key: applied.get(key) for key, value in wanted.items() if applied.get(key) != value}
    if missing:
        raise RuntimeError(f'Stack configuration did not take effect: {missing}')


class Resolved(dict):
    """Path parameters keyed by (path, name), plus `fields`: route -> json/params/form/files.

    A dict, so every caller that only wants path parameters is unaffected.
    """

    def __init__(self, *args, fields=None, failures=None, blocked=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields = fields or {}
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
        if backed_seed_waived(entry) or route_waived(entry['route']):
            continue
        for step in entry.get('setup', []):
            method, path = step['via'].split(' ', 1)
            ctx.request(
                method, _fill_field(path, ctx, path=True), actor=ctx.admin, json=_fill_field(step.get('body', {}), ctx)
            )
        resolved = {kind: _fill_field(entry[kind], ctx) for kind in ('json', 'params', 'form') if kind in entry}
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


def resolve_parameters(
    client: AttackClient, *, admin: AttackClient | None = None, surface=SURFACE, spec=SPEC, collect_failures=False
):
    ordered = selected_parameters(surface)
    ctx = SeedContext(client, admin or client, surface)
    if surface.get('preserve_configuration'):
        recover_configuration(ctx.admin)
    # After recovery: an interrupted pass's snapshot would otherwise turn it back off.
    if surface.get('stack_configuration'):
        apply_stack_configuration(ctx)
    failures, blocked = {}, {}
    for p in ordered:
        key = p['key']
        if 'unseedable' in p:
            continue
        dependencies = [name for name in _dependencies(p) if name in failures]
        if dependencies:
            failures[key] = f'Failed dependencies: {dependencies}'
            continue
        try:
            if 'same_as' in p:
                value = ctx.values[p['same_as']]
            elif 'seeded_by' in p:
                value = _SEEDERS[p['seeded_by']](ctx, p)
                missing = [name for name in p.get('provides', []) if name not in ctx.values]
                if missing:
                    raise RuntimeError(f'{key}: seeder did not provide {missing}')
            else:
                value = _seed_endpoint(ctx, p)
            ctx.values[key] = _extract(
                {'value': value}, 'value', name=key, via=p.get('via', p.get('seeded_by', 'alias'))
            )
        except RuntimeError as error:
            if not collect_failures or isinstance(error, AuthenticationError):
                raise
            failures[key] = str(error)
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
            fields.update(resolve_fields(ctx, entries=[entry]))
        except RuntimeError as error:
            if not collect_failures or isinstance(error, AuthenticationError):
                raise
            failures[entry['route']] = str(error)
            blocked[entry['route']] = str(error)
    return Resolved(resolved, fields=fields, failures=failures, blocked=blocked)
