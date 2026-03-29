"""Shared sync router helpers for cloud providers.

Provides common endpoint logic used by both OneDrive and Google Drive routers.
Each provider router delegates to these functions, passing provider-specific config.
"""

import json
import time
import logging
from typing import Optional, List, Callable

from fastapi import HTTPException
from starlette.responses import HTMLResponse
from pydantic import BaseModel

from open_webui.models.knowledge import Knowledges
from open_webui.models.users import UserModel

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Shared Pydantic models
# ──────────────────────────────────────────────────────────────────────


class FailedFileInfo(BaseModel):
    """Information about a file that failed to sync."""

    filename: str
    error_type: str
    error_message: str


class SyncStatusResponse(BaseModel):
    """Response with sync status."""

    knowledge_id: str
    status: str
    progress_current: Optional[int] = None
    progress_total: Optional[int] = None
    last_sync_at: Optional[int] = None
    error: Optional[str] = None
    source_count: Optional[int] = None
    failed_files: Optional[List[FailedFileInfo]] = None


class RemoveSourceRequest(BaseModel):
    """Request to remove a source from a KB's sync configuration."""

    item_id: str


# ──────────────────────────────────────────────────────────────────────
# Shared endpoint logic
# ──────────────────────────────────────────────────────────────────────


def get_knowledge_or_raise(knowledge_id: str, user: UserModel):
    """Get a knowledge base, raising HTTPException if not found or not authorized."""
    knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
    if not knowledge:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if knowledge.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return knowledge


def handle_sync_items_request(
    knowledge_id: str,
    meta_key: str,
    new_sources: List[dict],
    access_token: str,
    user: UserModel,
    clear_delta_keys: List[str],
) -> dict:
    """
    Shared logic for the POST /sync/items endpoint.

    Validates the KB, handles stale/cancelled syncs, merges sources,
    and updates metadata. Returns the merged sources and updated meta.

    Args:
        knowledge_id: The knowledge base ID
        meta_key: e.g. "google_drive_sync" or "onedrive_sync"
        new_sources: List of source dicts extracted from the request items
        access_token: The OAuth access token
        user: The authenticated user
        clear_delta_keys: Keys to pop from sources on cancellation (e.g. ["page_token", "folder_map"])

    Returns:
        Dict with "all_sources" and "meta" keys
    """
    knowledge = get_knowledge_or_raise(knowledge_id, user)

    meta = knowledge.meta or {}
    existing_sync = meta.get(meta_key, {})

    # Prevent duplicate syncs (with staleness recovery)
    if existing_sync.get("status") == "syncing":
        sync_started = existing_sync.get("sync_started_at")
        stale_threshold = 30 * 60
        is_stale = not sync_started or (time.time() - sync_started) > stale_threshold
        if is_stale:
            log.warning(
                "Stale sync detected for KB %s (started_at=%s), allowing new sync",
                knowledge_id,
                sync_started,
            )
        else:
            raise HTTPException(
                status_code=409,
                detail="A sync is already in progress. Cancel it first or wait for it to complete.",
            )

    existing_sources = existing_sync.get("sources", [])

    # After cancellation, force full re-enumeration
    if existing_sync.get("status") == "cancelled":
        for source in existing_sources:
            for key in clear_delta_keys:
                source.pop(key, None)

    # Add new items (skip duplicates by item_id)
    existing_ids = {s["item_id"] for s in existing_sources}
    deduped_new = [s for s in new_sources if s["item_id"] not in existing_ids]
    all_sources = existing_sources + deduped_new

    meta[meta_key] = {
        **existing_sync,
        "sources": all_sources,
        "status": "syncing",
        "sync_started_at": int(time.time()),
        "last_sync_at": existing_sync.get("last_sync_at"),
    }
    Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)

    return {"all_sources": all_sources, "meta": meta}


def handle_get_sync_status(
    knowledge_id: str,
    meta_key: str,
    user: UserModel,
) -> SyncStatusResponse:
    """Shared logic for GET /sync/{knowledge_id}."""
    knowledge = get_knowledge_or_raise(knowledge_id, user)

    meta = knowledge.meta or {}
    sync_info = meta.get(meta_key, {})
    sources = sync_info.get("sources", [])
    last_result = sync_info.get("last_result", {})

    failed_files_raw = last_result.get("failed_files", [])
    failed_files = [FailedFileInfo(**f) for f in failed_files_raw] if failed_files_raw else None

    return SyncStatusResponse(
        knowledge_id=knowledge_id,
        status=sync_info.get("status", "idle"),
        progress_current=sync_info.get("progress_current"),
        progress_total=sync_info.get("progress_total"),
        last_sync_at=sync_info.get("last_sync_at"),
        error=sync_info.get("error"),
        source_count=len(sources),
        failed_files=failed_files,
    )


def handle_cancel_sync(
    knowledge_id: str,
    meta_key: str,
    user: UserModel,
) -> dict:
    """Shared logic for POST /sync/{knowledge_id}/cancel."""
    knowledge = get_knowledge_or_raise(knowledge_id, user)

    meta = knowledge.meta or {}
    sync_info = meta.get(meta_key, {})

    if sync_info.get("status") != "syncing":
        raise HTTPException(status_code=400, detail="No active sync to cancel")

    sync_info["status"] = "cancelled"
    meta[meta_key] = sync_info
    Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)

    log.info(f"Sync cancelled for knowledge base {knowledge_id}")
    return {"message": "Sync cancelled", "knowledge_id": knowledge_id}


def handle_remove_source(
    knowledge_id: str,
    meta_key: str,
    item_id: str,
    user: UserModel,
    remove_files_fn: Callable,
) -> dict:
    """Shared logic for POST /sync/{knowledge_id}/sources/remove.

    Args:
        remove_files_fn: Called with (knowledge_id, item_id, source_to_remove) to clean up files.
                         Must return the count of removed files.
    """
    knowledge = get_knowledge_or_raise(knowledge_id, user)

    meta = knowledge.meta or {}
    sync_info = meta.get(meta_key, {})

    if sync_info.get("status") == "syncing":
        raise HTTPException(
            status_code=409,
            detail="Cannot remove source while sync is in progress.",
        )

    sources = sync_info.get("sources", [])

    source_to_remove = None
    remaining_sources = []
    for source in sources:
        if source["item_id"] == item_id:
            source_to_remove = source
        else:
            remaining_sources.append(source)

    if not source_to_remove:
        raise HTTPException(status_code=404, detail="Source not found")

    removed_count = remove_files_fn(knowledge_id, item_id, source_to_remove)

    sync_info["sources"] = remaining_sources
    meta[meta_key] = sync_info
    Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)

    log.info(
        f"Removed source '{source_to_remove.get('name')}' from KB {knowledge_id}, " f"{removed_count} files cleaned up"
    )

    return {
        "message": "Source removed",
        "source_name": source_to_remove.get("name"),
        "files_removed": removed_count,
    }


def handle_list_synced_collections(
    meta_key: str,
    user: UserModel,
) -> List[dict]:
    """Shared logic for GET /synced-collections."""
    all_knowledge = Knowledges.get_knowledge_bases_by_user_id(user.id)
    synced = []
    for kb in all_knowledge:
        meta = kb.meta or {}
        if meta_key in meta:
            synced.append({"id": kb.id, "name": kb.name, "sync_info": meta[meta_key]})
    return synced


def handle_get_token_status(
    knowledge_id: str,
    meta_key: str,
    user: UserModel,
    get_stored_token_fn: Callable,
) -> dict:
    """Shared logic for GET /auth/token-status/{knowledge_id}."""
    knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
    if not knowledge or knowledge.user_id != user.id:
        raise HTTPException(404, "Knowledge base not found")

    token_data = get_stored_token_fn(user.id)
    if not token_data:
        return {"has_token": False}

    expires_at = token_data.get("expires_at", 0)
    is_expired = expires_at < time.time()

    meta = knowledge.meta or {}
    sync_info = meta.get(meta_key, {})

    return {
        "has_token": True,
        "is_expired": is_expired,
        "needs_reauth": sync_info.get("needs_reauth", False),
        "token_stored_at": sync_info.get("token_stored_at"),
    }


def handle_revoke_token(
    knowledge_id: str,
    provider_type: str,
    meta_key: str,
    user: UserModel,
    delete_stored_token_fn: Callable,
) -> dict:
    """Shared logic for POST /auth/revoke/{knowledge_id}."""
    knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
    if not knowledge or knowledge.user_id != user.id:
        raise HTTPException(404, "Knowledge base not found")

    deleted = delete_stored_token_fn(user.id)

    all_kbs = Knowledges.get_knowledge_bases_by_type(provider_type)
    for kb in all_kbs:
        if kb.user_id != user.id:
            continue
        meta = kb.meta or {}
        sync_info = meta.get(meta_key, {})
        sync_info["has_stored_token"] = False
        sync_info.pop("token_stored_at", None)
        sync_info["needs_reauth"] = False
        meta[meta_key] = sync_info
        Knowledges.update_knowledge_meta_by_id(kb.id, meta)

    return {"revoked": deleted}


async def complete_auth_callback(
    code: str,
    state: str,
    flow: dict,
    provider_type: str,
    meta_key: str,
    callback_type: str,
    exchange_code_fn: Callable,
) -> HTMLResponse:
    """Complete the async portion of the auth callback."""
    result = await exchange_code_fn(
        code=code,
        state=state,
        user_id=flow["user_id"],
    )

    if result["success"]:
        user_id = flow["user_id"]
        all_kbs = Knowledges.get_knowledge_bases_by_type(provider_type)
        for kb in all_kbs:
            if kb.user_id != user_id:
                continue
            meta = kb.meta or {}
            sync_info = meta.get(meta_key, {})
            sync_info["has_stored_token"] = True
            sync_info["token_stored_at"] = int(time.time())
            sync_info["needs_reauth"] = False
            meta[meta_key] = sync_info
            Knowledges.update_knowledge_meta_by_id(kb.id, meta)

    return auth_callback_html(
        callback_type=callback_type,
        success=result["success"],
        error=result.get("error"),
        knowledge_id=result.get("knowledge_id"),
    )


def auth_callback_html(
    callback_type: str,
    success: bool,
    error: str = None,
    knowledge_id: str = None,
) -> HTMLResponse:
    """Return HTML that communicates result to opener window and closes."""
    data = {
        "type": callback_type,
        "success": success,
    }
    if error:
        data["error"] = error
    if knowledge_id:
        data["knowledge_id"] = knowledge_id

    # Escape '</' to prevent script tag injection
    safe_json = json.dumps(data).replace("</", "<\\/")

    html = f"""<!DOCTYPE html>
<html><body><script>
    if (window.opener) {{
        window.opener.postMessage({safe_json}, window.location.origin);
    }}
    window.close();
</script></body></html>"""
    return HTMLResponse(html)


def remove_files_for_source_generic(
    knowledge_id: str,
    source_item_id: str,
    file_id_prefix: str,
    get_drive_id_fn: Callable = None,
    source: dict = None,
) -> int:
    """Remove all files associated with a specific source from a KB.

    Args:
        file_id_prefix: e.g. "googledrive-" or "onedrive-"
        get_drive_id_fn: Optional. For OneDrive legacy fallback matching by drive_id.
                         Called with (file_meta, source) -> bool to check legacy match.
    """
    from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT
    from open_webui.models.files import Files

    files = Knowledges.get_files_by_id(knowledge_id)
    if not files:
        return 0

    removed_count = 0
    for file in files:
        if not file.id.startswith(file_id_prefix):
            continue

        file_meta = file.meta or {}
        file_source_item_id = file_meta.get("source_item_id")

        if file_source_item_id:
            if file_source_item_id != source_item_id:
                continue
        elif get_drive_id_fn:
            # Legacy fallback (e.g. OneDrive files without source_item_id)
            if not get_drive_id_fn(file_meta, source):
                continue
        else:
            # No source_item_id and no legacy matcher — skip
            continue

        Knowledges.remove_file_from_knowledge_by_id(knowledge_id, file.id)

        try:
            VECTOR_DB_CLIENT.delete(
                collection_name=knowledge_id,
                filter={"file_id": file.id},
            )
        except Exception as e:
            log.warning(f"Failed to remove vectors for {file.id}: {e}")

        remaining = Knowledges.get_knowledge_files_by_file_id(file.id)
        if not remaining:
            try:
                VECTOR_DB_CLIENT.delete_collection(f"file-{file.id}")
            except Exception:
                pass
            Files.delete_file_by_id(file.id)

        removed_count += 1

    return removed_count
