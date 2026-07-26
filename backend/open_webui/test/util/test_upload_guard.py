"""Magic-byte sniffing upload guard (Phase 1.3).

Two levels of coverage:

* Pure-function tests exercise :func:`check_upload` directly against **real**
  libmagic sniffing (no ``magic`` mocks — the whole point is that the byte
  fingerprint, not a stubbed value, drives the decision).
* Route-level tests drive ``upload_file_handler`` (mirroring the
  ``test_files_image_reject.py`` pattern: monkeypatched ``Config.get`` +
  ``UploadFile`` + ``RAG_FILE_SNIFF_MODE`` via env) to prove the guard is
  actually wired in and that a rejection becomes the expected HTTP error.

The executable fixtures are chosen to sniff reliably on the local libmagic:
``MZ...`` -> ``application/x-dosexec`` and the Mach-O magic ->
``application/x-mach-binary`` (both in ``BLOCKED_MIMES``).
"""

from __future__ import annotations

import io
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, UploadFile, status
from open_webui.routers import files as files_router
from open_webui.routers.files import upload_file_handler
from open_webui.utils.upload_guard import GuardResult, check_upload

# --------------------------------------------------------------------------- #
# Byte fixtures — real content whose libmagic sniff we rely on.
# --------------------------------------------------------------------------- #
PE_BYTES = b'MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00' + b'\x00' * 128
MACHO_BYTES = b'\xcf\xfa\xed\xfe\x07\x00\x00\x01\x03\x00\x00\x00' + b'\x00' * 128
HTML_BYTES = b'<!DOCTYPE html><html><head><title>Report</title></head><body><h1>Hi</h1></body></html>'
CSV_BYTES = b'name,age,city\nalice,30,amsterdam\nbob,25,rotterdam\n'
MD_BYTES = b'# Title\n\nSome **markdown** body with a [link](http://example.com).\n\n- one\n- two\n'
JSON_BYTES = b'{"name": "alice", "age": 30, "roles": ["a", "b"], "active": true}'
PDF_BYTES = b'%PDF-1.4\n1 0 obj<< /Type /Catalog >>endobj\ntrailer<< /Root 1 0 R >>\n%%EOF'
TXT_BYTES = b'just some plain unstructured text with no particular shape to it at all here'


def _docx_bytes() -> bytes:
    """A minimal but structurally-valid OOXML (word) zip container."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types/>')
        z.writestr('_rels/.rels', '<?xml version="1.0"?><Relationships/>')
        z.writestr('word/document.xml', '<?xml version="1.0"?><w:document/>')
    return buf.getvalue()


def _plain_zip_bytes() -> bytes:
    """A generic zip (not an OOXML container) -> sniffs as application/zip."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('a.txt', 'hello')
        z.writestr('b.txt', 'world')
    return buf.getvalue()


DOCX_BYTES = _docx_bytes()
ZIP_BYTES = _plain_zip_bytes()


# ==========================================================================
# Pure-function tests: check_upload against real libmagic
# ==========================================================================

# --- (1) executables: always rejected in log & enforce, never in off -------


@pytest.mark.parametrize('mode', ['log', 'enforce'])
def test_pe_executable_rejected_in_log_and_enforce(mode):
    result = check_upload(PE_BYTES, 'invoice.pdf', 'pdf', ['pdf'], mode=mode)
    assert result.allowed is False
    assert result.sniffed_mime == 'application/x-dosexec'
    assert 'application/x-dosexec' in result.reason


@pytest.mark.parametrize('mode', ['log', 'enforce'])
def test_macho_executable_rejected_in_log_and_enforce(mode):
    result = check_upload(MACHO_BYTES, 'x.txt', 'txt', ['txt'], mode=mode)
    assert result.allowed is False
    assert result.sniffed_mime == 'application/x-mach-binary'


def test_executable_passes_in_off_mode():
    result = check_upload(PE_BYTES, 'invoice.pdf', 'pdf', ['pdf'], mode='off')
    assert result.allowed is True


def test_executable_rejected_even_when_extension_unknown_to_tables():
    # No table entry for .png, but an executable overrides the permissive path.
    result = check_upload(PE_BYTES, 'logo.png', 'png', ['pdf'], mode='enforce')
    assert result.allowed is False
    assert result.sniffed_mime == 'application/x-dosexec'


# --- (2) extensionless: sniff maps to ext, checked against allowlist --------


def test_extensionless_html_accepted_when_html_allowed():
    result = check_upload(HTML_BYTES, 'exported-page', '', ['pdf', 'html'], mode='enforce')
    assert result.allowed is True
    assert result.sniffed_mime == 'text/html'


def test_extensionless_html_rejected_in_enforce_when_no_matching_ext():
    result = check_upload(HTML_BYTES, 'exported-page', '', ['pdf'], mode='enforce')
    assert result.allowed is False
    assert 'text/html' in result.reason


def test_extensionless_html_accepted_with_warning_in_log_mode():
    result = check_upload(HTML_BYTES, 'exported-page', '', ['pdf'], mode='log')
    assert result.allowed is True
    # log mode still surfaces the reason it *would* have rejected on.
    assert 'text/html' in result.reason


def test_extensionless_accepted_when_allowlist_empty():
    result = check_upload(HTML_BYTES, 'exported-page', '', [], mode='enforce')
    assert result.allowed is True


# --- (3) extension present: content/extension consistency ------------------


def test_docx_zip_accepted_as_ooxml():
    result = check_upload(DOCX_BYTES, 'report.docx', 'docx', ['docx'], mode='enforce')
    assert result.allowed is True
    # Accept both the specific OOXML sniff and a bare application/zip
    # (older libmagic in the Debian image reports the latter).
    assert result.sniffed_mime in {
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/zip',
    }


def test_csv_text_plain_accepted_with_matching_extension():
    result = check_upload(CSV_BYTES, 'data.csv', 'csv', ['csv'], mode='enforce')
    assert result.allowed is True


def test_markdown_text_accepted_with_matching_extension():
    result = check_upload(MD_BYTES, 'notes.md', 'md', ['md'], mode='enforce')
    assert result.allowed is True


def test_json_accepted_with_matching_extension():
    # libmagic reports application/json (not text/*) — the text-adjacent set
    # must cover it.
    result = check_upload(JSON_BYTES, 'payload.json', 'json', ['json'], mode='enforce')
    assert result.allowed is True


def test_pdf_accepted_with_matching_extension():
    result = check_upload(PDF_BYTES, 'doc.pdf', 'pdf', ['pdf'], mode='enforce')
    assert result.allowed is True


def test_unknown_extension_treated_permissively():
    # .png is not in the mapping; harmless (non-executable) content -> accept.
    result = check_upload(TXT_BYTES, 'image.png', 'png', ['pdf'], mode='enforce')
    assert result.allowed is True


# --- (3) mismatch: reject in enforce, accept-with-warning in log -----------


def test_pdf_content_named_csv_rejected_in_enforce():
    result = check_upload(PDF_BYTES, 'data.csv', 'csv', ['csv'], mode='enforce')
    assert result.allowed is False
    assert 'application/pdf' in result.reason


def test_pdf_content_named_csv_accepted_with_warning_in_log():
    result = check_upload(PDF_BYTES, 'data.csv', 'csv', ['csv'], mode='log')
    assert result.allowed is True
    assert 'application/pdf' in result.reason


def test_zip_content_named_txt_rejected_in_enforce():
    result = check_upload(ZIP_BYTES, 'notes.txt', 'txt', ['txt'], mode='enforce')
    assert result.allowed is False
    assert 'application/zip' in result.sniffed_mime


def test_zip_content_named_txt_accepted_with_warning_in_log():
    result = check_upload(ZIP_BYTES, 'notes.txt', 'txt', ['txt'], mode='log')
    assert result.allowed is True


# --- mode resolution --------------------------------------------------------


def test_mode_defaults_to_log_from_env(monkeypatch):
    monkeypatch.delenv('RAG_FILE_SNIFF_MODE', raising=False)
    # Default (log) -> executable still hard-stops, mismatch soft-passes.
    assert check_upload(PDF_BYTES, 'data.csv', 'csv', ['csv']).allowed is True
    assert check_upload(PE_BYTES, 'x.pdf', 'pdf', ['pdf']).allowed is False


def test_mode_read_from_env_when_not_passed(monkeypatch):
    monkeypatch.setenv('RAG_FILE_SNIFF_MODE', 'enforce')
    assert check_upload(PDF_BYTES, 'data.csv', 'csv', ['csv']).allowed is False


def test_unknown_mode_falls_back_to_log(monkeypatch):
    monkeypatch.delenv('RAG_FILE_SNIFF_MODE', raising=False)
    # An unrecognised mode must not silently disable the guard.
    assert check_upload(PE_BYTES, 'x.pdf', 'pdf', ['pdf'], mode='bogus').allowed is False
    assert check_upload(PDF_BYTES, 'x.csv', 'csv', ['csv'], mode='bogus').allowed is True


def test_guard_result_shape():
    result = check_upload(PDF_BYTES, 'doc.pdf', 'pdf', ['pdf'], mode='enforce')
    assert isinstance(result, GuardResult)
    assert isinstance(result.allowed, bool)
    assert isinstance(result.reason, str)
    assert isinstance(result.sniffed_mime, str)


# ==========================================================================
# Route-level tests: guard wired into upload_file_handler
# ==========================================================================


def _patch_config(monkeypatch, *, allowed=None, engine='external') -> None:
    values = {
        'rag.content_extraction_engine': engine,
        'audio.stt.supported_content_types': [],
        'rag.file.allowed_extensions': allowed,
        'rag.file.max_size': None,
    }

    async def fake_get(key, default=None):
        return values.get(key, default)

    monkeypatch.setattr(files_router.Config, 'get', staticmethod(fake_get))


def _request() -> MagicMock:
    return MagicMock()


def _user() -> SimpleNamespace:
    return SimpleNamespace(id='u1', email='u@example.com', name='User', role='user')


def _upload(filename: str, content_type: str, content: bytes) -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(content), headers={'content-type': content_type})


async def _run(monkeypatch, *, filename, content_type, content, allowed, mode, process=True):
    monkeypatch.setenv('RAG_FILE_SNIFF_MODE', mode)
    _patch_config(monkeypatch, allowed=allowed)
    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(
            _request(),
            file=_upload(filename, content_type, content),
            process=process,
            process_in_background=False,
            user=_user(),
        )
    return exc_info.value


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['enforce', 'log'])
async def test_route_pe_bytes_named_pdf_rejected(monkeypatch, mode):
    # Executable hard-stop fires in BOTH enforce and log mode.
    exc = await _run(
        monkeypatch,
        filename='invoice.pdf',
        content_type='application/pdf',
        content=PE_BYTES,
        allowed=['pdf'],
        mode=mode,
    )
    assert exc.status_code == status.HTTP_400_BAD_REQUEST
    assert 'application/x-dosexec' in str(exc.detail)


@pytest.mark.asyncio
async def test_route_pe_bytes_pass_in_off_mode(monkeypatch):
    # off disables the guard entirely — the executable is NOT rejected by it
    # (it fails downstream on unmocked Storage/DB instead).
    exc = await _run(
        monkeypatch,
        filename='invoice.pdf',
        content_type='application/pdf',
        content=PE_BYTES,
        allowed=['pdf'],
        mode='off',
    )
    assert 'application/x-dosexec' not in str(exc.detail)


@pytest.mark.asyncio
async def test_route_extensionless_html_accepted_when_html_allowed(monkeypatch):
    # application/x-unknowntype -> mimetypes derives no extension, so this stays
    # extensionless and branch (2) governs it.
    exc = await _run(
        monkeypatch,
        filename='exported-page',
        content_type='application/x-unknowntype',
        content=HTML_BYTES,
        allowed=['pdf', 'html'],
        mode='enforce',
    )
    # Guard accepts -> fails downstream (Storage/DB unmocked), NOT a guard reject.
    assert 'does not map' not in str(exc.detail)


@pytest.mark.asyncio
async def test_route_extensionless_html_rejected_in_enforce_without_match(monkeypatch):
    exc = await _run(
        monkeypatch,
        filename='exported-page',
        content_type='application/x-unknowntype',
        content=HTML_BYTES,
        allowed=['pdf'],
        mode='enforce',
    )
    assert exc.status_code == status.HTTP_400_BAD_REQUEST
    assert 'text/html' in str(exc.detail)


@pytest.mark.asyncio
async def test_route_docx_zip_accepted(monkeypatch):
    exc = await _run(
        monkeypatch,
        filename='report.docx',
        content_type='application/octet-stream',
        content=DOCX_BYTES,
        allowed=['docx'],
        mode='enforce',
    )
    # Consistent OOXML -> guard accepts -> downstream failure, not a guard reject.
    assert 'does not match' not in str(exc.detail)


@pytest.mark.asyncio
async def test_route_zip_named_txt_rejected_in_enforce(monkeypatch):
    exc = await _run(
        monkeypatch,
        filename='notes.txt',
        content_type='text/plain',
        content=ZIP_BYTES,
        allowed=['txt'],
        mode='enforce',
    )
    assert exc.status_code == status.HTTP_400_BAD_REQUEST
    assert 'application/zip' in str(exc.detail)


@pytest.mark.asyncio
async def test_route_zip_named_txt_accepted_in_log(monkeypatch):
    exc = await _run(
        monkeypatch,
        filename='notes.txt',
        content_type='text/plain',
        content=ZIP_BYTES,
        allowed=['txt'],
        mode='log',
    )
    # log mode soft-passes the mismatch -> downstream failure, not a guard reject.
    assert 'does not match' not in str(exc.detail)


# --- process=false must NOT bypass the guard on the user route -------------
# The user route exposes ``process`` as a query param; guarding is decoupled
# from it (sniff_guard defaults True) so a client can't smuggle a binary in by
# opting out of parsing.


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['enforce', 'log'])
async def test_route_pe_bytes_rejected_even_with_process_false(monkeypatch, mode):
    # The executable hard-stop must fire for process=false user uploads too,
    # in both enforce and log mode.
    exc = await _run(
        monkeypatch,
        filename='invoice.pdf',
        content_type='application/pdf',
        content=PE_BYTES,
        allowed=['pdf'],
        mode=mode,
        process=False,
    )
    assert exc.status_code == status.HTTP_400_BAD_REQUEST
    assert 'application/x-dosexec' in str(exc.detail)


@pytest.mark.asyncio
async def test_route_mismatch_rejected_in_enforce_with_process_false(monkeypatch):
    # Full mode semantics apply to process=false user uploads: an enforce-mode
    # content/extension mismatch is rejected regardless of process.
    exc = await _run(
        monkeypatch,
        filename='notes.txt',
        content_type='text/plain',
        content=ZIP_BYTES,
        allowed=['txt'],
        mode='enforce',
        process=False,
    )
    assert exc.status_code == status.HTTP_400_BAD_REQUEST
    assert 'application/zip' in str(exc.detail)


@pytest.mark.asyncio
async def test_route_mismatch_accepted_in_log_with_process_false(monkeypatch):
    # And log mode soft-passes the same mismatch for process=false — identical
    # mode semantics to the process=true path.
    exc = await _run(
        monkeypatch,
        filename='notes.txt',
        content_type='text/plain',
        content=ZIP_BYTES,
        allowed=['txt'],
        mode='log',
        process=False,
    )
    assert 'does not match' not in str(exc.detail)


@pytest.mark.asyncio
async def test_route_internal_sniff_guard_false_skips_guard(monkeypatch):
    # The internal opt-out (sniff_guard=False) that images/audio/agent callers
    # use must NOT sniff — even executable bytes pass the guard (they fail
    # downstream on unmocked Storage/DB instead). This locks the internal
    # callers' behaviour so the fix can't regress them.
    monkeypatch.setenv('RAG_FILE_SNIFF_MODE', 'enforce')
    _patch_config(monkeypatch, allowed=['pdf'])
    with pytest.raises(HTTPException) as exc_info:
        await upload_file_handler(
            _request(),
            file=_upload('invoice.pdf', 'application/pdf', PE_BYTES),
            process=False,
            process_in_background=False,
            user=_user(),
            sniff_guard=False,
        )
    assert 'application/x-dosexec' not in str(exc_info.value.detail)
