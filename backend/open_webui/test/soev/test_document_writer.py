"""Agent document writer configuration and stored-message compatibility."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui.config import DEFAULT_USER_PERMISSIONS
from open_webui.routers import configs
from open_webui.routers.users import FeaturesPermissions
from open_webui.utils.middleware import serialize_output


def test_stored_document_output_still_serializes() -> None:
    """Legacy document output items still render when an existing message is serialized."""
    output = [{'type': 'open_webui:document', 'title': 'A & B', 'markdown': '# Body', 'status': 'completed'}]
    assert serialize_output(output) == (
        '<details type="document" done="true" title="A &amp; B">\n<summary>Document</summary>\n# Body\n</details>'
    )


def test_default_user_permissions_cover_every_feature_permission() -> None:
    """Every feature a user can be granted has an env-driven default, document_writer included."""
    assert set(DEFAULT_USER_PERMISSIONS['features']) == set(FeaturesPermissions.model_fields)
