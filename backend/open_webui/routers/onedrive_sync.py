"""OneDrive Sync Router - Endpoints for OneDrive folder sync to Knowledge bases."""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import RedirectResponse
from starlette.requests import Request
from starlette.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, List, Literal
import json
import time
import logging

from open_webui.utils.auth import get_verified_user
from open_webui.models.users import UserModel
from open_webui.models.knowledge import Knowledges
from open_webui.config import MICROSOFT_CLIENT_SECRET


log = logging.getLogger(__name__)
router = APIRouter()


class SyncItem(BaseModel):
    """A single OneDrive item (file or folder) to sync."""

    type: Literal["file", "folder"]
    drive_id: str
    item_id: str
    item_path: str
    name: str


class SyncItemsRequest(BaseModel):
    """Request to sync multiple OneDrive items to a Knowledge base."""

    knowledge_id: str
    items: List[SyncItem]
    access_token: str


class FailedFileInfo(BaseModel):
    """Information about a file that failed to sync."""

    filename: str
    error_type: str  # "timeout", "empty_content", "processing_error", "download_error"
    error_message: str


class SyncStatusResponse(BaseModel):
    """Response with sync status."""

    knowledge_id: str
    status: str  # "idle", "syncing", "completed", "failed", "completed_with_errors"
    progress_current: Optional[int] = None
    progress_total: Optional[int] = None
    last_sync_at: Optional[int] = None
    error: Optional[str] = None
    source_count: Optional[int] = None
    failed_files: Optional[List[FailedFileInfo]] = None


@router.post("/sync/items")
async def sync_items(
    request: SyncItemsRequest,
    fastapi_request: Request,
    background_tasks: BackgroundTasks,
    user: UserModel = Depends(get_verified_user),
):
    """Start OneDrive sync for multiple items (files and folders)."""

    # Verify knowledge base exists and user has access
    knowledge = Knowledges.get_knowledge_by_id(request.knowledge_id)
    if not knowledge:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if knowledge.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Get existing sources or initialize empty list
    meta = knowledge.meta or {}
    existing_sync = meta.get("onedrive_sync", {})
    # Prevent duplicate syncs
    if existing_sync.get("status") == "syncing":
        raise HTTPException(
            status_code=409,
            detail="A sync is already in progress. Cancel it first or wait for it to complete.",
        )
    existing_sources = existing_sync.get("sources", [])

    # Add new items (skip duplicates by item_id)
    existing_ids = {s["item_id"] for s in existing_sources}
    new_sources = [
        {
            "type": item.type,
            "drive_id": item.drive_id,
            "item_id": item.item_id,
            "item_path": item.item_path,
            "name": item.name,
        }
        for item in request.items
        if item.item_id not in existing_ids
    ]

    all_sources = existing_sources + new_sources

    # Update metadata
    meta["onedrive_sync"] = {
        "sources": all_sources,
        "status": "syncing",
        "last_sync_at": existing_sync.get("last_sync_at"),
    }
    Knowledges.update_knowledge_meta_by_id(request.knowledge_id, meta)

    # Queue background sync
    background_tasks.add_task(
        sync_items_to_knowledge,
        knowledge_id=request.knowledge_id,
        sources=all_sources,
        access_token=request.access_token,
        user_id=user.id,
        app=fastapi_request.app,
    )

    return {"message": "Sync started", "knowledge_id": request.knowledge_id}


async def sync_items_to_knowledge(
    knowledge_id: str,
    sources: List[dict],
    access_token: str,
    user_id: str,
    app,
):
    """Background task to sync multiple OneDrive items."""
    from open_webui.services.onedrive.sync_worker import OneDriveSyncWorker

    worker = OneDriveSyncWorker(
        knowledge_id=knowledge_id,
        sources=sources,
        access_token=access_token,
        user_id=user_id,
        app=app,
    )
    await worker.sync()


@router.get("/sync/{knowledge_id}")
async def get_sync_status(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
) -> SyncStatusResponse:
    """Get sync status for a Knowledge base."""
    knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
    if not knowledge:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if knowledge.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    meta = knowledge.meta or {}
    sync_info = meta.get("onedrive_sync", {})
    sources = sync_info.get("sources", [])
    last_result = sync_info.get("last_result", {})

    # Convert failed_files dicts to FailedFileInfo models
    failed_files_raw = last_result.get("failed_files", [])
    failed_files = (
        [FailedFileInfo(**f) for f in failed_files_raw] if failed_files_raw else None
    )

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


@router.post("/sync/{knowledge_id}/cancel")
async def cancel_sync(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
):
    """Cancel an ongoing OneDrive sync for a Knowledge base."""
    knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
    if not knowledge:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if knowledge.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    meta = knowledge.meta or {}
    sync_info = meta.get("onedrive_sync", {})

    # Only allow cancelling if currently syncing
    if sync_info.get("status") != "syncing":
        raise HTTPException(status_code=400, detail="No active sync to cancel")

    # Set status to cancelled - the worker will check this and stop
    sync_info["status"] = "cancelled"
    meta["onedrive_sync"] = sync_info
    Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)

    log.info(f"Sync cancelled for knowledge base {knowledge_id}")

    return {"message": "Sync cancelled", "knowledge_id": knowledge_id}


@router.get("/synced-collections")
async def list_synced_collections(
    user: UserModel = Depends(get_verified_user),
) -> List[dict]:
    """List all Knowledge bases with OneDrive sync enabled for current user."""
    all_knowledge = Knowledges.get_knowledge_bases_by_user_id(user.id)

    synced = []
    for kb in all_knowledge:
        meta = kb.meta or {}
        if "onedrive_sync" in meta:
            synced.append(
                {"id": kb.id, "name": kb.name, "sync_info": meta["onedrive_sync"]}
            )

    return synced


# ──────────────────────────────────────────────────────────────────────
# Background Sync OAuth Endpoints
# ──────────────────────────────────────────────────────────────────────


@router.get("/auth/initiate")
async def initiate_auth(
    knowledge_id: str,
    request: Request,
    user: UserModel = Depends(get_verified_user),
):
    """Initiate OAuth auth code flow for background sync."""
    from open_webui.services.onedrive.auth import get_authorization_url

    if not MICROSOFT_CLIENT_SECRET.value:
        raise HTTPException(400, "OneDrive client secret not configured")

    knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
    if not knowledge or knowledge.user_id != user.id:
        raise HTTPException(404, "Knowledge base not found")

    # Use the existing /oauth/microsoft/callback registered in Azure AD
    redirect_uri = str(request.base_url).rstrip("/") + "/oauth/microsoft/callback"

    auth_url = get_authorization_url(
        user_id=user.id,
        knowledge_id=knowledge_id,
        redirect_uri=redirect_uri,
    )
    return RedirectResponse(auth_url)


async def handle_onedrive_auth_callback(request: Request):
    """
    Handle OAuth callback from Microsoft for OneDrive background sync.
    Called from the shared /oauth/microsoft/callback route in main.py
    when the state matches a pending OneDrive auth flow.
    Returns HTML that posts result to the opener window and closes.
    """
    from open_webui.services.onedrive.auth import exchange_code_for_tokens, _pending_flows

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")
    error_description = request.query_params.get("error_description")

    if error:
        return _auth_callback_html(
            success=False,
            error=error_description or error,
        )

    if not code or not state:
        return _auth_callback_html(
            success=False,
            error="Missing authorization code or state",
        )

    # Get user_id from the pending flow (stored when initiate was called)
    flow = _pending_flows.get(state)
    if not flow:
        return _auth_callback_html(
            success=False,
            error="Invalid or expired authorization flow",
        )

    result = await exchange_code_for_tokens(
        code=code,
        state=state,
        user_id=flow["user_id"],
    )

    if result["success"]:
        # Update knowledge meta to reflect stored token
        knowledge_id = result["knowledge_id"]
        knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
        if knowledge:
            meta = knowledge.meta or {}
            sync_info = meta.get("onedrive_sync", {})
            sync_info["has_stored_token"] = True
            sync_info["token_stored_at"] = int(time.time())
            sync_info["needs_reauth"] = False
            meta["onedrive_sync"] = sync_info
            Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)

    return _auth_callback_html(
        success=result["success"],
        error=result.get("error"),
        knowledge_id=result.get("knowledge_id"),
    )


def _auth_callback_html(success: bool, error: str = None, knowledge_id: str = None):
    """Return HTML that communicates result to opener and closes."""
    data = {
        "type": "onedrive_auth_callback",
        "success": success,
    }
    if error:
        data["error"] = error
    if knowledge_id:
        data["knowledge_id"] = knowledge_id

    html = f"""<!DOCTYPE html>
<html><body><script>
    if (window.opener) {{
        window.opener.postMessage({json.dumps(data)}, window.location.origin);
    }}
    window.close();
</script></body></html>"""
    return HTMLResponse(html)


@router.get("/auth/token-status/{knowledge_id}")
async def get_token_status(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
):
    """Check if a stored token exists and is valid for a KB."""
    from open_webui.services.onedrive.auth import get_stored_token

    knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
    if not knowledge or knowledge.user_id != user.id:
        raise HTTPException(404, "Knowledge base not found")

    token_data = get_stored_token(user.id, knowledge_id)
    if not token_data:
        return {"has_token": False}

    expires_at = token_data.get("expires_at", 0)
    is_expired = expires_at < time.time()

    meta = knowledge.meta or {}
    sync_info = meta.get("onedrive_sync", {})

    return {
        "has_token": True,
        "is_expired": is_expired,
        "needs_reauth": sync_info.get("needs_reauth", False),
        "token_stored_at": sync_info.get("token_stored_at"),
    }


@router.post("/auth/revoke/{knowledge_id}")
async def revoke_token(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
):
    """Revoke and delete stored token for a KB."""
    from open_webui.services.onedrive.auth import delete_stored_token

    knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
    if not knowledge or knowledge.user_id != user.id:
        raise HTTPException(404, "Knowledge base not found")

    deleted = delete_stored_token(user.id, knowledge_id)

    # Update meta
    meta = knowledge.meta or {}
    sync_info = meta.get("onedrive_sync", {})
    sync_info["has_stored_token"] = False
    sync_info.pop("token_stored_at", None)
    sync_info["needs_reauth"] = False
    meta["onedrive_sync"] = sync_info
    Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)

    return {"revoked": deleted}
