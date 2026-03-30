"""Shared constants for cloud sync providers."""

from dataclasses import dataclass
from enum import Enum


class SyncErrorType(str, Enum):
    """Error types for cloud sync failures."""

    TIMEOUT = 'timeout'
    EMPTY_CONTENT = 'empty_content'
    PROCESSING_ERROR = 'processing_error'
    DOWNLOAD_ERROR = 'download_error'


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
}
