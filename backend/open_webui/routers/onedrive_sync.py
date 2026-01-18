"""OneDrive Sync Router - Endpoints for OneDrive folder sync to Knowledge bases."""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from pydantic import BaseModel
from typing import Optional, List
import logging

from open_webui.utils.auth import get_verified_user
from open_webui.models.users import UserModel
from open_webui.models.knowledge import Knowledges
from open_webui.services.onedrive.sync_worker import sync_folder_to_knowledge


log = logging.getLogger(__name__)
router = APIRouter()


class SyncFolderRequest(BaseModel):
    """Request to sync a OneDrive folder to a Knowledge base."""

    knowledge_id: str
    drive_id: str
    folder_id: str
    folder_path: str
    access_token: str  # Delegated Graph API token from frontend
    user_token: str  # Open WebUI JWT for internal API calls


class SyncStatusResponse(BaseModel):
    """Response with sync status."""

    knowledge_id: str
    status: str  # "idle", "syncing", "completed", "failed"
    progress_current: Optional[int] = None
    progress_total: Optional[int] = None
    last_sync_at: Optional[int] = None
    error: Optional[str] = None


@router.post("/sync")
async def start_sync(
    request: SyncFolderRequest,
    background_tasks: BackgroundTasks,
    user: UserModel = Depends(get_verified_user),
):
    """Start a sync of OneDrive folder to Knowledge base."""
    # Verify user has write access to the knowledge base
    knowledge = Knowledges.get_knowledge_by_id(request.knowledge_id)
    if not knowledge:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    if knowledge.user_id != user.id:
        # TODO: Check access_control for write permission
        raise HTTPException(
            status_code=403, detail="Not authorized to sync this Knowledge base"
        )

    # Update knowledge meta with sync configuration
    meta = knowledge.meta or {}
    meta["onedrive_sync"] = {
        "drive_id": request.drive_id,
        "folder_id": request.folder_id,
        "folder_path": request.folder_path,
        "status": "syncing",
    }

    Knowledges.update_knowledge_meta_by_id(request.knowledge_id, meta)

    # Queue background sync task
    background_tasks.add_task(
        sync_folder_to_knowledge,
        knowledge_id=request.knowledge_id,
        drive_id=request.drive_id,
        folder_id=request.folder_id,
        access_token=request.access_token,
        user_id=user.id,
        user_token=request.user_token,
    )

    return {"message": "Sync started", "knowledge_id": request.knowledge_id}


@router.get("/sync/{knowledge_id}")
async def get_sync_status(
    knowledge_id: str,
    user: UserModel = Depends(get_verified_user),
) -> SyncStatusResponse:
    """Get sync status for a Knowledge base."""
    knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
    if not knowledge:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    meta = knowledge.meta or {}
    sync_info = meta.get("onedrive_sync", {})

    return SyncStatusResponse(
        knowledge_id=knowledge_id,
        status=sync_info.get("status", "idle"),
        progress_current=sync_info.get("progress_current"),
        progress_total=sync_info.get("progress_total"),
        last_sync_at=sync_info.get("last_sync_at"),
        error=sync_info.get("error"),
    )


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
