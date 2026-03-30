"""
TOTP two-factor authentication utilities.

Provides AES-GCM encryption for TOTP secrets, TOTP generation/verification,
QR code generation, and recovery code operations.
"""

import base64
import hashlib
import io
import os
import re
import secrets
import string
from datetime import datetime

import pyotp
import qrcode

from open_webui.env import WEBUI_SECRET_KEY


# --- AES-GCM encryption for TOTP secrets ---


def _get_encryption_key() -> bytes:
    """Derive AES-256 key from WEBUI_SECRET_KEY via SHA-256."""
    return hashlib.sha256(WEBUI_SECRET_KEY.encode('utf-8')).digest()


def encrypt_secret(plaintext: str) -> str:
    """Encrypt TOTP base32 secret with AES-GCM. Returns base64(nonce + ciphertext + tag)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _get_encryption_key()
    nonce = os.urandom(12)  # 96-bit nonce for AES-GCM
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode('utf-8'), None)
    # ciphertext includes the 16-byte tag appended by AESGCM
    return base64.b64encode(nonce + ciphertext).decode('utf-8')


def decrypt_secret(encrypted: str) -> str:
    """Decrypt AES-GCM encrypted TOTP secret."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _get_encryption_key()
    raw = base64.b64decode(encrypted)
    nonce = raw[:12]
    ciphertext = raw[12:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode('utf-8')


# --- TOTP operations ---


def generate_totp_secret() -> str:
    """Generate a new base32 TOTP secret via pyotp."""
    return pyotp.random_base32()


def generate_provisioning_uri(secret: str, email: str, issuer: str = 'soev.ai') -> str:
    """Generate otpauth:// URI for QR code scanning."""
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name=issuer)


def generate_qr_code_base64(uri: str) -> str:
    """Generate QR code as base64 PNG data URI."""
    img = qrcode.make(uri, box_size=6, border=2)
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
    return f'data:image/png;base64,{b64}'


def verify_totp(secret: str, code: str, last_used_at: int | None) -> tuple[bool, int | None]:
    """
    Verify TOTP code with valid_window=1 and replay protection.
    Returns (is_valid, new_timecode_if_valid).
    """
    totp = pyotp.TOTP(secret)
    current_timecode = totp.timecode(datetime.now())

    # Replay protection: reject if same timecode was already used
    if last_used_at is not None and current_timecode <= last_used_at:
        # Still check if the code is valid for a future window
        if not totp.verify(code, valid_window=1):
            return False, None
        # Code is valid but timecode already used — replay
        if current_timecode == last_used_at:
            return False, None

    if totp.verify(code, valid_window=1):
        return True, current_timecode

    return False, None


# --- Recovery code operations ---


def generate_recovery_codes(count: int = 10) -> list[str]:
    """Generate formatted recovery codes (XXXXX-XXXXX)."""
    alphabet = string.ascii_uppercase + string.digits
    codes = []
    for _ in range(count):
        part1 = ''.join(secrets.choice(alphabet) for _ in range(5))
        part2 = ''.join(secrets.choice(alphabet) for _ in range(5))
        codes.append(f'{part1}-{part2}')
    return codes


def is_recovery_code_format(code: str) -> bool:
    """Check if a code matches the recovery code format XXXXX-XXXXX."""
    return bool(re.match(r'^[A-Z0-9]{5}-[A-Z0-9]{5}$', code))


def is_totp_code_format(code: str) -> bool:
    """Check if a code matches the 6-digit TOTP format."""
    return bool(re.match(r'^\d{6}$', code))
