import os

from open_webui.utils.upload_guard import _mime_consistent_with_ext

EXTENSION_MIME: dict[str, str] = {
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

DEFAULT_ALLOWED_EXTENSIONS = list(EXTENSION_MIME)


def content_type_for(filename: str, declared: str | None) -> str:
    ext = os.path.splitext(filename)[1].lstrip('.').lower()
    mime = (declared or '').split(';', 1)[0].strip().lower()
    if mime not in {'', 'application/octet-stream', 'binary/octet-stream'} and _mime_consistent_with_ext(ext, mime):
        return declared
    return EXTENSION_MIME.get(ext) or declared or 'application/octet-stream'
