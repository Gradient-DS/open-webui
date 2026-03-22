---
date: 2026-03-22T10:30:00+01:00
researcher: Claude
git_commit: 4ff39f4a7dc789f6da1caad9e4555ef474b595b1
branch: fix/database-migrations
repository: open-webui
topic: "access_grant table missing after upstream merge - migration failure analysis"
tags: [research, codebase, alembic, migrations, access-grant, postgresql, upstream-merge]
status: complete
last_updated: 2026-03-22
last_updated_by: Claude
---

# Research: access_grant Migration Failure on New Tenants

**Date**: 2026-03-22T10:30:00+01:00
**Researcher**: Claude
**Git Commit**: 4ff39f4a7dc789f6da1caad9e4555ef474b595b1
**Branch**: fix/database-migrations
**Repository**: open-webui

## Research Question

Since the recent upstream merge (v0.8.9, commit `c26ae48d6`), new tenants fail at runtime with:
```
sqlalchemy.exc.ProgrammingError: (psycopg2.errors.UndefinedTable) relation "access_grant" does not exist
```
Why is the `access_grant` table not being created during migrations?

## Summary

The `access_grant` table is created by Alembic migration `f1e2d3c4b5a6`, which sits on one of two branches in the migration graph that were merged in `e5f6a7b8c9d0`. Three compounding issues cause this migration to silently fail:

1. **PostgreSQL transaction abort in `374d2f66af06`**: This migration (a prerequisite of `f1e2d3c4b5a6`) has a bare `try/except` around a SELECT query that can fail on PostgreSQL. PostgreSQL marks the transaction as aborted, making all subsequent SQL in the same transaction fail silently.

2. **Single-transaction migration**: `env.py` runs ALL migrations in one transaction (`context.begin_transaction()` with default `transaction_per_migration=False`). A failure in ANY migration rolls back ALL migrations, including the `alembic_version` stamps.

3. **Silent error swallowing**: `config.py:run_migrations()` catches all exceptions and only logs them. The app starts with a broken schema, serving requests against missing tables.

## Detailed Findings

### Migration Graph Structure

The upstream merge introduced a branched migration graph with two merge points:

```
Linear chain:
  7e5b5dc7342b (init) → ... → 018012973d35 → 3af16a1c9fb6 → 38d63c18f30f
  → a5c220713937 → 37f288994c47 → 2f1211949ecc → b10670c03dd5 → 90ef40d4714e
  → 3e0e00844bb0 → 6283dc0e4d8d → 81cc2ce44d79 → c440947495f3

Branch point at c440947495f3:
  UPSTREAM branch: 374d2f66af06 → 8452d01d26d7 → f1e2d3c4b5a6 (access_grant)
                   → a1b2c3d4e5f6 (skill) → b2c3d4e5f6a7 (scim)
  CUSTOM branch:   f8e1a9c2d3b4 (user_archive, merges 018012973d35 + c440947495f3)
                   → 2c5f92a9fd66 (knowledge type) → eaa33ce2752e (invite)
                   → d4e5f6a7b8c9 (soft delete)

Final merge:
  e5f6a7b8c9d0 ← (b2c3d4e5f6a7, d4e5f6a7b8c9)
```

The `access_grant` table (`f1e2d3c4b5a6`) is on the upstream branch. Both branches must complete before the merge migration can be applied.

### Issue 1: PostgreSQL Transaction Abort in `374d2f66af06`

**File**: `backend/open_webui/migrations/versions/374d2f66af06_add_prompt_history_table.py:39-52`

```python
try:
    existing_prompts = conn.execute(
        sa.select(
            old_prompt_table.c.command,
            old_prompt_table.c.user_id,
            old_prompt_table.c.title,
            old_prompt_table.c.content,
            old_prompt_table.c.timestamp,
            old_prompt_table.c.access_control,  # May not exist!
        )
    ).fetchall()
except Exception:
    existing_prompts = []  # Exception caught, but PG transaction is ABORTED
```

This migration reads from the old `prompt` table including the `access_control` column. The `access_control` column is added by `922e7a387820` (earlier in the chain), so it SHOULD exist at this point. However, if for any reason the column doesn't exist (e.g., `922e7a387820` was skipped or failed):

- **SQLite**: The `try/except` works correctly - the transaction continues.
- **PostgreSQL**: The failed query puts the connection in `InFailedSqlTransaction` state. ALL subsequent SQL operations within the same transaction fail with: `"current transaction is aborted, commands ignored until end of transaction block"`. The Python `except` catches the Python exception but does NOT reset PostgreSQL's transaction state.

This means the subsequent `op.create_table("prompt_new", ...)` on line 55 fails, which cascades to ALL remaining migrations in the same transaction, including `f1e2d3c4b5a6` (access_grant creation).

**Fix**: Use a SAVEPOINT (nested transaction) around the try/except:
```python
savepoint = conn.begin_nested()
try:
    existing_prompts = conn.execute(...).fetchall()
    savepoint.commit()
except Exception:
    savepoint.rollback()  # Resets PG transaction state
    existing_prompts = []
```

### Issue 2: Single Transaction for All Migrations

**File**: `backend/open_webui/migrations/env.py:106-110`

```python
with connectable.connect() as connection:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()
```

All 39+ migrations run in a SINGLE transaction. If migration N fails:
- Migrations 1 through N-1 are rolled back (despite succeeding)
- All `alembic_version` stamps are rolled back
- On next restart, ALL migrations run again from scratch
- If the same migration keeps failing, the app can never progress

**Fix**: Enable per-migration transactions:
```python
context.configure(
    connection=connection,
    target_metadata=target_metadata,
    transaction_per_migration=True,
)
```

This commits each migration independently. If migration N fails, migrations 1 through N-1 remain committed. On next restart, only migration N onwards needs to run.

### Issue 3: Silent Error Swallowing

**File**: `backend/open_webui/config.py:55-75`

```python
def run_migrations():
    log.info("Running migrations")
    try:
        # ... setup ...
        command.upgrade(alembic_cfg, "head")
    except Exception as e:
        log.exception(f"Error running migrations: {e}")
        # NO RAISE! App continues with broken schema
```

The deployed code at HEAD does NOT re-raise the exception. The app starts and serves requests against missing tables, producing the `UndefinedTable` errors at runtime.

**Fix**: Re-raise the exception so the app fails fast:
```python
    except Exception as e:
        log.exception(f"Error running migrations: {e}")
        raise  # Fail fast - don't serve with broken schema
```

A local change adding `raise` already exists in the working copy but is not yet committed.

### The `access_grant` Table Itself

**Migration**: `backend/open_webui/migrations/versions/f1e2d3c4b5a6_add_access_grant_table.py`

This migration:
1. Creates the `access_grant` table with columns: `id`, `resource_type`, `resource_id`, `principal_type`, `principal_id`, `permission`, `created_at`
2. Creates indexes: `idx_access_grant_resource`, `idx_access_grant_principal`
3. Backfills from JSON `access_control` columns on 7 tables (knowledge, prompt, tool, model, note, channel, file)
4. Drops the `access_control` column from those tables

The migration itself is well-guarded (`if "access_grant" not in existing_tables:`). The problem is that it never gets a chance to run because a PREDECESSOR migration (`374d2f66af06`) aborts the transaction.

### Impact: `access_grant` is Referenced Everywhere

The `access_grant` table is used by:
- **All resource routers**: knowledge, prompts, tools, models, notes, channels, files, skills (8 routers)
- **WebSocket handler**: channel/note permission checks
- **RAG retrieval**: knowledge base access filtering
- **Built-in tools**: note/knowledge permission checks
- **67 files total** across backend and frontend

A missing `access_grant` table makes the entire application non-functional for any authenticated operation.

### Deployment Context

**Helm chart**: `helm/open-webui-tenant/templates/open-webui/configmap.yaml`
- No `ENABLE_DB_MIGRATIONS` override → defaults to `True`
- No `DATABASE_SCHEMA` override → defaults to `None` (public schema)
- Each tenant gets isolated PostgreSQL via namespace-local service
- Migrations run on every pod startup

## Code References

- `backend/open_webui/config.py:55-75` - Migration runner with silent error handling
- `backend/open_webui/migrations/env.py:106-110` - Single-transaction migration execution
- `backend/open_webui/migrations/versions/374d2f66af06_add_prompt_history_table.py:39-52` - Bare try/except causing PG transaction abort
- `backend/open_webui/migrations/versions/f1e2d3c4b5a6_add_access_grant_table.py:29-228` - access_grant table creation
- `backend/open_webui/migrations/versions/e5f6a7b8c9d0_merge_upstream_v089.py` - Merge migration joining both branches
- `backend/open_webui/models/access_grants.py:21-900` - ORM model and operations class
- `backend/open_webui/migrations/versions/922e7a387820_add_group_table.py:56-60` - Adds `access_control` to prompt table

## Proposed Fixes

### Fix 1: Savepoint in `374d2f66af06` (Critical)

Wrap the try/except SELECT in a savepoint to prevent PostgreSQL transaction abort. This is the most impactful single fix.

### Fix 2: Re-raise migration exceptions in `config.py` (Critical)

Add `raise` after the log statement. Already done as local change, needs to be committed. Prevents app from running with broken schema.

### Fix 3: Per-migration transactions in `env.py` (Recommended)

Set `transaction_per_migration=True` in Alembic's context.configure(). This makes migrations resilient: successful migrations stay committed even if a later one fails.

### Fix 4: Audit other migrations for bare try/except (Preventive)

Search all migration files for `except Exception:` or `except:` patterns that could cause the same issue on PostgreSQL. Any such pattern in a migration that runs within the single-transaction context is a potential time bomb.

## Open Questions

1. **Exact trigger condition**: What specific database state causes the `374d2f66af06` SELECT to fail? The `access_control` column should exist (added by `922e7a387820`). Is there a scenario where a Peewee migration creates the `prompt` table without this column, and then `922e7a387820` fails to add it?

2. **Existing tenant state**: For tenants already deployed at `d4e5f6a7b8c9` (pre-merge), what is their current `alembic_version` after a failed upgrade attempt? Need to check if manual intervention is needed.

3. **Other bare try/except patterns**: Are there other migrations with the same PostgreSQL transaction abort risk?
