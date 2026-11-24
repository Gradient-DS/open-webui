"""Keep each destination's copy outcome separate from the original File status."""

from sqlalchemy import select

from open_webui.models import files


def outcome(file, collection: str) -> dict:
    return ((file.meta or {}).get('soev_copies') or {}).get(collection) or {}


def metadata(meta: dict | None, collection: str) -> dict | None:
    state = ((meta or {}).get('soev_copies') or {}).get(collection)
    return {**(meta or {}), **state} if state else meta


async def record(file, collection: str, status: str, *, error: str | None = None) -> None:
    async with files.get_async_db_context() as session:
        row = await session.scalar(select(files.File).where(files.File.id == file.id).with_for_update())
        if row is None:
            raise RuntimeError('File disappeared while recording copy outcome')
        meta = row.meta or {}
        copies = {**meta.get('soev_copies', {}), collection: {'status': status, 'error': error}}
        row.meta = {**meta, 'soev_copies': copies}
        await session.commit()
