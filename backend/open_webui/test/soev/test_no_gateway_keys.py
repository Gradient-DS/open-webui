"""Runtime source, frontend configuration and chart cannot acquire inference keys."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
# Explicit spellings include scalar/list defaults, dedicated providers, chart
# values/secrets, and persisted configuration keys, not just OPENAI_API_KEY.
PATTERNS = [
    r'\b(?:[A-Z0-9_]*_)?OPENAI_API_KEYS?\b',
    r'\bLITELLM[A-Z0-9_]*\b',
    r'(?i:\blitellm[_-](?:api[_-]key|base[_-]url)\b)',
    r'\b(?:[A-Z0-9_]*_)?OPENAI_API_BASE_URLS?\b',
    r'\b(?:imagesOpenaiApiKey|openaiApiKey|ragOpenaiApiKey|litellmApiKey|litellmBaseUrl)\b',
    r'\b(?:[a-z-]+-)?openai-api-key\b',
    r'\b(?:AUDIO_(?:TTS|STT)_[A-Z0-9_]*API_KEY|DEEPGRAM_API_KEY|COMFYUI_API_KEY|'
    r'IMAGES_[A-Z0-9_]*API_KEY|GEMINI_API_KEY)\b',
    r'(?:openai\.api_keys|rag\.(?:openai|azure_openai|external_reranker)\.api_key|'
    r'audio\.(?:tts|stt)\.[a-z_.]*api_key|image_generation\.[a-z_.]*api_key|images\.edit\.[a-z_.]*api_key)',
]
FORBIDDEN = re.compile('|'.join(PATTERNS))
EXCEPTIONS = {
    'backend/open_webui/migrations/versions/3ff2c63645b8_reshape_config_to_per_key_rows.py': (
        'Historical Alembic migration reads legacy config to preserve upgrade history; no runtime defaults.'
    ),
}


def test_no_gateway_key_configuration_remains():
    violations = []
    roots = [REPO / 'backend', REPO / 'src', REPO / 'helm', REPO / '.env.example']
    for root in roots:
        paths = root.rglob('*') if root.is_dir() else [root]
        for path in paths:
            if not path.is_file() or path.suffix not in {
                '.py',
                '.ts',
                '.svelte',
                '.json',
                '.yaml',
                '.yml',
                '.tpl',
                '.md',
                '.example',
            }:
                continue
            relative = path.relative_to(REPO).as_posix()
            # Tests assert retired settings and use fictional legacy snapshots.
            if '/test/' in relative or relative.endswith(('.test.ts', '.spec.ts')):
                continue
            # Only en-US/nl-NL are maintained in this fork; upstream locale
            # dictionaries retain untranslated labels, never runtime settings.
            if '/i18n/locales/' in relative:
                continue
            if relative in EXCEPTIONS:
                assert EXCEPTIONS[relative]
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if FORBIDDEN.search(line):
                    violations.append(f'{relative}:{number}: {line.strip()}')
    assert not violations, '\n'.join(violations)
