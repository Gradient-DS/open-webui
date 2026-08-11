"""[Gradient] Regression tests for temporary-chat id parsing (GRA-221).

Temporary chats used to be identified by ``local:<socket_id>`` alone, so every
temporary chat in one browser session shared an agent thread and inherited the
previous conversation's history and documents. They are now
``local:<socket_id>:<chat_uuid>``; the socket id must still be recoverable
because task list/stop authorise temporary chats through the session pool.
"""

from open_webui.utils.chat_ids import socket_id_from_chat_id


def test_extracts_socket_id_from_per_chat_temporary_id():
    assert socket_id_from_chat_id('local:p555nxnIcxn8eLaHAAD1:5d8f9f0e-1c2b-4a3d-9e6f-7a8b9c0d1e2f') == (
        'p555nxnIcxn8eLaHAAD1'
    )


def test_still_parses_legacy_two_part_temporary_id():
    """Clients on the previous build keep working during a rollout."""
    assert socket_id_from_chat_id('local:p555nxnIcxn8eLaHAAD1') == 'p555nxnIcxn8eLaHAAD1'


def test_channel_ids_keep_their_previous_parse():
    """Unchanged behaviour — channel ids never carried a socket id.

    ``channel:`` is 8 characters and the caller strips ``len('local:')`` == 6,
    so the result has always been a mangled remainder that finds no session and
    fails the ownership check. Pinned here so this refactor provably does not
    change it; fixing it is out of scope.
    """
    assert socket_id_from_chat_id('channel:abc123') == 'l:abc123'


def test_missing_socket_id_yields_empty_owner_lookup():
    """A not-yet-connected socket must not leak the chat uuid as a session id."""
    assert socket_id_from_chat_id('local::5d8f9f0e-1c2b-4a3d-9e6f-7a8b9c0d1e2f') == ''
