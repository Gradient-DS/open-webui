"""Include originals in owner exports using the owner's current platform authority."""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

from open_webui import config
from open_webui.models.files import Files
from open_webui.soev import acting
from open_webui.soev.client import SoevApiError
from open_webui.soev.originals import open_original, prefer_local
from open_webui.storage.provider import Storage


async def include(zf, file_ids, *, user_id: str) -> tuple[int, int]:
    included, unavailable = 0, []
    for file_id in file_ids:
        file = await Files.get_file_by_id(file_id)
        if file is None or file.user_id != user_id:
            unavailable.append({'id': file_id, 'reason': 'unavailable'})
            continue
        name = Path((file.meta or {}).get('name') or file.filename or file_id).name
        target = f'files/uploads/{quote(file_id, safe="")}_{name}'
        try:
            local = not config.SOEV_API_URL or await prefer_local(file)
            if not local:
                ref = f'owui:user:{user_id}'
                acting.require_owner(user_id)
                if not await acting.user_token(ref):
                    raise SoevApiError(403, 'forbidden', 'Original unavailable')
                try:
                    # Stage one original at a time so a failed stream never leaves a partial ZIP member.
                    with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as spool:
                        async with open_original(file, as_user=ref) as response:
                            async for chunk in response.aiter_bytes():
                                await asyncio.to_thread(spool.write, chunk)
                        spool.seek(0)
                        with zf.open(target, 'w') as member:
                            await asyncio.to_thread(shutil.copyfileobj, spool, member)
                    included += 1
                    continue
                except SoevApiError as error:
                    if error.status != 404 or (file.meta or {}).get('source'):
                        raise
            if not file.path:
                raise FileNotFoundError
            local_path = Storage.get_file(file.path)
            if not local_path or not Path(local_path).is_file():
                raise FileNotFoundError
            await asyncio.to_thread(zf.write, local_path, target)
            included += 1
        except (SoevApiError, OSError, RuntimeError):
            unavailable.append({'id': file_id, 'reason': 'unavailable'})
    zf.writestr('files/unavailable.json', json.dumps(unavailable, indent=2))
    return included, len(unavailable)
