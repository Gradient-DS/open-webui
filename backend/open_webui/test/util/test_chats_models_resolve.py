"""Chat pydantic models must resolve their annotations at validation time.

models/chats.py uses ``from __future__ import annotations``, so pydantic
resolves field annotations lazily: a missing ``typing.Optional`` import does
NOT fail at import time — pydantic installs a mock validator and the first
live validation raises PydanticUserError ("class not fully defined … call
.rebuild()"), i.e. the first POST /api/v1/chats/new after boot 500s while
every import-level check stays green (v0.10.2 merge took upstream's typing
sweep import block but kept our Optional-annotated fields). Constructing the
models here forces resolution so the regression is caught at test time.
"""

from open_webui.models.chats import ChatForm, ChatModel


def test_chat_form_validates():
    form = ChatForm(chat={})
    assert form.folder_id is None
    assert form.meta is None


def test_chat_model_validates():
    chat = ChatModel(id='c1', user_id='u1', title='t', chat={}, created_at=1, updated_at=1)
    assert chat.deleted_at is None
    assert chat.share_id is None
