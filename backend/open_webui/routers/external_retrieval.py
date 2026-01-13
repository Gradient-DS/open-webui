"""
External Pipeline Integration for Open WebUI RAG
This module contains all logic for integrating with external document processing pipelines.

Architecture:
- External pipeline receives: file (text/bytes) + metadata
- External pipeline returns: processed chunks (text + metadata)
- Open WebUI handles: embedding generation + vector storage
"""

import logging
import requests
from typing import Optional, List
from langchain_core.documents import Document

from open_webui.models.files import Files
from open_webui.utils.misc import calculate_sha256_string

log = logging.getLogger(__name__)


def check_external_pipeline_health(
    external_pipeline_url: str,
    external_pipeline_api_key: Optional[str] = None,
    timeout: int = 5,
) -> bool:
    """
    Check if external pipeline is available and healthy.
    
    Args:
        external_pipeline_url: Base URL of the external pipeline
        external_pipeline_api_key: Optional API key for authentication
        timeout: Request timeout in seconds (default: 5 for health check)
    
    Returns:
        bool: True if pipeline is healthy, False otherwise
    """
    try:
        headers = {}
        if external_pipeline_api_key:
            headers["Authorization"] = f"Bearer {external_pipeline_api_key}"
            headers["X-API-Key"] = external_pipeline_api_key
        
        base_url = external_pipeline_url.rstrip("/")
        health_url = f"{base_url}/health"
        
        response = requests.get(health_url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return True
    except Exception as e:
        log.warning(f"External pipeline health check failed: {e}")
        return False


def call_external_pipeline(
    file_path: str,
    filename: str,
    content_type: str,
    external_pipeline_url: str,
    external_pipeline_api_key: Optional[str] = None,
    timeout: int = 120,
    loader_instance=None,
) -> dict:
    """
    Load document text and call external pipeline to chunk it.
    
    This function:
    1. Loads the document using Open WebUI's loader (which may use external parsing via
       EXTERNAL_DOCUMENT_LOADER_URL if configured)
    2. Sends the extracted text to the /chunk endpoint for chunking
    3. Receives processed chunks back from the external pipeline
    
    The external pipeline's /chunk endpoint handles:
    - Text chunking
    - Chunk formatting for embedding
    
    Open WebUI handles:
    - Document loading/text extraction (via loader, potentially using external parser)
    - Embedding generation (after receiving chunks)
    - Vector database storage
    
    Args:
        file_path: Path to the uploaded file
        filename: Original filename
        content_type: MIME type of the file
        external_pipeline_url: Base URL of the external pipeline (e.g., http://localhost:6006)
        external_pipeline_api_key: Optional API key for authentication
        timeout: Request timeout in seconds (default: 120)
        loader_instance: Pre-configured Loader instance (optional, will be created if None)
    
    Returns:
        dict: Response from external pipeline with chunks in format:
            {
                "success": bool,
                "filename": str,
                "chunk_count": int,
                "chunks": List[{
                    "id": str,
                    "text": str,
                    "metadata": dict,
                }],
                "errors": List[dict],
                "processing_time": float
            }
    """
    try:
        headers = {"Content-Type": "application/json"}
        if external_pipeline_api_key:
            headers["Authorization"] = f"Bearer {external_pipeline_api_key}"
            headers["X-API-Key"] = external_pipeline_api_key
        
        # Construct full URL with /chunk endpoint
        base_url = external_pipeline_url.rstrip("/")
        endpoint_url = f"{base_url}/chunk"
        
        # Determine file type from content_type or filename
        filetype = "TXT"  # Default - text is already parsed
        if content_type:
            if "pdf" in content_type.lower():
                filetype = "PDF"
            elif "word" in content_type.lower() or "docx" in content_type.lower():
                filetype = "DOCX"
            elif "excel" in content_type.lower() or "xlsx" in content_type.lower():
                filetype = "XLSX"
            elif "powerpoint" in content_type.lower() or "pptx" in content_type.lower():
                filetype = "PPTX"
            elif "text" in content_type.lower():
                filetype = "TXT"
        
        # Load document using Open WebUI's loader to extract text
        # If EXTERNAL_DOCUMENT_LOADER_URL is configured, this will use the external parser
        log.info(f"Loading document: {filename} (type: {filetype})")
        
        if loader_instance is None:
            log.warning("No loader instance provided, external pipeline may not work correctly")
            raise Exception("Loader instance required for external pipeline")
        
        # Load document and extract text
        # The loader returns a list of Document objects with page_content
        # If using ExternalDocumentLoader, the text is already parsed by the external API
        docs = loader_instance.load(filename, content_type, file_path)
        
        if not docs:
            raise Exception("Loader returned no documents")
        
        # Combine all document page_content into single text
        text_content = "\n".join([doc.page_content for doc in docs])
        
        # Get total pages from metadata if available
        total_pages = 1
        if docs and hasattr(docs[0], 'metadata'):
            total_pages = docs[0].metadata.get('total_pages', len(docs))
        
        log.info(
            f"Loaded document: {len(text_content)} chars from {len(docs)} pages/sections"
        )
        
        # Prepare JSON payload for /chunk endpoint
        payload = {
            "text": text_content,
            "filename": filename,
            "filetype": filetype,
            "title": filename,
            "total_pages": total_pages,
        }
        
        log.info(f"Calling external pipeline: {endpoint_url} for chunking")
        response = requests.post(
            endpoint_url,
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        
        result = response.json()
        chunks_count = len(result.get("chunks", []))
        log.info(f"External pipeline returned {chunks_count} chunks")
        return result
            
    except requests.exceptions.Timeout as e:
        log.error(f"Timeout calling external pipeline after {timeout}s: {e}")
        raise Exception(f"External pipeline timeout after {timeout} seconds")
    except requests.exceptions.RequestException as e:
        log.error(f"Error calling external pipeline: {e}")
        if hasattr(e, 'response') and e.response is not None:
            log.error(f"Response status: {e.response.status_code}, body: {e.response.text}")
        raise Exception(f"Failed to call external pipeline: {str(e)}")
    except Exception as e:
        log.error(f"Unexpected error in external pipeline call: {e}")
        raise


def process_file_with_external_pipeline(
    request,
    file,
    file_path: str,
    collection_name: str,
    form_data,
    loader_instance,
    save_docs_to_vector_db_func,
    user,
) -> dict:
    """
    Process a file using the external pipeline.
    
    This is the main entry point for external pipeline processing. It:
    1. Calls the external pipeline to get processed chunks (via /chunk endpoint)
    2. Converts chunks to Document objects
    3. Uses save_docs_to_vector_db to handle embedding and storage
    4. Updates file metadata and status
    
    The flow is:
    1. loader_instance.load() extracts text (may use external parser if configured)
    2. Text is sent to /chunk endpoint for chunking
    3. Chunks are embedded and stored in vector DB
    
    Args:
        request: FastAPI request object (contains app.state.config)
        file: File object from database
        file_path: Path to the uploaded file
        collection_name: Name of the vector database collection
        form_data: Form data from the upload request
        loader_instance: Pre-configured Loader instance
        save_docs_to_vector_db_func: Function to save documents to vector DB
        user: User object
    
    Returns:
        dict: Processing result with status, collection_name, filename, and content
    
    Raises:
        Exception: If external pipeline fails
    """
    # Get external pipeline configuration
    external_pipeline_url = getattr(
        request.app.state.config, "EXTERNAL_PIPELINE_URL", None
    )
    external_pipeline_api_key = getattr(
        request.app.state.config, "EXTERNAL_PIPELINE_API_KEY", None
    )
    external_pipeline_timeout = getattr(
        request.app.state.config, "EXTERNAL_PIPELINE_TIMEOUT", 120
    )
    
    log.info(f"Processing file with external pipeline: {file.filename}")
    
    # Call external pipeline with loader instance
    # The flow is:
    # 1. loader_instance.load() - extracts text (uses external parser if EXTERNAL_DOCUMENT_LOADER_URL is set)
    # 2. Sends text to /chunk endpoint for chunking
    # 3. Returns chunks ready for embedding
    pipeline_result = call_external_pipeline(
        file_path=file_path,
        filename=file.filename,
        content_type=file.meta.get("content_type", "application/octet-stream"),
        external_pipeline_url=external_pipeline_url,
        external_pipeline_api_key=external_pipeline_api_key,
        timeout=external_pipeline_timeout,
        loader_instance=loader_instance,
    )
    
    # Extract chunks from pipeline result
    # /chunk endpoint response structure:
    # {
    #   "success": bool,
    #   "chunk_count": int,
    #   "chunks": [
    #     {
    #       "id": str,
    #       "text": str,
    #       "metadata": dict,
    #     }
    #   ]
    # }
    chunks = pipeline_result.get("chunks", [])
    if not chunks:
        raise ValueError("External pipeline returned no chunks")
    
    log.info(f"Received {len(chunks)} chunks from external pipeline")
    
    # Combine text from all chunks for file content
    text_content = " ".join([
        chunk.get("text", "")
        for chunk in chunks
    ])
    
    # Update file content and hash
    Files.update_file_data_by_id(
        file.id,
        {"content": text_content},
    )
    hash = calculate_sha256_string(text_content)
    Files.update_file_hash_by_id(file.id, hash)
    
    # Convert external chunks to Document objects
    # This allows reuse of save_docs_to_vector_db() which handles:
    # - Embedding generation
    # - Deduplication
    # - Vector DB insertion
    docs = [
        Document(
            page_content=chunk.get("text", ""),
            metadata={
                **chunk.get("metadata", {}),
                "name": file.filename,
                "created_by": file.user_id,
                "file_id": file.id,
                "source": file.filename,
                "chunk_id": chunk.get("id"),  # Use "id" from /chunk response
            },
        )
        for chunk in chunks
    ]
    
    # Use existing function to handle embedding, dedup, and insert
    result = save_docs_to_vector_db_func(
        request,
        docs=docs,
        collection_name=collection_name,
        metadata={"file_id": file.id, "name": file.filename, "hash": hash},
        split=False,  # External pipeline already chunked
        add=(True if form_data.collection_name else False),
        user=user,
    )
    
    log.info(f"Saved {len(chunks)} chunks to collection {collection_name} via external pipeline")
    
    if result:
        Files.update_file_metadata_by_id(
            file.id,
            {
                "collection_name": collection_name,
            },
        )
        
        Files.update_file_data_by_id(
            file.id,
            {"status": "completed"},
        )
        
        return {
            "status": True,
            "collection_name": collection_name,
            "filename": file.filename,
            "content": text_content,
        }
    
    raise Exception("Failed to save documents to vector database")

