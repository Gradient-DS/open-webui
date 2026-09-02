"""Environment pinning for the util test package.

``open_webui.env`` resolves ``DATABASE_URL`` at import time, and
``open_webui.internal.db`` builds its engines (and runs peewee
migrations) at import time too. Test modules setting ``os.environ``
at their own module top are therefore order-dependent: whichever test
module imports an ``open_webui`` module first freezes the database
choice for the whole pytest session.

This conftest imports before ANY test module in this directory is
collected, so the pinning below wins regardless of file order:

- ``DATABASE_URL`` → throwaway per-session sqlite file;
- the ``DATABASE_*`` component vars → empty. ``env.py`` composes a URL
  from them (beating ``DATABASE_URL``) when ALL are set, and the local
  dev ``.env`` sets them; present-but-empty both skips the composition
  and blocks the dotenv load (``override=False``) from filling them in.

``ENABLE_DB_MIGRATIONS`` stays at its default (true): importing
``open_webui.config`` executes ``get_config()``, which needs the
``config`` table — the peewee migration chain creates it on the fresh
sqlite file, same as on a first boot.
"""

from __future__ import annotations

import os
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix='owui-util-tests-')

os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret-key')
os.environ['DATABASE_URL'] = f'sqlite:///{_TMP_DIR}/test.db'
os.environ.setdefault('DATA_DIR', _TMP_DIR)
for _var in ('DATABASE_TYPE', 'DATABASE_USER', 'DATABASE_PASSWORD', 'DATABASE_HOST', 'DATABASE_PORT', 'DATABASE_NAME'):
    os.environ[_var] = ''

# ``open_webui.config`` imports the driver for whichever backend ``VECTOR_DB``
# names, and upstream's default is ``chroma``, whose package this fork no longer
# ships. These tests touch no vector store; pin the value we actually deploy so
# the import resolves. Weaviate needs no import here — config only reads its
# host/port env vars, and nothing connects until the vector factory is imported,
# which these tests never do.
os.environ.setdefault('VECTOR_DB', 'weaviate')

# Build the schema through the REAL migration chain (peewee via
# internal.db + alembic via config's import-time run_migrations), not
# metadata.create_all. Deliberate: a model column that lacks its alembic
# migration now breaks these tests the same way it would break a
# production tenant. (This schema-drift class was a *suspected* cause of
# the July 2026 analytics incident but turned out NOT to be it — the
# migration was applied; guarding it is still worthwhile.) Import order
# also stops being test-file dependent: the schema exists before any
# test module imports.
import open_webui.config  # noqa: E402, F401
