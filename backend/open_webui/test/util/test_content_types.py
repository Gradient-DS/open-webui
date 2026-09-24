import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from open_webui.utils.content_types import DEFAULT_ALLOWED_EXTENSIONS, EXTENSION_MIME, content_type_for

EXPECTED_MIME = {
    'pdf': 'application/pdf',
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'dotx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.template',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'xltx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.template',
    'ppt': 'application/vnd.ms-powerpoint',
    'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'potx': 'application/vnd.openxmlformats-officedocument.presentationml.template',
    'csv': 'text/csv',
    'tsv': 'text/tab-separated-values',
    'txt': 'text/plain',
    'text': 'text/plain',
    'md': 'text/markdown',
    'markdown': 'text/markdown',
    'rtf': 'application/rtf',
    'odt': 'application/vnd.oasis.opendocument.text',
    'ods': 'application/vnd.oasis.opendocument.spreadsheet',
    'odp': 'application/vnd.oasis.opendocument.presentation',
    'html': 'text/html',
    'htm': 'text/html',
    'xml': 'application/xml',
    'json': 'application/json',
    'epub': 'application/epub+zip',
    'msg': 'application/vnd.ms-outlook',
    'eml': 'message/rfc822',
    'rst': 'text/x-rst',
}


def test_canonical_formats_and_chart_defaults_match():
    assert EXTENSION_MIME == EXPECTED_MIME
    assert DEFAULT_ALLOWED_EXTENSIONS == list(EXPECTED_MIME)
    root = Path(__file__).resolve().parents[4]
    values = yaml.safe_load((root / 'helm/open-webui-tenant/values.yaml').read_text())
    settings = values['openWebui']['config']
    assert settings['ragAllowedFileExtensions'].split(',') == DEFAULT_ALLOWED_EXTENSIONS
    assert settings['ragFileSniffMode'] == 'enforce'


@pytest.mark.parametrize('ext,mime', EXPECTED_MIME.items())
@pytest.mark.parametrize(
    'declared',
    [None, '', 'application/octet-stream', 'binary/octet-stream', ' Application/Octet-Stream; charset=UTF-8'],
)
def test_missing_or_generic_types_derive_from_extension(ext, mime, declared):
    assert content_type_for(f'/uploads/REPORT.{ext.upper()}', declared) == mime


@pytest.mark.parametrize('ext,mime', EXPECTED_MIME.items())
def test_mismatched_type_uses_extension(ext, mime):
    assert content_type_for(f'report.{ext}', 'image/png') == mime


@pytest.mark.parametrize(
    'ext,declared',
    [
        ('md', 'text/plain; charset=utf-8'),
        ('xml', 'text/xml'),
        ('html', 'application/xhtml+xml'),
        ('rtf', 'text/rtf'),
        ('pdf', 'Application/PDF'),
        ('doc', 'application/msword'),
        ('eml', 'message/rfc822'),
    ],
)
def test_specific_consistent_type_is_preserved(ext, declared):
    assert content_type_for(f'report.{ext}', declared) == declared


@pytest.mark.parametrize('filename', ['download', 'unknown.xyz', 'photo.png'])
@pytest.mark.parametrize('declared', [None, '', 'application/octet-stream', 'image/png'])
def test_unknown_extension_falls_back_to_declared(filename, declared):
    assert content_type_for(filename, declared) == (declared or 'application/octet-stream')


@pytest.mark.parametrize('setting', [None, '', '  , ', 'pdf, eml'])
def test_config_allowlist_default_and_override(setting):
    env = {**os.environ, 'PYTHON_DOTENV_DISABLED': '1'}
    env.pop('RAG_ALLOWED_FILE_EXTENSIONS', None)
    if setting is not None:
        env['RAG_ALLOWED_FILE_EXTENSIONS'] = setting
    result = subprocess.run(
        [
            sys.executable,
            '-c',
            'import json; from open_webui.config import RAG_ALLOWED_FILE_EXTENSIONS; print(json.dumps(RAG_ALLOWED_FILE_EXTENSIONS))',
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    expected = ['pdf', 'eml'] if setting == 'pdf, eml' else DEFAULT_ALLOWED_EXTENSIONS
    assert json.loads(result.stdout.splitlines()[-1]) == expected
