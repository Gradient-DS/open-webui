"""[Gradient] Agent threads: download a v2 thread's trace file from soev-api as the caller.

soev-api answers only the caller's own threads; any other thread is a 404 there. The trace holds every stored
event, the assistant's instructions included, so the download is for admins, on tenants that set
`ENABLE_AGENT_TRACE_DOWNLOAD`.
"""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import StreamingResponse
from open_webui import config
from open_webui.soev import identity
from open_webui.soev.availability import SoevRoute, require_soev
from open_webui.utils.auth import get_admin_user

router = APIRouter(route_class=SoevRoute, dependencies=[Depends(require_soev)])

# soev-api thread ids are URL-safe tokens; the pattern also keeps dot segments and header text out.
ThreadId = Annotated[str, Path(pattern=r'^[A-Za-z0-9_-]{1,256}$')]
