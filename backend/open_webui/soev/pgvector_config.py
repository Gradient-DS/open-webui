"""Pgvector settings shared by startup and the standalone schema upgrade."""

import os

from open_webui.env import DATABASE_URL

VECTOR_DB = os.getenv('VECTOR_DB', 'chroma')

PGVECTOR_DB_URL = os.getenv('PGVECTOR_DB_URL', DATABASE_URL)
if VECTOR_DB == 'pgvector' and not PGVECTOR_DB_URL.startswith('postgres'):
    raise ValueError(
        'Pgvector requires setting PGVECTOR_DB_URL or using Postgres with vector extension as the primary database.'
    )
PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH = int(os.getenv('PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH', '1536'))

PGVECTOR_USE_HALFVEC = os.getenv('PGVECTOR_USE_HALFVEC', 'false').lower() == 'true'

if PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH > 2000 and not PGVECTOR_USE_HALFVEC:
    raise ValueError(
        'PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH is set to '
        f'{PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH}, which exceeds the 2000 dimension limit of the '
        "'vector' type. Set PGVECTOR_USE_HALFVEC=true to enable the 'halfvec' "
        'type required for high-dimensional embeddings.'
    )

PGVECTOR_CREATE_EXTENSION = os.getenv('PGVECTOR_CREATE_EXTENSION', 'true').lower() == 'true'
PGVECTOR_PGCRYPTO = os.getenv('PGVECTOR_PGCRYPTO', 'false').lower() == 'true'
PGVECTOR_PGCRYPTO_KEY = os.getenv('PGVECTOR_PGCRYPTO_KEY', None)
if PGVECTOR_PGCRYPTO and not PGVECTOR_PGCRYPTO_KEY:
    raise ValueError('PGVECTOR_PGCRYPTO is enabled but PGVECTOR_PGCRYPTO_KEY is not set. Please provide a valid key.')


PGVECTOR_POOL_SIZE = os.getenv('PGVECTOR_POOL_SIZE', None)

if PGVECTOR_POOL_SIZE != None:
    try:
        PGVECTOR_POOL_SIZE = int(PGVECTOR_POOL_SIZE)
    except Exception:
        PGVECTOR_POOL_SIZE = None

PGVECTOR_POOL_MAX_OVERFLOW = os.getenv('PGVECTOR_POOL_MAX_OVERFLOW', 0)

if PGVECTOR_POOL_MAX_OVERFLOW == '':
    PGVECTOR_POOL_MAX_OVERFLOW = 0
else:
    try:
        PGVECTOR_POOL_MAX_OVERFLOW = int(PGVECTOR_POOL_MAX_OVERFLOW)
    except Exception:
        PGVECTOR_POOL_MAX_OVERFLOW = 0

PGVECTOR_POOL_TIMEOUT = os.getenv('PGVECTOR_POOL_TIMEOUT', 30)

if PGVECTOR_POOL_TIMEOUT == '':
    PGVECTOR_POOL_TIMEOUT = 30
else:
    try:
        PGVECTOR_POOL_TIMEOUT = int(PGVECTOR_POOL_TIMEOUT)
    except Exception:
        PGVECTOR_POOL_TIMEOUT = 30

PGVECTOR_POOL_RECYCLE = os.getenv('PGVECTOR_POOL_RECYCLE', 3600)

if PGVECTOR_POOL_RECYCLE == '':
    PGVECTOR_POOL_RECYCLE = 3600
else:
    try:
        PGVECTOR_POOL_RECYCLE = int(PGVECTOR_POOL_RECYCLE)
    except Exception:
        PGVECTOR_POOL_RECYCLE = 3600

PGVECTOR_INDEX_METHOD = os.getenv('PGVECTOR_INDEX_METHOD', '').strip().lower()
if PGVECTOR_INDEX_METHOD not in ('ivfflat', 'hnsw', ''):
    PGVECTOR_INDEX_METHOD = ''

PGVECTOR_HNSW_M = os.getenv('PGVECTOR_HNSW_M', 16)

if PGVECTOR_HNSW_M == '':
    PGVECTOR_HNSW_M = 16
else:
    try:
        PGVECTOR_HNSW_M = int(PGVECTOR_HNSW_M)
    except Exception:
        PGVECTOR_HNSW_M = 16

PGVECTOR_HNSW_EF_CONSTRUCTION = os.getenv('PGVECTOR_HNSW_EF_CONSTRUCTION', 64)

if PGVECTOR_HNSW_EF_CONSTRUCTION == '':
    PGVECTOR_HNSW_EF_CONSTRUCTION = 64
else:
    try:
        PGVECTOR_HNSW_EF_CONSTRUCTION = int(PGVECTOR_HNSW_EF_CONSTRUCTION)
    except Exception:
        PGVECTOR_HNSW_EF_CONSTRUCTION = 64

PGVECTOR_IVFFLAT_LISTS = os.getenv('PGVECTOR_IVFFLAT_LISTS', 100)

if PGVECTOR_IVFFLAT_LISTS == '':
    PGVECTOR_IVFFLAT_LISTS = 100
else:
    try:
        PGVECTOR_IVFFLAT_LISTS = int(PGVECTOR_IVFFLAT_LISTS)
    except Exception:
        PGVECTOR_IVFFLAT_LISTS = 100
