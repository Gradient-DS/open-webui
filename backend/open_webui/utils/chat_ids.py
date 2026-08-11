"""[Gradient] Parsing helpers for non-persisted chat ids.

Chats that are never written to the ``chat`` table carry a prefixed id instead
of a row: ``local:`` for temporary chats and ``channel:`` for channel threads.
Endpoints that authorise by chat ownership therefore cannot load a row and fall
back to resolving the Socket.IO session instead — see
``list_tasks_by_chat_id_endpoint`` / ``stop_tasks_by_chat_id_endpoint``.

Kept as a pure function in its own module so it is testable without importing
``open_webui.main`` (which pulls in the app config at import time).
"""

LOCAL_CHAT_ID_PREFIX = 'local:'


def socket_id_from_chat_id(chat_id: str) -> str:
    """Recover the Socket.IO session id embedded in a non-persisted chat id.

    Temporary chats are ``local:<socket_id>:<chat_uuid>``. The trailing uuid is
    what gives each temporary chat its own agent thread — without it every
    temporary chat opened in one browser session reused the same socket id and
    they all shared a single agent ledger, inheriting each other's history
    (GRA-221). Only the first segment is the socket id.

    Two-part ids (``local:<socket_id>``) written by older clients, and
    ``channel:`` ids, keep their previous parse exactly.
    """
    if not chat_id.startswith(LOCAL_CHAT_ID_PREFIX):
        return chat_id[len(LOCAL_CHAT_ID_PREFIX) :]

    return chat_id[len(LOCAL_CHAT_ID_PREFIX) :].split(':', 1)[0]
