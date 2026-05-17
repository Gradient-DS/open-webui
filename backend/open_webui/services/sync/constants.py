"""Shared constants for cloud sync providers."""

from dataclasses import dataclass
from enum import Enum


class SyncErrorType(str, Enum):
    """Error types for cloud sync failures.

    Mirrors the loader-worker's ``error_code`` enum
    (genai-utils/api/gateway/loader_worker/error_codes.py) — open-webui maps
    each loader-worker code to one of these in
    ``base_worker._LOADER_ERROR_CODE_TO_SYNC_TYPE`` for the failed-files toast.
    """

    TIMEOUT = 'timeout'
    EMPTY_CONTENT = 'empty_content'
    PROCESSING_ERROR = 'processing_error'
    DOWNLOAD_ERROR = 'download_error'
    CONFIG_ERROR = 'config_error'
    SCHEMA_ERROR = 'schema_error'
    NEEDS_TOKEN_REFRESH = 'needs_token_refresh'
    UNSUPPORTED_CONTENT_TYPE = 'unsupported_content_type'
    SOURCE_ACCESS_REVOKED = 'source_access_revoked'


@dataclass
class FailedFile:
    """Represents a file that failed to sync."""

    filename: str
    error_type: str
    error_message: str


# Supported file extensions for processing
SUPPORTED_EXTENSIONS = {
    '.pdf',
    '.doc',
    '.docx',
    '.xls',
    '.xlsx',
    '.ppt',
    '.pptx',
    '.txt',
    '.md',
    '.html',
    '.htm',
    '.json',
    '.xml',
    '.csv',
    '.ifc',
}

# MIME types for supported extensions
CONTENT_TYPES = {
    '.pdf': 'application/pdf',
    '.doc': 'application/msword',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xls': 'application/vnd.ms-excel',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.ppt': 'application/vnd.ms-powerpoint',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.txt': 'text/plain',
    '.md': 'text/markdown',
    '.html': 'text/html',
    '.htm': 'text/html',
    '.json': 'application/json',
    '.xml': 'application/xml',
    '.csv': 'text/csv',
    '.ifc': 'application/x-ifc',
}
