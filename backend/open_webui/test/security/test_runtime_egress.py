"""Static fetch inventory and CI seal checks; never import the application.

This detects explicit HTTP calls, not arbitrary network activity hidden inside
SDKs or dynamically loaded tools. The internal-only app network is the runtime
backstop for those and for URLs supplied by requests. Declarations must explain
how ordinary CI traffic is stubbed or why an optional integration stays closed.
"""

import ast
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

SERVER = Path(__file__).resolve().parents[2]
REPO = SERVER.parents[1]

DECLARED_FETCH_SINKS: dict[str, str] = {
    'config.py': 'Branding fetches require CUSTOM_NAME (empty); explicit Ollama URL avoids startup TCP probes.',
    'main.py': 'OFFLINE_MODE blocks GitHub updates; configured OpenAI and Ollama model calls go to stub.',
    'retrieval/loaders/datalab_marker.py': 'Marker requires a selected engine and DATALAB_MARKER_API_KEY; both empty in CI.',
    'retrieval/loaders/external_document.py': 'External document loader is not selected; native CONTENT_EXTRACTION_ENGINE is empty.',
    'retrieval/loaders/external_web.py': 'External web loader is not selected; WEB_LOADER_ENGINE is empty.',
    'retrieval/loaders/main.py': 'Remote OCR/Docling/Tika branches are not selected; CONTENT_EXTRACTION_ENGINE is empty.',
    'retrieval/loaders/microsoft_web_iq.py': 'Microsoft web loader is not selected; WEB_LOADER_ENGINE is empty.',
    'retrieval/loaders/mineru.py': 'MinerU loader is not selected; CONTENT_EXTRACTION_ENGINE is empty.',
    'retrieval/loaders/mistral.py': 'Mistral OCR is not selected; CONTENT_EXTRACTION_ENGINE is empty.',
    'retrieval/loaders/paddleocr_vl.py': 'PaddleOCR is not selected; CONTENT_EXTRACTION_ENGINE is empty.',
    'retrieval/loaders/tavily.py': 'Tavily web loader is not selected; WEB_LOADER_ENGINE is empty.',
    'retrieval/models/external.py': 'External reranking requires a selected model/engine; both empty in CI.',
    'retrieval/utils.py': 'Embeddings use RAG_OPENAI_API_BASE_URL at stub; supplied download URLs are findings.',
    'retrieval/web/bing.py': 'bing search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/bocha.py': 'bocha search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/brave.py': 'brave search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/brave_llm_context.py': 'brave_llm_context search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/exa.py': 'exa search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/external.py': 'external search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/firecrawl.py': 'firecrawl search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/google_pse.py': 'google_pse search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/jina_search.py': 'jina_search search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/kagi.py': 'kagi search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/linkup.py': 'linkup search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/microsoft_web_iq.py': 'microsoft_web_iq search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/mojeek.py': 'mojeek search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/ollama.py': 'ollama search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/perplexity.py': 'perplexity search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/perplexity_search.py': 'perplexity_search search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/searchapi.py': 'searchapi search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/searxng.py': 'searxng search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/serpapi.py': 'serpapi search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/serper.py': 'serper search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/serphouse.py': 'serphouse search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/serply.py': 'serply search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/serpstack.py': 'serpstack search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/tavily.py': 'tavily search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/utils.py': 'Native web fetches consume supplied URLs; public destinations are findings, CI documents use stub.',
    'retrieval/web/yacy.py': 'yacy search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/yandex.py': 'yandex search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'retrieval/web/ydc.py': 'ydc search is not selected; empty WEB_SEARCH_ENGINE raises in search_web before dispatch.',
    'routers/agent_proxy.py': 'Agent passthrough uses AGENT_API_BASE_URL at stub.',
    'routers/audio.py': 'OpenAI audio URLs explicitly point to stub; other speech engines remain unselected.',
    'routers/auths.py': 'OAuth avatar fetch requires a completed provider login; no OAuth provider is configured.',
    'routers/configs.py': 'Admin connection verification consumes supplied URLs; off-network destinations are findings.',
    'routers/discovery.py': 'Discovery uses SEARCH_API_BASE_URL at stub.',
    'routers/functions.py': 'Admin URL imports consume supplied URLs; off-network destinations are findings.',
    'routers/images.py': 'OpenAI image URL explicitly points to stub; other image engines remain unselected.',
    'routers/ollama.py': 'Ollama passthrough uses OLLAMA_BASE_URL and OLLAMA_BASE_URLS at stub.',
    'routers/openai.py': 'OpenAI passthrough uses OPENAI_API_BASE_URLS at stub; supplied verification URLs are findings.',
    'routers/pipelines.py': 'Pipeline management uses configured OpenAI connections at stub; supplied import URLs are findings.',
    'routers/terminals.py': 'No terminal connections configured; supplied connection destinations are findings.',
    'routers/tools.py': 'Admin URL imports consume supplied URLs; off-network destinations are findings.',
    'services/confluence/auth.py': 'Confluence integration disabled and OAuth client credentials empty; no login tokens seeded.',
    'services/confluence/basic_auth.py': 'Confluence integration disabled with no service credentials; supplied connection URLs are findings.',
    'services/confluence/confluence_client.py': 'No Confluence integration credentials or stored OAuth tokens in the fresh CI database.',
    'services/confluence/token_refresh.py': 'No Confluence integration credentials or stored refresh tokens in the fresh CI database.',
    'services/email/auth.py': 'Missing EMAIL_GRAPH credentials raise before the Microsoft token request.',
    'services/email/graph_mail_client.py': 'Mail token acquisition raises before Graph sendMail when EMAIL_GRAPH credentials are empty.',
    'services/google_drive/auth.py': 'Google Drive integration disabled and OAuth credentials empty; no login tokens seeded.',
    'services/google_drive/drive_client.py': 'No Google Drive integration credentials or stored OAuth tokens in the fresh CI database.',
    'services/google_drive/token_refresh.py': 'No stored Google refresh token in the fresh CI database; refresh returns before HTTP.',
    'services/onedrive/auth.py': 'OneDrive integration disabled and OAuth credentials empty; no login tokens seeded.',
    'services/onedrive/graph_client.py': 'No OneDrive integration credentials or stored OAuth tokens in the fresh CI database.',
    'services/onedrive/token_refresh.py': 'No stored OneDrive refresh token in the fresh CI database; refresh returns before HTTP.',
    'services/sync/daemon_client.py': 'Manual sync and cancellation use SYNC_DAEMON_URL at stub.',
    'utils/agent.py': 'Agent completions use AGENT_API_BASE_URL at stub, including streaming requests.',
    'utils/anthropic.py': 'Provider passthrough uses configured OpenAI connections at stub; no Anthropic connection is seeded.',
    'utils/auth.py': 'License checks require LICENSE_KEY, empty in CI; an injected public license request is a finding.',
    'utils/automations.py': 'No automation webhook is seeded; request-supplied public webhooks are findings.',
    'utils/code_interpreter.py': 'No Jupyter URL configured; native code execution stays local.',
    'utils/doc_pipeline.py': 'Document submission and polling use PIPELINE_API_BASE_URL at stub.',
    'utils/feedback_report.py': 'Feedback Slack and notification-router URLs both point at stub.',
    'utils/files.py': 'Remote file download consumes supplied URLs; off-network destinations are findings.',
    'utils/images/comfyui.py': 'ComfyUI is not the selected image engine and its base URL is empty.',
    'utils/oauth.py': 'All OAuth providers unconfigured; no IdP credentials, sessions, or external tools seeded.',
    'utils/tools.py': 'No external tool servers configured; request-supplied public server URLs are findings.',
    'utils/webhook.py': 'Configured signup/chat webhook points at stub; supplied public webhook URLs are findings.',
}

_HTTP_CLIENTS = {'requests', 'http_requests', 'httpx', 'urllib3', 'aiohttp'}
_HTTP_VERBS = {
    'get',
    'post',
    'put',
    'patch',
    'delete',
    'head',
    'options',
    'request',
    'urlopen',
    'ws_connect',
    'stream',
    'send',
}
_SESSION_FACTORIES = {
    'Session',
    'Client',
    'AsyncClient',
    'ClientSession',
    'PoolManager',
    'ProxyManager',
    'HTTPConnectionPool',
    'HTTPSConnectionPool',
}
# These wrappers return HTTP sessions. Do not match arbitrary get_session()
# calls: SQLAlchemy uses that spelling too.
_LOCAL_SESSION_FACTORIES = {
    'open_webui.utils.session_pool.get_session',
    'open_webui.retrieval.web.utils.get_ssrf_safe_session',
}
_SKIP_DIRS = {'venv', '.venv', 'site-packages', 'node_modules', '__pycache__', 'test', 'tests'}


def _imports(tree: ast.AST) -> dict[str, str]:
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                aliases[name.asname or name.name.split('.')[0]] = name.name if name.asname else name.name.split('.')[0]
        elif isinstance(node, ast.ImportFrom):
            for name in node.names:
                aliases[name.asname or name.name] = f'{node.module}.{name.name}'
    return aliases


def _qualified(node: ast.AST, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return f'{_qualified(node.value, aliases)}.{node.attr}'
    return ''


def _is_session(value: ast.AST, aliases: dict[str, str], factories=()) -> bool:
    if isinstance(value, ast.Await):
        value = value.value
    if not isinstance(value, ast.Call):
        return False
    name = _qualified(value.func, aliases)
    return (
        name.split('.')[-1] in factories
        or name in _LOCAL_SESSION_FACTORIES
        or (name.split('.')[0] in _HTTP_CLIENTS and name.split('.')[-1] in _SESSION_FACTORIES)
    )


def _session_attributes(tree: ast.AST) -> set[str]:
    """Collect local names and attributes bound to HTTP sessions, including with/as.

    Imports qualify constructors so SQLAlchemy Session and pymilvus Client do
    not turn database get/delete calls into HTTP findings. Like the reference,
    this is a module-level approximation, not interprocedural data-flow analysis.
    """
    aliases = _imports(tree)
    bound = set()
    factories = set()
    # A small fixed point sees wrappers that return/yield a bound session, e.g.
    # self._get_client() and doc_pipeline._client(), without importing code.
    previous = None
    while previous != (bound, factories):
        previous = (bound.copy(), factories.copy())
        for node in ast.walk(tree):
            bindings = []
            if isinstance(node, ast.Assign):
                bindings = [(target, node.value) for target in node.targets]
            elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
                bindings = [(node.target, node.value)]
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                bindings = [(item.optional_vars, item.context_expr) for item in node.items]
            for target, value in bindings:
                if not _is_session(value, aliases, factories):
                    continue
                if isinstance(target, ast.Name):
                    bound.add(target.id)
                elif isinstance(target, ast.Attribute):
                    bound.add(target.attr)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(node):
                    if not isinstance(child, (ast.Return, ast.Yield)):
                        continue
                    value = child.value
                    if (
                        _is_session(value, aliases, factories)
                        or isinstance(value, ast.Name)
                        and value.id in bound
                        or isinstance(value, ast.Attribute)
                        and value.attr in bound
                    ):
                        factories.add(node.name)
    return bound


def _module_fetches(tree: ast.AST) -> bool:
    """Find explicit HTTP verbs, bound session calls, and urllib urlopen."""
    aliases = _imports(tree)
    sessions = _session_attributes(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = _qualified(func, aliases)
        if name in {'urlopen', 'urllib.request.urlopen'}:
            return True
        if name.split('.')[0] in _HTTP_CLIENTS and name.split('.')[-1] in _HTTP_VERBS:
            return True
        if isinstance(func, ast.Attribute):
            if func.attr == 'urlopen':
                return True
            if func.attr not in _HTTP_VERBS:
                continue
            value = func.value
            if isinstance(value, ast.Name) and value.id in sessions:
                return True
            if isinstance(value, ast.Attribute) and value.attr in sessions:
                return True
            if _is_session(value, aliases):
                return True
    return False


def _discovered_fetch_sinks() -> set[str]:
    found = set()
    for path in SERVER.rglob('*.py'):
        if any(part in _SKIP_DIRS for part in path.relative_to(SERVER).parts):
            continue
        if path.name.startswith('test_') or path.name == 'conftest.py':
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        if _module_fetches(tree):
            found.add(str(path.relative_to(SERVER)))
    return found


class TestOutboundFetchSinksAreDeclared:
    """A fetch sink the team did not write down is the bug, restated."""

    def test_no_undeclared_sink(self):
        undeclared = _discovered_fetch_sinks() - set(DECLARED_FETCH_SINKS)

        assert not undeclared, (
            'a module reaches the network that no one declared: '
            f'{sorted(undeclared)}. Review the destination and add a stub or '
            'a written fail-closed justification to DECLARED_FETCH_SINKS.'
        )

    def test_no_stale_declaration(self):
        stale = set(DECLARED_FETCH_SINKS) - _discovered_fetch_sinks()

        assert not stale, (
            f'declared fetch sinks that no longer fetch: {sorted(stale)}. '
            'Remove them -- a map padded with dead entries stops being read.'
        )


class TestTheWalkerSeesTheSinksItClaimsTo:
    """Pin HTTP and ordinary-object shapes without importing client libraries."""

    @pytest.mark.parametrize(
        'label,source',
        [
            (
                'returned client',
                'import httpx\nclass C:\n    def _client(self):\n        self.c = httpx.Client()\n        return self.c\n    def f(self):\n        s = self._client()\n        s.get(url)',
            ),
            (
                'yielded client',
                'import httpx\ndef factory():\n    with httpx.Client() as s:\n        yield s\nwith factory() as c:\n    c.post(url)',
            ),
            ('aliased module', 'import httpx as h\nh.patch(url)'),
            ('imported verb', 'from requests import get as fetch\nfetch(url)'),
            ('aliased urlopen', 'from urllib.request import urlopen as fetch\nfetch(url)'),
            ('context session', 'import requests\nwith requests.Session() as s:\n    s.get(url)'),
            (
                'async context',
                'from aiohttp import ClientSession as CS\nasync def f():\n    async with CS() as s:\n        await s.post(url)',
            ),
            (
                'async client',
                'import httpx\nasync def f():\n    async with httpx.AsyncClient() as c:\n        await c.get(url)',
            ),
            ('annotated client', 'import httpx\nc: object = httpx.Client()\nc.get(url)'),
            ('urllib3 pool', "import urllib3\np = urllib3.PoolManager()\np.request('GET', url)"),
            ('inline client', 'import httpx\nhttpx.Client().get(url)'),
            (
                'pooled client',
                'from open_webui.utils.session_pool import get_session\nasync def f():\n    s = await get_session()\n    await s.get(url)',
            ),
            (
                'safe client',
                'from open_webui.retrieval.web.utils import get_ssrf_safe_session\nasync def f():\n    async with get_ssrf_safe_session() as s:\n        await s.get(url)',
            ),
            ('module-level verb', 'import requests\nrequests.get(url)\n'),
            (
                'local session',
                'import requests\ndef f(url):\n    s = requests.Session()\n    return s.get(url)\n',
            ),
            (
                'session on self',
                'import requests\nclass C:\n    def __init__(self):\n'
                '        self.session = requests.Session()\n'
                '    def f(self, url):\n        return self.session.get(url)\n',
            ),
            (
                'httpx client on self',
                'import httpx\nclass C:\n    def __init__(self):\n'
                '        self.client = httpx.Client()\n'
                '    def f(self, url):\n        return self.client.post(url)\n',
            ),
            ('urlopen', 'from urllib.request import urlopen\nurlopen(url)\n'),
            ('urlopen attribute', 'import urllib.request\nurllib.request.urlopen(url)\n'),
        ],
        ids=lambda v: v if isinstance(v, str) and ' ' in v else '',
    )
    def test_an_outbound_shape_is_seen(self, label, source):
        assert _module_fetches(ast.parse(source)), label

    @pytest.mark.parametrize(
        'label,source',
        [
            ('sqlalchemy session', 'from sqlalchemy.orm import Session\ns = Session(engine)\ns.get(User, 1)'),
            ('database client', 'from pymilvus import MilvusClient as Client\nc = Client()\nc.delete(1)'),
            ('unrelated factory', 'from database import get_session\ns = get_session()\ns.get(1)'),
            ('dict get', "def f(data):\n    return data.get('url')\n"),
            ('nested dict get', "def f(row):\n    return row.meta.get('url')\n"),
            ('database delete', 'def f(db):\n    return db.delete(1)\n'),
            (
                'flask session',
                "from flask import session\ndef f():\n    return session.get('user_id')\n",
            ),
        ],
        ids=lambda v: v if isinstance(v, str) and ' ' in v else '',
    )
    def test_an_ordinary_attribute_call_is_not_mistaken_for_one(self, label, source):
        """The other direction, and the reason the session rule is bound to
        names this module was seen assigning a session to: ``get`` is an HTTP
        verb *and* the most common method name in Python, so a rule that
        matched every ``a.b.get(...)`` would report the whole server."""
        assert not _module_fetches(ast.parse(source)), label


# Run from the repository root with only pytest and PyYAML installed:
# python -m pytest backend/open_webui/test/security -v
# The local pytest.ini avoids executing open_webui/__init__.py (CLI dependencies).

STUB_URLS = {
    'OPENAI_API_BASE_URL': 'http://stub:8000/v1',
    'OPENAI_API_BASE_URLS': 'http://stub:8000/v1',
    'OLLAMA_BASE_URL': 'http://stub:8000',
    'OLLAMA_BASE_URLS': 'http://stub:8000',
    'AGENT_API_BASE_URL': 'http://stub:8000',
    'SEARCH_API_BASE_URL': 'http://stub:8000',
    'SYNC_DAEMON_URL': 'http://stub:8000',
    'PIPELINE_API_BASE_URL': 'http://stub:8000',
    'RAG_OPENAI_API_BASE_URL': 'http://stub:8000/v1',
    'AUDIO_STT_OPENAI_API_BASE_URL': 'http://stub:8000/v1',
    'AUDIO_TTS_OPENAI_API_BASE_URL': 'http://stub:8000/v1',
    'IMAGES_OPENAI_API_BASE_URL': 'http://stub:8000/v1',
    'WEBHOOK_URL': 'http://stub:8000/webhook',
    'FEEDBACK_REPORT_SLACK_WEBHOOK_URL': 'http://stub:8000/slack',
    'FEEDBACK_REPORT_WEBHOOK_URL': 'http://stub:8000/feedback',
}
CLOSED_SETTINGS = (
    'ENABLE_OTEL',
    'DATALAB_MARKER_API_KEY',
    'WEB_SEARCH_ENGINE',
    'RAG_CONTENT_EXTRACTION_ENGINE',
    'CONTENT_EXTRACTION_ENGINE',
    'WEB_LOADER_ENGINE',
    'CUSTOM_NAME',
    'LICENSE_KEY',
    'RAG_RERANKING_ENGINE',
    'RAG_RERANKING_MODEL',
    'COMFYUI_BASE_URL',
    'CODE_EXECUTION_JUPYTER_URL',
    'CODE_INTERPRETER_JUPYTER_URL',
)
OAUTH_CREDENTIALS = (
    'GOOGLE_CLIENT_ID',
    'GOOGLE_CLIENT_SECRET',
    'MICROSOFT_CLIENT_ID',
    'MICROSOFT_CLIENT_SECRET',
    'GITHUB_CLIENT_ID',
    'GITHUB_CLIENT_SECRET',
    'OAUTH_CLIENT_ID',
    'OAUTH_CLIENT_SECRET',
    'OPENID_PROVIDER_URL',
    'FEISHU_CLIENT_ID',
    'FEISHU_CLIENT_SECRET',
    'GOOGLE_DRIVE_CLIENT_ID',
    'ONEDRIVE_CLIENT_ID',
    'ONEDRIVE_CLIENT_ID_PERSONAL',
    'ONEDRIVE_CLIENT_ID_BUSINESS',
    'CONFLUENCE_OAUTH_CLIENT_ID',
    'CONFLUENCE_OAUTH_CLIENT_SECRET',
    'CONFLUENCE_BASIC_AUTH_USERNAME',
    'CONFLUENCE_BASIC_AUTH_API_TOKEN',
    'CONFLUENCE_SCOPED_API_TOKEN',
    'EMAIL_GRAPH_TENANT_ID',
    'EMAIL_GRAPH_CLIENT_ID',
    'EMAIL_GRAPH_CLIENT_SECRET',
)


@pytest.fixture(scope='module')
def compose():
    return yaml.safe_load((REPO / 'docker-compose.ci.yaml').read_text())


class TestCiStubsEveryDeclaredSink:
    @pytest.mark.parametrize('setting,expected', STUB_URLS.items())
    def test_required_destinations_are_internal(self, compose, setting, expected):
        env = compose['services']['open-webui']['environment']
        assert env.get(setting) == expected, setting
        url = urlsplit(env[setting])
        assert url.scheme == 'http' and url.hostname == 'stub' and url.port == 8000
        assert compose['services'][url.hostname]['networks'] == ['internal']

    @pytest.mark.parametrize('setting', CLOSED_SETTINGS + OAUTH_CREDENTIALS)
    def test_optional_destinations_stay_unconfigured(self, compose, setting):
        # Explicit empty strings cannot inherit host credentials as YAML null can.
        assert compose['services']['open-webui']['environment'].get(setting) == '', setting

    def test_offline_mode_and_remote_embeddings(self, compose):
        env = compose['services']['open-webui']['environment']
        assert env['OFFLINE_MODE'] == 'true'
        assert env['HF_HUB_OFFLINE'] == '1'
        assert env['RAG_EMBEDDING_ENGINE'] == 'openai'
        assert env['RAG_EMBEDDING_MODEL'] == 'text-embedding-3-small'
        assert env['RAG_OPENAI_API_KEY']
        assert env['ENABLE_PERSISTENT_CONFIG'] == 'false'
        for provider in ('GOOGLE_DRIVE', 'ONEDRIVE', 'CONFLUENCE'):
            assert env[f'ENABLE_{provider}_INTEGRATION'] == 'false'

    def test_redis_is_real_so_signout_revokes_tokens(self, compose):
        services = compose['services']
        assert services['open-webui']['environment']['REDIS_URL'] == 'redis://redis:6379/0'
        assert services['redis']['image'].startswith('redis:')
        assert services['redis']['healthcheck']['test'] == ['CMD', 'redis-cli', 'ping']
        assert services['open-webui']['depends_on']['redis']['condition'] == 'service_healthy'

    def test_database_and_vector_services_are_internal(self, compose):
        env = compose['services']['open-webui']['environment']
        assert urlsplit(env['DATABASE_URL']).hostname == 'postgres'
        assert env['DATABASE_HOST'] == 'postgres'
        assert env['VECTOR_DB'] == 'weaviate'
        assert env['WEAVIATE_HTTP_HOST'] == env['WEAVIATE_GRPC_HOST'] == 'weaviate'
        assert compose['services']['weaviate']['environment']['DISABLE_TELEMETRY'] == 'true'

    def test_only_entry_has_an_external_network(self, compose):
        assert compose['networks']['internal']['internal'] is True
        assert set(compose['networks']) == {'internal', 'edge'}
        services = compose['services']
        assert set(services) == {'entry', 'open-webui', 'postgres', 'weaviate', 'redis', 'stub'}
        for name, service in services.items():
            assert set(service['networks']) == ({'internal', 'edge'} if name == 'entry' else {'internal'})
            assert not service.get('network_mode')
            assert not service.get('privileged')
            assert not service.get('cap_add')
            assert not service.get('extra_hosts')
            assert not service.get('env_file')
            assert service.get('healthcheck') and not service['healthcheck'].get('disable')
            if name != 'entry':
                assert not service.get('ports'), name
            # No inherited host environment, proxy variables or interpolation.
            for key, value in service.get('environment', {}).items():
                assert isinstance(value, str) and '${' not in value, key
                assert key.lower() not in {'http_proxy', 'https_proxy', 'all_proxy'}
        app = services['open-webui']
        assert app['container_name'] == 'open-webui-ci'
        assert app['build']['context'] == '.'
        assert app['build']['dockerfile'] == 'Dockerfile'
        for dependency in ('postgres', 'weaviate', 'redis', 'stub'):
            assert app['depends_on'][dependency]['condition'] == 'service_healthy'

    def test_empty_search_engine_rejects_before_dispatch(self, compose):
        # Execute only this function's AST, with deferred annotations and no
        # provider functions available. Importing the router would boot the app.
        tree = ast.parse((SERVER / 'routers/retrieval.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'search_web')
        module = ast.Module(
            body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), function],
            type_ignores=[],
        )
        namespace = {}
        exec(compile(ast.fix_missing_locations(module), '<search_web>', 'exec'), namespace)
        engine = compose['services']['open-webui']['environment']['WEB_SEARCH_ENGINE']
        with pytest.raises(Exception, match='No search engine API key found'):
            namespace['search_web'](None, engine, 'CI probe', config=object())

    def test_marker_key_guards_the_public_url_fallback(self, compose):
        from types import SimpleNamespace

        tree = ast.parse((SERVER / 'retrieval/loaders/main.py').read_text())
        conditions = [
            n.test
            for n in ast.walk(tree)
            if isinstance(n, ast.If)
            and any(isinstance(c, ast.Constant) and c.value == 'datalab_marker' for c in ast.walk(n.test))
        ]
        assert len(conditions) == 1
        key = compose['services']['open-webui']['environment']['DATALAB_MARKER_API_KEY']
        scope = {
            'self': SimpleNamespace(engine='datalab_marker', kwargs={'DATALAB_MARKER_API_KEY': key}),
            'file_ext': 'pdf',
        }
        assert not eval(compile(ast.Expression(conditions[0]), '<marker guard>', 'eval'), scope)
