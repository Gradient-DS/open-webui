"""Explicit, report-first retirement of verified local document copies."""

import argparse
import hashlib
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa

from open_webui.utils.upload_guard import sniff_content_type


@dataclass(frozen=True)
class Decision:
    category: str
    remove_bytes: bool = False
    remove_row: bool = False


def decide(
    file,
    *,
    held: bool,
    local_bytes: bool,
    protected: bool,
    linked: bool,
    uncertain: bool = False,
    differs: bool = False,
) -> Decision:
    meta = file.meta or {}
    if differs:
        return Decision('platform_differs')
    if protected:
        return Decision('protected')
    if meta.get('soev_job') or meta.get('status') in ('pending', 'processing'):
        return Decision('in_flight')
    if uncertain:
        return Decision('unverified_storage')
    if held:
        return Decision('platform_held', remove_bytes=local_bytes)
    if local_bytes:
        return Decision('unknown_with_bytes')
    # A scoped absence cannot disprove a historical successful ingest whose markers were lost.
    historical = meta.get('status') == 'completed' or any(
        meta.get(k)
        for k in (
            'source',
            'soev_read_collection',
            'soev_collection_key',
            'soev_last_job',
            'collection_name',
        )
    )
    return Decision(
        'unknown_without_bytes', remove_row=not linked and not historical and not (file.data or {}).get('content')
    )


def protected_file(file, prefix: bytes, *, skill: bool, export: bool) -> bool:
    meta = file.meta or {}
    detected = sniff_content_type(prefix) if prefix else ''
    content_type = meta.get('content_type') or ''
    return bool(
        skill
        or export
        or meta.get('local_blob')
        or meta.get('local_kind') in ('skill', 'generated', 'export')
        or meta.get('source')
        or detected.startswith(('image/', 'video/'))
        or (content_type.startswith('audio/') and file.filename.startswith('generated-'))
    )


async def retire(
    session,
    client,
    *,
    upload_dir: Path,
    export_dir: Path,
    apply: bool = False,
    owner_id: str | None = None,
    as_user: str | None = None,
) -> dict[str, int]:
    from open_webui.models.files import File, FileModel
    from open_webui.soev import originals
    from open_webui.soev.client import SoevApiError

    counts = {
        key: 0
        for key in (
            'platform_held',
            'platform_differs',
            'unknown_with_bytes',
            'unknown_without_bytes',
            'protected',
            'in_flight',
            'unverified_storage',
            'platform_error',
            'bytes_removed',
            'rows_removed',
        )
    }
    connection = await session.connection()
    tables = await connection.run_sync(lambda bind: set(sa.inspect(bind).get_table_names()))
    query = sa.select(File.id).order_by(File.id)
    if owner_id is not None:
        query = query.where(File.user_id == owner_id)
    ids = list(await session.scalars(query))
    paths = list(await session.scalars(sa.select(File.path).where(File.path.is_not(None))))
    await session.rollback()
    for file_id in ids:
        unlink = None
        try:
            query = sa.select(File).where(File.id == file_id).execution_options(populate_existing=True)
            if apply:
                query = query.with_for_update()
            row = await session.scalar(query)
            if row is None:
                await session.rollback()
                continue
            file = FileModel.model_validate(row)
            linked, skill = False, False
            for table_name in ('chat_file', 'knowledge_file', 'skill_file'):
                if table_name in tables:
                    table = sa.table(table_name, sa.column('file_id', sa.Text))
                    exists = await session.scalar(sa.select(table.c.file_id).where(table.c.file_id == file.id).limit(1))
                    linked = linked or exists is not None
                    skill = skill or (table_name == 'skill_file' and exists is not None)
            from open_webui.soev.file_collections import collections_for

            linked = linked or len(await collections_for(file, session=session)) > 1
            stored_path = file.path or (file.meta or {}).get('soev_retire_path')
            local = Path(stored_path) if stored_path and '://' not in stored_path else None
            export = bool(local and local.resolve().is_relative_to(export_dir.resolve()))
            uncertain = bool(
                stored_path and (local is None or not local.resolve().is_relative_to(upload_dir.resolve()))
            )
            prefix, present, local_info = b'', False, None
            if local and not uncertain:
                try:
                    digest, size = hashlib.sha256(), 0
                    with local.open('rb') as source:
                        while chunk := source.read(64 * 1024):
                            if not prefix:
                                prefix = chunk
                            digest.update(chunk)
                            size += len(chunk)
                    local_info, present = (size, digest.hexdigest()), True
                except FileNotFoundError:
                    pass
                except OSError:
                    uncertain = True
            protected = protected_file(file, prefix, skill=skill, export=export)
            held, differs = False, False
            try:
                keys, seen = await originals.candidates(file, client, as_user=as_user, session=session)
                differs = seen and (not keys or (present and local_info != originals.fingerprint(file)))
                if keys and not differs:
                    async with client.stream_response(originals.path_for(keys[0], file.id), as_user=as_user):
                        held = True
            except SoevApiError:
                counts['platform_error'] += 1
                await session.rollback()
                continue
            decision = decide(
                file,
                held=held,
                local_bytes=present,
                protected=protected,
                linked=linked,
                uncertain=uncertain,
                differs=differs,
            )
            counts[decision.category] += 1
            if not apply:
                await session.rollback()
                continue
            if decision.remove_bytes and local and paths.count(stored_path) <= 1:
                row.path = None
                row.meta = {**(row.meta or {}), 'soev_retire_path': stored_path}
                unlink = local
            if decision.remove_row and as_user == f'owui:user:{file.user_id}':
                await session.delete(row)
                counts['rows_removed'] += 1
            # Commit this row before touching bytes, independently of all later files.
            await session.commit()
            if unlink is not None:
                unlink.unlink(missing_ok=True)
                counts['bytes_removed'] += 1
        except BaseException:
            await session.rollback()
            raise
    return counts


async def run(args) -> dict[str, int]:
    # Config import normally runs Alembic. An inventory/retirement command never does.
    os.environ['ENABLE_DB_MIGRATIONS'] = 'false'
    from open_webui import env

    env.ENABLE_DB_MIGRATIONS = False
    from open_webui import config
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.users import Users
    from open_webui.soev import acting, identity
    from open_webui.soev.client import build_service_client, close_client
    from open_webui.soev.persons import _person_id

    if not config.SOEV_API_URL:
        raise ValueError('Platform configuration is required')
    client = build_service_client()
    requester = resolver = None
    as_user = None
    try:
        if args.owner_id:
            if not args.login_token_stdin:
                raise ValueError('An owner login token on stdin is required')
            token = sys.stdin.readline().strip()
            client = identity.build_client()
            owner = await Users.get_user_by_id(args.owner_id)
            if owner is None or _person_id(await client.me(token)) != owner.person_id:
                raise ValueError('Owner login does not match')
            as_user = identity.external_ref(owner)

            async def resolve(**kwargs):
                return token

            requester = acting._requester_ref.set(as_user)
            resolver = acting._token_resolver.set(resolve)
        async with get_async_db_context() as session:
            return await retire(
                session,
                client,
                upload_dir=config.UPLOAD_DIR,
                export_dir=config.CACHE_DIR / 'exports',
                apply=args.apply,
                owner_id=args.owner_id,
                as_user=as_user,
            )
    finally:
        if resolver is not None:
            acting._token_resolver.reset(resolver)
            acting._requester_ref.reset(requester)
        await close_client()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Retire verified copies and eligible unlinked empty rows')
    parser.add_argument('--owner-id', help='Restrict to one owner; verify their login before deleting empty rows')
    parser.add_argument('--login-token-stdin', action='store_true', help='Read the owner login token from stdin')
    args = parser.parse_args()
    try:
        counts = asyncio.run(run(args))
    except Exception:
        print(json.dumps({'error': 'retirement_failed'}))
        return 1
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
