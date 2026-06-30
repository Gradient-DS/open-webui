"""Unit tests for password-reset token helpers (pure functions)."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

from open_webui.utils.password_reset import (
    generate_reset_token,
    hash_reset_token,
    is_reset_token_usable,
)


def test_generate_reset_token_returns_raw_and_matching_hash():
    raw, token_hash = generate_reset_token()
    assert isinstance(raw, str) and len(raw) >= 32
    assert token_hash == hashlib.sha256(raw.encode()).hexdigest()
    # Two calls produce different tokens.
    raw2, _ = generate_reset_token()
    assert raw != raw2


def test_hash_reset_token_is_deterministic():
    assert hash_reset_token('abc') == hashlib.sha256(b'abc').hexdigest()


def test_is_reset_token_usable():
    now = 1000
    assert is_reset_token_usable(None, now) is False
    assert is_reset_token_usable(SimpleNamespace(used_at=None, expires_at=now + 1), now) is True
    # Expired.
    assert is_reset_token_usable(SimpleNamespace(used_at=None, expires_at=now - 1), now) is False
    # Already used.
    assert is_reset_token_usable(SimpleNamespace(used_at=now - 5, expires_at=now + 100), now) is False
