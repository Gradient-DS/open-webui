"""Pytest configuration for router tests.

Router modules transitively import ``open_webui.config``, which calls
``get_config()`` at module level — a synchronous SQLAlchemy query that fails
without a live database.

We break the chain by patching ``sqlalchemy.orm.Session.execute`` to return an
empty result before any test module is imported.  This lets ``CONFIG_DATA =
get_config()`` return the default config dict without touching Postgres.
"""

from __future__ import annotations

from unittest.mock import patch

# Patch Session.execute so Config.query().first() returns None → get_config()
# falls back to DEFAULT_CONFIG without raising.
_session_exec_patcher = patch(
    'sqlalchemy.orm.query.Query.first',
    return_value=None,
)
_session_exec_patcher.start()
