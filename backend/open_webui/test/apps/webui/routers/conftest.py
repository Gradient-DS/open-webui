"""Pytest configuration for router tests.

Router modules transitively import ``open_webui.config``, which calls
``get_config()`` at module level (line 155-161 of config.py) — a synchronous
SQLAlchemy query that fails without a live database.

We break the chain by patching ``sqlalchemy.orm.query.Query.first`` BEFORE any
test module is imported (at conftest module import time).  A fixture, even a
session-scoped autouse one, fires AFTER collection, which is too late because
``open_webui.config`` is imported during collection.

The patch is NARROW: it only short-circuits queries that target the ``Config``
model (the only one accessed at import time) and delegates every other
``.first()`` call to the original implementation.  This avoids nutering all
synchronous ORM ``.first()`` calls for the entire session, which would cause
sibling router tests (test_auths, test_users, test_models, etc.) to silently
receive ``None`` from legitimate queries.

The original ``.first()`` is restored via ``pytest_unconfigure`` at the very
end of the session.
"""

from __future__ import annotations

import sqlalchemy.orm.query

# Capture the real implementation BEFORE patching.
_orig_first = sqlalchemy.orm.query.Query.first


def _patched_first(self):  # type: ignore[no-untyped-def]
    """Delegating wrapper that only short-circuits Config-model queries.

    Returns ``None`` when the query targets the ``Config`` model so that
    ``get_config()`` in ``open_webui.config`` falls back to
    ``DEFAULT_CONFIG`` without touching the database.  All other queries are
    forwarded to the real ``.first()`` implementation.

    Discrimination logic: inspect ``self.column_descriptions`` (a list of
    dicts, one per selected entity/column).  If any descriptor's ``entity``
    or ``type`` key is a class whose ``__name__`` is ``'Config'``, treat this
    as the import-time Config query.  The check is wrapped in a broad
    try/except so a malformed descriptor (e.g. raw-column queries that return
    a plain dict without an ``entity`` key) never breaks delegation.
    """
    try:
        for desc in self.column_descriptions:
            entity = desc.get('entity') or desc.get('type')
            if entity is not None and getattr(entity, '__name__', None) == 'Config':
                return None
    except Exception:  # noqa: BLE001 — never let inspection break real queries
        pass
    return _orig_first(self)


# Apply at conftest import time (before collection/imports of test modules).
sqlalchemy.orm.query.Query.first = _patched_first  # type: ignore[method-assign]


def pytest_unconfigure(config):  # noqa: ANN001 — pytest Config, not OWUI Config
    """Restore the original Query.first at session end."""
    sqlalchemy.orm.query.Query.first = _orig_first  # type: ignore[method-assign]
