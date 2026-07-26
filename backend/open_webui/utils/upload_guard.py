"""Magic-byte sniffing for the user-upload path.

The extension allow-list in ``upload_file_handler`` only sees the *name* of a
file — it can't tell a renamed Windows binary (``payload.pdf``) from a real
PDF, and it gives extensionless uploads an automatic pass. This module looks at
the actual bytes with libmagic and turns the sniff into an accept/reject
decision:

1. **Executables are always rejected** (in ``log`` and ``enforce`` modes) — the
   one hard stop, even when everything else is only warn-logged.
2. **Uploads with no usable extension** — no filename extension and none the
   caller could derive from a specific content type (a generic
   ``application/octet-stream`` is deliberately left unmapped rather than
   becoming ``.bin``) — are mapped from their sniffed MIME back to a canonical
   extension and admitted only when that extension is allowed (or the allow-list
   is empty). This replaces the old "no extension → always pass". It does not
   fire for uploads that already carry a usable extension (those go to 3).
3. **Extension-present uploads** must have content that is *consistent* with the
   claimed extension; a clear content/extension lie is rejected.

The design is deliberately permissive: it exists to catch executables and clear
lies, not to fight libmagic's gaps. Text-like extensions accept any ``text/*``
(plus a few text-adjacent application types libmagic emits for JSON/XML), OOXML
/ ODF / EPUB containers accept ``application/zip`` and their specific container
MIMEs, and an ``application/octet-stream`` / unfingerprintable sniff on an
extension-bearing file is accepted rather than second-guessed.

Modes (env ``RAG_FILE_SNIFF_MODE``, default ``log``):

* ``off``     — guard fully disabled (escape hatch), no sniffing at all.
* ``log``     — executables rejected; every other failure is warn-logged but
                accepted. Safe to roll out on staging.
* ``enforce`` — executables rejected; extensionless-without-a-match and
                content/extension mismatches are rejected too.

Only ``upload_file_handler`` calls this; cloud-sync ingestion (``integrations``
/ S3) never passes through here.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import magic

log = logging.getLogger(__name__)

# How many bytes libmagic gets to look at. The signatures we care about all
# live in the first few bytes; 64 KiB is plenty and bounds the work per upload.
_SNIFF_BYTES = 65536

MODE_OFF = 'off'
MODE_LOG = 'log'
MODE_ENFORCE = 'enforce'
_VALID_MODES = frozenset({MODE_OFF, MODE_LOG, MODE_ENFORCE})
DEFAULT_MODE = MODE_LOG
_MODE_ENV_VAR = 'RAG_FILE_SNIFF_MODE'

# Executable / loadable-binary MIMEs. These are never accepted (outside off
# mode) regardless of the file's name or extension.
BLOCKED_MIMES = frozenset(
    {
        'application/x-dosexec',  # Windows PE (.exe/.dll)
        'application/x-executable',  # ELF executable
        'application/x-elf',  # ELF (some libmagic builds)
        'application/x-sharedlib',  # ELF shared object
        'application/x-mach-binary',  # macOS Mach-O
        'application/x-pie-executable',  # position-independent ELF
    }
)

# Sniffs we don't trust enough to act on when an extension is present — accept
# and move on. libmagic returns these for plenty of legitimate documents it
# can't fingerprint, and empty files never reach this module (storage rejects
# them first).
_PERMISSIVE_MIMES = frozenset(
    {
        'application/octet-stream',
        'application/x-empty',
        'inode/x-empty',
    }
)

# Extensionless uploads: sniffed MIME -> canonical extension. Only entries here
# can satisfy branch (2); an unmapped sniff is treated as "unknown".
MIME_TO_EXT = {
    'application/pdf': 'pdf',
    'text/html': 'html',
    'application/xhtml+xml': 'html',
    'text/xml': 'xml',
    'application/xml': 'xml',
    'application/json': 'json',
    'text/csv': 'csv',
    'text/tab-separated-values': 'tsv',
    'text/markdown': 'md',
    'text/rtf': 'rtf',
    'application/rtf': 'rtf',
    'text/plain': 'txt',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'pptx',
    'application/vnd.oasis.opendocument.text': 'odt',
    'application/vnd.oasis.opendocument.spreadsheet': 'ods',
    'application/vnd.oasis.opendocument.presentation': 'odp',
    'application/epub+zip': 'epub',
    'application/vnd.ms-outlook': 'msg',
    'application/x-ole-storage': 'msg',
}

# Text-like extensions: any ``text/*`` sniff is consistent. libmagic frequently
# reports these as text/plain (csv/md/txt) or as an application/* text format
# (json/xml), so both are accepted.
_TEXT_EXTS = frozenset(
    {
        'csv',
        'tsv',
        'txt',
        'text',
        'md',
        'markdown',
        'html',
        'htm',
        'xml',
        'json',
        'rst',
    }
)
_TEXT_ADJACENT_MIMES = frozenset(
    {
        'application/json',
        'application/xml',
        'application/xhtml+xml',
        'application/csv',
        'application/x-ndjson',
    }
)

# Zip-container extensions (OOXML / ODF / EPUB): a bare ``application/zip`` sniff
# (older libmagic) or the specific container MIME (newer libmagic) is consistent.
_CONTAINER_EXTS = frozenset(
    {
        'docx',
        'dotx',
        'xlsx',
        'xltx',
        'pptx',
        'potx',
        'odt',
        'ods',
        'odp',
        'epub',
    }
)
_CONTAINER_MIMES = frozenset(
    {
        'application/zip',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.template',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.template',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'application/vnd.openxmlformats-officedocument.presentationml.template',
        'application/vnd.oasis.opendocument.text',
        'application/vnd.oasis.opendocument.spreadsheet',
        'application/vnd.oasis.opendocument.presentation',
        'application/epub+zip',
    }
)

# Remaining extensions with a concrete, non-permissive acceptable-MIME set.
EXT_TO_MIMES = {
    'pdf': frozenset({'application/pdf'}),
    'rtf': frozenset({'application/rtf', 'text/rtf'}),
    'msg': frozenset(
        {
            'application/vnd.ms-outlook',
            'application/x-ole-storage',
            'application/vnd.ms-office',
            'application/CDFV2',
        }
    ),
}


@dataclass(frozen=True)
class GuardResult:
    """Outcome of a sniff check.

    ``reason`` is populated on rejection (used as the HTTP error detail) and,
    in ``log`` mode, on a soft-pass (the failure the guard *would* have rejected
    on in enforce mode) — handy for both log lines and assertions.
    """

    allowed: bool
    reason: str
    sniffed_mime: str


def resolve_mode(mode: str | None = None) -> str:
    """Normalise the sniff mode, falling back to the env var then ``log``.

    Read at call time (not import time) so the mode is cleanly overridable in
    tests and reflects live env changes. An unrecognised value never silently
    disables the guard — it falls back to ``log``.
    """
    if mode is None:
        mode = os.environ.get(_MODE_ENV_VAR, DEFAULT_MODE)
    mode = (mode or DEFAULT_MODE).strip().lower()
    if mode not in _VALID_MODES:
        log.warning('upload_guard: unknown %s=%r; falling back to %s', _MODE_ENV_VAR, mode, DEFAULT_MODE)
        return DEFAULT_MODE
    return mode


def _sniff(contents: bytes) -> str:
    """Return the libmagic MIME for the first ``_SNIFF_BYTES`` bytes, or ''."""
    try:
        return magic.from_buffer(contents[:_SNIFF_BYTES], mime=True) or ''
    except Exception:
        log.exception('upload_guard: libmagic sniff failed; treating content as unknown')
        return ''


def _clean_allowed(allowed_exts: list[str] | None) -> set[str]:
    return {e.strip().lower().lstrip('.') for e in (allowed_exts or []) if e and e.strip()}


def _mime_consistent_with_ext(ext: str, mime: str) -> bool:
    """Is a sniffed ``mime`` acceptable for a file claiming extension ``ext``?"""
    if mime in _PERMISSIVE_MIMES:
        return True
    if ext in _TEXT_EXTS:
        return mime.startswith('text/') or mime in _TEXT_ADJACENT_MIMES
    if ext in _CONTAINER_EXTS:
        return mime in _CONTAINER_MIMES
    acceptable = EXT_TO_MIMES.get(ext)
    if acceptable is not None:
        return mime in acceptable
    # Extension unknown to our tables -> permissive (executables already handled).
    return True


def _decide(mode: str, reason: str, sniffed: str, filename: str) -> GuardResult:
    """Apply mode policy to a branch-2/branch-3 failure."""
    if mode == MODE_ENFORCE:
        log.warning('upload_guard: rejecting %r — %s', filename, reason)
        return GuardResult(False, reason, sniffed)
    # log mode: accept, but record what enforce mode would have blocked.
    log.warning('upload_guard: %r accepted in log mode (would reject in enforce) — %s', filename, reason)
    return GuardResult(True, reason, sniffed)


def check_upload(
    contents: bytes,
    filename: str,
    ext: str,
    allowed_exts: list[str],
    *,
    mode: str | None = None,
) -> GuardResult:
    """Decide whether an upload's real content is acceptable.

    ``ext`` is the lower-cased extension already derived by the caller — '' when
    the upload has no usable one (no filename extension and no extension the
    caller mapped from the content type; a generic application/octet-stream is
    left empty on purpose). An empty ``ext`` routes to branch 2; a non-empty one
    routes to branch 3. ``allowed_exts`` is the configured allow-list (empty
    means "any"). ``mode`` defaults to the ``RAG_FILE_SNIFF_MODE`` env var.
    """
    mode = resolve_mode(mode)
    if mode == MODE_OFF:
        return GuardResult(True, 'sniffing disabled (RAG_FILE_SNIFF_MODE=off)', '')

    sniffed = _sniff(contents)

    # (1) Executables: always rejected — the one hard stop, even in log mode.
    if sniffed in BLOCKED_MIMES:
        reason = f'detected executable content ({sniffed}); executables are never accepted'
        log.warning('upload_guard: blocking executable upload %r (sniffed %s)', filename, sniffed)
        return GuardResult(False, reason, sniffed)

    if not sniffed:
        # libmagic couldn't fingerprint the bytes at all — don't punish that.
        return GuardResult(True, 'content could not be fingerprinted', '')

    ext = (ext or '').strip().lower().lstrip('.')
    allowed = _clean_allowed(allowed_exts)

    # (2) Extensionless upload: the sniff, not an automatic pass, decides.
    if not ext:
        mapped = MIME_TO_EXT.get(sniffed)
        if not allowed or (mapped is not None and mapped in allowed):
            return GuardResult(True, f'extensionless upload sniffed as {sniffed}', sniffed)
        reason = f'detected content type {sniffed} does not map to an allowed file type'
        return _decide(mode, reason, sniffed, filename)

    # (3) Extension present: content must be consistent with the extension.
    if _mime_consistent_with_ext(ext, sniffed):
        return GuardResult(True, f'.{ext} upload sniffed as {sniffed}', sniffed)
    reason = f'detected content type {sniffed} does not match the .{ext} file extension'
    return _decide(mode, reason, sniffed, filename)
