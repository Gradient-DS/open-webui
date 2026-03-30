"""
TOTP Two-Factor Authentication router.

Mounted at /api/v1/auths/2fa in main.py.
"""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette import status

from open_webui.internal.db import get_session
from open_webui.models.auths import Auths
from open_webui.models.recovery_codes import RecoveryCodes
from open_webui.models.users import Users
from open_webui.utils.auth import (
    create_token,
    decode_token,
    get_current_user,
    verify_password,
)
from open_webui.utils.redis import get_redis_client
from open_webui.utils.rate_limit import RateLimiter
from open_webui.utils.totp import (
    decrypt_secret,
    encrypt_secret,
    generate_provisioning_uri,
    generate_qr_code_base64,
    generate_totp_secret,
    is_recovery_code_format,
    is_totp_code_format,
    verify_totp,
)
from open_webui.routers.auths import create_session_response

log = logging.getLogger(__name__)
router = APIRouter()

# Rate limiter: 5 attempts per 15 minutes per user
totp_verify_rate_limiter = RateLimiter(redis_client=get_redis_client(), limit=5, window=60 * 15)


####################
# Forms
####################


class TotpEnableForm(BaseModel):
    password: str
    secret: str  # base32 secret from /setup
    code: str  # 6-digit TOTP code to verify


class TotpDisableForm(BaseModel):
    password: str


class TotpVerifyForm(BaseModel):
    code: str  # 6-digit TOTP code or XXXXX-XXXXX recovery code


class RegenerateRecoveryCodesForm(BaseModel):
    password: str


####################
# Endpoints
####################


@router.get('/status')
async def get_2fa_status(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Get 2FA status for the current user."""
    if not request.app.state.config.ENABLE_2FA:
        raise HTTPException(status_code=404, detail='2FA is not enabled')

    auth = Auths.get_auth_by_user_id(user.id, db=db)
    if not auth:
        raise HTTPException(status_code=404, detail='Auth record not found')

    return {
        'totp_enabled': auth.totp_enabled or False,
        'recovery_codes_remaining': RecoveryCodes.count_unused(user.id, db=db) if auth.totp_enabled else 0,
        'is_oauth_user': bool(user.oauth),
    }


@router.post('/totp/setup')
async def setup_totp(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Generate a new TOTP secret and QR code. Does NOT persist until /totp/enable."""
    if not request.app.state.config.ENABLE_2FA:
        raise HTTPException(status_code=404, detail='2FA is not enabled')

    auth = Auths.get_auth_by_user_id(user.id, db=db)
    if auth and auth.totp_enabled:
        raise HTTPException(status_code=400, detail='TOTP is already enabled')

    secret = generate_totp_secret()
    uri = generate_provisioning_uri(secret, user.email)
    qr_code = generate_qr_code_base64(uri)

    return {
        'secret': secret,
        'provisioning_uri': uri,
        'qr_code_base64': qr_code,
    }


@router.post('/totp/enable')
async def enable_totp(
    request: Request,
    form_data: TotpEnableForm,
    user=Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Verify a TOTP code against the provided secret, then enable 2FA and return recovery codes."""
    if not request.app.state.config.ENABLE_2FA:
        raise HTTPException(status_code=404, detail='2FA is not enabled')

    auth = Auths.get_auth_by_user_id(user.id, db=db)
    if not auth:
        raise HTTPException(status_code=404, detail='Auth record not found')

    if auth.totp_enabled:
        raise HTTPException(status_code=400, detail='TOTP is already enabled')

    # Re-verify password
    if not verify_password(form_data.password, auth.password):
        raise HTTPException(status_code=403, detail='Invalid password')

    # Verify the TOTP code against the provided secret
    is_valid, timecode = verify_totp(form_data.secret, form_data.code, None)
    if not is_valid:
        raise HTTPException(status_code=400, detail='Invalid TOTP code')

    # Encrypt and save the secret
    encrypted_secret = encrypt_secret(form_data.secret)
    Auths.update_totp(user.id, encrypted_secret, True, db=db)
    if timecode is not None:
        Auths.update_totp_last_used(user.id, timecode, db=db)

    # Generate recovery codes
    recovery_codes = RecoveryCodes.generate_codes(user.id, db=db)

    return {
        'totp_enabled': True,
        'recovery_codes': recovery_codes,
    }


@router.post('/totp/disable')
async def disable_totp(
    request: Request,
    form_data: TotpDisableForm,
    user=Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Disable TOTP 2FA. Requires password re-verification."""
    if not request.app.state.config.ENABLE_2FA:
        raise HTTPException(status_code=404, detail='2FA is not enabled')

    auth = Auths.get_auth_by_user_id(user.id, db=db)
    if not auth:
        raise HTTPException(status_code=404, detail='Auth record not found')

    if not auth.totp_enabled:
        raise HTTPException(status_code=400, detail='TOTP is not enabled')

    # Re-verify password
    if not verify_password(form_data.password, auth.password):
        raise HTTPException(status_code=403, detail='Invalid password')

    Auths.update_totp(user.id, None, False, db=db)
    RecoveryCodes.delete_all(user.id, db=db)

    return {'totp_enabled': False}


@router.post('/verify')
async def verify_2fa(
    request: Request,
    response: Response,
    form_data: TotpVerifyForm,
    db: Session = Depends(get_session),
):
    """
    Verify a TOTP code or recovery code during login.
    Accepts a partial JWT token (purpose=2fa_pending) via Authorization header.
    Returns a full session on success.
    """
    # Extract partial token from Authorization header
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Missing authorization')

    token = auth_header.split(' ', 1)[1]

    try:
        data = decode_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail='Invalid or expired token')

    if not data or data.get('purpose') != '2fa_pending':
        raise HTTPException(status_code=401, detail='Invalid token type')

    user_id = data.get('id')
    if not user_id:
        raise HTTPException(status_code=401, detail='Invalid token')

    # Rate limit by user_id
    if totp_verify_rate_limiter.is_limited(user_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail='Too many verification attempts. Please try again later.',
        )

    user = Users.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail='User not found')

    auth = Auths.get_auth_by_user_id(user_id, db=db)
    if not auth or not auth.totp_enabled:
        raise HTTPException(status_code=400, detail='2FA is not enabled for this user')

    code = form_data.code.strip()

    if is_totp_code_format(code):
        # TOTP verification
        secret = decrypt_secret(auth.totp_secret)
        is_valid, timecode = verify_totp(secret, code, auth.totp_last_used_at)
        if not is_valid:
            raise HTTPException(status_code=400, detail='Invalid TOTP code')
        if timecode is not None:
            Auths.update_totp_last_used(user_id, timecode, db=db)

    elif is_recovery_code_format(code):
        # Recovery code verification
        if not RecoveryCodes.verify_code(user_id, code, db=db):
            raise HTTPException(status_code=400, detail='Invalid recovery code')

    else:
        raise HTTPException(status_code=400, detail='Invalid code format')

    # Success — issue full session
    return create_session_response(request, user, db, response, set_cookie=True)


@router.post('/recovery/regenerate')
async def regenerate_recovery_codes(
    request: Request,
    form_data: RegenerateRecoveryCodesForm,
    user=Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Regenerate recovery codes. Requires password re-verification."""
    if not request.app.state.config.ENABLE_2FA:
        raise HTTPException(status_code=404, detail='2FA is not enabled')

    auth = Auths.get_auth_by_user_id(user.id, db=db)
    if not auth:
        raise HTTPException(status_code=404, detail='Auth record not found')

    if not auth.totp_enabled:
        raise HTTPException(status_code=400, detail='TOTP is not enabled')

    # Re-verify password
    if not verify_password(form_data.password, auth.password):
        raise HTTPException(status_code=403, detail='Invalid password')

    recovery_codes = RecoveryCodes.generate_codes(user.id, db=db)

    return {'recovery_codes': recovery_codes}
