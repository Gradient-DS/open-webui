"""Read-only checks for schema objects normally created by the vector adapter."""

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.schema import Table


def verify_pgvector_schema(session: Session, table: Table, index_method: str, encrypted: bool) -> None:
    def missing(name: str) -> None:
        raise RuntimeError(
            f'Missing database object {name}; ENABLE_DB_MIGRATIONS=false. '
            'Run python -m open_webui.soev.migrate with the migration login before starting Open WebUI.'
        )

    for extension in ('vector', 'pgcrypto') if encrypted else ('vector',):
        if not session.execute(text('SELECT 1 FROM pg_extension WHERE extname = :name'), {'name': extension}).scalar():
            missing(f'extension {extension}')

    inspector = inspect(session.connection())
    if not inspector.has_table(table.name):
        missing(f'table {table.name}')
    columns = {column['name'] for column in inspector.get_columns(table.name)}
    for column in table.columns:
        if column.name not in columns:
            missing(f'column {table.name}.{column.name}')

    indexes = dict(
        session.execute(
            text("""
                SELECT c.relname, am.amname
                FROM pg_index i
                JOIN pg_class c ON c.oid = i.indexrelid
                JOIN pg_am am ON am.oid = c.relam
                WHERE i.indrelid = to_regclass(:table) AND i.indisvalid AND i.indisready
            """),
            {'table': table.name},
        ).all()
    )
    required = {'idx_document_chunk_collection_name': 'btree', 'idx_document_chunk_vector': index_method}
    if not encrypted:
        required['idx_document_chunk_text_search'] = 'gin'
    for name, method in required.items():
        if name not in indexes:
            missing(f'index {name}')
        if indexes[name] != method:
            raise RuntimeError(f'Database index {name} uses {indexes[name]}; configuration requires {method}.')
