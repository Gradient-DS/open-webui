"""[Gradient] GRA-221 socket ownership for isolated temporary-chat histories."""

import pytest

from open_webui.utils.chat_id import (
    get_temporary_chat_session_id,
    is_saved_chat_id,
    is_temporary_chat_id,
)


@pytest.mark.parametrize(
    ('chat_id', 'session_id'),
    [
        ('local:p555nxnIcxn8eLaHAAD1:5d8f9f0e-1c2b-4a3d-9e6f-7a8b9c0d1e2f', 'p555nxnIcxn8eLaHAAD1'),
        ('local:p555nxnIcxn8eLaHAAD1', 'p555nxnIcxn8eLaHAAD1'),
        ('temporary:p555nxnIcxn8eLaHAAD1', 'p555nxnIcxn8eLaHAAD1'),
        ('temporary:p555nxnIcxn8eLaHAAD1:5d8f9f0e-1c2b-4a3d-9e6f-7a8b9c0d1e2f', 'p555nxnIcxn8eLaHAAD1'),
        ('local::5d8f9f0e-1c2b-4a3d-9e6f-7a8b9c0d1e2f', ''),
        ('temporary::5d8f9f0e-1c2b-4a3d-9e6f-7a8b9c0d1e2f', ''),
        ('channel:abc123', None),
        ('saved-chat-uuid', None),
        ('', None),
    ],
)
def test_temporary_chat_session_owner(chat_id, session_id):
    assert get_temporary_chat_session_id(chat_id) == session_id


@pytest.mark.parametrize(
    ('chat_id', 'saved', 'temporary'),
    [
        ('local:session:chat-uuid', False, True),
        ('temporary:session', False, True),
        ('temporary:session:chat-uuid', False, True),
        ('channel:abc123', False, False),
        ('saved-chat-uuid', True, False),
        ('', False, False),
        (None, False, False),
    ],
)
def test_chat_id_classification(chat_id, saved, temporary):
    assert is_saved_chat_id(chat_id) is saved
    assert is_temporary_chat_id(chat_id) is temporary
