"""Explicit, report-first retirement of verified local document copies."""

import argparse
import asyncio
import json
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


def decide(file, *, held: bool, local_bytes: bool, protected: bool, linked: bool, uncertain: bool = False) -> Decision:
    meta = file.meta or {}
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
    return Decision('unknown_without_bytes', remove_row=not linked and not historical)


def protected_file(file, prefix: bytes, *, skill: bool, export: bool) -> bool:
    meta = file.meta or {}
    detected = sniff_content_type(prefix) if prefix else ''
    content_type = meta.get('content_type') or ''
    return bool(
        skill
        or export
        or meta.get('local_blob')
        or meta.get('local_kind')
        or meta.get('source')
        or detected.startswith(('image/', 'video/'))
        or content_type.startswith(('image/', 'video/'))
        or (content_type.startswith('audio/') and file.filename.startswith('generated-'))
        or (
            not prefix
            and Path(file.filename).suffix.lower()
            in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.mp4', '.mov', '.webm'}
        )
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
    query = sa.select(File).order_by(File.id)
    if owner_id is not None:
        query = query.where(File.user_id == owner_id)
    if apply:
        query = query.with_for_update()
    rows = list((await session.scalars(query)).all())
    # Do not unlink an object also referenced by a retained File.
    paths = [row.path for row in (await session.scalars(sa.select(File))).all() if row.path]
    for row in rows:
        file = FileModel.model_validate(row)
        linked, skill = False, False
        for table_name in ('chat_file', 'knowledge_file', 'skill_file'):
            if table_name in tables:
                table = sa.table(table_name, sa.column('file_id', sa.Text))
                exists = await session.scalar(sa.select(table.c.file_id).where(table.c.file_id == file.id).limit(1))
                linked = linked or exists is not None
                skill = skill or (table_name == 'skill_file' and exists is not None)
        local = Path(file.path) if file.path and '://' not in file.path else None
        export = bool(local and local.resolve().is_relative_to(export_dir.resolve()))
        uncertain = bool(file.path and (local is None or not local.resolve().is_relative_to(upload_dir.resolve())))
        prefix, present = b'', False
        if local and not uncertain:
            try:
                with local.open('rb') as source:
                    prefix = source.read(64 * 1024)
                present = True
            except FileNotFoundError:
                pass
            except OSError:
                uncertain = True
        protected = protected_file(file, prefix, skill=skill, export=export)
        held = False
        try:
            collection = await originals.lookup(file, client, as_user=as_user)
            if collection:
                # A published original, not just a declaration or a locally stored job id.
                async with client.stream_response(originals.path_for(collection, file.id), as_user=as_user):
                    held = True
        except SoevApiError:
            counts['platform_error'] += 1
            continue
        decision = decide(file, held=held, local_bytes=present, protected=protected, linked=linked, uncertain=uncertain)
        counts[decision.category] += 1
        if not apply:
            continue
        if decision.remove_bytes and local and paths.count(file.path) == 1:
            local.unlink(missing_ok=True)
            row.path = None
            counts['bytes_removed'] += 1
        # Only an authenticated owner may act on scoped absence; product-only reporting cannot prove it.
        if decision.remove_row and as_user == f'owui:user:{file.user_id}':
            await session.delete(row)
            counts['rows_removed'] += 1
    if apply:
        await session.commit()
    else:
        await session.rollback()
    return counts


async def run(args) -> dict[str, int]:
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
