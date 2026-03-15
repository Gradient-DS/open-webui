import hashlib
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from langchain_core.documents import Document
from pydantic import BaseModel

from open_webui.models.files import FileForm, Files
from open_webui.models.knowledge import KnowledgeForm, Knowledges
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT
from open_webui.routers.retrieval import save_docs_to_vector_db
from open_webui.utils.auth import get_verified_user

router = APIRouter()
log = logging.getLogger(__name__)


# --- Pydantic Models ---


class IngestCollection(BaseModel):
    source_id: str
    name: str
    description: str = ""
    language: Optional[str] = None
    tags: list[str] = []
    metadata: dict = {}


class IngestDocument(BaseModel):
    source_id: str
    filename: str
    content_type: str = "text/plain"
    text: str
    title: Optional[str] = None
    source_url: Optional[str] = None
    language: Optional[str] = None
    author: Optional[str] = None
    modified_at: Optional[str] = None
    tags: list[str] = []
    metadata: dict = {}


class IngestForm(BaseModel):
    collection: IngestCollection
    documents: list[IngestDocument]


# --- Helper Functions ---


def get_integration_provider(request: Request, user) -> tuple[str, dict]:
    """Resolve the integration provider from the authenticated service account."""
    provider_slug = (user.info or {}).get("integration_provider")
    if not provider_slug:
        raise HTTPException(
            status_code=403,
            detail="This account is not configured as an integration service account",
        )
    providers = request.app.state.config.INTEGRATION_PROVIDERS
    if not providers:
        raise HTTPException(
            status_code=403,
            detail=f"Integration provider '{provider_slug}' is not registered",
        )
    provider_config = providers.get(provider_slug)
    if not provider_config:
        raise HTTPException(
            status_code=403,
            detail=f"Integration provider '{provider_slug}' is not registered",
        )
    return provider_slug, provider_config


def _find_kb_by_source_id(provider: str, source_id: str):
    """Find a knowledge base by provider slug + external source_id."""
    kbs = Knowledges.get_knowledge_bases_by_type(provider)
    for kb in kbs:
        meta = kb.meta or {}
        if meta.get("integration", {}).get("source_id") == source_id:
            return kb
    return None


def _create_kb_for_provider(
    provider: str,
    provider_config: dict,
    collection: IngestCollection,
    user_id: str,
):
    """Create a new knowledge base for a push provider."""
    form = KnowledgeForm(
        name=collection.name,
        description=collection.description,
        type=provider,
        access_control={},
    )
    knowledge = Knowledges.insert_new_knowledge(user_id, form)
    meta = {
        "integration": {
            "provider": provider,
            "source_id": collection.source_id,
            "language": collection.language,
            "tags": collection.tags,
            "provider_metadata": collection.metadata,
        }
    }
    Knowledges.update_knowledge_meta_by_id(knowledge.id, meta)
    return Knowledges.get_knowledge_by_id(knowledge.id)


def _process_ingest_document(
    request: Request,
    knowledge_id: str,
    provider: str,
    doc: IngestDocument,
    user_id: str,
) -> dict:
    """Process a single document: create file record, chunk, embed, store."""
    file_id = f"{provider}-{doc.source_id}"
    status = "created"

    existing_file = Files.get_file_by_id(file_id)
    if existing_file:
        status = "updated"
        Files.update_file_metadata_by_id(file_id, {
            "name": doc.title or doc.filename,
            "content_type": doc.content_type,
            "source": provider,
            "source_id": doc.source_id,
            "source_url": doc.source_url,
            "language": doc.language,
            "author": doc.author,
            "tags": doc.tags,
            "provider_metadata": doc.metadata,
        })
        Files.update_file_data_by_id(file_id, {"content": doc.text})
    else:
        text_hash = hashlib.sha256(doc.text.encode()).hexdigest()
        file_form = FileForm(
            id=file_id,
            filename=doc.filename,
            hash=text_hash,
            path="",
            data={"content": doc.text},
            meta={
                "name": doc.title or doc.filename,
                "content_type": doc.content_type,
                "source": provider,
                "source_id": doc.source_id,
                "source_url": doc.source_url,
                "language": doc.language,
                "author": doc.author,
                "tags": doc.tags,
                "provider_metadata": doc.metadata,
            },
        )
        Files.insert_new_file(user_id, file_form)
        Knowledges.add_file_to_knowledge_by_id(knowledge_id, file_id, user_id)

    # Create langchain Document for vector storage
    text_hash = hashlib.sha256(doc.text.encode()).hexdigest()
    lc_doc = Document(
        page_content=doc.text,
        metadata={
            "name": doc.title or doc.filename,
            "source": doc.source_url or doc.filename,
            "file_id": file_id,
            "created_by": user_id,
            "author": doc.author,
            "language": doc.language,
            "source_provider": provider,
        },
    )

    try:
        save_docs_to_vector_db(
            request=request,
            docs=[lc_doc],
            collection_name=knowledge_id,
            metadata={
                "file_id": file_id,
                "name": doc.title or doc.filename,
                "hash": text_hash,
            },
            add=True,
        )
        Files.update_file_data_by_id(file_id, {"status": "completed"})
    except Exception as e:
        log.exception(f"Failed to store document {doc.source_id} in vector DB")
        Files.update_file_data_by_id(file_id, {"status": "error", "error": str(e)})
        return {
            "source_id": doc.source_id,
            "file_id": file_id,
            "status": "error",
            "error": str(e),
        }

    return {"source_id": doc.source_id, "file_id": file_id, "status": status}


# --- Endpoints ---


@router.post("/ingest")
def ingest_documents(
    request: Request,
    form_data: IngestForm,
    user=Depends(get_verified_user),
):
    provider, provider_config = get_integration_provider(request, user)

    # Validate batch size
    max_per_request = provider_config.get("max_documents_per_request", 50)
    if len(form_data.documents) > max_per_request:
        raise HTTPException(
            400, f"Too many documents. Maximum {max_per_request} per request."
        )

    # Find or create KB
    knowledge = _find_kb_by_source_id(provider, form_data.collection.source_id)
    if not knowledge:
        knowledge = _create_kb_for_provider(
            provider, provider_config, form_data.collection, user.id
        )

    # Check file limit
    max_files = provider_config.get("max_files_per_kb", 250)
    current_files = Knowledges.get_files_by_id(knowledge.id)
    existing_ids = {f.id for f in current_files} if current_files else set()
    new_doc_ids = {f"{provider}-{doc.source_id}" for doc in form_data.documents}
    net_new = len(new_doc_ids - existing_ids)
    if len(existing_ids) + net_new > max_files:
        raise HTTPException(
            400, f"Would exceed {max_files} file limit for this knowledge base."
        )

    # Process documents
    results = []
    created = updated = errors = 0
    for doc in form_data.documents:
        result = _process_ingest_document(
            request=request,
            knowledge_id=knowledge.id,
            provider=provider,
            doc=doc,
            user_id=user.id,
        )
        results.append(result)
        if result["status"] == "created":
            created += 1
        elif result["status"] == "updated":
            updated += 1
        elif result["status"] == "error":
            errors += 1

    return {
        "knowledge_id": knowledge.id,
        "collection_source_id": form_data.collection.source_id,
        "provider": provider,
        "total": len(form_data.documents),
        "created": created,
        "updated": updated,
        "errors": errors,
        "documents": results,
    }


@router.delete("/collections/{source_id}")
def delete_collection(
    request: Request,
    source_id: str,
    user=Depends(get_verified_user),
):
    provider, _ = get_integration_provider(request, user)

    knowledge = _find_kb_by_source_id(provider, source_id)
    if not knowledge:
        raise HTTPException(
            404, f"Collection '{source_id}' not found for provider '{provider}'"
        )

    if knowledge.type != provider:
        raise HTTPException(
            403, "Cannot delete collections belonging to another provider"
        )

    # Remove all files and vector data
    current_files = Knowledges.get_files_by_id(knowledge.id)
    file_ids = [f.id for f in current_files] if current_files else []
    for file_id in file_ids:
        try:
            VECTOR_DB_CLIENT.delete(
                collection_name=knowledge.id,
                filter={"file_id": file_id},
            )
        except Exception:
            pass
        Files.delete_file_by_id(file_id)

    Knowledges.soft_delete_by_id(knowledge.id)

    return {"status": "deleted", "source_id": source_id, "provider": provider}


@router.delete("/collections/{source_id}/documents/{document_source_id}")
def delete_document(
    request: Request,
    source_id: str,
    document_source_id: str,
    user=Depends(get_verified_user),
):
    provider, _ = get_integration_provider(request, user)

    knowledge = _find_kb_by_source_id(provider, source_id)
    if not knowledge:
        raise HTTPException(
            404, f"Collection '{source_id}' not found for provider '{provider}'"
        )

    if knowledge.type != provider:
        raise HTTPException(
            403, "Cannot delete documents from another provider's collection"
        )

    file_id = f"{provider}-{document_source_id}"
    file = Files.get_file_by_id(file_id)
    if not file:
        raise HTTPException(404, f"Document '{document_source_id}' not found")

    try:
        VECTOR_DB_CLIENT.delete(
            collection_name=knowledge.id,
            filter={"file_id": file_id},
        )
    except Exception:
        pass

    Knowledges.remove_file_from_knowledge_by_id(knowledge.id, file_id)
    Files.delete_file_by_id(file_id)

    return {
        "status": "deleted",
        "source_id": source_id,
        "document_source_id": document_source_id,
        "provider": provider,
    }
