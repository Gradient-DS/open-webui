"""Upgrade the application and pgvector schemas without importing application config."""

import json
import logging
import os
import sys
import warnings
from contextlib import redirect_stdout


# Named, not imported: pgvector_config raises at import when its settings are wrong.
_FIXED_SENTENCE_MODULES = frozenset({'open_webui.soev.db_schema', 'open_webui.soev.pgvector_config'})


def upgrade_alembic() -> int:
    from alembic import command
    from alembic.config import Config

    from open_webui.env import OPEN_WEBUI_DIR

    count = 0

    def applied(**kwargs):
        nonlocal count
        count += 1

    config = Config(OPEN_WEBUI_DIR / 'alembic.ini')
    config.set_main_option('script_location', str(OPEN_WEBUI_DIR / 'migrations'))
    config.attributes['skip_model_imports'] = True
    config.attributes['on_version_apply'] = applied
    command.upgrade(config, 'head')
    return count


def _failure(step: str, error: Exception) -> str:
    """Name the step and a safe cause; driver messages can contain row values."""
    cause = type(error).__name__
    sqlstate = getattr(getattr(error, 'orig', error), 'sqlstate', None)
    if sqlstate:
        cause += f', SQLSTATE {sqlstate}'
    # These two modules raise fixed sentences about settings and object names only.
    raised_in = error.__traceback__
    while raised_in is not None and raised_in.tb_next is not None:
        raised_in = raised_in.tb_next
    origin = raised_in.tb_frame.f_globals.get('__name__') if raised_in is not None else None
    if origin in _FIXED_SENTENCE_MODULES:
        cause += f': {error}'
    return f'Schema migration failed at {step} ({cause}); database upgrade did not complete.'


def main() -> int:
    # This command is the explicit schema step, even if its ConfigMap defaults to false.
    os.environ['ENABLE_DB_MIGRATIONS'] = 'true'
    logging.disable(logging.CRITICAL)
    step = 'alembic upgrade'
    try:
        # Legacy revisions print row identifiers. Discard them rather than retaining data in logs.
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), warnings.catch_warnings():
            warnings.simplefilter('ignore')
            counts = {'alembic_upgrades': upgrade_alembic(), 'pgvector_tables': 0, 'pgvector_indexes': 0}
            if os.getenv('VECTOR_DB', 'chroma') == 'pgvector':
                step = 'pgvector schema'
                from open_webui.retrieval.vector.dbs.pgvector import PGVECTOR_PGCRYPTO, PgvectorClient

                client = PgvectorClient()
                client.session.remove()
                counts.update(pgvector_tables=1, pgvector_indexes=2 if PGVECTOR_PGCRYPTO else 3)
    except Exception as error:
        print(_failure(step, error), file=sys.stderr)
        return 1
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
