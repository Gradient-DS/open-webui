import hashlib
import secrets


def hash_reset_token(raw_token: str) -> str:
    """SHA-256 hex digest of a reset token. The token is high-entropy random,
    so a fast unsalted hash is appropriate (unlike user passwords)."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


def generate_reset_token() -> tuple[str, str]:
    """Return (raw_token, token_hash). The raw token goes in the email URL;
    only the hash is persisted."""
    raw_token = secrets.token_urlsafe(32)
    return raw_token, hash_reset_token(raw_token)


def is_reset_token_usable(record, now: int) -> bool:
    """A token is usable iff it exists, is unused, and is not expired."""
    return record is not None and record.used_at is None and record.expires_at > now
