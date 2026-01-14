#!/usr/bin/env python3
"""
LibreChat to Open WebUI Migration Script

This script migrates data from a LibreChat MongoDB backup to Open WebUI.
Run this inside the Open WebUI environment or with access to its database.

Usage:
    # Set environment variables
    export BACKUP_DIR="./librechat-backup-20260113-140000"
    export DATA_DIR="/app/backend/data"  # Open WebUI data directory
    export DATABASE_URL="sqlite:///path/to/webui.db"  # Optional, auto-detected

    # Preview migration (no changes made)
    python restore-to-openwebui.py --dry-run

    # Run actual migration
    python restore-to-openwebui.py

    # Prefer imported passwords for duplicate emails
    python restore-to-openwebui.py --prefer-import-password

Requirements:
    - Python 3.10+
    - SQLAlchemy (only for actual migration, not dry-run)
    - Access to Open WebUI models (only for actual migration)

What gets migrated:
    - Users (email/password only, all set to "user" role)
    - Files (copied to Open WebUI uploads directory)
    - Conversations with full message history
    - Prompts (flattened from PromptGroup + Prompt)
    - Agents (converted to Open WebUI models)
"""
import argparse
import json
import uuid
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

# Global flags
DRY_RUN = False
PREFER_IMPORT_PASSWORD = False

# Configuration - set these via environment variables or edit directly
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "./librechat-backup"))
OPENWEBUI_DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
OPENWEBUI_UPLOAD_DIR = OPENWEBUI_DATA_DIR / "uploads"

# ID mapping tables (populated during migration)
user_id_map: Dict[str, str] = {}  # librechat_id -> openwebui_uuid
file_id_map: Dict[str, str] = {}  # librechat_file_id -> openwebui_file_id


@dataclass
class MigrationStats:
    """Track migration statistics."""
    users: int = 0
    conversations: int = 0
    messages: int = 0
    files: int = 0
    chat_files: int = 0  # Files linked to chat messages
    prompts: int = 0
    agents: int = 0
    errors: List[str] = field(default_factory=list)


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def load_json(filename: str) -> List[dict]:
    """
    Load newline-delimited JSON from mongoexport.

    Args:
        filename: Name of JSON file in BACKUP_DIR/data/

    Returns:
        List of parsed documents
    """
    filepath = BACKUP_DIR / "data" / filename
    if not filepath.exists():
        print(f"  Warning: {filename} not found, skipping")
        return []

    documents = []
    with open(filepath, encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                documents.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  Warning: Failed to parse line {line_num} in {filename}: {e}")

    return documents


def parse_mongo_date(date_obj) -> int:
    """
    Convert MongoDB date to Unix epoch (seconds).

    Handles various MongoDB date formats:
    - {"$date": "2026-01-13T14:00:00Z"}
    - {"$date": 1736776800000}
    - Python datetime object
    """
    if date_obj is None:
        return int(datetime.utcnow().timestamp())

    if isinstance(date_obj, dict) and "$date" in date_obj:
        date_value = date_obj["$date"]
        if isinstance(date_value, str):
            # ISO format string
            try:
                dt = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
                return int(dt.timestamp())
            except ValueError:
                return int(datetime.utcnow().timestamp())
        elif isinstance(date_value, (int, float)):
            # Milliseconds since epoch
            return int(date_value // 1000)

    if isinstance(date_obj, datetime):
        return int(date_obj.timestamp())

    return int(datetime.utcnow().timestamp())


def get_mongo_id(obj) -> str:
    """
    Extract string ID from MongoDB ObjectId.

    Handles:
    - {"$oid": "507f1f77bcf86cd799439011"}
    - Plain string
    """
    if obj is None:
        return ""
    if isinstance(obj, dict) and "$oid" in obj:
        return obj["$oid"]
    return str(obj)


# =============================================================================
# CITATION CONVERSION
# =============================================================================

# LibreChat citation patterns:
# \ue200 - Start of citation block (Unicode private use area)
# \ue201 - End of citation block
# \ue202turnXfileY - Reference to file Y in turn X
# \ue202turnXsearchY - Reference to search result Y in turn X
# Note: These are actual Unicode characters, not escaped strings

CITATION_MARKER = '\ue202'  # Unicode private use area character
CITATION_BLOCK_START = '\ue200'
CITATION_BLOCK_END = '\ue201'

# Pattern matches both actual Unicode and escaped versions (from JSON)
CITATION_PATTERN = re.compile(
    r'(?:\ue202|\\ue202)turn(\d+)(file|search)(\d+)'
)


def extract_sources_from_attachments(attachments: List[dict]) -> Tuple[List[dict], List[dict]]:
    """
    Extract file and search sources from LibreChat message attachments.

    Args:
        attachments: List of attachment objects from LibreChat message

    Returns:
        Tuple of (file_sources, search_sources) where each is a list of source dicts
        containing: name, url (for search), fileId (for files), content snippet
    """
    file_sources = []
    search_sources = []

    for attachment in attachments:
        att_type = attachment.get("type", "")

        if att_type == "file_search":
            # File search results
            file_search = attachment.get("file_search", {})
            for source in file_search.get("sources", []):
                file_sources.append({
                    "name": source.get("fileName", "Unknown File"),
                    "fileId": source.get("fileId"),
                    "content": source.get("content", "")[:200],  # Preview
                    "relevance": source.get("relevance", 0),
                    "pages": source.get("pages", []),
                })

        elif att_type == "web_search":
            # Web search results
            web_search = attachment.get("web_search", {})
            for result in web_search.get("organic", []):
                search_sources.append({
                    "name": result.get("title", "Search Result"),
                    "url": result.get("link", ""),
                    "snippet": result.get("snippet", ""),
                })

    return file_sources, search_sources


def convert_librechat_citations(content: str, file_sources: List[dict], search_sources: List[dict]) -> Tuple[str, List[dict]]:
    """
    Convert LibreChat citation format to Open WebUI format.

    LibreChat format: \ue202turn0file0, \ue202turn0search1
    Open WebUI format: [1], [2], [3]

    Args:
        content: Message content with LibreChat citations
        file_sources: List of file sources extracted from attachments
        search_sources: List of search sources extracted from attachments

    Returns:
        Tuple of (converted_content, sources) where sources is the list of
        Open WebUI source objects for citation display
    """
    if not content:
        return content, []

    # Build mapping from citation reference to index
    # Open WebUI uses 1-based indexing for citations
    sources = []  # Open WebUI sources array
    citation_map = {}  # (type, index) -> owui_index

    # First pass: find all unique citations and build source list
    matches = list(CITATION_PATTERN.finditer(content))
    for match in matches:
        turn = int(match.group(1))
        ref_type = match.group(2)  # 'file' or 'search'
        ref_index = int(match.group(3))

        key = (ref_type, ref_index)
        if key not in citation_map:
            # Build Open WebUI source object
            if ref_type == 'file' and ref_index < len(file_sources):
                src = file_sources[ref_index]
                new_file_id = file_id_map.get(src.get("fileId"))
                source_obj = {
                    "source": {
                        "id": new_file_id or src.get("fileId", ""),
                        "name": src.get("name", f"File {ref_index + 1}"),
                        "type": "file",
                    },
                    "document": [src.get("content", "")[:500] if src.get("content") else ""],
                    "metadata": [{
                        "file_id": new_file_id or src.get("fileId", ""),
                        "name": src.get("name", ""),
                        "source": src.get("name", ""),
                        "pages": src.get("pages", []),
                    }],
                }
                sources.append(source_obj)
            elif ref_type == 'search' and ref_index < len(search_sources):
                src = search_sources[ref_index]
                url = src.get("url", "")
                source_obj = {
                    "source": {
                        "id": url,
                        "name": url or src.get("name", f"Search {ref_index + 1}"),
                        "url": url,
                        "type": "url",
                    },
                    "document": [src.get("snippet", "")],
                    "metadata": [{
                        "source": url,
                        "name": src.get("name", ""),
                        "url": url,
                    }],
                }
                sources.append(source_obj)
            else:
                # Placeholder source
                source_obj = {
                    "source": {
                        "id": f"source-{len(sources) + 1}",
                        "name": f"Source {len(sources) + 1}",
                        "type": "unknown",
                    },
                    "document": [""],
                    "metadata": [{}],
                }
                sources.append(source_obj)

            citation_map[key] = len(sources)  # 1-based index

    # Second pass: replace citations with Open WebUI format
    def replace_citation(match):
        ref_type = match.group(2)
        ref_index = int(match.group(3))
        key = (ref_type, ref_index)
        owui_index = citation_map.get(key, 1)
        return f"[{owui_index}]"

    converted = CITATION_PATTERN.sub(replace_citation, content)

    # Remove citation block markers
    converted = converted.replace(CITATION_BLOCK_START, '')
    converted = converted.replace(CITATION_BLOCK_END, '')

    # Also handle escaped versions
    converted = converted.replace('\\ue200', '')
    converted = converted.replace('\\ue201', '')

    return converted, sources


# =============================================================================
# MODEL MAPPING CONFIGURATION
# =============================================================================

# Default fallback model when no mapping exists
# Set this to a model that's guaranteed to be available in your Open WebUI instance
DEFAULT_BASE_MODEL = os.getenv("MIGRATION_DEFAULT_MODEL", "gpt-4o")

# Model name mapping for LibreChat provider+model -> Open WebUI base_model_id
# Models not in this mapping will use DEFAULT_BASE_MODEL
MODEL_MAPPING = {
    # OpenAI models
    ("openAI", "gpt-4"): "gpt-4",
    ("openAI", "gpt-4-turbo"): "gpt-4-turbo",
    ("openAI", "gpt-4-turbo-preview"): "gpt-4-turbo",
    ("openAI", "gpt-4o"): "gpt-4o",
    ("openAI", "gpt-4o-mini"): "gpt-4o-mini",
    ("openAI", "gpt-3.5-turbo"): "gpt-3.5-turbo",
    # Anthropic models
    ("anthropic", "claude-3-opus-20240229"): "claude-3-opus",
    ("anthropic", "claude-3-sonnet-20240229"): "claude-3-sonnet",
    ("anthropic", "claude-3-haiku-20240307"): "claude-3-haiku",
    ("anthropic", "claude-3-5-sonnet-20240620"): "claude-3.5-sonnet",
    ("anthropic", "claude-3-5-sonnet-20241022"): "claude-3.5-sonnet",
    # Add more mappings as needed for your available models
}


def strip_provider_suffix(model: str) -> str:
    """
    Strip provider suffixes like :groq, :openai from model IDs.

    LibreChat model IDs sometimes include provider routing suffixes
    (e.g., "openai/gpt-oss-120b:groq") that need to be stripped.

    Args:
        model: Model name potentially with suffix

    Returns:
        Model name with provider suffix stripped
    """
    # Common provider suffixes
    suffixes = [":groq", ":openai", ":anthropic", ":azure", ":together"]
    for suffix in suffixes:
        if model.endswith(suffix):
            return model[:-len(suffix)]
    return model


def resolve_base_model_id(provider: str, model: str) -> tuple:
    """
    Resolve LibreChat provider+model to Open WebUI base_model_id.

    Args:
        provider: LibreChat provider (e.g., "openAI", "anthropic")
        model: Model name (e.g., "gpt-4o", "claude-3-5-sonnet-20241022")

    Returns:
        Tuple of (base_model_id, used_fallback)
    """
    # Strip provider suffixes first (e.g., :groq, :openai)
    model = strip_provider_suffix(model)

    # Try exact match first
    key = (provider, model)
    if key in MODEL_MAPPING:
        return MODEL_MAPPING[key], False

    # Try with lowercase provider
    key = (provider.lower(), model)
    if key in MODEL_MAPPING:
        return MODEL_MAPPING[key], False

    # Return the cleaned model name directly as fallback (not the default model)
    # This allows models like "openai/gpt-oss-120b" to be used directly
    return model, True


def map_librechat_tools_to_capabilities(tools: List[str]) -> dict:
    """
    Map LibreChat tool names to Open WebUI capability flags.

    Args:
        tools: List of LibreChat tool names

    Returns:
        Dict of Open WebUI capability flags
    """
    capabilities = {
        "vision": True,  # Default to True, base model will validate
        "file_upload": False,
        "web_search": False,
        "image_generation": False,
        "code_interpreter": False,
        "citations": True,
        "status_updates": True,
        "usage": False,
    }

    tool_capability_map = {
        "file_search": "file_upload",
        "retrieval": "file_upload",
        "web_search": "web_search",
        "code_interpreter": "code_interpreter",
        "execute_code": "code_interpreter",
    }

    for tool in tools:
        if tool in tool_capability_map:
            capabilities[tool_capability_map[tool]] = True

    return capabilities


# =============================================================================
# USER MIGRATION
# =============================================================================

def migrate_users(session) -> int:
    """
    Migrate users from LibreChat to Open WebUI.

    - Email/password users only (OAuth skipped)
    - All users set to "user" role
    - Passwords are bcrypt hashes - direct copy

    Returns:
        Number of users migrated
    """
    from open_webui.models.users import User
    from open_webui.models.auths import Auth

    users = load_json("users.json")
    count = 0

    for user in users:
        old_id = get_mongo_id(user.get("_id"))
        new_id = str(uuid.uuid4())
        user_id_map[old_id] = new_id

        email = user.get("email", "").lower().strip()
        if not email:
            print(f"  Skipping user without email: {old_id}")
            continue

        # Check for existing user by email
        existing = session.query(User).filter(User.email == email).first()
        if existing:
            if PREFER_IMPORT_PASSWORD and user.get("password"):
                # Update existing Auth record with LibreChat password
                existing_auth = session.query(Auth).filter(Auth.id == existing.id).first()
                if existing_auth:
                    existing_auth.password = user.get("password")
                    print(f"  Updated password for existing user: {email}")
                else:
                    print(f"  Skipping duplicate (no auth record): {email}")
            else:
                print(f"  Skipping duplicate email: {email}")
            user_id_map[old_id] = existing.id  # Map to existing user
            continue

        # Create Auth record (password is bcrypt - direct copy)
        auth = Auth(
            id=new_id,
            email=email,
            password=user.get("password", ""),
            active=True,
        )

        # Create User record
        owui_user = User(
            id=new_id,
            email=email,
            username=user.get("username"),
            role="user",  # Force all to user role per requirement
            name=user.get("name") or user.get("username") or email.split("@")[0],
            profile_image_url=user.get("avatar") or "/user.png",
            settings={},
            oauth=None,  # No OAuth per requirement
            last_active_at=int(datetime.utcnow().timestamp()),
            created_at=parse_mongo_date(user.get("createdAt")),
            updated_at=parse_mongo_date(user.get("updatedAt")),
        )

        session.add(auth)
        session.add(owui_user)
        count += 1

    return count


# =============================================================================
# FILE MIGRATION
# =============================================================================

def migrate_files(session) -> int:
    """
    Migrate files from LibreChat to Open WebUI.

    - Copies physical files to Open WebUI uploads directory
    - Creates database records
    - Skips non-local files (S3, OpenAI, etc.)

    Returns:
        Number of files migrated
    """
    from open_webui.models.files import Files, FileForm

    files = load_json("files.json")
    count = 0

    OPENWEBUI_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    for file_doc in files:
        old_file_id = file_doc.get("file_id")
        old_user_id = get_mongo_id(file_doc.get("user"))
        new_user_id = user_id_map.get(old_user_id)

        if not new_user_id:
            print(f"  Skipping file for unknown user: {old_user_id}")
            continue
        if not old_file_id:
            continue

        # Skip non-local files (S3, OpenAI, etc.)
        source = file_doc.get("source", "local")
        if source not in ("local", "", None):
            print(f"  Skipping non-local file: {old_file_id} (source: {source})")
            continue

        # Find physical file in backup
        old_filepath = file_doc.get("filepath", "")
        filename = file_doc.get("filename", "unknown")

        # LibreChat paths: /uploads/{userId}/{file_id}__{filename}
        # or /images/{userId}/{file_id}__{filename}
        source_file = None

        # Try to find file based on filepath
        if old_filepath.startswith("/uploads/"):
            relative_path = old_filepath.lstrip("/uploads/")
            source_file = BACKUP_DIR / "files" / "uploads" / relative_path
        elif old_filepath.startswith("/images/"):
            relative_path = old_filepath.lstrip("/images/")
            source_file = BACKUP_DIR / "files" / "images" / relative_path

        # Try alternative path constructions if not found
        if not source_file or not source_file.exists():
            for subdir in ["uploads", "images"]:
                alt_path = BACKUP_DIR / "files" / subdir / old_user_id / f"{old_file_id}__{filename}"
                if alt_path.exists():
                    source_file = alt_path
                    break
                # Also try without double underscore
                alt_path = BACKUP_DIR / "files" / subdir / old_user_id / filename
                if alt_path.exists():
                    source_file = alt_path
                    break

        if not source_file or not source_file.exists():
            print(f"  Skipping file not found in backup: {old_file_id} ({filename})")
            continue

        # Create new file in Open WebUI format
        new_file_id = str(uuid.uuid4())
        file_id_map[old_file_id] = new_file_id
        new_filename = f"{new_file_id}_{filename}"
        new_path = OPENWEBUI_UPLOAD_DIR / new_filename

        # Copy file
        try:
            shutil.copy2(source_file, new_path)
        except Exception as e:
            print(f"  Failed to copy file {old_file_id}: {e}")
            continue

        # Create DB record
        try:
            Files.insert_new_file(
                new_user_id,
                FileForm(
                    id=new_file_id,
                    filename=filename,
                    path=str(new_path),
                    data={},
                    meta={
                        "name": filename,
                        "content_type": file_doc.get("type", "application/octet-stream"),
                        "size": file_doc.get("bytes", 0),
                    },
                ),
            )
            count += 1
        except Exception as e:
            print(f"  Failed to create file record {old_file_id}: {e}")
            # Clean up copied file
            if new_path.exists():
                new_path.unlink()

    return count


def reingest_files(base_url: str = "http://localhost:8080") -> Tuple[int, int]:
    """
    Re-ingest migrated files through Open WebUI's retrieval pipeline.

    This function calls the /api/v1/retrieval/process/file endpoint for each
    migrated file to extract text content and populate the vector database.

    Note: This requires the Open WebUI server to be running and accessible.
    Run this AFTER the migration is complete and the server is started.

    Args:
        base_url: Open WebUI server URL (default: http://localhost:8080)

    Returns:
        Tuple of (success_count, error_count)
    """
    import requests
    from open_webui.models.files import Files
    from open_webui.models.auths import Auths

    print("\nRe-ingesting files through retrieval pipeline...")
    print(f"Using server: {base_url}")

    success_count = 0
    error_count = 0

    # Get all files that were migrated (have empty data field)
    all_files = Files.get_files()

    # Group files by user for token generation
    files_by_user = {}
    for file in all_files:
        if not file.data or not file.data.get("content"):
            user_id = file.user_id
            if user_id not in files_by_user:
                files_by_user[user_id] = []
            files_by_user[user_id].append(file)

    total_files = sum(len(files) for files in files_by_user.values())
    print(f"Found {total_files} files to process across {len(files_by_user)} users")

    # Process files for each user
    for user_id, user_files in files_by_user.items():
        # Generate a token for this user
        try:
            token = Auths.create_token(user_id)
        except Exception as e:
            print(f"  Failed to create token for user {user_id}: {e}")
            error_count += len(user_files)
            continue

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        for file in user_files:
            try:
                response = requests.post(
                    f"{base_url}/api/v1/retrieval/process/file",
                    headers=headers,
                    json={"file_id": file.id},
                    timeout=120,  # Allow 2 minutes per file
                )

                if response.ok:
                    success_count += 1
                    print(f"  Processed: {file.filename}")
                else:
                    error_count += 1
                    print(f"  Failed ({response.status_code}): {file.filename} - {response.text[:100]}")

            except requests.exceptions.Timeout:
                error_count += 1
                print(f"  Timeout: {file.filename}")
            except Exception as e:
                error_count += 1
                print(f"  Error: {file.filename} - {e}")

    return success_count, error_count


def reingest_files_direct() -> Tuple[int, int]:
    """
    Re-ingest migrated files directly through Open WebUI's internal APIs.

    This function bypasses the HTTP endpoint and calls the file processing
    logic directly. Use this when running inside the Open WebUI container
    or when the HTTP server is not available.

    Returns:
        Tuple of (success_count, error_count)
    """
    from open_webui.models.files import Files, FileUpdateForm
    from open_webui.retrieval.loaders.main import Loader
    from open_webui.config import CONTENT_EXTRACTION_ENGINE
    import logging

    log = logging.getLogger(__name__)

    print("\nRe-ingesting files directly (no HTTP)...")

    success_count = 0
    error_count = 0

    # Get all files that need processing (empty data field)
    all_files = Files.get_files()
    files_to_process = [f for f in all_files if not f.data or not f.data.get("content")]

    print(f"Found {len(files_to_process)} files to process")

    for file in files_to_process:
        try:
            if not file.path or not Path(file.path).exists():
                print(f"  Skipping (file not found): {file.filename}")
                error_count += 1
                continue

            # Use basic loader for text extraction
            loader = Loader(engine=CONTENT_EXTRACTION_ENGINE)
            docs = loader.load(
                file.filename,
                file.meta.get("content_type", "application/octet-stream"),
                file.path,
            )

            if docs:
                # Combine all document content
                content = "\n\n".join(doc.page_content for doc in docs if doc.page_content)

                # Update file with extracted content
                Files.update_file_data_by_id(
                    file.id,
                    {"content": content},
                )

                success_count += 1
                print(f"  Processed: {file.filename} ({len(content)} chars)")
            else:
                print(f"  No content extracted: {file.filename}")
                error_count += 1

        except Exception as e:
            error_count += 1
            print(f"  Error: {file.filename} - {e}")
            log.exception(f"Failed to process file {file.id}")

    return success_count, error_count


# =============================================================================
# CONVERSATION/CHAT MIGRATION
# =============================================================================

def extract_message_content(msg: dict) -> str:
    """
    Extract message content from LibreChat message.

    LibreChat stores:
    - User messages: text field contains the message
    - Assistant messages: content[] array with {type: "text", text: "..."} blocks
      (text field is often empty for assistant messages)

    Args:
        msg: LibreChat message document

    Returns:
        The extracted message text
    """
    # For user messages, use text field directly
    if msg.get("isCreatedByUser"):
        return msg.get("text", "")

    # For assistant messages, check content array first
    content_array = msg.get("content", [])
    if content_array and isinstance(content_array, list):
        text_parts = []
        for block in content_array:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        if text_parts:
            return "\n\n".join(text_parts)

    # Fall back to text field
    return msg.get("text", "")


def build_message_tree(messages: List[dict]) -> Tuple[dict, List[Tuple[str, str, List[str]]]]:
    """
    Build Open WebUI message structure from LibreChat messages.

    LibreChat stores messages as separate documents with parentMessageId.
    Open WebUI stores them as a nested dict with childrenIds.

    Args:
        messages: List of LibreChat message documents

    Returns:
        Tuple of:
        - Dict of message_id -> message object for Open WebUI chat.history.messages
        - List of (message_id, user_id, file_ids) tuples for chat_file linking
    """
    messages_map = {}
    children_map = {}
    chat_file_links = []  # (message_id, user_id, [file_ids])

    # First pass: create message objects
    for msg in messages:
        msg_id = msg.get("messageId")
        if not msg_id:
            continue

        parent_id = msg.get("parentMessageId")
        # Track children for second pass
        if parent_id and parent_id != "00000000-0000-0000-0000-000000000000":
            if parent_id not in children_map:
                children_map[parent_id] = []
            children_map[parent_id].append(msg_id)

        # Map files to Open WebUI format
        files = []
        linked_file_ids = []
        for file_ref in msg.get("files", []):
            old_file_id = file_ref.get("file_id")
            new_file_id = file_id_map.get(old_file_id)
            if new_file_id:
                files.append({
                    "type": "file",
                    "id": new_file_id,
                    "name": file_ref.get("filename", ""),
                    "url": new_file_id,
                    "content_type": file_ref.get("type", ""),
                    "size": file_ref.get("bytes", 0),
                    "status": "uploaded",
                })
                linked_file_ids.append(new_file_id)

        # Extract content properly (handles assistant message content array)
        message_content = extract_message_content(msg)

        # Extract sources from attachments and convert citations
        attachments = msg.get("attachments", [])
        sources = []
        if attachments and not msg.get("isCreatedByUser"):
            file_sources, search_sources = extract_sources_from_attachments(attachments)
            message_content, sources = convert_librechat_citations(
                message_content, file_sources, search_sources
            )

            # Also track file IDs from file_search sources for chat_file linking
            for source in file_sources:
                old_file_id = source.get("fileId")
                if old_file_id:
                    new_file_id = file_id_map.get(old_file_id)
                    if new_file_id and new_file_id not in linked_file_ids:
                        linked_file_ids.append(new_file_id)

        # Strip provider suffix from model ID (e.g., :groq, :openai)
        model_id = msg.get("model")
        if model_id:
            model_id = strip_provider_suffix(model_id)

        messages_map[msg_id] = {
            "id": msg_id,
            "role": "user" if msg.get("isCreatedByUser") else "assistant",
            "content": message_content,
            "parentId": parent_id if parent_id != "00000000-0000-0000-0000-000000000000" else None,
            "childrenIds": [],
            "timestamp": parse_mongo_date(msg.get("createdAt")),
            "model": model_id,
        }

        # Add sources for citation display (only for assistant messages with sources)
        # Open WebUI uses message.sources array with {source, document, metadata} objects
        if sources:
            messages_map[msg_id]["sources"] = sources

        # Only add files if there are any
        if files:
            messages_map[msg_id]["files"] = files

        # Track files for chat_file linking
        if linked_file_ids:
            old_user_id = get_mongo_id(msg.get("user"))
            new_user_id = user_id_map.get(old_user_id)
            if new_user_id:
                chat_file_links.append((msg_id, new_user_id, linked_file_ids))

    # Second pass: populate children
    for msg_id, children in children_map.items():
        if msg_id in messages_map:
            messages_map[msg_id]["childrenIds"] = children

    return messages_map, chat_file_links


def migrate_conversations(session) -> Tuple[int, int]:
    """
    Migrate conversations with their messages.

    LibreChat stores conversations and messages as separate collections.
    Open WebUI stores them as a single JSON structure in the chat column.

    Returns:
        Tuple of (conversations_migrated, chat_files_linked)
    """
    from open_webui.models.chats import Chat, ChatFile
    from open_webui.models.tags import Tags

    conversations = load_json("conversations.json")
    messages = load_json("messages.json")
    count = 0
    chat_file_count = 0

    # Group messages by conversation
    messages_by_convo = {}
    for msg in messages:
        cid = msg.get("conversationId")
        if cid:
            if cid not in messages_by_convo:
                messages_by_convo[cid] = []
            messages_by_convo[cid].append(msg)

    for convo in conversations:
        convo_id = convo.get("conversationId")
        old_user_id = get_mongo_id(convo.get("user"))
        new_user_id = user_id_map.get(old_user_id)

        if not new_user_id:
            print(f"  Skipping conversation for unknown user: {old_user_id}")
            continue
        if not convo_id:
            continue

        convo_messages = messages_by_convo.get(convo_id, [])
        messages_map, chat_file_links = build_message_tree(convo_messages)

        # Find current (last) message
        current_id = None
        if messages_map:
            current_id = max(
                messages_map.keys(),
                key=lambda k: messages_map[k].get("timestamp", 0),
            )

        # Convert tags to Open WebUI format (ID-based)
        tag_ids = []
        for tag_name in convo.get("tags", []):
            tag_id = tag_name.replace(" ", "_").lower()
            tag_ids.append(tag_id)
            # Create tag if needed
            try:
                Tags.insert_new_tag(tag_name, new_user_id)
            except Exception:
                pass  # Tag may already exist

        # Get the model from the conversation or from the last message
        chat_model = None
        if convo.get("model"):
            chat_model = strip_provider_suffix(convo.get("model"))
        elif current_id and messages_map.get(current_id, {}).get("model"):
            chat_model = messages_map[current_id]["model"]

        new_chat_id = str(uuid.uuid4())
        chat_content = {
            "history": {
                "currentId": current_id,
                "messages": messages_map,
            }
        }
        # Add models field at chat level for model selector
        if chat_model:
            chat_content["models"] = [chat_model]

        chat = Chat(
            id=new_chat_id,
            user_id=new_user_id,
            title=convo.get("title", "New Chat"),
            chat=chat_content,
            created_at=parse_mongo_date(convo.get("createdAt")),
            updated_at=parse_mongo_date(convo.get("updatedAt")),
            archived=convo.get("isArchived", False),
            pinned=False,
            meta={"tags": tag_ids} if tag_ids else {},
            folder_id=None,
        )

        session.add(chat)

        # Create chat_file entries to link files to messages
        # Note: There's a unique constraint on (chat_id, file_id), so we track seen pairs
        now = int(datetime.utcnow().timestamp())
        seen_chat_files = set()
        for msg_id, user_id, file_ids in chat_file_links:
            for file_id in file_ids:
                # Skip if we've already linked this file to this chat
                pair_key = (new_chat_id, file_id)
                if pair_key in seen_chat_files:
                    continue
                seen_chat_files.add(pair_key)

                try:
                    chat_file = ChatFile(
                        id=str(uuid.uuid4()),
                        user_id=user_id,
                        chat_id=new_chat_id,
                        file_id=file_id,
                        message_id=msg_id,  # First message that uses this file
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(chat_file)
                    chat_file_count += 1
                except Exception as e:
                    print(f"  Warning: Failed to link file {file_id} to chat {new_chat_id}: {e}")

        count += 1

    return count, chat_file_count


# =============================================================================
# PROMPT MIGRATION
# =============================================================================

def migrate_prompts(session) -> int:
    """
    Migrate prompts from LibreChat to Open WebUI.

    LibreChat uses PromptGroup + Prompt (versioned).
    Open WebUI uses a single Prompt table (command-keyed globally).
    Migration uses the production/active version.

    Note: In Open WebUI, the 'command' field is the primary key (not per-user).
    If multiple users have prompts with the same command, we add a suffix to
    make them unique.

    Returns:
        Number of prompts migrated
    """
    from open_webui.models.prompts import Prompts, PromptForm

    prompt_groups = load_json("promptgroups.json")
    prompts = load_json("prompts.json")
    count = 0
    used_commands = set()  # Track commands we've already used

    # Check for existing commands in the database
    # Note: Open WebUI stores commands WITH leading / prefix, but we track without for uniqueness
    try:
        existing_prompts = Prompts.get_prompts()
        for p in existing_prompts:
            # Strip leading / to match our uniqueness tracking format
            cmd = p.command.lstrip("/") if p.command else ""
            if cmd:
                used_commands.add(cmd)
    except Exception:
        pass  # If we can't read existing prompts, proceed anyway

    # Index prompts by groupId
    prompts_by_group = {}
    for prompt in prompts:
        group_id = get_mongo_id(prompt.get("groupId"))
        if group_id not in prompts_by_group:
            prompts_by_group[group_id] = []
        prompts_by_group[group_id].append(prompt)

    for group in prompt_groups:
        group_id = get_mongo_id(group.get("_id"))
        # Author might be a populated reference (dict with _id) or a simple ObjectID
        author_ref = group.get("author")
        if isinstance(author_ref, dict) and "_id" in author_ref:
            old_user_id = get_mongo_id(author_ref.get("_id"))
        else:
            old_user_id = get_mongo_id(author_ref)
        new_user_id = user_id_map.get(old_user_id)

        if not new_user_id:
            continue

        # Get production prompt (or first available)
        group_prompts = prompts_by_group.get(group_id, [])
        prod_id = get_mongo_id(group.get("productionId")) if group.get("productionId") else None
        active_prompt = None

        for p in group_prompts:
            if prod_id and get_mongo_id(p.get("_id")) == prod_id:
                active_prompt = p
                break
        if not active_prompt and group_prompts:
            active_prompt = group_prompts[0]

        if not active_prompt:
            continue

        # Generate unique command
        base_command = group.get("command", "").strip("/")
        if not base_command:
            base_command = group.get("name", "").replace(" ", "_").lower()

        # Sanitize command (remove special characters, keep alphanumeric and underscore)
        base_command = re.sub(r'[^a-zA-Z0-9_-]', '_', base_command)
        base_command = re.sub(r'_+', '_', base_command).strip('_')  # Collapse multiple underscores

        if not base_command:
            base_command = f"prompt_{group_id[:8]}"

        # Ensure uniqueness by adding suffix if needed
        command = base_command
        suffix = 1
        while command in used_commands:
            command = f"{base_command}_{suffix}"
            suffix += 1

        used_commands.add(command)

        try:
            # Open WebUI stores commands WITH leading / prefix
            db_command = f"/{command}"
            Prompts.insert_new_prompt(
                new_user_id,
                PromptForm(
                    command=db_command,
                    title=group.get("name", command),
                    content=active_prompt.get("prompt", ""),
                    access_control={},  # Private - owner only
                ),
            )
            count += 1
            if command != base_command:
                print(f"  Renamed prompt '{base_command}' -> '{command}' (conflict)")
        except Exception as e:
            print(f"  Failed to migrate prompt '{command}': {e}")

    return count


# =============================================================================
# AGENT TO MODEL MIGRATION
# =============================================================================

def migrate_agents_to_models(session, fallback_user_id: str) -> tuple:
    """
    Migrate LibreChat agents to Open WebUI models.

    Args:
        session: SQLAlchemy session
        fallback_user_id: User ID to own models when original owner can't be found

    Returns:
        Tuple of (count, fallbacks) where fallbacks is list of
        (name, original_model, fallback_model) for agents that used fallback
    """
    from open_webui.models.models import Model

    agents = load_json("agents.json")
    count = 0
    skipped = []
    fallbacks = []

    for agent in agents:
        agent_id = agent.get("id")
        name = agent.get("name", "Unnamed Agent")
        provider = agent.get("provider", "openAI")
        model_name = agent.get("model", "")
        instructions = agent.get("instructions", "").strip()
        description = agent.get("description", "").strip()

        # Get the agent's owner from LibreChat
        # LibreChat stores owner as either 'author' or 'user' field
        # The author/user might be a populated reference (dict with _id) or a simple ObjectID
        owner_ref = agent.get("author") or agent.get("user")
        if isinstance(owner_ref, dict) and "_id" in owner_ref:
            # Populated reference: {_id: {...}, name: ..., email: ...}
            old_owner_id = get_mongo_id(owner_ref.get("_id"))
        else:
            # Simple ObjectID reference
            old_owner_id = get_mongo_id(owner_ref)
        new_owner_id = user_id_map.get(old_owner_id)
        if not new_owner_id:
            # Owner not found in user mapping, use fallback
            new_owner_id = fallback_user_id
            print(f"  Warning: Agent '{name}' owner not found (old_id={old_owner_id}), using fallback user")

        if not model_name:
            skipped.append((name, "No model specified"))
            continue

        # Skip auto-generated model aliases (agents with no real customization)
        # These are created when users select a model in LibreChat
        has_customization = (
            instructions or
            description or
            agent.get("conversation_starters") or
            agent.get("tools")
        )
        if not has_customization:
            skipped.append((name, "No customization (auto-generated model alias)"))
            continue

        # Resolve base model
        base_model_id, used_fallback = resolve_base_model_id(provider, model_name)
        if used_fallback:
            fallbacks.append((name, f"{provider}/{model_name}", base_model_id))

        # Build params from agent configuration
        params = {}
        if agent.get("instructions"):
            params["system"] = agent["instructions"]

        model_params = agent.get("model_parameters", {})
        if model_params.get("temperature") is not None:
            params["temperature"] = float(model_params["temperature"])
        if model_params.get("max_output_tokens") is not None:
            params["max_tokens"] = int(model_params["max_output_tokens"])
        if model_params.get("top_p") is not None:
            params["top_p"] = float(model_params["top_p"])
        if model_params.get("frequency_penalty") is not None:
            params["frequency_penalty"] = float(model_params["frequency_penalty"])
        if model_params.get("presence_penalty") is not None:
            params["presence_penalty"] = float(model_params["presence_penalty"])

        # Build capabilities from tools
        tools = agent.get("tools", [])
        capabilities = map_librechat_tools_to_capabilities(tools)

        # Build suggestion prompts from conversation starters
        suggestion_prompts = []
        for starter in agent.get("conversation_starters", []):
            suggestion_prompts.append({
                "content": starter,
                "title": [starter[:30] + "..." if len(starter) > 30 else starter, ""],
            })

        # Build meta
        meta = {
            "description": agent.get("description"),
            "capabilities": capabilities,
            "tags": [{"name": "migrated"}, {"name": "librechat-agent"}],
            "suggestion_prompts": suggestion_prompts if suggestion_prompts else None,
            "toolIds": [],
            "filterIds": [],
            "actionIds": [],
            "knowledge": [],
        }

        # Handle avatar
        avatar = agent.get("avatar")
        if avatar and isinstance(avatar, dict):
            meta["profile_image_url"] = avatar.get("filepath", "/static/favicon.png")
        else:
            meta["profile_image_url"] = "/static/favicon.png"

        # Create unique model ID (prefix with 'agent-' to avoid conflicts)
        model_id = f"agent-{agent_id}" if agent_id else f"agent-{name.lower().replace(' ', '-')}"

        now = int(datetime.utcnow().timestamp())

        try:
            owui_model = Model(
                id=model_id,
                user_id=new_owner_id,
                base_model_id=base_model_id,
                name=name,
                params=params,
                meta=meta,
                access_control=None,  # Public - available to all users
                is_active=True,
                created_at=now,
                updated_at=now,
            )

            session.add(owui_model)
            count += 1
        except Exception as e:
            print(f"  Failed to migrate agent '{name}': {e}")

    if skipped:
        print(f"   Skipped {len(skipped)} agents:")
        for name, reason in skipped:
            print(f"     - {name}: {reason}")

    if fallbacks:
        print(f"   Used fallback model for {len(fallbacks)} agents:")
        for name, original, fallback in fallbacks:
            print(f"     - {name}: {original} -> {fallback}")

    return count, fallbacks


def generate_agent_migration_report(fallbacks: List[tuple]) -> str:
    """
    Generate a markdown report of agent->model migration for review.

    Args:
        fallbacks: List of (name, original_model, fallback_model) tuples

    Returns:
        Markdown report string
    """
    agents = load_json("agents.json")
    report = ["# Agent to Model Migration Report\n"]
    report.append(f"Generated: {datetime.utcnow().isoformat()}Z\n")
    report.append(f"Default fallback model: `{DEFAULT_BASE_MODEL}`\n")
    report.append("=" * 60 + "\n")

    for agent in agents:
        name = agent.get("name", "Unnamed")
        provider = agent.get("provider", "openAI")
        model = agent.get("model", "unknown")
        base_model, used_fallback = resolve_base_model_id(provider, model)
        tools = agent.get("tools", [])
        capabilities = map_librechat_tools_to_capabilities(tools)

        report.append(f"\n## {name}\n")
        report.append(f"**LibreChat ID**: `{agent.get('id')}`\n")
        report.append(f"**Open WebUI ID**: `agent-{agent.get('id')}`\n")

        if used_fallback:
            report.append(f"**Base Model**: `{base_model}` (FALLBACK - original: {provider}/{model})\n")
        else:
            report.append(f"**Base Model**: `{base_model}` (from {provider}/{model})\n")

        enabled_caps = [k for k, v in capabilities.items() if v]
        report.append(f"**Capabilities**: {', '.join(enabled_caps)}\n")

        if agent.get("instructions"):
            preview = agent["instructions"][:100]
            if len(agent["instructions"]) > 100:
                preview += "..."
            report.append(f"**System Prompt Preview**: {preview}\n")

        if agent.get("actions"):
            report.append(f"\n**Manual Action Required**: This agent has custom actions:\n")
            for action_id in agent["actions"]:
                report.append(f"  - `{action_id}` - Create as Open WebUI Tool/Function\n")

        report.append("\n" + "-" * 40 + "\n")

    # Summary section
    if fallbacks:
        report.append("\n## Model Fallback Summary\n")
        report.append(f"The following {len(fallbacks)} agents used the fallback model:\n\n")
        report.append("| Agent | Original Model | Fallback Model |\n")
        report.append("|-------|----------------|----------------|\n")
        for name, original, fallback in fallbacks:
            report.append(f"| {name} | {original} | {fallback} |\n")
        report.append("\nTo use specific models, add mappings to `MODEL_MAPPING` in the migration script and re-run.\n")

    return "\n".join(report)


# =============================================================================
# DRY RUN ANALYSIS
# =============================================================================

def analyze_backup() -> dict:
    """
    Analyze backup contents without making any changes.
    Returns detailed statistics about what would be migrated.
    """
    stats = {
        "users": {"total": 0, "with_email": 0, "with_password": 0, "oauth_only": 0},
        "files": {"total": 0, "local": 0, "remote": 0, "found_in_backup": 0},
        "conversations": {"total": 0, "with_messages": 0, "archived": 0},
        "messages": {"total": 0},
        "prompts": {"total": 0, "groups": 0},
        "agents": {"total": 0, "with_model": 0, "mapped": 0, "fallback": 0},
        "tags": set(),
        "issues": [],
    }

    # Analyze users
    users = load_json("users.json")
    stats["users"]["total"] = len(users)
    for user in users:
        email = user.get("email", "").strip()
        password = user.get("password", "")
        provider = user.get("provider", "local")

        if email:
            stats["users"]["with_email"] += 1
        if password:
            stats["users"]["with_password"] += 1
        if provider != "local" and not password:
            stats["users"]["oauth_only"] += 1

        # Build user ID map for later analysis
        old_id = get_mongo_id(user.get("_id"))
        if old_id and email:
            user_id_map[old_id] = f"preview-{old_id}"

    # Analyze files
    files = load_json("files.json")
    stats["files"]["total"] = len(files)
    for file_doc in files:
        source = file_doc.get("source", "local")
        if source in ("local", "", None):
            stats["files"]["local"] += 1
            # Check if file exists in backup
            old_filepath = file_doc.get("filepath", "")
            filename = file_doc.get("filename", "unknown")
            old_file_id = file_doc.get("file_id", "")
            old_user_id = get_mongo_id(file_doc.get("user"))

            found = False
            if old_filepath.startswith("/uploads/"):
                path = BACKUP_DIR / "files" / "uploads" / old_filepath.lstrip("/uploads/")
                if path.exists():
                    found = True
            elif old_filepath.startswith("/images/"):
                path = BACKUP_DIR / "files" / "images" / old_filepath.lstrip("/images/")
                if path.exists():
                    found = True

            if not found:
                for subdir in ["uploads", "images"]:
                    alt_path = BACKUP_DIR / "files" / subdir / old_user_id / f"{old_file_id}__{filename}"
                    if alt_path.exists():
                        found = True
                        break

            if found:
                stats["files"]["found_in_backup"] += 1
        else:
            stats["files"]["remote"] += 1

    # Analyze conversations and messages
    conversations = load_json("conversations.json")
    messages = load_json("messages.json")
    stats["conversations"]["total"] = len(conversations)
    stats["messages"]["total"] = len(messages)

    messages_by_convo = {}
    for msg in messages:
        cid = msg.get("conversationId")
        if cid:
            messages_by_convo[cid] = messages_by_convo.get(cid, 0) + 1

    for convo in conversations:
        convo_id = convo.get("conversationId")
        if convo_id and messages_by_convo.get(convo_id, 0) > 0:
            stats["conversations"]["with_messages"] += 1
        if convo.get("isArchived"):
            stats["conversations"]["archived"] += 1
        for tag in convo.get("tags", []):
            stats["tags"].add(tag)

    # Analyze prompts
    prompt_groups = load_json("promptgroups.json")
    prompts = load_json("prompts.json")
    stats["prompts"]["groups"] = len(prompt_groups)
    stats["prompts"]["total"] = len(prompts)

    # Analyze agents
    agents = load_json("agents.json")
    stats["agents"]["total"] = len(agents)
    for agent in agents:
        model_name = agent.get("model", "")
        provider = agent.get("provider", "openAI")
        if model_name:
            stats["agents"]["with_model"] += 1
            base_model, used_fallback = resolve_base_model_id(provider, model_name)
            if used_fallback:
                stats["agents"]["fallback"] += 1
            else:
                stats["agents"]["mapped"] += 1

    # Convert tags set to count
    stats["tags"] = len(stats["tags"])

    return stats


def print_dry_run_report(stats: dict):
    """Print a formatted dry-run report."""
    print()
    print("=" * 60)
    print("DRY RUN ANALYSIS - No changes will be made")
    print("=" * 60)
    print()

    # Users
    print("USERS")
    print("-" * 40)
    print(f"  Total in backup:      {stats['users']['total']}")
    print(f"  With email:           {stats['users']['with_email']}")
    print(f"  With password:        {stats['users']['with_password']} (will migrate)")
    print(f"  OAuth-only:           {stats['users']['oauth_only']} (will skip)")
    print()

    # Files
    print("FILES")
    print("-" * 40)
    print(f"  Total in backup:      {stats['files']['total']}")
    print(f"  Local files:          {stats['files']['local']}")
    print(f"  Remote (S3, etc):     {stats['files']['remote']} (will skip)")
    print(f"  Found in backup:      {stats['files']['found_in_backup']} (will migrate)")
    missing = stats['files']['local'] - stats['files']['found_in_backup']
    if missing > 0:
        print(f"  Missing from backup:  {missing} (will skip)")
    print()

    # Conversations
    print("CONVERSATIONS")
    print("-" * 40)
    print(f"  Total:                {stats['conversations']['total']}")
    print(f"  With messages:        {stats['conversations']['with_messages']}")
    print(f"  Archived:             {stats['conversations']['archived']}")
    print(f"  Total messages:       {stats['messages']['total']}")
    print(f"  Unique tags:          {stats['tags']}")
    print()

    # Prompts
    print("PROMPTS")
    print("-" * 40)
    print(f"  Prompt groups:        {stats['prompts']['groups']} (will migrate)")
    print(f"  Prompt versions:      {stats['prompts']['total']} (latest per group)")
    print()

    # Agents
    print("AGENTS -> MODELS")
    print("-" * 40)
    print(f"  Total agents:         {stats['agents']['total']}")
    print(f"  With model config:    {stats['agents']['with_model']}")
    print(f"  Model mapped:         {stats['agents']['mapped']}")
    print(f"  Using fallback:       {stats['agents']['fallback']} (-> {DEFAULT_BASE_MODEL})")
    print()

    # Summary
    print("=" * 60)
    print("MIGRATION SUMMARY")
    print("=" * 60)
    print(f"  Users to migrate:     ~{stats['users']['with_password']}")
    print(f"  Files to copy:        ~{stats['files']['found_in_backup']}")
    print(f"  Conversations:        ~{stats['conversations']['total']}")
    print(f"  Prompts:              ~{stats['prompts']['groups']}")
    print(f"  Agents -> Models:     ~{stats['agents']['with_model']}")
    print()
    print("Run without --dry-run to perform the actual migration.")
    print()


# =============================================================================
# MAIN MIGRATION
# =============================================================================

def main():
    """Run the full migration."""
    global DRY_RUN, PREFER_IMPORT_PASSWORD

    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Migrate LibreChat data to Open WebUI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python restore-to-openwebui.py --dry-run                  Preview what would be migrated
  python restore-to-openwebui.py                            Run the actual migration
  python restore-to-openwebui.py --prefer-import-password   Update passwords for duplicate emails
  python restore-to-openwebui.py --reingest-files           Re-ingest migrated files (run after migration)
  python restore-to-openwebui.py --reingest-files --reingest-url http://localhost:8080

Environment variables:
  BACKUP_DIR                 Path to extracted backup directory
  DATA_DIR                   Open WebUI data directory
  DATABASE_URL               Database connection string
  MIGRATION_DEFAULT_MODEL    Fallback model for unmapped agents
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze backup and show what would be migrated without making changes",
    )
    parser.add_argument(
        "--prefer-import-password",
        action="store_true",
        help="For duplicate emails, update existing user's password with the imported one",
    )
    parser.add_argument(
        "--reingest-files",
        action="store_true",
        help="Re-ingest migrated files through Open WebUI's retrieval pipeline (run after migration)",
    )
    parser.add_argument(
        "--reingest-url",
        type=str,
        default="http://localhost:8080",
        help="Open WebUI server URL for file re-ingestion (default: http://localhost:8080)",
    )
    parser.add_argument(
        "--reingest-direct",
        action="store_true",
        help="Re-ingest files directly without HTTP (use when running inside container)",
    )
    args = parser.parse_args()
    DRY_RUN = args.dry_run
    PREFER_IMPORT_PASSWORD = args.prefer_import_password

    # Handle file re-ingestion mode
    if args.reingest_files or args.reingest_direct:
        if args.reingest_direct:
            success, errors = reingest_files_direct()
        else:
            success, errors = reingest_files(args.reingest_url)
        print()
        print("=" * 60)
        print("FILE RE-INGESTION COMPLETE")
        print("=" * 60)
        print(f"  Successful: {success}")
        print(f"  Errors:     {errors}")
        return 0 if errors == 0 else 1

    print("=" * 60)
    print("LibreChat to Open WebUI Migration")
    if DRY_RUN:
        print("MODE: Dry Run (no changes will be made)")
    print("=" * 60)
    print(f"Backup directory: {BACKUP_DIR}")
    print(f"Data directory: {OPENWEBUI_DATA_DIR}")
    print(f"Upload directory: {OPENWEBUI_UPLOAD_DIR}")
    print()

    # Validate backup directory
    if not BACKUP_DIR.exists():
        print(f"ERROR: Backup directory not found: {BACKUP_DIR}")
        print("Set BACKUP_DIR environment variable to the extracted backup location")
        return 1

    if not (BACKUP_DIR / "data").exists():
        print(f"ERROR: No data/ subdirectory in backup: {BACKUP_DIR}")
        return 1

    # Dry run mode - analyze only
    if DRY_RUN:
        stats = analyze_backup()
        print_dry_run_report(stats)
        return 0

    # Import database dependencies only when actually running migration
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Connect to Open WebUI database
    db_url = os.getenv("DATABASE_URL", f"sqlite:///{OPENWEBUI_DATA_DIR}/webui.db")
    print(f"Database: {db_url}")
    print()

    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)
    session = Session()

    stats = MigrationStats()

    try:
        print("1. Migrating users...")
        stats.users = migrate_users(session)
        session.commit()
        print(f"   Migrated {stats.users} users")

        # Get first migrated user ID as fallback owner for agents without known owners
        fallback_user_id = next(iter(user_id_map.values()), None)
        if not fallback_user_id:
            print("ERROR: No users migrated - cannot assign model ownership")
            return 1

        print("2. Migrating files...")
        stats.files = migrate_files(session)
        session.commit()
        print(f"   Migrated {stats.files} files")

        print("3. Migrating conversations...")
        stats.conversations, stats.chat_files = migrate_conversations(session)
        session.commit()
        print(f"   Migrated {stats.conversations} conversations")
        print(f"   Linked {stats.chat_files} files to messages (citations)")

        print("4. Migrating prompts...")
        stats.prompts = migrate_prompts(session)
        session.commit()
        print(f"   Migrated {stats.prompts} prompts")

        print("5. Migrating agents to models...")
        stats.agents, fallbacks = migrate_agents_to_models(session, fallback_user_id)
        session.commit()
        print(f"   Migrated {stats.agents} agents to models")

        print("6. Generating migration report...")
        report = generate_agent_migration_report(fallbacks)
        report_path = BACKUP_DIR / "MIGRATION_REPORT.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"   Report saved to: {report_path}")

        print()
        print("=" * 60)
        print("MIGRATION COMPLETE")
        print("=" * 60)
        print(f"Users:         {stats.users}")
        print(f"Files:         {stats.files}")
        print(f"Chat files:    {stats.chat_files} (citations linked)")
        print(f"Conversations: {stats.conversations}")
        print(f"Prompts:       {stats.prompts}")
        print(f"Agents->Models: {stats.agents}")
        print()
        print(f"Migration report: {report_path}")
        print()
        print("Next steps:")
        print("1. Review MIGRATION_REPORT.md for any manual actions needed")
        print("2. Verify base model availability in Open WebUI")
        print("3. Test user logins and conversation access")
        print("4. Test migrated models (prefixed with 'agent-')")

        return 0

    except Exception as e:
        session.rollback()
        print(f"ERROR: Migration failed - {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    exit(main())
