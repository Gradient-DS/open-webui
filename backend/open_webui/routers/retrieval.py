from __future__ import annotations

import asyncio
import io
import logging
import mimetypes
import os
import re
import shutil
from types import SimpleNamespace
from typing import List, Optional, Union
from urllib.parse import unquote, urlparse

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from langchain_core.documents import Document
from open_webui.config import (
    DEFAULT_LOCALE,
    UPLOAD_DIR,
)
from open_webui.constants import ERROR_MESSAGES
from open_webui.env import (
    AIOHTTP_CLIENT_ALLOW_REDIRECTS,
    AIOHTTP_CLIENT_SESSION_SSL,
)
from open_webui.events import EVENTS, publish_event
from open_webui.internal.db import get_async_db, get_async_session
from open_webui.models.config import Config
from open_webui.models.files import FileModel, Files
from open_webui.models.knowledge import Knowledges

# Document loaders
from open_webui.retrieval.loaders.youtube import YoutubeTranscriptError
from open_webui.retrieval.utils import (
    filter_accessible_collections,
    get_content_from_url,
    get_loader_config,
    is_youtube_url,
)
from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
from open_webui.retrieval.web.azure import search_azure
from open_webui.retrieval.web.bing import search_bing
from open_webui.retrieval.web.bocha import search_bocha
from open_webui.retrieval.web.brave import search_brave
from open_webui.retrieval.web.brave_llm_context import search_brave_llm_context
from open_webui.retrieval.web.duckduckgo import search_duckduckgo
from open_webui.retrieval.web.exa import search_exa
from open_webui.retrieval.web.external import search_external
from open_webui.retrieval.web.firecrawl import search_firecrawl
from open_webui.retrieval.web.google_pse import search_google_pse
from open_webui.retrieval.web.jina_search import search_jina
from open_webui.retrieval.web.kagi import search_kagi
from open_webui.retrieval.web.linkup import search_linkup

# Web search engines
from open_webui.retrieval.web.main import SearchResult
from open_webui.retrieval.web.microsoft_web_iq import search_microsoft_web_iq
from open_webui.retrieval.web.mojeek import search_mojeek
from open_webui.retrieval.web.ollama import search_ollama_cloud
from open_webui.retrieval.web.openserp import search_openserp
from open_webui.retrieval.web.perplexity import search_perplexity
from open_webui.retrieval.web.perplexity_search import search_perplexity_search
from open_webui.retrieval.web.searchapi import search_searchapi
from open_webui.retrieval.web.searxng import search_searxng
from open_webui.retrieval.web.serpapi import search_serpapi
from open_webui.retrieval.web.serper import search_serper
from open_webui.retrieval.web.serphouse import search_serphouse
from open_webui.retrieval.web.serply import search_serply
from open_webui.retrieval.web.serpstack import search_serpstack
from open_webui.retrieval.web.sougou import search_sougou
from open_webui.retrieval.web.tavily import search_tavily
from open_webui.retrieval.web.utils import get_ssrf_safe_session, get_web_loader, validate_url
from open_webui.retrieval.web.yacy import search_yacy
from open_webui.retrieval.web.yandex import search_yandex
from open_webui.retrieval.web.ydc import search_youcom
from open_webui.soev import identity, ingest
from open_webui.soev.availability import SoevNotConfigured, configured, ensure_configured
from open_webui.soev.client import SoevApiError
from open_webui.utils.access_control import has_permission
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.content_types import content_type_for  # [Gradient]
from open_webui.utils.loop_bridge import run_on_main_loop
from open_webui.utils.misc import (
    calculate_sha256_string,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from open_webui.utils.access_control import can_bypass_access_control

log = logging.getLogger(__name__)

TIKTOKEN_DISALLOWED_SPECIAL = ()

# [Gradient] ── Per-key config access (replaces the removed per-request app-state config object) ──
# Legacy UPPERCASE attribute name -> per-key Config storage key. Authoritative mapping
# derived from config.py::DEFAULT_CONFIG (upstream renamed several rag.* keys).
RAG_CONFIG_KEYS: dict[str, str] = {
    'ALLOWED_FILE_EXTENSIONS': 'rag.file.allowed_extensions',
    'AZURE_AI_SEARCH_API_KEY': 'web.search.azure_ai_search_api_key',
    'AZURE_AI_SEARCH_ENDPOINT': 'web.search.azure_ai_search_endpoint',
    'AZURE_AI_SEARCH_INDEX_NAME': 'web.search.azure_ai_search_index_name',
    'BING_SEARCH_V7_ENDPOINT': 'web.search.bing_search_v7_endpoint',
    'BING_SEARCH_V7_SUBSCRIPTION_KEY': 'web.search.bing_search_v7_subscription_key',
    'BOCHA_SEARCH_API_KEY': 'web.search.bocha_search_api_key',
    'BRAVE_SEARCH_API_KEY': 'web.search.brave_search_api_key',
    'BRAVE_SEARCH_CONTEXT_TOKENS': 'web.search.brave_search_context_tokens',
    'BYPASS_EMBEDDING_AND_RETRIEVAL': 'rag.bypass_embedding_and_retrieval',
    'BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL': 'web.search.bypass_embedding_and_retrieval',
    'BYPASS_WEB_SEARCH_WEB_LOADER': 'web.search.bypass_web_loader',
    'CHUNK_MIN_SIZE_TARGET': 'rag.chunk_min_size_target',
    'CHUNK_OVERLAP': 'rag.chunk_overlap',
    'CHUNK_SIZE': 'rag.chunk_size',
    'CONTENT_EXTRACTION_SUPPORTED_MEDIA_MIME_TYPES': 'rag.content_extraction.supported_media_mime_types',
    'CONTENT_EXTRACTION_ENGINE': 'rag.content_extraction_engine',
    'DATALAB_MARKER_ADDITIONAL_CONFIG': 'rag.datalab_marker_additional_config',
    'DATALAB_MARKER_API_BASE_URL': 'rag.datalab_marker_api_base_url',
    'DATALAB_MARKER_API_KEY': 'rag.datalab_marker_api_key',
    'DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION': 'rag.datalab_marker_disable_image_extraction',
    'DATALAB_MARKER_FORCE_OCR': 'rag.datalab_marker_force_ocr',
    'DATALAB_MARKER_FORMAT_LINES': 'rag.datalab_marker_format_lines',
    'DATALAB_MARKER_OUTPUT_FORMAT': 'rag.datalab_marker_output_format',
    'DATALAB_MARKER_PAGINATE': 'rag.datalab_marker_paginate',
    'DATALAB_MARKER_SKIP_CACHE': 'rag.datalab_marker_skip_cache',
    'DATALAB_MARKER_STRIP_EXISTING_OCR': 'rag.datalab_marker_strip_existing_ocr',
    'DATALAB_MARKER_USE_LLM': 'rag.datalab_marker_use_llm',
    'DDGS_BACKEND': 'web.search.ddgs_backend',
    'DOCLING_API_KEY': 'rag.docling_api_key',
    'DOCLING_PARAMS': 'rag.docling_params',
    'DOCLING_SERVER_URL': 'rag.docling_server_url',
    'DOCUMENT_INTELLIGENCE_ENDPOINT': 'rag.document_intelligence_endpoint',
    'DOCUMENT_INTELLIGENCE_KEY': 'rag.document_intelligence_key',
    'DOCUMENT_INTELLIGENCE_MODEL': 'rag.document_intelligence_model',
    'ENABLE_CONFLUENCE_INTEGRATION': 'confluence.enable',
    'ENABLE_GOOGLE_DRIVE_INTEGRATION': 'google_drive.enable',
    'ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER': 'rag.enable_markdown_header_text_splitter',
    'ENABLE_ONEDRIVE_INTEGRATION': 'onedrive.enable',
    'ENABLE_RAG_HYBRID_SEARCH': 'rag.enable_hybrid_search',
    'ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS': 'rag.enable_hybrid_search_enriched_texts',
    'ENABLE_WEB_LOADER_SSL_VERIFICATION': 'web.loader.ssl_verification',
    'ENABLE_WEB_SEARCH': 'web.search.enable',
    'EXA_API_KEY': 'web.search.exa_api_key',
    'EXTERNAL_DOCUMENT_LOADER_API_KEY': 'rag.external_document_loader_api_key',
    'EXTERNAL_DOCUMENT_LOADER_URL': 'rag.external_document_loader_url',
    'EXTERNAL_WEB_LOADER_API_KEY': 'web.loader.external_web_loader_api_key',
    'EXTERNAL_WEB_LOADER_URL': 'web.loader.external_web_loader_url',
    'EXTERNAL_WEB_SEARCH_API_KEY': 'web.search.external_web_search_api_key',
    'EXTERNAL_WEB_SEARCH_URL': 'web.search.external_web_search_url',
    'FILE_IMAGE_COMPRESSION_HEIGHT': 'file.image_compression_height',
    'FILE_IMAGE_COMPRESSION_WIDTH': 'file.image_compression_width',
    'FILE_MAX_COUNT': 'rag.file.max_count',
    'FILE_MAX_SIZE': 'rag.file.max_size',
    'FIRECRAWL_API_BASE_URL': 'web.loader.firecrawl_api_url',
    'FIRECRAWL_API_KEY': 'web.loader.firecrawl_api_key',
    'FIRECRAWL_TIMEOUT': 'web.loader.firecrawl_timeout',
    'GOOGLE_PSE_API_KEY': 'web.search.google_pse_api_key',
    'GOOGLE_PSE_ENGINE_ID': 'web.search.google_pse_engine_id',
    'HYBRID_BM25_WEIGHT': 'rag.hybrid_bm25_weight',
    'JINA_API_BASE_URL': 'web.search.jina_api_base_url',
    'JINA_API_KEY': 'web.search.jina_api_key',
    'KAGI_SEARCH_API_KEY': 'web.search.kagi_search_api_key',
    'MINERU_API_KEY': 'rag.mineru_api_key',
    'MINERU_API_MODE': 'rag.mineru_api_mode',
    'MINERU_API_TIMEOUT': 'rag.mineru_api_timeout',
    'MINERU_API_URL': 'rag.mineru_api_url',
    'MINERU_PARAMS': 'rag.mineru_params',
    'MISTRAL_OCR_API_BASE_URL': 'rag.mistral_ocr_api_base_url',
    'MISTRAL_OCR_API_KEY': 'rag.mistral_ocr_api_key',
    'MOJEEK_SEARCH_API_KEY': 'web.search.mojeek_search_api_key',
    'OLLAMA_CLOUD_WEB_SEARCH_API_KEY': 'web.search.ollama_cloud_api_key',
    'PADDLEOCR_VL_BASE_URL': 'rag.paddleocr_vl_base_url',
    'PADDLEOCR_VL_TOKEN': 'rag.paddleocr_vl_token',
    'PDF_EXTRACT_IMAGES': 'rag.pdf_extract_images',
    'PDF_LOADER_MODE': 'rag.pdf_loader_mode',
    'PERPLEXITY_API_KEY': 'web.search.perplexity_api_key',
    'PERPLEXITY_MODEL': 'web.search.perplexity_model',
    'PERPLEXITY_SEARCH_API_URL': 'web.search.perplexity_search_api_url',
    'PERPLEXITY_SEARCH_CONTEXT_USAGE': 'web.search.perplexity_search_context_usage',
    'PLAYWRIGHT_TIMEOUT': 'web.loader.playwright_timeout',
    'PLAYWRIGHT_WS_URL': 'web.loader.playwright_ws_url',
    'RAG_FULL_CONTEXT': 'rag.full_context',
    'RAG_TEMPLATE': 'rag.template',
    'RELEVANCE_THRESHOLD': 'rag.relevance_threshold',
    'SEARCHAPI_API_KEY': 'web.search.searchapi_api_key',
    'SEARCHAPI_ENGINE': 'web.search.searchapi_engine',
    'SEARXNG_LANGUAGE': 'web.search.searxng_language',
    'SEARXNG_QUERY_URL': 'web.search.searxng_query_url',
    'OPENSERP_BASE_URL': 'web.search.openserp_base_url',
    'SERPAPI_API_KEY': 'web.search.serpapi_api_key',
    'SERPAPI_ENGINE': 'web.search.serpapi_engine',
    'SERPER_API_KEY': 'web.search.serper_api_key',
    'SERPLY_API_KEY': 'web.search.serply_api_key',
    'SERPSTACK_API_KEY': 'web.search.serpstack_api_key',
    'SERPSTACK_HTTPS': 'web.search.serpstack_https',
    'SOUGOU_API_SID': 'web.search.sougou_api_sid',
    'SOUGOU_API_SK': 'web.search.sougou_api_sk',
    'TAVILY_API_KEY': 'web.search.tavily_api_key',
    'TAVILY_EXTRACT_DEPTH': 'web.search.tavily_extract_depth',
    'TEXT_SPLITTER': 'rag.text_splitter',
    'TIKA_SERVER_URL': 'rag.tika_server_url',
    'TIKA_SERVER_VERSION': 'rag.tika_server_version',
    'TIKTOKEN_ENCODING_NAME': 'rag.tiktoken_encoding_name',
    'TOP_K': 'rag.top_k',
    'TOP_K_RERANKER': 'rag.top_k_reranker',
    'USER_PERMISSIONS': 'user.permissions',
    'WEBUI_URL': 'webui.url',
    'WEB_FETCH_MAX_CONTENT_LENGTH': 'web.fetch.max_content_length',
    'WEB_LOADER_CONCURRENT_REQUESTS': 'web.loader.concurrent_requests',
    'WEB_LOADER_ENGINE': 'web.loader.engine',
    'WEB_LOADER_TIMEOUT': 'web.loader.timeout',
    'WEB_SEARCH_CONCURRENT_REQUESTS': 'web.search.concurrent_requests',
    'WEB_SEARCH_DOMAIN_FILTER_LIST': 'web.search.domain.filter_list',
    'WEB_SEARCH_ENGINE': 'web.search.engine',
    'WEB_SEARCH_RESULT_COUNT': 'web.search.result_count',
    'WEB_SEARCH_TRUST_ENV': 'web.search.trust_env',
    'YACY_PASSWORD': 'web.search.yacy_password',
    'YACY_QUERY_URL': 'web.search.yacy_query_url',
    'YACY_USERNAME': 'web.search.yacy_username',
    'YANDEX_WEB_SEARCH_API_KEY': 'web.search.yandex_web_search_api_key',
    'YANDEX_WEB_SEARCH_CONFIG': 'web.search.yandex_web_search_config',
    'YANDEX_WEB_SEARCH_URL': 'web.search.yandex_web_search_url',
    'YOUCOM_API_KEY': 'web.search.youcom_api_key',
    'YOUTUBE_LOADER_LANGUAGE': 'rag.youtube_loader_language',
    'YOUTUBE_LOADER_PROXY_URL': 'rag.youtube_loader_proxy_url',
    'ENABLE_WEB_SEARCH_CONFIRMATION': 'web.search.confirmation.enable',
    'EXTERNAL_DOCUMENT_LOADER_HEADERS': 'rag.external_document_loader_headers',
    'LINKUP_API_KEY': 'web.search.linkup_api_key',
    'LINKUP_SEARCH_PARAMS': 'web.search.linkup_search_params',
    'MICROSOFT_WEB_IQ_API_BASE_URL': 'web.search.microsoft_web_iq_api_base_url',
    'MICROSOFT_WEB_IQ_API_KEY': 'web.search.microsoft_web_iq_api_key',
    'MICROSOFT_WEB_IQ_LANGUAGE': 'web.search.microsoft_web_iq_language',
    'MINERU_FILE_EXTENSIONS': 'rag.mineru_file_extensions',
    'MISTRAL_OCR_USE_BASE64': 'rag.mistral_ocr_use_base64',
    'RAG_TOKENIZER_MODEL': 'rag.tokenizer_model',
    'SERPHOUSE_API_KEY': 'web.search.serphouse_api_key',
    'SERPHOUSE_DOMAIN': 'web.search.serphouse_domain',
    'WEB_SEARCH_CONFIRMATION_CONTENT': 'web.search.confirmation.content',
}


async def get_rag_config_state() -> SimpleNamespace:
    """All retrieval config as an attribute-access namespace (one Config.get_many).

    Drop-in for the removed per-request app-state config object: handlers keep the
    UPPERCASE attribute names they always used; values now come from the per-key
    Config store. Sync callers (threadpool workers) fetch this via run_on_main_loop.
    """
    values = await Config.get_many(*RAG_CONFIG_KEYS.values())
    return SimpleNamespace(**{attr: values.get(key) for attr, key in RAG_CONFIG_KEYS.items()})


##########################################
#
# Utility functions
# Give us this day our relevant chunks, and lead us
# not into hallucination, but deliver us from noise.
#
##########################################


##########################################
#
# API routes
#
##########################################


router = APIRouter()


class CollectionNameForm(BaseModel):
    collection_name: Optional[str] = None


class ProcessUrlForm(CollectionNameForm):
    url: str


class ProcessUrlResponse(BaseModel):
    status: bool
    type: str
    name: str
    url: str
    collection_name: str | None = None
    content: str | None = None
    file: dict | None = None


class SearchForm(BaseModel):
    queries: List[str]


@router.get('/config')
async def get_rag_config(request: Request, user=Depends(get_admin_user)):
    config = await get_rag_config_state()
    return {
        'status': True,
        # RAG settings
        'RAG_TEMPLATE': config.RAG_TEMPLATE,
        'TOP_K': config.TOP_K,
        'BYPASS_EMBEDDING_AND_RETRIEVAL': config.BYPASS_EMBEDDING_AND_RETRIEVAL,
        'RAG_FULL_CONTEXT': config.RAG_FULL_CONTEXT,
        # Hybrid search settings
        'ENABLE_RAG_HYBRID_SEARCH': config.ENABLE_RAG_HYBRID_SEARCH,
        'ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS': config.ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS,
        'TOP_K_RERANKER': config.TOP_K_RERANKER,
        'RELEVANCE_THRESHOLD': config.RELEVANCE_THRESHOLD,
        'HYBRID_BM25_WEIGHT': config.HYBRID_BM25_WEIGHT,
        # Content extraction settings
        'CONTENT_EXTRACTION_ENGINE': config.CONTENT_EXTRACTION_ENGINE,
        'CONTENT_EXTRACTION_SUPPORTED_MEDIA_MIME_TYPES': config.CONTENT_EXTRACTION_SUPPORTED_MEDIA_MIME_TYPES,
        'PDF_EXTRACT_IMAGES': config.PDF_EXTRACT_IMAGES,
        'PDF_LOADER_MODE': config.PDF_LOADER_MODE,
        'DATALAB_MARKER_API_KEY': config.DATALAB_MARKER_API_KEY,
        'DATALAB_MARKER_API_BASE_URL': config.DATALAB_MARKER_API_BASE_URL,
        'DATALAB_MARKER_ADDITIONAL_CONFIG': config.DATALAB_MARKER_ADDITIONAL_CONFIG,
        'DATALAB_MARKER_SKIP_CACHE': config.DATALAB_MARKER_SKIP_CACHE,
        'DATALAB_MARKER_FORCE_OCR': config.DATALAB_MARKER_FORCE_OCR,
        'DATALAB_MARKER_PAGINATE': config.DATALAB_MARKER_PAGINATE,
        'DATALAB_MARKER_STRIP_EXISTING_OCR': config.DATALAB_MARKER_STRIP_EXISTING_OCR,
        'DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION': config.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION,
        'DATALAB_MARKER_FORMAT_LINES': config.DATALAB_MARKER_FORMAT_LINES,
        'DATALAB_MARKER_USE_LLM': config.DATALAB_MARKER_USE_LLM,
        'DATALAB_MARKER_OUTPUT_FORMAT': config.DATALAB_MARKER_OUTPUT_FORMAT,
        'EXTERNAL_DOCUMENT_LOADER_URL': config.EXTERNAL_DOCUMENT_LOADER_URL,
        'EXTERNAL_DOCUMENT_LOADER_API_KEY': config.EXTERNAL_DOCUMENT_LOADER_API_KEY,
        'EXTERNAL_DOCUMENT_LOADER_HEADERS': config.EXTERNAL_DOCUMENT_LOADER_HEADERS,
        'TIKA_SERVER_URL': config.TIKA_SERVER_URL,
        'TIKA_SERVER_VERSION': config.TIKA_SERVER_VERSION,
        'DOCLING_SERVER_URL': config.DOCLING_SERVER_URL,
        'DOCLING_API_KEY': config.DOCLING_API_KEY,
        'DOCLING_PARAMS': config.DOCLING_PARAMS,
        'DOCUMENT_INTELLIGENCE_ENDPOINT': config.DOCUMENT_INTELLIGENCE_ENDPOINT,
        'DOCUMENT_INTELLIGENCE_KEY': config.DOCUMENT_INTELLIGENCE_KEY,
        'DOCUMENT_INTELLIGENCE_MODEL': config.DOCUMENT_INTELLIGENCE_MODEL,
        'MISTRAL_OCR_API_BASE_URL': config.MISTRAL_OCR_API_BASE_URL,
        'MISTRAL_OCR_API_KEY': config.MISTRAL_OCR_API_KEY,
        'MISTRAL_OCR_USE_BASE64': config.MISTRAL_OCR_USE_BASE64,
        'PADDLEOCR_VL_BASE_URL': config.PADDLEOCR_VL_BASE_URL,
        'PADDLEOCR_VL_TOKEN': config.PADDLEOCR_VL_TOKEN,
        # MinerU settings
        'MINERU_API_MODE': config.MINERU_API_MODE,
        'MINERU_API_URL': config.MINERU_API_URL,
        'MINERU_API_KEY': config.MINERU_API_KEY,
        'MINERU_API_TIMEOUT': config.MINERU_API_TIMEOUT,
        'MINERU_PARAMS': config.MINERU_PARAMS,
        'MINERU_FILE_EXTENSIONS': config.MINERU_FILE_EXTENSIONS,
        # Chunking settings
        'TEXT_SPLITTER': config.TEXT_SPLITTER,
        'RAG_TOKENIZER_MODEL': config.RAG_TOKENIZER_MODEL,
        'ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER': config.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER,
        'CHUNK_SIZE': config.CHUNK_SIZE,
        'CHUNK_MIN_SIZE_TARGET': config.CHUNK_MIN_SIZE_TARGET,
        'CHUNK_OVERLAP': config.CHUNK_OVERLAP,
        # File upload settings
        'FILE_MAX_SIZE': config.FILE_MAX_SIZE,
        'FILE_MAX_COUNT': config.FILE_MAX_COUNT,
        'FILE_IMAGE_COMPRESSION_WIDTH': config.FILE_IMAGE_COMPRESSION_WIDTH,
        'FILE_IMAGE_COMPRESSION_HEIGHT': config.FILE_IMAGE_COMPRESSION_HEIGHT,
        'ALLOWED_FILE_EXTENSIONS': config.ALLOWED_FILE_EXTENSIONS,
        # Integration settings
        'ENABLE_GOOGLE_DRIVE_INTEGRATION': config.ENABLE_GOOGLE_DRIVE_INTEGRATION,
        'ENABLE_ONEDRIVE_INTEGRATION': config.ENABLE_ONEDRIVE_INTEGRATION,
        'ENABLE_CONFLUENCE_INTEGRATION': config.ENABLE_CONFLUENCE_INTEGRATION,
        # Web search settings
        'web': {
            'ENABLE_WEB_SEARCH': config.ENABLE_WEB_SEARCH,
            'ENABLE_WEB_SEARCH_CONFIRMATION': config.ENABLE_WEB_SEARCH_CONFIRMATION,
            'WEB_SEARCH_CONFIRMATION_CONTENT': config.WEB_SEARCH_CONFIRMATION_CONTENT,
            'WEB_SEARCH_ENGINE': config.WEB_SEARCH_ENGINE,
            'WEB_SEARCH_TRUST_ENV': config.WEB_SEARCH_TRUST_ENV,
            'WEB_SEARCH_RESULT_COUNT': config.WEB_SEARCH_RESULT_COUNT,
            'WEB_SEARCH_CONCURRENT_REQUESTS': config.WEB_SEARCH_CONCURRENT_REQUESTS,
            'WEB_FETCH_MAX_CONTENT_LENGTH': config.WEB_FETCH_MAX_CONTENT_LENGTH,
            'WEB_LOADER_CONCURRENT_REQUESTS': config.WEB_LOADER_CONCURRENT_REQUESTS,
            'WEB_SEARCH_DOMAIN_FILTER_LIST': config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            'BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL': config.BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL,
            'BYPASS_WEB_SEARCH_WEB_LOADER': config.BYPASS_WEB_SEARCH_WEB_LOADER,
            'OLLAMA_CLOUD_WEB_SEARCH_API_KEY': config.OLLAMA_CLOUD_WEB_SEARCH_API_KEY,
            'SEARXNG_QUERY_URL': config.SEARXNG_QUERY_URL,
            'SEARXNG_LANGUAGE': config.SEARXNG_LANGUAGE,
            'OPENSERP_BASE_URL': config.OPENSERP_BASE_URL,
            'YACY_QUERY_URL': config.YACY_QUERY_URL,
            'YACY_USERNAME': config.YACY_USERNAME,
            'YACY_PASSWORD': config.YACY_PASSWORD,
            'GOOGLE_PSE_API_KEY': config.GOOGLE_PSE_API_KEY,
            'GOOGLE_PSE_ENGINE_ID': config.GOOGLE_PSE_ENGINE_ID,
            'BRAVE_SEARCH_API_KEY': config.BRAVE_SEARCH_API_KEY,
            'BRAVE_SEARCH_CONTEXT_TOKENS': config.BRAVE_SEARCH_CONTEXT_TOKENS,
            'KAGI_SEARCH_API_KEY': config.KAGI_SEARCH_API_KEY,
            'MOJEEK_SEARCH_API_KEY': config.MOJEEK_SEARCH_API_KEY,
            'BOCHA_SEARCH_API_KEY': config.BOCHA_SEARCH_API_KEY,
            'SERPSTACK_API_KEY': config.SERPSTACK_API_KEY,
            'SERPSTACK_HTTPS': config.SERPSTACK_HTTPS,
            'SERPER_API_KEY': config.SERPER_API_KEY,
            'SERPHOUSE_API_KEY': config.SERPHOUSE_API_KEY,
            'SERPHOUSE_DOMAIN': config.SERPHOUSE_DOMAIN,
            'SERPLY_API_KEY': config.SERPLY_API_KEY,
            'DDGS_BACKEND': config.DDGS_BACKEND,
            'TAVILY_API_KEY': config.TAVILY_API_KEY,
            'SEARCHAPI_API_KEY': config.SEARCHAPI_API_KEY,
            'SEARCHAPI_ENGINE': config.SEARCHAPI_ENGINE,
            'SERPAPI_API_KEY': config.SERPAPI_API_KEY,
            'SERPAPI_ENGINE': config.SERPAPI_ENGINE,
            'JINA_API_KEY': config.JINA_API_KEY,
            'JINA_API_BASE_URL': config.JINA_API_BASE_URL,
            'BING_SEARCH_V7_ENDPOINT': config.BING_SEARCH_V7_ENDPOINT,
            'BING_SEARCH_V7_SUBSCRIPTION_KEY': config.BING_SEARCH_V7_SUBSCRIPTION_KEY,
            'EXA_API_KEY': config.EXA_API_KEY,
            'PERPLEXITY_API_KEY': config.PERPLEXITY_API_KEY,
            'PERPLEXITY_MODEL': config.PERPLEXITY_MODEL,
            'PERPLEXITY_SEARCH_CONTEXT_USAGE': config.PERPLEXITY_SEARCH_CONTEXT_USAGE,
            'PERPLEXITY_SEARCH_API_URL': config.PERPLEXITY_SEARCH_API_URL,
            'MICROSOFT_WEB_IQ_API_BASE_URL': config.MICROSOFT_WEB_IQ_API_BASE_URL,
            'MICROSOFT_WEB_IQ_API_KEY': config.MICROSOFT_WEB_IQ_API_KEY,
            'MICROSOFT_WEB_IQ_LANGUAGE': config.MICROSOFT_WEB_IQ_LANGUAGE,
            'SOUGOU_API_SID': config.SOUGOU_API_SID,
            'SOUGOU_API_SK': config.SOUGOU_API_SK,
            'WEB_LOADER_ENGINE': config.WEB_LOADER_ENGINE,
            'WEB_LOADER_TIMEOUT': config.WEB_LOADER_TIMEOUT,
            'ENABLE_WEB_LOADER_SSL_VERIFICATION': config.ENABLE_WEB_LOADER_SSL_VERIFICATION,
            'PLAYWRIGHT_WS_URL': config.PLAYWRIGHT_WS_URL,
            'PLAYWRIGHT_TIMEOUT': config.PLAYWRIGHT_TIMEOUT,
            'FIRECRAWL_API_KEY': config.FIRECRAWL_API_KEY,
            'FIRECRAWL_API_BASE_URL': config.FIRECRAWL_API_BASE_URL,
            'FIRECRAWL_TIMEOUT': config.FIRECRAWL_TIMEOUT,
            'TAVILY_EXTRACT_DEPTH': config.TAVILY_EXTRACT_DEPTH,
            'EXTERNAL_WEB_SEARCH_URL': config.EXTERNAL_WEB_SEARCH_URL,
            'EXTERNAL_WEB_SEARCH_API_KEY': config.EXTERNAL_WEB_SEARCH_API_KEY,
            'EXTERNAL_WEB_LOADER_URL': config.EXTERNAL_WEB_LOADER_URL,
            'EXTERNAL_WEB_LOADER_API_KEY': config.EXTERNAL_WEB_LOADER_API_KEY,
            'YOUTUBE_LOADER_LANGUAGE': config.YOUTUBE_LOADER_LANGUAGE,
            'YOUTUBE_LOADER_PROXY_URL': config.YOUTUBE_LOADER_PROXY_URL,
            'YOUTUBE_LOADER_TRANSLATION': request.app.state.YOUTUBE_LOADER_TRANSLATION,
            'YANDEX_WEB_SEARCH_URL': config.YANDEX_WEB_SEARCH_URL,
            'YANDEX_WEB_SEARCH_API_KEY': config.YANDEX_WEB_SEARCH_API_KEY,
            'YANDEX_WEB_SEARCH_CONFIG': config.YANDEX_WEB_SEARCH_CONFIG,
            'YOUCOM_API_KEY': config.YOUCOM_API_KEY,
            'LINKUP_API_KEY': config.LINKUP_API_KEY,
            'LINKUP_SEARCH_PARAMS': config.LINKUP_SEARCH_PARAMS,
        },
    }


class WebConfig(BaseModel):
    ENABLE_WEB_SEARCH: Optional[bool] = None
    WEB_SEARCH_ENGINE: Optional[str] = None
    WEB_SEARCH_TRUST_ENV: Optional[bool] = None
    WEB_SEARCH_RESULT_COUNT: Optional[int] = None
    WEB_SEARCH_CONCURRENT_REQUESTS: Optional[int] = None
    WEB_FETCH_MAX_CONTENT_LENGTH: Optional[int] = None
    WEB_LOADER_CONCURRENT_REQUESTS: Optional[int] = None
    WEB_SEARCH_DOMAIN_FILTER_LIST: Optional[List[str]] = []
    BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL: Optional[bool] = None
    BYPASS_WEB_SEARCH_WEB_LOADER: Optional[bool] = None
    OLLAMA_CLOUD_WEB_SEARCH_API_KEY: Optional[str] = None
    SEARXNG_QUERY_URL: Optional[str] = None
    SEARXNG_LANGUAGE: Optional[str] = None
    YACY_QUERY_URL: Optional[str] = None
    YACY_USERNAME: Optional[str] = None
    YACY_PASSWORD: Optional[str] = None
    GOOGLE_PSE_API_KEY: Optional[str] = None
    GOOGLE_PSE_ENGINE_ID: Optional[str] = None
    BRAVE_SEARCH_API_KEY: Optional[str] = None
    BRAVE_SEARCH_CONTEXT_TOKENS: Optional[int] = None
    KAGI_SEARCH_API_KEY: Optional[str] = None
    MOJEEK_SEARCH_API_KEY: Optional[str] = None
    BOCHA_SEARCH_API_KEY: Optional[str] = None
    SERPSTACK_API_KEY: Optional[str] = None
    SERPSTACK_HTTPS: Optional[bool] = None
    SERPER_API_KEY: Optional[str] = None
    SERPLY_API_KEY: Optional[str] = None
    DDGS_BACKEND: Optional[str] = None
    TAVILY_API_KEY: Optional[str] = None
    SEARCHAPI_API_KEY: Optional[str] = None
    SEARCHAPI_ENGINE: Optional[str] = None
    SERPAPI_API_KEY: Optional[str] = None
    SERPAPI_ENGINE: Optional[str] = None
    JINA_API_KEY: Optional[str] = None
    JINA_API_BASE_URL: Optional[str] = None
    BING_SEARCH_V7_ENDPOINT: Optional[str] = None
    BING_SEARCH_V7_SUBSCRIPTION_KEY: Optional[str] = None
    EXA_API_KEY: Optional[str] = None
    PERPLEXITY_API_KEY: Optional[str] = None
    PERPLEXITY_MODEL: Optional[str] = None
    PERPLEXITY_SEARCH_CONTEXT_USAGE: Optional[str] = None
    PERPLEXITY_SEARCH_API_URL: Optional[str] = None
    SOUGOU_API_SID: Optional[str] = None
    SOUGOU_API_SK: Optional[str] = None
    WEB_LOADER_ENGINE: Optional[str] = None
    WEB_LOADER_TIMEOUT: Optional[str] = None
    ENABLE_WEB_LOADER_SSL_VERIFICATION: Optional[bool] = None
    PLAYWRIGHT_WS_URL: Optional[str] = None
    PLAYWRIGHT_TIMEOUT: Optional[int] = None
    FIRECRAWL_API_KEY: Optional[str] = None
    FIRECRAWL_API_BASE_URL: Optional[str] = None
    FIRECRAWL_TIMEOUT: Optional[str] = None
    TAVILY_EXTRACT_DEPTH: Optional[str] = None
    EXTERNAL_WEB_SEARCH_URL: Optional[str] = None
    EXTERNAL_WEB_SEARCH_API_KEY: Optional[str] = None
    EXTERNAL_WEB_LOADER_URL: Optional[str] = None
    EXTERNAL_WEB_LOADER_API_KEY: Optional[str] = None
    YOUTUBE_LOADER_LANGUAGE: Optional[List[str]] = None
    YOUTUBE_LOADER_PROXY_URL: Optional[str] = None
    YOUTUBE_LOADER_TRANSLATION: Optional[str] = None
    YANDEX_WEB_SEARCH_URL: Optional[str] = None
    YANDEX_WEB_SEARCH_API_KEY: Optional[str] = None
    YANDEX_WEB_SEARCH_CONFIG: Optional[str] = None
    YOUCOM_API_KEY: Optional[str] = None
    ENABLE_WEB_SEARCH_CONFIRMATION: Optional[bool] = None
    WEB_SEARCH_CONFIRMATION_CONTENT: Optional[str] = None
    OPENSERP_BASE_URL: Optional[str] = None
    SERPHOUSE_API_KEY: Optional[str] = None
    SERPHOUSE_DOMAIN: Optional[str] = None
    MICROSOFT_WEB_IQ_API_BASE_URL: Optional[str] = None
    MICROSOFT_WEB_IQ_API_KEY: Optional[str] = None
    MICROSOFT_WEB_IQ_LANGUAGE: Optional[str] = None
    LINKUP_API_KEY: Optional[str] = None
    LINKUP_SEARCH_PARAMS: Optional[dict] = None


class ConfigForm(BaseModel):
    # RAG settings
    RAG_TEMPLATE: Optional[str] = None
    TOP_K: Optional[int] = None
    BYPASS_EMBEDDING_AND_RETRIEVAL: Optional[bool] = None
    RAG_FULL_CONTEXT: Optional[bool] = None

    # Hybrid search settings
    ENABLE_RAG_HYBRID_SEARCH: Optional[bool] = None
    ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS: Optional[bool] = None
    TOP_K_RERANKER: Optional[int] = None
    RELEVANCE_THRESHOLD: Optional[float] = None
    HYBRID_BM25_WEIGHT: Optional[float] = None

    # Content extraction settings
    CONTENT_EXTRACTION_ENGINE: Optional[str] = None
    PDF_EXTRACT_IMAGES: Optional[bool] = None
    PDF_LOADER_MODE: Optional[str] = None
    CONTENT_EXTRACTION_SUPPORTED_MEDIA_MIME_TYPES: Optional[list[str]] = None

    DATALAB_MARKER_API_KEY: Optional[str] = None
    DATALAB_MARKER_API_BASE_URL: Optional[str] = None
    DATALAB_MARKER_ADDITIONAL_CONFIG: Optional[str] = None
    DATALAB_MARKER_SKIP_CACHE: Optional[bool] = None
    DATALAB_MARKER_FORCE_OCR: Optional[bool] = None
    DATALAB_MARKER_PAGINATE: Optional[bool] = None
    DATALAB_MARKER_STRIP_EXISTING_OCR: Optional[bool] = None
    DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION: Optional[bool] = None
    DATALAB_MARKER_FORMAT_LINES: Optional[bool] = None
    DATALAB_MARKER_USE_LLM: Optional[bool] = None
    DATALAB_MARKER_OUTPUT_FORMAT: Optional[str] = None

    EXTERNAL_DOCUMENT_LOADER_URL: Optional[str] = None
    EXTERNAL_DOCUMENT_LOADER_API_KEY: Optional[str] = None

    TIKA_SERVER_URL: Optional[str] = None
    DOCLING_SERVER_URL: Optional[str] = None
    DOCLING_API_KEY: Optional[str] = None
    DOCLING_PARAMS: Optional[dict] = None
    DOCUMENT_INTELLIGENCE_ENDPOINT: Optional[str] = None
    DOCUMENT_INTELLIGENCE_KEY: Optional[str] = None
    DOCUMENT_INTELLIGENCE_MODEL: Optional[str] = None
    MISTRAL_OCR_API_BASE_URL: Optional[str] = None
    MISTRAL_OCR_API_KEY: Optional[str] = None
    PADDLEOCR_VL_BASE_URL: Optional[str] = None
    PADDLEOCR_VL_TOKEN: Optional[str] = None
    TIKA_SERVER_VERSION: Optional[str] = None
    MISTRAL_OCR_USE_BASE64: Optional[bool] = None

    # MinerU settings
    MINERU_API_MODE: Optional[str] = None
    MINERU_API_URL: Optional[str] = None
    MINERU_API_KEY: Optional[str] = None
    MINERU_API_TIMEOUT: Optional[str] = None
    MINERU_PARAMS: Optional[dict] = None

    # Chunking settings
    TEXT_SPLITTER: Optional[str] = None
    ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER: Optional[bool] = None
    CHUNK_SIZE: Optional[int] = None
    CHUNK_MIN_SIZE_TARGET: Optional[int] = None
    CHUNK_OVERLAP: Optional[int] = None

    # File upload settings
    FILE_MAX_SIZE: Optional[Union[int, str]] = None
    FILE_MAX_COUNT: Optional[Union[int, str]] = None
    FILE_IMAGE_COMPRESSION_WIDTH: Optional[Union[int, str]] = None
    FILE_IMAGE_COMPRESSION_HEIGHT: Optional[Union[int, str]] = None
    ALLOWED_FILE_EXTENSIONS: Optional[List[str]] = None

    # Integration settings
    ENABLE_GOOGLE_DRIVE_INTEGRATION: Optional[bool] = None
    ENABLE_ONEDRIVE_INTEGRATION: Optional[bool] = None
    ENABLE_CONFLUENCE_INTEGRATION: Optional[bool] = None

    # Web search settings
    web: Optional[WebConfig] = None
    EXTERNAL_DOCUMENT_LOADER_HEADERS: Optional[dict] = None
    MINERU_FILE_EXTENSIONS: Optional[list[str]] = None
    RAG_TOKENIZER_MODEL: Optional[str] = None


@router.post('/config/update')
async def update_rag_config(request: Request, form_data: ConfigForm, user=Depends(get_admin_user)):
    # RAG settings
    config = await get_rag_config_state()
    _original_config = dict(vars(config))
    # [Gradient] Keep the re-synced form fields in the per-key persistence path.
    if form_data.EXTERNAL_DOCUMENT_LOADER_HEADERS is not None:
        config.EXTERNAL_DOCUMENT_LOADER_HEADERS = form_data.EXTERNAL_DOCUMENT_LOADER_HEADERS
    if form_data.MINERU_FILE_EXTENSIONS is not None:
        config.MINERU_FILE_EXTENSIONS = form_data.MINERU_FILE_EXTENSIONS
    if form_data.MISTRAL_OCR_USE_BASE64 is not None:
        config.MISTRAL_OCR_USE_BASE64 = form_data.MISTRAL_OCR_USE_BASE64
    if form_data.RAG_TOKENIZER_MODEL is not None:
        config.RAG_TOKENIZER_MODEL = form_data.RAG_TOKENIZER_MODEL
    config.RAG_TEMPLATE = form_data.RAG_TEMPLATE if form_data.RAG_TEMPLATE is not None else config.RAG_TEMPLATE
    config.TOP_K = form_data.TOP_K if form_data.TOP_K is not None else config.TOP_K
    config.BYPASS_EMBEDDING_AND_RETRIEVAL = (
        form_data.BYPASS_EMBEDDING_AND_RETRIEVAL
        if form_data.BYPASS_EMBEDDING_AND_RETRIEVAL is not None
        else config.BYPASS_EMBEDDING_AND_RETRIEVAL
    )
    config.RAG_FULL_CONTEXT = (
        form_data.RAG_FULL_CONTEXT if form_data.RAG_FULL_CONTEXT is not None else config.RAG_FULL_CONTEXT
    )

    # Hybrid search settings
    config.ENABLE_RAG_HYBRID_SEARCH = (
        form_data.ENABLE_RAG_HYBRID_SEARCH
        if form_data.ENABLE_RAG_HYBRID_SEARCH is not None
        else config.ENABLE_RAG_HYBRID_SEARCH
    )
    config.ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS = (
        form_data.ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS
        if form_data.ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS is not None
        else config.ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS
    )

    config.TOP_K_RERANKER = form_data.TOP_K_RERANKER if form_data.TOP_K_RERANKER is not None else config.TOP_K_RERANKER
    config.RELEVANCE_THRESHOLD = (
        form_data.RELEVANCE_THRESHOLD if form_data.RELEVANCE_THRESHOLD is not None else config.RELEVANCE_THRESHOLD
    )
    config.HYBRID_BM25_WEIGHT = (
        form_data.HYBRID_BM25_WEIGHT if form_data.HYBRID_BM25_WEIGHT is not None else config.HYBRID_BM25_WEIGHT
    )

    # Content extraction settings
    config.CONTENT_EXTRACTION_ENGINE = (
        form_data.CONTENT_EXTRACTION_ENGINE
        if form_data.CONTENT_EXTRACTION_ENGINE is not None
        else config.CONTENT_EXTRACTION_ENGINE
    )
    config.CONTENT_EXTRACTION_SUPPORTED_MEDIA_MIME_TYPES = (
        form_data.CONTENT_EXTRACTION_SUPPORTED_MEDIA_MIME_TYPES
        if form_data.CONTENT_EXTRACTION_SUPPORTED_MEDIA_MIME_TYPES is not None
        else config.CONTENT_EXTRACTION_SUPPORTED_MEDIA_MIME_TYPES
    )
    config.PDF_EXTRACT_IMAGES = (
        form_data.PDF_EXTRACT_IMAGES if form_data.PDF_EXTRACT_IMAGES is not None else config.PDF_EXTRACT_IMAGES
    )
    config.PDF_LOADER_MODE = (
        form_data.PDF_LOADER_MODE if form_data.PDF_LOADER_MODE is not None else config.PDF_LOADER_MODE
    )
    config.DATALAB_MARKER_API_KEY = (
        form_data.DATALAB_MARKER_API_KEY
        if form_data.DATALAB_MARKER_API_KEY is not None
        else config.DATALAB_MARKER_API_KEY
    )
    config.DATALAB_MARKER_API_BASE_URL = (
        form_data.DATALAB_MARKER_API_BASE_URL
        if form_data.DATALAB_MARKER_API_BASE_URL is not None
        else config.DATALAB_MARKER_API_BASE_URL
    )
    config.DATALAB_MARKER_ADDITIONAL_CONFIG = (
        form_data.DATALAB_MARKER_ADDITIONAL_CONFIG
        if form_data.DATALAB_MARKER_ADDITIONAL_CONFIG is not None
        else config.DATALAB_MARKER_ADDITIONAL_CONFIG
    )
    config.DATALAB_MARKER_SKIP_CACHE = (
        form_data.DATALAB_MARKER_SKIP_CACHE
        if form_data.DATALAB_MARKER_SKIP_CACHE is not None
        else config.DATALAB_MARKER_SKIP_CACHE
    )
    config.DATALAB_MARKER_FORCE_OCR = (
        form_data.DATALAB_MARKER_FORCE_OCR
        if form_data.DATALAB_MARKER_FORCE_OCR is not None
        else config.DATALAB_MARKER_FORCE_OCR
    )
    config.DATALAB_MARKER_PAGINATE = (
        form_data.DATALAB_MARKER_PAGINATE
        if form_data.DATALAB_MARKER_PAGINATE is not None
        else config.DATALAB_MARKER_PAGINATE
    )
    config.DATALAB_MARKER_STRIP_EXISTING_OCR = (
        form_data.DATALAB_MARKER_STRIP_EXISTING_OCR
        if form_data.DATALAB_MARKER_STRIP_EXISTING_OCR is not None
        else config.DATALAB_MARKER_STRIP_EXISTING_OCR
    )
    config.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION = (
        form_data.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION
        if form_data.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION is not None
        else config.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION
    )
    config.DATALAB_MARKER_FORMAT_LINES = (
        form_data.DATALAB_MARKER_FORMAT_LINES
        if form_data.DATALAB_MARKER_FORMAT_LINES is not None
        else config.DATALAB_MARKER_FORMAT_LINES
    )
    config.DATALAB_MARKER_OUTPUT_FORMAT = (
        form_data.DATALAB_MARKER_OUTPUT_FORMAT
        if form_data.DATALAB_MARKER_OUTPUT_FORMAT is not None
        else config.DATALAB_MARKER_OUTPUT_FORMAT
    )
    config.DATALAB_MARKER_USE_LLM = (
        form_data.DATALAB_MARKER_USE_LLM
        if form_data.DATALAB_MARKER_USE_LLM is not None
        else config.DATALAB_MARKER_USE_LLM
    )
    config.EXTERNAL_DOCUMENT_LOADER_URL = (
        form_data.EXTERNAL_DOCUMENT_LOADER_URL
        if form_data.EXTERNAL_DOCUMENT_LOADER_URL is not None
        else config.EXTERNAL_DOCUMENT_LOADER_URL
    )
    config.EXTERNAL_DOCUMENT_LOADER_API_KEY = (
        form_data.EXTERNAL_DOCUMENT_LOADER_API_KEY
        if form_data.EXTERNAL_DOCUMENT_LOADER_API_KEY is not None
        else config.EXTERNAL_DOCUMENT_LOADER_API_KEY
    )
    config.TIKA_SERVER_URL = (
        form_data.TIKA_SERVER_URL if form_data.TIKA_SERVER_URL is not None else config.TIKA_SERVER_URL
    )
    config.TIKA_SERVER_VERSION = (
        form_data.TIKA_SERVER_VERSION if form_data.TIKA_SERVER_VERSION is not None else config.TIKA_SERVER_VERSION
    )
    config.DOCLING_SERVER_URL = (
        form_data.DOCLING_SERVER_URL if form_data.DOCLING_SERVER_URL is not None else config.DOCLING_SERVER_URL
    )
    config.DOCLING_API_KEY = (
        form_data.DOCLING_API_KEY if form_data.DOCLING_API_KEY is not None else config.DOCLING_API_KEY
    )
    config.DOCLING_PARAMS = form_data.DOCLING_PARAMS if form_data.DOCLING_PARAMS is not None else config.DOCLING_PARAMS
    config.DOCUMENT_INTELLIGENCE_ENDPOINT = (
        form_data.DOCUMENT_INTELLIGENCE_ENDPOINT
        if form_data.DOCUMENT_INTELLIGENCE_ENDPOINT is not None
        else config.DOCUMENT_INTELLIGENCE_ENDPOINT
    )
    config.DOCUMENT_INTELLIGENCE_KEY = (
        form_data.DOCUMENT_INTELLIGENCE_KEY
        if form_data.DOCUMENT_INTELLIGENCE_KEY is not None
        else config.DOCUMENT_INTELLIGENCE_KEY
    )
    config.DOCUMENT_INTELLIGENCE_MODEL = (
        form_data.DOCUMENT_INTELLIGENCE_MODEL
        if form_data.DOCUMENT_INTELLIGENCE_MODEL is not None
        else config.DOCUMENT_INTELLIGENCE_MODEL
    )

    config.MISTRAL_OCR_API_BASE_URL = (
        form_data.MISTRAL_OCR_API_BASE_URL
        if form_data.MISTRAL_OCR_API_BASE_URL is not None
        else config.MISTRAL_OCR_API_BASE_URL
    )
    config.MISTRAL_OCR_API_KEY = (
        form_data.MISTRAL_OCR_API_KEY if form_data.MISTRAL_OCR_API_KEY is not None else config.MISTRAL_OCR_API_KEY
    )
    config.PADDLEOCR_VL_BASE_URL = (
        form_data.PADDLEOCR_VL_BASE_URL if form_data.PADDLEOCR_VL_BASE_URL is not None else config.PADDLEOCR_VL_BASE_URL
    )
    config.PADDLEOCR_VL_TOKEN = (
        form_data.PADDLEOCR_VL_TOKEN if form_data.PADDLEOCR_VL_TOKEN is not None else config.PADDLEOCR_VL_TOKEN
    )

    # MinerU settings
    config.MINERU_API_MODE = (
        form_data.MINERU_API_MODE if form_data.MINERU_API_MODE is not None else config.MINERU_API_MODE
    )
    config.MINERU_API_URL = form_data.MINERU_API_URL if form_data.MINERU_API_URL is not None else config.MINERU_API_URL
    config.MINERU_API_KEY = form_data.MINERU_API_KEY if form_data.MINERU_API_KEY is not None else config.MINERU_API_KEY
    config.MINERU_API_TIMEOUT = (
        form_data.MINERU_API_TIMEOUT if form_data.MINERU_API_TIMEOUT is not None else config.MINERU_API_TIMEOUT
    )
    config.MINERU_PARAMS = form_data.MINERU_PARAMS if form_data.MINERU_PARAMS is not None else config.MINERU_PARAMS

    # Chunking settings
    config.TEXT_SPLITTER = form_data.TEXT_SPLITTER if form_data.TEXT_SPLITTER is not None else config.TEXT_SPLITTER
    config.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER = (
        form_data.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER
        if form_data.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER is not None
        else config.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER
    )
    config.CHUNK_SIZE = form_data.CHUNK_SIZE if form_data.CHUNK_SIZE is not None else config.CHUNK_SIZE
    config.CHUNK_MIN_SIZE_TARGET = (
        form_data.CHUNK_MIN_SIZE_TARGET if form_data.CHUNK_MIN_SIZE_TARGET is not None else config.CHUNK_MIN_SIZE_TARGET
    )
    config.CHUNK_OVERLAP = form_data.CHUNK_OVERLAP if form_data.CHUNK_OVERLAP is not None else config.CHUNK_OVERLAP

    # File upload settings
    # Empty string means "clear to None" (unlimited/no compression),
    # None means "don't change", int means "set to this value"
    if form_data.FILE_MAX_SIZE is not None:
        config.FILE_MAX_SIZE = None if form_data.FILE_MAX_SIZE == '' else form_data.FILE_MAX_SIZE
    if form_data.FILE_MAX_COUNT is not None:
        config.FILE_MAX_COUNT = None if form_data.FILE_MAX_COUNT == '' else form_data.FILE_MAX_COUNT
    if form_data.FILE_IMAGE_COMPRESSION_WIDTH is not None:
        config.FILE_IMAGE_COMPRESSION_WIDTH = (
            None if form_data.FILE_IMAGE_COMPRESSION_WIDTH == '' else form_data.FILE_IMAGE_COMPRESSION_WIDTH
        )
    if form_data.FILE_IMAGE_COMPRESSION_HEIGHT is not None:
        config.FILE_IMAGE_COMPRESSION_HEIGHT = (
            None if form_data.FILE_IMAGE_COMPRESSION_HEIGHT == '' else form_data.FILE_IMAGE_COMPRESSION_HEIGHT
        )

    config.ALLOWED_FILE_EXTENSIONS = (
        form_data.ALLOWED_FILE_EXTENSIONS
        if form_data.ALLOWED_FILE_EXTENSIONS is not None
        else config.ALLOWED_FILE_EXTENSIONS
    )

    # Integration settings
    config.ENABLE_GOOGLE_DRIVE_INTEGRATION = (
        form_data.ENABLE_GOOGLE_DRIVE_INTEGRATION
        if form_data.ENABLE_GOOGLE_DRIVE_INTEGRATION is not None
        else config.ENABLE_GOOGLE_DRIVE_INTEGRATION
    )
    config.ENABLE_ONEDRIVE_INTEGRATION = (
        form_data.ENABLE_ONEDRIVE_INTEGRATION
        if form_data.ENABLE_ONEDRIVE_INTEGRATION is not None
        else config.ENABLE_ONEDRIVE_INTEGRATION
    )
    config.ENABLE_CONFLUENCE_INTEGRATION = (
        form_data.ENABLE_CONFLUENCE_INTEGRATION
        if form_data.ENABLE_CONFLUENCE_INTEGRATION is not None
        else config.ENABLE_CONFLUENCE_INTEGRATION
    )

    if form_data.web is not None:
        if form_data.web.ENABLE_WEB_SEARCH_CONFIRMATION is not None:
            config.ENABLE_WEB_SEARCH_CONFIRMATION = form_data.web.ENABLE_WEB_SEARCH_CONFIRMATION
        if form_data.web.WEB_SEARCH_CONFIRMATION_CONTENT is not None:
            config.WEB_SEARCH_CONFIRMATION_CONTENT = form_data.web.WEB_SEARCH_CONFIRMATION_CONTENT
        if form_data.web.SERPHOUSE_API_KEY is not None:
            config.SERPHOUSE_API_KEY = form_data.web.SERPHOUSE_API_KEY
        if form_data.web.SERPHOUSE_DOMAIN is not None:
            config.SERPHOUSE_DOMAIN = form_data.web.SERPHOUSE_DOMAIN
        if form_data.web.MICROSOFT_WEB_IQ_API_BASE_URL is not None:
            config.MICROSOFT_WEB_IQ_API_BASE_URL = form_data.web.MICROSOFT_WEB_IQ_API_BASE_URL
        if form_data.web.MICROSOFT_WEB_IQ_API_KEY is not None:
            config.MICROSOFT_WEB_IQ_API_KEY = form_data.web.MICROSOFT_WEB_IQ_API_KEY
        if form_data.web.MICROSOFT_WEB_IQ_LANGUAGE is not None:
            config.MICROSOFT_WEB_IQ_LANGUAGE = form_data.web.MICROSOFT_WEB_IQ_LANGUAGE
        if form_data.web.LINKUP_API_KEY is not None:
            config.LINKUP_API_KEY = form_data.web.LINKUP_API_KEY
        if form_data.web.LINKUP_SEARCH_PARAMS is not None:
            config.LINKUP_SEARCH_PARAMS = form_data.web.LINKUP_SEARCH_PARAMS
        # Web search settings
        config.ENABLE_WEB_SEARCH = form_data.web.ENABLE_WEB_SEARCH
        config.WEB_SEARCH_ENGINE = form_data.web.WEB_SEARCH_ENGINE
        config.WEB_SEARCH_TRUST_ENV = form_data.web.WEB_SEARCH_TRUST_ENV
        config.WEB_SEARCH_RESULT_COUNT = form_data.web.WEB_SEARCH_RESULT_COUNT
        config.WEB_SEARCH_CONCURRENT_REQUESTS = form_data.web.WEB_SEARCH_CONCURRENT_REQUESTS
        config.WEB_FETCH_MAX_CONTENT_LENGTH = form_data.web.WEB_FETCH_MAX_CONTENT_LENGTH
        config.WEB_LOADER_CONCURRENT_REQUESTS = form_data.web.WEB_LOADER_CONCURRENT_REQUESTS
        config.WEB_SEARCH_DOMAIN_FILTER_LIST = form_data.web.WEB_SEARCH_DOMAIN_FILTER_LIST
        config.BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL = form_data.web.BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL
        config.BYPASS_WEB_SEARCH_WEB_LOADER = form_data.web.BYPASS_WEB_SEARCH_WEB_LOADER
        config.OLLAMA_CLOUD_WEB_SEARCH_API_KEY = form_data.web.OLLAMA_CLOUD_WEB_SEARCH_API_KEY
        config.SEARXNG_QUERY_URL = form_data.web.SEARXNG_QUERY_URL
        config.SEARXNG_LANGUAGE = form_data.web.SEARXNG_LANGUAGE
        config.OPENSERP_BASE_URL = form_data.web.OPENSERP_BASE_URL
        config.YACY_QUERY_URL = form_data.web.YACY_QUERY_URL
        config.YACY_USERNAME = form_data.web.YACY_USERNAME
        config.YACY_PASSWORD = form_data.web.YACY_PASSWORD
        config.GOOGLE_PSE_API_KEY = form_data.web.GOOGLE_PSE_API_KEY
        config.GOOGLE_PSE_ENGINE_ID = form_data.web.GOOGLE_PSE_ENGINE_ID
        config.BRAVE_SEARCH_API_KEY = form_data.web.BRAVE_SEARCH_API_KEY
        if form_data.web.BRAVE_SEARCH_CONTEXT_TOKENS is not None:
            config.BRAVE_SEARCH_CONTEXT_TOKENS = form_data.web.BRAVE_SEARCH_CONTEXT_TOKENS
        config.KAGI_SEARCH_API_KEY = form_data.web.KAGI_SEARCH_API_KEY
        config.MOJEEK_SEARCH_API_KEY = form_data.web.MOJEEK_SEARCH_API_KEY
        config.BOCHA_SEARCH_API_KEY = form_data.web.BOCHA_SEARCH_API_KEY
        config.SERPSTACK_API_KEY = form_data.web.SERPSTACK_API_KEY
        config.SERPSTACK_HTTPS = form_data.web.SERPSTACK_HTTPS
        config.SERPER_API_KEY = form_data.web.SERPER_API_KEY
        config.SERPLY_API_KEY = form_data.web.SERPLY_API_KEY
        config.DDGS_BACKEND = form_data.web.DDGS_BACKEND
        config.TAVILY_API_KEY = form_data.web.TAVILY_API_KEY
        config.SEARCHAPI_API_KEY = form_data.web.SEARCHAPI_API_KEY
        config.SEARCHAPI_ENGINE = form_data.web.SEARCHAPI_ENGINE
        config.SERPAPI_API_KEY = form_data.web.SERPAPI_API_KEY
        config.SERPAPI_ENGINE = form_data.web.SERPAPI_ENGINE
        config.JINA_API_KEY = form_data.web.JINA_API_KEY
        config.JINA_API_BASE_URL = form_data.web.JINA_API_BASE_URL
        config.BING_SEARCH_V7_ENDPOINT = form_data.web.BING_SEARCH_V7_ENDPOINT
        config.BING_SEARCH_V7_SUBSCRIPTION_KEY = form_data.web.BING_SEARCH_V7_SUBSCRIPTION_KEY
        config.EXA_API_KEY = form_data.web.EXA_API_KEY
        config.PERPLEXITY_API_KEY = form_data.web.PERPLEXITY_API_KEY
        config.PERPLEXITY_MODEL = form_data.web.PERPLEXITY_MODEL
        config.PERPLEXITY_SEARCH_CONTEXT_USAGE = form_data.web.PERPLEXITY_SEARCH_CONTEXT_USAGE
        config.PERPLEXITY_SEARCH_API_URL = form_data.web.PERPLEXITY_SEARCH_API_URL
        config.SOUGOU_API_SID = form_data.web.SOUGOU_API_SID
        config.SOUGOU_API_SK = form_data.web.SOUGOU_API_SK

        # Web loader settings
        config.WEB_LOADER_ENGINE = form_data.web.WEB_LOADER_ENGINE
        config.WEB_LOADER_TIMEOUT = form_data.web.WEB_LOADER_TIMEOUT

        config.ENABLE_WEB_LOADER_SSL_VERIFICATION = form_data.web.ENABLE_WEB_LOADER_SSL_VERIFICATION
        config.PLAYWRIGHT_WS_URL = form_data.web.PLAYWRIGHT_WS_URL
        config.PLAYWRIGHT_TIMEOUT = form_data.web.PLAYWRIGHT_TIMEOUT
        config.FIRECRAWL_API_KEY = form_data.web.FIRECRAWL_API_KEY
        config.FIRECRAWL_API_BASE_URL = form_data.web.FIRECRAWL_API_BASE_URL
        config.FIRECRAWL_TIMEOUT = form_data.web.FIRECRAWL_TIMEOUT
        config.EXTERNAL_WEB_SEARCH_URL = form_data.web.EXTERNAL_WEB_SEARCH_URL
        config.EXTERNAL_WEB_SEARCH_API_KEY = form_data.web.EXTERNAL_WEB_SEARCH_API_KEY
        config.EXTERNAL_WEB_LOADER_URL = form_data.web.EXTERNAL_WEB_LOADER_URL
        config.EXTERNAL_WEB_LOADER_API_KEY = form_data.web.EXTERNAL_WEB_LOADER_API_KEY
        config.TAVILY_EXTRACT_DEPTH = form_data.web.TAVILY_EXTRACT_DEPTH
        config.YOUTUBE_LOADER_LANGUAGE = form_data.web.YOUTUBE_LOADER_LANGUAGE
        config.YOUTUBE_LOADER_PROXY_URL = form_data.web.YOUTUBE_LOADER_PROXY_URL
        request.app.state.YOUTUBE_LOADER_TRANSLATION = form_data.web.YOUTUBE_LOADER_TRANSLATION
        config.YANDEX_WEB_SEARCH_URL = form_data.web.YANDEX_WEB_SEARCH_URL
        config.YANDEX_WEB_SEARCH_API_KEY = form_data.web.YANDEX_WEB_SEARCH_API_KEY
        config.YANDEX_WEB_SEARCH_CONFIG = form_data.web.YANDEX_WEB_SEARCH_CONFIG
        config.YOUCOM_API_KEY = form_data.web.YOUCOM_API_KEY

    await Config.upsert({RAG_CONFIG_KEYS[k]: v for k, v in vars(config).items() if _original_config.get(k) != v})
    return {
        'status': True,
        # RAG settings
        'RAG_TEMPLATE': config.RAG_TEMPLATE,
        'TOP_K': config.TOP_K,
        'BYPASS_EMBEDDING_AND_RETRIEVAL': config.BYPASS_EMBEDDING_AND_RETRIEVAL,
        'RAG_FULL_CONTEXT': config.RAG_FULL_CONTEXT,
        # Hybrid search settings
        'ENABLE_RAG_HYBRID_SEARCH': config.ENABLE_RAG_HYBRID_SEARCH,
        'TOP_K_RERANKER': config.TOP_K_RERANKER,
        'RELEVANCE_THRESHOLD': config.RELEVANCE_THRESHOLD,
        'HYBRID_BM25_WEIGHT': config.HYBRID_BM25_WEIGHT,
        # Content extraction settings
        'CONTENT_EXTRACTION_ENGINE': config.CONTENT_EXTRACTION_ENGINE,
        'CONTENT_EXTRACTION_SUPPORTED_MEDIA_MIME_TYPES': config.CONTENT_EXTRACTION_SUPPORTED_MEDIA_MIME_TYPES,
        'PDF_EXTRACT_IMAGES': config.PDF_EXTRACT_IMAGES,
        'PDF_LOADER_MODE': config.PDF_LOADER_MODE,
        'DATALAB_MARKER_API_KEY': config.DATALAB_MARKER_API_KEY,
        'DATALAB_MARKER_API_BASE_URL': config.DATALAB_MARKER_API_BASE_URL,
        'DATALAB_MARKER_ADDITIONAL_CONFIG': config.DATALAB_MARKER_ADDITIONAL_CONFIG,
        'DATALAB_MARKER_SKIP_CACHE': config.DATALAB_MARKER_SKIP_CACHE,
        'DATALAB_MARKER_FORCE_OCR': config.DATALAB_MARKER_FORCE_OCR,
        'DATALAB_MARKER_PAGINATE': config.DATALAB_MARKER_PAGINATE,
        'DATALAB_MARKER_STRIP_EXISTING_OCR': config.DATALAB_MARKER_STRIP_EXISTING_OCR,
        'DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION': config.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION,
        'DATALAB_MARKER_USE_LLM': config.DATALAB_MARKER_USE_LLM,
        'DATALAB_MARKER_OUTPUT_FORMAT': config.DATALAB_MARKER_OUTPUT_FORMAT,
        'EXTERNAL_DOCUMENT_LOADER_URL': config.EXTERNAL_DOCUMENT_LOADER_URL,
        'EXTERNAL_DOCUMENT_LOADER_API_KEY': config.EXTERNAL_DOCUMENT_LOADER_API_KEY,
        'EXTERNAL_DOCUMENT_LOADER_HEADERS': config.EXTERNAL_DOCUMENT_LOADER_HEADERS,
        'TIKA_SERVER_URL': config.TIKA_SERVER_URL,
        'TIKA_SERVER_VERSION': config.TIKA_SERVER_VERSION,
        'DOCLING_SERVER_URL': config.DOCLING_SERVER_URL,
        'DOCLING_API_KEY': config.DOCLING_API_KEY,
        'DOCLING_PARAMS': config.DOCLING_PARAMS,
        'DOCUMENT_INTELLIGENCE_ENDPOINT': config.DOCUMENT_INTELLIGENCE_ENDPOINT,
        'DOCUMENT_INTELLIGENCE_KEY': config.DOCUMENT_INTELLIGENCE_KEY,
        'DOCUMENT_INTELLIGENCE_MODEL': config.DOCUMENT_INTELLIGENCE_MODEL,
        'MISTRAL_OCR_API_BASE_URL': config.MISTRAL_OCR_API_BASE_URL,
        'MISTRAL_OCR_API_KEY': config.MISTRAL_OCR_API_KEY,
        'MISTRAL_OCR_USE_BASE64': config.MISTRAL_OCR_USE_BASE64,
        'PADDLEOCR_VL_BASE_URL': config.PADDLEOCR_VL_BASE_URL,
        'PADDLEOCR_VL_TOKEN': config.PADDLEOCR_VL_TOKEN,
        # MinerU settings
        'MINERU_API_MODE': config.MINERU_API_MODE,
        'MINERU_API_URL': config.MINERU_API_URL,
        'MINERU_API_KEY': config.MINERU_API_KEY,
        'MINERU_API_TIMEOUT': config.MINERU_API_TIMEOUT,
        'MINERU_PARAMS': config.MINERU_PARAMS,
        'MINERU_FILE_EXTENSIONS': config.MINERU_FILE_EXTENSIONS,
        # Reranking settings
        # Chunking settings
        'TEXT_SPLITTER': config.TEXT_SPLITTER,
        'RAG_TOKENIZER_MODEL': config.RAG_TOKENIZER_MODEL,
        'CHUNK_SIZE': config.CHUNK_SIZE,
        'CHUNK_MIN_SIZE_TARGET': config.CHUNK_MIN_SIZE_TARGET,
        'ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER': config.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER,
        'CHUNK_OVERLAP': config.CHUNK_OVERLAP,
        # File upload settings
        'FILE_MAX_SIZE': config.FILE_MAX_SIZE,
        'FILE_MAX_COUNT': config.FILE_MAX_COUNT,
        'FILE_IMAGE_COMPRESSION_WIDTH': config.FILE_IMAGE_COMPRESSION_WIDTH,
        'FILE_IMAGE_COMPRESSION_HEIGHT': config.FILE_IMAGE_COMPRESSION_HEIGHT,
        'ALLOWED_FILE_EXTENSIONS': config.ALLOWED_FILE_EXTENSIONS,
        # Integration settings
        'ENABLE_GOOGLE_DRIVE_INTEGRATION': config.ENABLE_GOOGLE_DRIVE_INTEGRATION,
        'ENABLE_ONEDRIVE_INTEGRATION': config.ENABLE_ONEDRIVE_INTEGRATION,
        'ENABLE_CONFLUENCE_INTEGRATION': config.ENABLE_CONFLUENCE_INTEGRATION,
        # Web search settings
        'web': {
            'ENABLE_WEB_SEARCH': config.ENABLE_WEB_SEARCH,
            'ENABLE_WEB_SEARCH_CONFIRMATION': config.ENABLE_WEB_SEARCH_CONFIRMATION,
            'WEB_SEARCH_CONFIRMATION_CONTENT': config.WEB_SEARCH_CONFIRMATION_CONTENT,
            'WEB_SEARCH_ENGINE': config.WEB_SEARCH_ENGINE,
            'WEB_SEARCH_TRUST_ENV': config.WEB_SEARCH_TRUST_ENV,
            'WEB_SEARCH_RESULT_COUNT': config.WEB_SEARCH_RESULT_COUNT,
            'WEB_SEARCH_CONCURRENT_REQUESTS': config.WEB_SEARCH_CONCURRENT_REQUESTS,
            'WEB_FETCH_MAX_CONTENT_LENGTH': config.WEB_FETCH_MAX_CONTENT_LENGTH,
            'WEB_LOADER_CONCURRENT_REQUESTS': config.WEB_LOADER_CONCURRENT_REQUESTS,
            'WEB_SEARCH_DOMAIN_FILTER_LIST': config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            'BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL': config.BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL,
            'BYPASS_WEB_SEARCH_WEB_LOADER': config.BYPASS_WEB_SEARCH_WEB_LOADER,
            'OLLAMA_CLOUD_WEB_SEARCH_API_KEY': config.OLLAMA_CLOUD_WEB_SEARCH_API_KEY,
            'SEARXNG_QUERY_URL': config.SEARXNG_QUERY_URL,
            'SEARXNG_LANGUAGE': config.SEARXNG_LANGUAGE,
            'OPENSERP_BASE_URL': config.OPENSERP_BASE_URL,
            'YACY_QUERY_URL': config.YACY_QUERY_URL,
            'YACY_USERNAME': config.YACY_USERNAME,
            'YACY_PASSWORD': config.YACY_PASSWORD,
            'GOOGLE_PSE_API_KEY': config.GOOGLE_PSE_API_KEY,
            'GOOGLE_PSE_ENGINE_ID': config.GOOGLE_PSE_ENGINE_ID,
            'BRAVE_SEARCH_API_KEY': config.BRAVE_SEARCH_API_KEY,
            'BRAVE_SEARCH_CONTEXT_TOKENS': config.BRAVE_SEARCH_CONTEXT_TOKENS,
            'KAGI_SEARCH_API_KEY': config.KAGI_SEARCH_API_KEY,
            'MOJEEK_SEARCH_API_KEY': config.MOJEEK_SEARCH_API_KEY,
            'BOCHA_SEARCH_API_KEY': config.BOCHA_SEARCH_API_KEY,
            'SERPSTACK_API_KEY': config.SERPSTACK_API_KEY,
            'SERPSTACK_HTTPS': config.SERPSTACK_HTTPS,
            'SERPER_API_KEY': config.SERPER_API_KEY,
            'SERPHOUSE_API_KEY': config.SERPHOUSE_API_KEY,
            'SERPHOUSE_DOMAIN': config.SERPHOUSE_DOMAIN,
            'SERPLY_API_KEY': config.SERPLY_API_KEY,
            'TAVILY_API_KEY': config.TAVILY_API_KEY,
            'SEARCHAPI_API_KEY': config.SEARCHAPI_API_KEY,
            'SEARCHAPI_ENGINE': config.SEARCHAPI_ENGINE,
            'SERPAPI_API_KEY': config.SERPAPI_API_KEY,
            'SERPAPI_ENGINE': config.SERPAPI_ENGINE,
            'JINA_API_KEY': config.JINA_API_KEY,
            'JINA_API_BASE_URL': config.JINA_API_BASE_URL,
            'BING_SEARCH_V7_ENDPOINT': config.BING_SEARCH_V7_ENDPOINT,
            'BING_SEARCH_V7_SUBSCRIPTION_KEY': config.BING_SEARCH_V7_SUBSCRIPTION_KEY,
            'EXA_API_KEY': config.EXA_API_KEY,
            'PERPLEXITY_API_KEY': config.PERPLEXITY_API_KEY,
            'PERPLEXITY_MODEL': config.PERPLEXITY_MODEL,
            'PERPLEXITY_SEARCH_CONTEXT_USAGE': config.PERPLEXITY_SEARCH_CONTEXT_USAGE,
            'PERPLEXITY_SEARCH_API_URL': config.PERPLEXITY_SEARCH_API_URL,
            'MICROSOFT_WEB_IQ_API_BASE_URL': config.MICROSOFT_WEB_IQ_API_BASE_URL,
            'MICROSOFT_WEB_IQ_API_KEY': config.MICROSOFT_WEB_IQ_API_KEY,
            'MICROSOFT_WEB_IQ_LANGUAGE': config.MICROSOFT_WEB_IQ_LANGUAGE,
            'SOUGOU_API_SID': config.SOUGOU_API_SID,
            'SOUGOU_API_SK': config.SOUGOU_API_SK,
            'WEB_LOADER_ENGINE': config.WEB_LOADER_ENGINE,
            'WEB_LOADER_TIMEOUT': config.WEB_LOADER_TIMEOUT,
            'ENABLE_WEB_LOADER_SSL_VERIFICATION': config.ENABLE_WEB_LOADER_SSL_VERIFICATION,
            'PLAYWRIGHT_WS_URL': config.PLAYWRIGHT_WS_URL,
            'PLAYWRIGHT_TIMEOUT': config.PLAYWRIGHT_TIMEOUT,
            'FIRECRAWL_API_KEY': config.FIRECRAWL_API_KEY,
            'FIRECRAWL_API_BASE_URL': config.FIRECRAWL_API_BASE_URL,
            'FIRECRAWL_TIMEOUT': config.FIRECRAWL_TIMEOUT,
            'TAVILY_EXTRACT_DEPTH': config.TAVILY_EXTRACT_DEPTH,
            'EXTERNAL_WEB_SEARCH_URL': config.EXTERNAL_WEB_SEARCH_URL,
            'EXTERNAL_WEB_SEARCH_API_KEY': config.EXTERNAL_WEB_SEARCH_API_KEY,
            'EXTERNAL_WEB_LOADER_URL': config.EXTERNAL_WEB_LOADER_URL,
            'EXTERNAL_WEB_LOADER_API_KEY': config.EXTERNAL_WEB_LOADER_API_KEY,
            'YOUTUBE_LOADER_LANGUAGE': config.YOUTUBE_LOADER_LANGUAGE,
            'YOUTUBE_LOADER_PROXY_URL': config.YOUTUBE_LOADER_PROXY_URL,
            'YOUTUBE_LOADER_TRANSLATION': request.app.state.YOUTUBE_LOADER_TRANSLATION,
            'YANDEX_WEB_SEARCH_URL': config.YANDEX_WEB_SEARCH_URL,
            'YANDEX_WEB_SEARCH_API_KEY': config.YANDEX_WEB_SEARCH_API_KEY,
            'YANDEX_WEB_SEARCH_CONFIG': config.YANDEX_WEB_SEARCH_CONFIG,
            'YOUCOM_API_KEY': config.YOUCOM_API_KEY,
            'LINKUP_API_KEY': config.LINKUP_API_KEY,
            'LINKUP_SEARCH_PARAMS': config.LINKUP_SEARCH_PARAMS,
        },
    }


####################################
#
# Document process and retrieval
#
####################################


class ProcessFileForm(BaseModel):
    file_id: str
    content: Optional[str] = None
    collection_name: Optional[str] = None


@router.post('/process/file')
async def process_file(
    request: Request,
    form_data: ProcessFileForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    ensure_configured()
    if can_bypass_access_control(user):
        file = await Files.get_file_by_id(form_data.file_id, db=db)
    else:
        file = await Files.get_file_by_id_and_user_id(form_data.file_id, user.id, db=db)

    if file:
        try:
            collection_name = form_data.collection_name
            file_collection_name = f'file-{file.id}'

            if collection_name is None:
                collection_name = file_collection_name
            else:
                await _validate_collection_access([collection_name], user, access_type='write')

            try:
                collection_key = form_data.collection_name or await ingest.ensure_attachments_collection(
                    user.id, identity.build_client()
                )
                job_id = await ingest.submit(
                    file, collection_key=collection_key, user_id=user.id, text=form_data.content
                )
            except SoevNotConfigured:
                raise
            except SoevApiError as e:
                raise ValueError(f'{e.code}: {e.detail}') from e
            return {
                'status': True,
                'collection_name': form_data.collection_name,
                'filename': file.filename,
                'content': '',
                'job_id': job_id,
            }

        except ingest.IngestBusy as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='file is being processed') from e
        except SoevNotConfigured:
            raise
        except Exception as e:
            log.exception(e)
            # Fresh session for error status update.
            async with get_async_db() as session:
                await Files.set_status(file.id, 'failed', error=str(e), db=session)
                # Clear the hash so the file can be re-uploaded after fixing the issue
                await Files.update_file_hash_by_id(file.id, None, db=session)

            await publish_event(
                request,
                EVENTS.RETRIEVAL_CONTENT_PROCESS_FAILED,
                actor=user,
                subject_id=file.id,
                subject_type='file',
                data={
                    'collection_name': collection_name,
                    'filename': file.filename,
                    'message': f'{file.filename}: {e}',
                },
            )

            if 'No pandoc was found' in str(e):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.PANDOC_NOT_INSTALLED,
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(e),
                )

    else:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)


async def _fetch_url(url: str, max_size_mb: int | str | None) -> dict:
    await asyncio.to_thread(validate_url, url)
    max_bytes = None
    if max_size_mb:
        try:
            max_bytes = int(max_size_mb) * 1024 * 1024
        except (TypeError, ValueError):
            max_bytes = None

    async with get_ssrf_safe_session() as session:
        async with session.get(
            url, ssl=AIOHTTP_CLIENT_SESSION_SSL, allow_redirects=AIOHTTP_CLIENT_ALLOW_REDIRECTS
        ) as response:
            response.raise_for_status()

            content_type = response.headers.get('Content-Type', '')
            content_disposition = response.headers.get('Content-Disposition', '')
            content_length = response.headers.get('Content-Length')
            base_content_type = content_type.split(';')[0].strip().lower()
            is_attachment = content_disposition.split(';')[0].strip().lower() == 'attachment'

            chunks = []
            total = 0

            iterator = response.content.iter_chunked(64 * 1024)
            first_chunk = await anext(iterator, b'')

            if not is_attachment and base_content_type in {'text/html', 'application/xhtml+xml'}:
                return {'kind': 'web'}

            if not is_attachment and base_content_type in {'', 'application/octet-stream', 'binary/octet-stream'}:
                sample = first_chunk[:4096].lstrip().lower()
                if (
                    sample.startswith((b'<!doctype html', b'<html', b'<head', b'<body', b'<?xml'))
                    or b'<html' in sample[:1024]
                ):
                    return {'kind': 'web'}

            if max_bytes and content_length:
                try:
                    if int(content_length) > max_bytes:
                        raise HTTPException(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=ERROR_MESSAGES.FILE_TOO_LARGE(size=f'{max_size_mb} MB'),
                        )
                except ValueError:
                    pass

            if first_chunk:
                chunks.append(first_chunk)
                total += len(first_chunk)
                if max_bytes and total > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=ERROR_MESSAGES.FILE_TOO_LARGE(size=f'{max_size_mb} MB'),
                    )

            async for chunk in iterator:
                if not chunk:
                    continue
                chunks.append(chunk)
                total += len(chunk)
                if max_bytes and total > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=ERROR_MESSAGES.FILE_TOO_LARGE(size=f'{max_size_mb} MB'),
                    )

            data = b''.join(chunks)

            image_mime = None
            try:
                from PIL import Image

                image = Image.open(io.BytesIO(data))
                image.verify()
                image_mime = Image.MIME.get(image.format) if image.format else None
            except Exception:
                image_mime = None

            if base_content_type.startswith('image/') and image_mime is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT('Invalid image content'),
                )

            filename = ''
            filename_star = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition, re.IGNORECASE)
            filename_plain = re.search(r'filename="?([^";]+)"?', content_disposition, re.IGNORECASE)
            if filename_star:
                filename = unquote(filename_star.group(1))
            elif filename_plain:
                filename = filename_plain.group(1)
            if not filename:
                filename = os.path.basename(urlparse(url).path)
            filename = os.path.basename(filename or 'download')

            # [Gradient]
            resolved_content_type = image_mime or content_type_for(filename, base_content_type)
            if not os.path.splitext(filename)[1]:
                filename = f'{filename}{mimetypes.guess_extension(resolved_content_type) or ".bin"}'

            return {
                'kind': 'file',
                'data': data,
                'filename': filename,
                'content_type': resolved_content_type,
            }


@router.post('/process/url', response_model=ProcessUrlResponse)
async def process_url(
    request: Request,
    form_data: ProcessUrlForm,
    process: bool = Query(True, description='Whether to process and save the content'),
    user=Depends(get_verified_user),
):
    try:
        if is_youtube_url(form_data.url):
            result = await process_web(request, form_data, process=process, user=user)
            return {
                'status': True,
                'type': 'youtube',
                'name': form_data.url,
                'url': form_data.url,
                'collection_name': result.get('collection_name'),
                'content': result.get('content'),
            }

        config = await get_rag_config_state()
        url_result = await _fetch_url(form_data.url, config.FILE_MAX_SIZE)

        if url_result['kind'] == 'web':
            result = await process_web(request, form_data, process=process, user=user)
            return {
                'status': True,
                'type': 'web',
                'name': form_data.url,
                'url': form_data.url,
                'collection_name': result.get('collection_name'),
                'content': result.get('content'),
            }

        from open_webui.routers.files import upload_file_handler

        is_image = url_result['content_type'].startswith('image/')
        file = UploadFile(
            file=io.BytesIO(url_result['data']),
            filename=url_result['filename'],
            headers={'content-type': url_result['content_type']},
        )
        uploaded_file = await upload_file_handler(
            request,
            file=file,
            metadata={'source_url': form_data.url},
            process=process and not is_image,
            process_in_background=False,
            user=user,
        )
        file_data = uploaded_file.model_dump() if hasattr(uploaded_file, 'model_dump') else uploaded_file
        file_id = file_data.get('id') if isinstance(file_data, dict) else None
        if file_id:
            refreshed_file = await Files.get_file_by_id(file_id)
            if refreshed_file:
                file_data = refreshed_file.model_dump()
        file_data.pop('path', None)
        return {
            'status': True,
            'type': 'image' if is_image else 'file',
            'name': url_result['filename'],
            'url': form_data.url,
            'collection_name': (file_data.get('meta') or {}).get('collection_name'),
            'file': file_data,
        }
    except SoevNotConfigured:
        raise
    except HTTPException:
        raise
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e, 'Error processing URL'),
        )


@router.post('/process/youtube')
@router.post('/process/web')
async def process_web(
    request: Request,
    form_data: ProcessUrlForm,
    process: bool = Query(True, description='Whether to process and save the content'),
    overwrite: bool = Query(True, description='Whether to overwrite existing collection'),
    user=Depends(get_verified_user),
):
    config = await get_rag_config_state()

    try:
        content, docs = await get_content_from_url(request, form_data.url)
    except HTTPException:
        raise
    except YoutubeTranscriptError as e:
        log.warning('YouTube transcript unavailable')
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e, f'Could not read content from {form_data.url}'),
        )

    try:
        log.debug(f'text_content length: {len(content) if content else 0}')

        if process:
            collection_name = form_data.collection_name
            if not collection_name:
                collection_name = calculate_sha256_string(form_data.url)[:63]
            else:
                await _validate_collection_access([collection_name], user, access_type='write')

            collection_name = None

            return {
                'status': True,
                'collection_name': collection_name,
                'filename': form_data.url,
                'content': content,
                'file': {
                    'data': {
                        'content': content,
                    },
                    'meta': {
                        'name': form_data.url,
                        'source': form_data.url,
                    },
                },
            }
        else:
            return {
                'status': True,
                'content': content,
            }
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


# [Gradient] TODO(merge-v0.11.3): Converge search_web to async with the retrieval config carve-out?
# Keep its worker-thread contract; pooled async engines run on the serving loop.
def search_web(request: Request, engine: str, query: str, user=None, config=None) -> list[SearchResult]:
    """Search the web using a search engine and return the results as a list of SearchResult objects.
    Will look for a search engine API key in environment variables in the following order:
    - SEARXNG_QUERY_URL
    - YACY_QUERY_URL + YACY_USERNAME + YACY_PASSWORD
    - GOOGLE_PSE_API_KEY + GOOGLE_PSE_ENGINE_ID
    - BRAVE_SEARCH_API_KEY
    - KAGI_SEARCH_API_KEY
    - MOJEEK_SEARCH_API_KEY
    - BOCHA_SEARCH_API_KEY
    - SERPSTACK_API_KEY
    - SERPER_API_KEY
    - SERPLY_API_KEY
    - TAVILY_API_KEY
    - EXA_API_KEY
    - PERPLEXITY_API_KEY
    - SOUGOU_API_SID + SOUGOU_API_SK
    - SEARCHAPI_API_KEY + SEARCHAPI_ENGINE (by default `google`)
    - SERPAPI_API_KEY + SERPAPI_ENGINE (by default `google`)
    Args:
        query (str): The query to search for
    """

    # TODO: add playwright to search the web
    if config is None:
        config = run_on_main_loop(get_rag_config_state())
    if engine == 'ollama_cloud':
        return search_ollama_cloud(
            'https://ollama.com',
            config.OLLAMA_CLOUD_WEB_SEARCH_API_KEY,
            query,
            config.WEB_SEARCH_RESULT_COUNT,
            config.WEB_SEARCH_DOMAIN_FILTER_LIST,
        )
    elif engine == 'perplexity_search':
        if config.PERPLEXITY_API_KEY:
            return search_perplexity_search(
                config.PERPLEXITY_API_KEY,
                query,
                config.WEB_SEARCH_RESULT_COUNT,
                config.WEB_SEARCH_DOMAIN_FILTER_LIST,
                config.PERPLEXITY_SEARCH_API_URL,
                user,
            )
        else:
            raise Exception('No PERPLEXITY_API_KEY found in environment variables')
    elif engine == 'searxng':
        if config.SEARXNG_QUERY_URL:
            searxng_kwargs = {'language': config.SEARXNG_LANGUAGE}
            return run_on_main_loop(
                search_searxng(
                    config.SEARXNG_QUERY_URL,
                    query,
                    config.WEB_SEARCH_RESULT_COUNT,
                    config.WEB_SEARCH_DOMAIN_FILTER_LIST,
                    **searxng_kwargs,
                )
            )
        else:
            raise Exception('No SEARXNG_QUERY_URL found in environment variables')
    elif engine == 'openserp':
        if config.OPENSERP_BASE_URL:
            return run_on_main_loop(
                search_openserp(
                    config.OPENSERP_BASE_URL,
                    query,
                    config.WEB_SEARCH_RESULT_COUNT,
                    config.WEB_SEARCH_DOMAIN_FILTER_LIST,
                )
            )
        else:
            raise Exception('No OPENSERP_BASE_URL found in environment variables')
    elif engine == 'yacy':
        if config.YACY_QUERY_URL:
            return search_yacy(
                config.YACY_QUERY_URL,
                config.YACY_USERNAME,
                config.YACY_PASSWORD,
                query,
                config.WEB_SEARCH_RESULT_COUNT,
                config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception('No YACY_QUERY_URL found in environment variables')
    elif engine == 'google_pse':
        if config.GOOGLE_PSE_API_KEY and config.GOOGLE_PSE_ENGINE_ID:
            return run_on_main_loop(
                search_google_pse(
                    config.GOOGLE_PSE_API_KEY,
                    config.GOOGLE_PSE_ENGINE_ID,
                    query,
                    config.WEB_SEARCH_RESULT_COUNT,
                    config.WEB_SEARCH_DOMAIN_FILTER_LIST,
                    referer=config.WEBUI_URL,
                )
            )
        else:
            raise Exception('No GOOGLE_PSE_API_KEY or GOOGLE_PSE_ENGINE_ID found in environment variables')
    elif engine == 'brave':
        if config.BRAVE_SEARCH_API_KEY:
            return run_on_main_loop(
                search_brave(
                    config.BRAVE_SEARCH_API_KEY,
                    query,
                    config.WEB_SEARCH_RESULT_COUNT,
                    config.WEB_SEARCH_DOMAIN_FILTER_LIST,
                )
            )
        else:
            raise Exception('No BRAVE_SEARCH_API_KEY found in environment variables')
    elif engine == 'brave_llm_context':
        if config.BRAVE_SEARCH_API_KEY:
            return search_brave_llm_context(
                config.BRAVE_SEARCH_API_KEY,
                query,
                config.WEB_SEARCH_RESULT_COUNT,
                config.WEB_SEARCH_DOMAIN_FILTER_LIST,
                config.BRAVE_SEARCH_CONTEXT_TOKENS,
            )
        else:
            raise Exception('No BRAVE_SEARCH_API_KEY found in environment variables')
    elif engine == 'kagi':
        if config.KAGI_SEARCH_API_KEY:
            return search_kagi(
                config.KAGI_SEARCH_API_KEY,
                query,
                config.WEB_SEARCH_RESULT_COUNT,
                config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception('No KAGI_SEARCH_API_KEY found in environment variables')
    elif engine == 'mojeek':
        if config.MOJEEK_SEARCH_API_KEY:
            return search_mojeek(
                config.MOJEEK_SEARCH_API_KEY,
                query,
                config.WEB_SEARCH_RESULT_COUNT,
                config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception('No MOJEEK_SEARCH_API_KEY found in environment variables')
    elif engine == 'bocha':
        if config.BOCHA_SEARCH_API_KEY:
            return search_bocha(
                config.BOCHA_SEARCH_API_KEY,
                query,
                config.WEB_SEARCH_RESULT_COUNT,
                config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception('No BOCHA_SEARCH_API_KEY found in environment variables')
    elif engine == 'serpstack':
        if config.SERPSTACK_API_KEY:
            return run_on_main_loop(
                search_serpstack(
                    config.SERPSTACK_API_KEY,
                    query,
                    config.WEB_SEARCH_RESULT_COUNT,
                    config.WEB_SEARCH_DOMAIN_FILTER_LIST,
                    https_enabled=config.SERPSTACK_HTTPS,
                )
            )
        else:
            raise Exception('No SERPSTACK_API_KEY found in environment variables')
    elif engine == 'serper':
        if config.SERPER_API_KEY:
            return run_on_main_loop(
                search_serper(
                    config.SERPER_API_KEY,
                    query,
                    config.WEB_SEARCH_RESULT_COUNT,
                    config.WEB_SEARCH_DOMAIN_FILTER_LIST,
                )
            )
        else:
            raise Exception('No SERPER_API_KEY found in environment variables')
    elif engine == 'serphouse':
        if config.SERPHOUSE_API_KEY:
            return run_on_main_loop(
                search_serphouse(
                    config.SERPHOUSE_API_KEY,
                    config.SERPHOUSE_DOMAIN,
                    query,
                    config.WEB_SEARCH_RESULT_COUNT,
                    config.WEB_SEARCH_DOMAIN_FILTER_LIST,
                )
            )
        else:
            raise Exception('No SERPHOUSE_API_KEY found in environment variables')
    elif engine == 'serply':
        if config.SERPLY_API_KEY:
            return search_serply(
                config.SERPLY_API_KEY,
                query,
                config.WEB_SEARCH_RESULT_COUNT,
                filter_list=config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception('No SERPLY_API_KEY found in environment variables')
    elif engine == 'duckduckgo':
        return search_duckduckgo(
            query,
            config.WEB_SEARCH_RESULT_COUNT,
            config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            concurrent_requests=config.WEB_SEARCH_CONCURRENT_REQUESTS,
            backend=config.DDGS_BACKEND,
        )
    elif engine == 'tavily':
        if config.TAVILY_API_KEY:
            return search_tavily(
                config.TAVILY_API_KEY,
                query,
                config.WEB_SEARCH_RESULT_COUNT,
                config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception('No TAVILY_API_KEY found in environment variables')
    elif engine == 'exa':
        if config.EXA_API_KEY:
            return search_exa(
                config.EXA_API_KEY,
                query,
                config.WEB_SEARCH_RESULT_COUNT,
                config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception('No EXA_API_KEY found in environment variables')
    elif engine == 'searchapi':
        if config.SEARCHAPI_API_KEY:
            return search_searchapi(
                config.SEARCHAPI_API_KEY,
                config.SEARCHAPI_ENGINE,
                query,
                config.WEB_SEARCH_RESULT_COUNT,
                config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception('No SEARCHAPI_API_KEY found in environment variables')
    elif engine == 'serpapi':
        if config.SERPAPI_API_KEY:
            return search_serpapi(
                config.SERPAPI_API_KEY,
                config.SERPAPI_ENGINE,
                query,
                config.WEB_SEARCH_RESULT_COUNT,
                config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception('No SERPAPI_API_KEY found in environment variables')
    elif engine == 'jina':
        return search_jina(
            config.JINA_API_KEY,
            query,
            config.WEB_SEARCH_RESULT_COUNT,
            config.JINA_API_BASE_URL,
        )
    elif engine == 'bing':
        return search_bing(
            config.BING_SEARCH_V7_SUBSCRIPTION_KEY,
            config.BING_SEARCH_V7_ENDPOINT,
            str(DEFAULT_LOCALE),
            query,
            config.WEB_SEARCH_RESULT_COUNT,
            config.WEB_SEARCH_DOMAIN_FILTER_LIST,
        )
    elif engine == 'azure':
        if config.AZURE_AI_SEARCH_API_KEY and config.AZURE_AI_SEARCH_ENDPOINT and config.AZURE_AI_SEARCH_INDEX_NAME:
            return search_azure(
                config.AZURE_AI_SEARCH_API_KEY,
                config.AZURE_AI_SEARCH_ENDPOINT,
                config.AZURE_AI_SEARCH_INDEX_NAME,
                query,
                config.WEB_SEARCH_RESULT_COUNT,
                config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception(
                'AZURE_AI_SEARCH_API_KEY, AZURE_AI_SEARCH_ENDPOINT, and AZURE_AI_SEARCH_INDEX_NAME are required for Azure AI Search'
            )
    elif engine == 'exa':
        return search_exa(
            config.EXA_API_KEY,
            query,
            config.WEB_SEARCH_RESULT_COUNT,
            config.WEB_SEARCH_DOMAIN_FILTER_LIST,
        )
    elif engine == 'perplexity':
        return search_perplexity(
            config.PERPLEXITY_API_KEY,
            query,
            config.WEB_SEARCH_RESULT_COUNT,
            config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            model=config.PERPLEXITY_MODEL,
            search_context_usage=config.PERPLEXITY_SEARCH_CONTEXT_USAGE,
        )
    elif engine == 'microsoft_web_iq':
        if config.MICROSOFT_WEB_IQ_API_KEY:
            return search_microsoft_web_iq(
                config.MICROSOFT_WEB_IQ_API_BASE_URL,
                config.MICROSOFT_WEB_IQ_API_KEY,
                query,
                config.WEB_SEARCH_RESULT_COUNT,
                config.WEB_SEARCH_DOMAIN_FILTER_LIST,
                config.MICROSOFT_WEB_IQ_LANGUAGE,
                user,
            )
        else:
            raise Exception('No MICROSOFT_WEB_IQ_API_KEY found in environment variables')
    elif engine == 'sougou':
        if config.SOUGOU_API_SID and config.SOUGOU_API_SK:
            return search_sougou(
                config.SOUGOU_API_SID,
                config.SOUGOU_API_SK,
                query,
                config.WEB_SEARCH_RESULT_COUNT,
                config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception('No SOUGOU_API_SID or SOUGOU_API_SK found in environment variables')
    elif engine == 'firecrawl':
        return search_firecrawl(
            config.FIRECRAWL_API_BASE_URL,
            config.FIRECRAWL_API_KEY,
            query,
            config.WEB_SEARCH_RESULT_COUNT,
            config.WEB_SEARCH_DOMAIN_FILTER_LIST,
        )
    elif engine == 'external':
        return search_external(
            request,
            config.EXTERNAL_WEB_SEARCH_URL,
            config.EXTERNAL_WEB_SEARCH_API_KEY,
            query,
            config.WEB_SEARCH_RESULT_COUNT,
            config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            user=user,
        )
    elif engine == 'yandex':
        return search_yandex(
            request,
            config.YANDEX_WEB_SEARCH_URL,
            config.YANDEX_WEB_SEARCH_API_KEY,
            config.YANDEX_WEB_SEARCH_CONFIG,
            query,
            config.WEB_SEARCH_RESULT_COUNT,
            config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            user=user,
        )
    elif engine == 'linkup':
        if config.LINKUP_API_KEY:
            return search_linkup(
                api_key=config.LINKUP_API_KEY,
                query=query,
                count=config.WEB_SEARCH_RESULT_COUNT,
                filter_list=config.WEB_SEARCH_DOMAIN_FILTER_LIST,
                params=config.LINKUP_SEARCH_PARAMS,
            )
        else:
            raise Exception('No LINKUP_API_KEY found in environment variables')
    elif engine == 'youcom':
        return search_youcom(
            config.YOUCOM_API_KEY,
            query,
            config.WEB_SEARCH_RESULT_COUNT,
            config.WEB_SEARCH_DOMAIN_FILTER_LIST,
        )
    else:
        raise Exception('No search engine API key found in environment variables')


@router.post('/process/web/search')
async def process_web_search(request: Request, form_data: SearchForm, user=Depends(get_verified_user)):
    config = await get_rag_config_state()
    if not config.ENABLE_WEB_SEARCH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if user.role != 'admin' and not await has_permission(user.id, 'features.web_search', config.USER_PERMISSIONS):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    urls = []
    result_items = []

    try:
        # [Gradient] Search logs contain counts only.
        log.debug('web search: engine=%s queries=%s', config.WEB_SEARCH_ENGINE, len(form_data.queries))

        # Use semaphore to limit concurrent requests based on WEB_SEARCH_CONCURRENT_REQUESTS
        # 0 or None = unlimited (previous behavior), positive number = limited concurrency
        # Set to 1 for sequential execution (rate-limited APIs like Brave free tier)
        concurrent_limit = config.WEB_SEARCH_CONCURRENT_REQUESTS

        if concurrent_limit:
            # Limited concurrency with semaphore
            semaphore = asyncio.Semaphore(concurrent_limit)

            async def search_query_with_semaphore(query):
                async with semaphore:
                    return await run_in_threadpool(
                        search_web,
                        request,
                        config.WEB_SEARCH_ENGINE,
                        query,
                        user,
                    )

            search_tasks = [search_query_with_semaphore(query) for query in form_data.queries]
        else:
            # Unlimited parallel execution (previous behavior)
            search_tasks = [
                run_in_threadpool(
                    search_web,
                    request,
                    config.WEB_SEARCH_ENGINE,
                    query,
                    user,
                )
                for query in form_data.queries
            ]

        search_results = await asyncio.gather(*search_tasks)

        for result in search_results:
            if result:
                for item in result:
                    if item and item.link:
                        result_items.append(item)
                        urls.append(item.link)

        urls = list(dict.fromkeys(urls))
        log.debug(f'web search returned {len(urls)} urls')

    except Exception as e:
        log.exception('Web search failed')
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e, ERROR_MESSAGES.WEB_SEARCH_ERROR),
        )

    if len(urls) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.DEFAULT('No results found from web search'),
        )

    try:
        if config.BYPASS_WEB_SEARCH_WEB_LOADER:
            search_results = [item for result in search_results for item in result if result]

            docs = [
                Document(
                    page_content=result.snippet,
                    metadata={
                        'source': result.link,
                        'title': result.title,
                        'snippet': result.snippet,
                        'link': result.link,
                    },
                )
                for result in search_results
                if hasattr(result, 'snippet') and result.snippet is not None
            ]
        else:
            loader_config = await get_loader_config()
            loader = get_web_loader(
                urls,
                verify_ssl=loader_config.get('web_loader_ssl_verification'),
                requests_per_second=loader_config.get('web_loader_concurrent_requests'),
                trust_env=loader_config.get('web_search_trust_env'),
                loader_config=loader_config,
            )
            docs = await loader.aload()

        urls = [
            doc.metadata.get('source') for doc in docs if doc.metadata.get('source')
        ]  # only keep the urls returned by the loader
        url_set = set(urls)
        result_items = [
            dict(item) for item in result_items if item.link in url_set
        ]  # only keep the search results that have been loaded

        return {
            'status': True,
            'collection_name': None,
            'filenames': urls,
            'items': result_items,
            'docs': [
                {
                    'content': doc.page_content,
                    'metadata': doc.metadata,
                }
                for doc in docs
            ],
            'loaded_count': len(docs),
        }
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e, ERROR_MESSAGES.WEB_SEARCH_ERROR),
        )


async def _validate_collection_access(collection_names: list[str], user, access_type: str = 'read') -> None:
    """
    Raise 403 if the user lacks access to any of the requested collections.
    Delegates to the shared filter_accessible_collections utility so the
    access rules (file-*, user-memory-*, web-search-*, KB ownership/grants,
    legacy collections) stay in one place.
    """
    requested = set(collection_names)
    allowed = await filter_accessible_collections(requested, user, access_type=access_type)
    denied = requested - allowed
    if denied:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )


####################################
#
# Vector DB operations
#
####################################


class DeleteForm(BaseModel):
    collection_name: str
    file_id: str


@router.post('/delete')
async def delete_entries_from_collection(
    form_data: DeleteForm,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    await _validate_collection_access([form_data.collection_name], user, access_type='write')
    try:
        if await ASYNC_VECTOR_DB_CLIENT.has_collection(collection_name=form_data.collection_name):
            file = await Files.get_file_by_id(form_data.file_id, db=db)
            if not file:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ERROR_MESSAGES.NOT_FOUND,
                )
            hash = file.hash

            # Refuse to issue a `filter={'hash': None}` query — the match
            # semantics of a null filter value are backend-dependent (some
            # backends ignore the key, some match every row whose metadata
            # lacks `hash`) and risk deleting unrelated entries. Files without
            # a hash are typically unprocessed / failed / legacy records that
            # can't be targeted by hash anyway.
            if hash is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT('File has no hash; cannot delete vector entries by hash.'),
                )

            # Pre-existing bug: this used `metadata=` which is not a parameter
            # on `VectorDBBase.delete` nor on any backend implementation, so
            # the call always raised TypeError that was silently swallowed by
            # the surrounding `except Exception` and the endpoint reported
            # `{'status': False}` for every request. Use `filter` to actually
            # do what the endpoint name promises.
            await ASYNC_VECTOR_DB_CLIENT.delete(
                collection_name=form_data.collection_name,
                filter={'hash': hash},
            )
            return {'status': True}
        else:
            return {'status': False}
    except HTTPException:
        # Caller-meaningful errors (404/400 above) must not be swallowed and
        # re-shaped as `{'status': False}`.
        raise
    except Exception as e:
        log.exception(e)
        return {'status': False}


@router.post('/reset/db')
async def reset_vector_db(user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)):
    if not can_bypass_access_control(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    await ASYNC_VECTOR_DB_CLIENT.reset()
    if configured():
        await Knowledges.delete_all_knowledge(db=db)


@router.post('/reset/uploads')
async def reset_upload_dir(request: Request, user=Depends(get_admin_user)) -> bool:
    if not can_bypass_access_control(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    folder = f'{UPLOAD_DIR}'
    try:
        # Check if the directory exists
        if await asyncio.to_thread(os.path.exists, folder):
            # Iterate over all the files and directories in the specified directory
            for filename in await asyncio.to_thread(os.listdir, folder):
                file_path = os.path.join(folder, filename)
                try:
                    if await asyncio.to_thread(os.path.isfile, file_path) or await asyncio.to_thread(
                        os.path.islink, file_path
                    ):
                        await asyncio.to_thread(os.unlink, file_path)  # Remove the file or link
                    elif await asyncio.to_thread(os.path.isdir, file_path):
                        await asyncio.to_thread(shutil.rmtree, file_path)  # Remove the directory
                except Exception as e:
                    log.exception(f'Failed to delete {file_path}. Reason: {e}')
        else:
            log.warning(f'The directory {folder} does not exist')
    except Exception as e:
        log.exception(f'Failed to process the directory {folder}. Reason: {e}')

    await publish_event(
        request,
        EVENTS.RETRIEVAL_UPLOADS_RESET,
        actor=user,
        subject_id='all',
        subject_type='file.uploads',
    )
    return True


class BatchProcessFilesForm(BaseModel):
    files: List[FileModel]
    collection_name: str


class BatchProcessFilesResult(BaseModel):
    file_id: str
    status: str
    error: Optional[str] = None


class BatchProcessFilesResponse(BaseModel):
    results: List[BatchProcessFilesResult]
    errors: List[BatchProcessFilesResult]


@router.post('/process/files/batch')
async def process_files_batch(
    request: Request,
    form_data: BatchProcessFilesForm,
    user=Depends(get_verified_user),
    db=None,
) -> BatchProcessFilesResponse:
    ensure_configured()
    collection_name = form_data.collection_name
    if collection_name:
        await _validate_collection_access([collection_name], user, access_type='write')
    results = []
    errors = []
    for file in form_data.files:
        db_file = await Files.get_file_by_id(file.id, db=db)
        if not db_file:
            errors.append(BatchProcessFilesResult(file_id=file.id, status='failed', error='File not found'))
            continue
        if db_file.user_id != user.id and not can_bypass_access_control(user):
            errors.append(
                BatchProcessFilesResult(file_id=file.id, status='failed', error='Permission denied: not file owner')
            )
            continue
        try:
            await ingest.submit(db_file, collection_key=collection_name, user_id=user.id)
        except SoevNotConfigured:
            raise
        except (SoevApiError, ingest.IngestBusy) as e:
            error = f'{e.code}: {e.detail}' if isinstance(e, SoevApiError) else 'file is being processed'
            errors.append(BatchProcessFilesResult(file_id=file.id, status='failed', error=error))
        else:
            results.append(BatchProcessFilesResult(file_id=file.id, status='processing'))
    return BatchProcessFilesResponse(results=results, errors=errors)
