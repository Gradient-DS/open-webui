"""
Real-Time Permission Enforcement

Checks source permissions at access time, not just share time.
Ensures users who lose source access immediately lose KB access.
"""

import logging
from typing import Optional, List
from pydantic import BaseModel

from open_webui.models.knowledge import Knowledges
from open_webui.models.files import Files
from open_webui.utils.access_control import has_access
from open_webui.services.permissions.registry import PermissionProviderRegistry

log = logging.getLogger(__name__)


class AccessDenialReason(BaseModel):
    """Details about why access was denied."""

    reason: str  # "no_kb_access", "source_access_revoked", "source_check_failed"
    message: str
    source_type: Optional[str] = None
    inaccessible_count: int = 0
    grant_access_url: Optional[str] = None


class KnowledgeAccessResult(BaseModel):
    """Result of checking knowledge base access."""

    allowed: bool
    denial: Optional[AccessDenialReason] = None
    accessible_file_ids: List[str] = []  # Files the user can access
    inaccessible_file_ids: List[str] = []  # Files the user cannot access


async def check_knowledge_access(
    user_id: str,
    knowledge_id: str,
    strict_mode: bool = True,
) -> KnowledgeAccessResult:
    """
    Check if user has access to a knowledge base, including source permissions.

    Args:
        user_id: User ID to check
        knowledge_id: Knowledge base ID
        strict_mode: If True, any inaccessible source file blocks all access.
                     If False, allows partial access.

    Returns:
        KnowledgeAccessResult with access status and details
    """
    knowledge = Knowledges.get_knowledge_by_id(knowledge_id)
    if not knowledge:
        return KnowledgeAccessResult(
            allowed=False,
            denial=AccessDenialReason(
                reason="not_found",
                message="Knowledge base not found.",
            ),
        )

    # Check standard Open WebUI access control
    is_owner = knowledge.user_id == user_id
    has_kb_access = is_owner or has_access(user_id, "read", knowledge.access_control)

    if not has_kb_access:
        return KnowledgeAccessResult(
            allowed=False,
            denial=AccessDenialReason(
                reason="no_kb_access",
                message="You don't have access to this knowledge base.",
            ),
        )

    # Get files and check source permissions
    file_ids = knowledge.data.get("file_ids", []) if knowledge.data else []
    if not file_ids:
        return KnowledgeAccessResult(allowed=True, accessible_file_ids=[])

    accessible_files = []
    inaccessible_files = []
    denial_info = None

    for file_id in file_ids:
        file = Files.get_file_by_id(file_id)
        if not file:
            continue

        source = file.meta.get("source", "local") if file.meta else "local"
        if source == "local":
            accessible_files.append(file_id)
            continue

        provider = PermissionProviderRegistry.get_provider(source)
        if not provider:
            # No provider = no source restrictions
            accessible_files.append(file_id)
            continue

        # Check source permission
        try:
            result = await provider.check_user_access(
                user_id, [file_id], "read", use_cache=False
            )
            if result.has_access:
                accessible_files.append(file_id)
            else:
                inaccessible_files.append(file_id)
                if denial_info is None:
                    denial_info = AccessDenialReason(
                        reason="source_access_revoked",
                        message=f"Your access to {source.title()} documents has been revoked.",
                        source_type=source,
                        grant_access_url=result.grant_access_url,
                    )
        except Exception as e:
            log.warning(f"Source permission check failed for {file_id}: {e}")
            if strict_mode:
                inaccessible_files.append(file_id)

    if inaccessible_files:
        if denial_info:
            denial_info.inaccessible_count = len(inaccessible_files)

        if strict_mode:
            return KnowledgeAccessResult(
                allowed=False,
                denial=denial_info,
                accessible_file_ids=accessible_files,
                inaccessible_file_ids=inaccessible_files,
            )

    return KnowledgeAccessResult(
        allowed=True,
        accessible_file_ids=accessible_files,
        inaccessible_file_ids=inaccessible_files,
    )


async def filter_accessible_files(
    user_id: str,
    file_ids: List[str],
) -> List[str]:
    """
    Filter a list of file IDs to only those the user can access.

    Used for retrieval to ensure users only see files they have source access to.
    """
    accessible = []

    for file_id in file_ids:
        file = Files.get_file_by_id(file_id)
        if not file:
            continue

        source = file.meta.get("source", "local") if file.meta else "local"
        if source == "local":
            accessible.append(file_id)
            continue

        provider = PermissionProviderRegistry.get_provider(source)
        if not provider:
            accessible.append(file_id)
            continue

        try:
            result = await provider.check_user_access(user_id, [file_id], "read")
            if result.has_access:
                accessible.append(file_id)
        except Exception as e:
            log.warning(f"Permission check failed for {file_id}: {e}")
            # Fail closed - don't include file if check fails

    return accessible


async def get_accessible_model_knowledge(
    model_id: str,
    user_id: str,
    strict_mode: bool = True,
) -> tuple[List[str], List[dict]]:
    """
    Get accessible knowledge bases for a model.

    Returns:
        Tuple of (accessible_kb_ids, inaccessible_warnings)
    """
    from open_webui.models.models import Models

    model = Models.get_model_by_id(model_id)
    if not model or not model.meta:
        return [], []

    knowledge_refs = model.meta.get("knowledge", [])
    if not knowledge_refs:
        return [], []

    accessible = []
    warnings = []

    for kb_ref in knowledge_refs:
        kb_id = kb_ref.get("collection_name") or kb_ref.get("id")
        if not kb_id:
            continue

        result = await check_knowledge_access(user_id, kb_id, strict_mode)

        if result.allowed:
            accessible.append(kb_id)
        else:
            warnings.append({
                "knowledge_id": kb_id,
                "reason": result.denial.reason if result.denial else "unknown",
                "message": result.denial.message if result.denial else "Access denied",
            })

    return accessible, warnings
