import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from typing import Optional

from open_webui.models.auths import Auths, SigninResponse
from open_webui.models.invites import AcceptInviteForm, InviteForm, InviteModel, Invites
from open_webui.models.users import Users

from open_webui.utils.auth import (
    create_token,
    get_admin_user,
    get_password_hash,
    validate_password,
)
from open_webui.utils.misc import validate_email_format
from open_webui.utils.groups import apply_default_group_assignment
from open_webui.env import CLIENT_NAME
from open_webui.config import DEFAULT_LOCALE

log = logging.getLogger(__name__)

router = APIRouter()


############################
# InviteResponse
############################


class InviteResponse(BaseModel):
    id: str
    email: str
    name: str
    role: str
    token: str
    invite_url: str
    email_sent: bool
    expires_at: int
    created_at: int


class InviteValidation(BaseModel):
    email: str
    name: str
    role: str
    invited_by_name: str
    expires_at: int


class InviteListItem(BaseModel):
    id: str
    email: str
    name: str
    role: str
    invited_by: str
    invited_by_name: str
    expires_at: int
    created_at: int


############################
# Create Invite (Admin)
############################


@router.post("/", response_model=InviteResponse)
async def create_invite(
    request: Request,
    form_data: InviteForm,
    user=Depends(get_admin_user),
):
    email = form_data.email.lower().strip()

    if not validate_email_format(email):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Invalid email format",
        )

    if Users.get_user_by_email(email):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="A user with this email already exists",
        )

    # Check for existing pending invite
    existing = Invites.get_pending_invite_by_email(email)
    if existing and existing.expires_at > int(time.time()):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="A pending invite already exists for this email",
        )

    expiry_hours = request.app.state.config.INVITE_EXPIRY_HOURS
    expires_at = int(time.time()) + (expiry_hours * 3600)

    invite = Invites.create_invite(
        email=email,
        name=form_data.name,
        role=form_data.role,
        invited_by=user.id,
        expires_at=expires_at,
    )

    if not invite:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create invite",
        )

    base_url = str(request.base_url).rstrip("/")
    invite_url = f"{base_url}/auth/invite/{invite.token}"

    email_sent = False
    if form_data.send_email and request.app.state.config.ENABLE_EMAIL_INVITES:
        try:
            from open_webui.services.email.graph_mail_client import (
                render_invite_email,
                render_invite_subject,
                send_mail,
            )

            locale = str(DEFAULT_LOCALE) or "en"
            expiry_hours = request.app.state.config.INVITE_EXPIRY_HOURS

            html_body = render_invite_email(
                invite_url=invite_url,
                invited_by_name=user.name,
                locale=locale,
                expiry_hours=expiry_hours,
                client_name=CLIENT_NAME,
            )
            await send_mail(
                app=request.app,
                to_address=email,
                subject=render_invite_subject(locale=locale, client_name=CLIENT_NAME),
                html_body=html_body,
            )
            email_sent = True
        except Exception as e:
            log.error(f"Failed to send invite email to {email}: {e}")
            # Don't fail the invite creation if email fails
            # The admin can still copy the link

    return InviteResponse(
        id=invite.id,
        email=invite.email,
        name=invite.name,
        role=invite.role,
        token=invite.token,
        invite_url=invite_url,
        email_sent=email_sent,
        expires_at=invite.expires_at,
        created_at=invite.created_at,
    )


############################
# Validate Invite (Public)
############################


@router.get("/{token}/validate", response_model=InviteValidation)
async def validate_invite(token: str):
    invite = Invites.get_invite_by_token(token)

    if not invite:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="Invalid invite link",
        )

    if invite.revoked_at:
        raise HTTPException(
            status.HTTP_410_GONE,
            detail="This invite has been revoked",
        )

    if invite.accepted_at:
        raise HTTPException(
            status.HTTP_410_GONE,
            detail="This invite has already been used",
        )

    if invite.expires_at < int(time.time()):
        raise HTTPException(
            status.HTTP_410_GONE,
            detail="This invite has expired",
        )

    # Resolve invited_by name
    invited_by_user = Users.get_user_by_id(invite.invited_by)
    invited_by_name = invited_by_user.name if invited_by_user else "An administrator"

    return InviteValidation(
        email=invite.email,
        name=invite.name,
        role=invite.role,
        invited_by_name=invited_by_name,
        expires_at=invite.expires_at,
    )


############################
# Accept Invite (Public)
############################


@router.post("/{token}/accept", response_model=SigninResponse)
async def accept_invite(
    request: Request,
    token: str,
    form_data: AcceptInviteForm,
):
    invite = Invites.get_invite_by_token(token)

    if not invite:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="Invalid invite link",
        )

    if invite.revoked_at:
        raise HTTPException(
            status.HTTP_410_GONE,
            detail="This invite has been revoked",
        )

    if invite.accepted_at:
        raise HTTPException(
            status.HTTP_410_GONE,
            detail="This invite has already been used",
        )

    if invite.expires_at < int(time.time()):
        raise HTTPException(
            status.HTTP_410_GONE,
            detail="This invite has expired",
        )

    # Validate password
    try:
        validate_password(form_data.password)
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Use overridden name if provided, otherwise use invite name
    name = form_data.name if form_data.name else invite.name

    # Hash password and create user
    hashed = get_password_hash(form_data.password)

    try:
        new_user = Auths.insert_new_auth(
            email=invite.email,
            password=hashed,
            name=name,
            role=invite.role,
        )

        if not new_user:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create user account",
            )

        # Apply default group assignment
        apply_default_group_assignment(
            request.app.state.config.DEFAULT_GROUP_ID,
            new_user.id,
        )

        # Mark invite as accepted
        Invites.accept_invite(token)

        # Create session token
        session_token = create_token(data={"id": new_user.id})

        return {
            "token": session_token,
            "token_type": "Bearer",
            "id": new_user.id,
            "email": new_user.email,
            "name": new_user.name,
            "role": new_user.role,
            "profile_image_url": new_user.profile_image_url,
        }

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Failed to accept invite: {e}")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred while creating the account",
        )


############################
# List Pending Invites (Admin)
############################


@router.get("/", response_model=list[InviteListItem])
async def list_invites(user=Depends(get_admin_user)):
    invites = Invites.get_pending_invites()

    result = []
    for invite in invites:
        invited_by_user = Users.get_user_by_id(invite.invited_by)
        invited_by_name = invited_by_user.name if invited_by_user else "Unknown"

        result.append(
            InviteListItem(
                id=invite.id,
                email=invite.email,
                name=invite.name,
                role=invite.role,
                invited_by=invite.invited_by,
                invited_by_name=invited_by_name,
                expires_at=invite.expires_at,
                created_at=invite.created_at,
            )
        )

    return result


############################
# Resend Invite (Admin)
############################


@router.post("/{id}/resend", response_model=InviteResponse)
async def resend_invite(
    request: Request,
    id: str,
    user=Depends(get_admin_user),
):
    invite = Invites.get_invite_by_id(id)

    if not invite:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Invite not found")

    if invite.accepted_at:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Invite has already been accepted"
        )

    if invite.revoked_at:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Invite has been revoked"
        )

    # Refresh token and expiry
    expiry_hours = request.app.state.config.INVITE_EXPIRY_HOURS
    new_expires_at = int(time.time()) + (expiry_hours * 3600)
    updated_invite = Invites.refresh_invite(id, new_expires_at)

    if not updated_invite:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to refresh invite"
        )

    base_url = str(request.base_url).rstrip("/")
    invite_url = f"{base_url}/auth/invite/{updated_invite.token}"

    email_sent = False
    if request.app.state.config.ENABLE_EMAIL_INVITES:
        try:
            from open_webui.services.email.graph_mail_client import (
                render_invite_email,
                render_invite_subject,
                send_mail,
            )

            locale = str(DEFAULT_LOCALE) or "en"

            html_body = render_invite_email(
                invite_url=invite_url,
                invited_by_name=user.name,
                locale=locale,
                expiry_hours=expiry_hours,
                client_name=CLIENT_NAME,
            )
            await send_mail(
                app=request.app,
                to_address=updated_invite.email,
                subject=render_invite_subject(locale=locale, client_name=CLIENT_NAME),
                html_body=html_body,
            )
            email_sent = True
        except Exception as e:
            log.error(f"Failed to resend invite email: {e}")

    return InviteResponse(
        id=updated_invite.id,
        email=updated_invite.email,
        name=updated_invite.name,
        role=updated_invite.role,
        token=updated_invite.token,
        invite_url=invite_url,
        email_sent=email_sent,
        expires_at=updated_invite.expires_at,
        created_at=updated_invite.created_at,
    )


############################
# Revoke Invite (Admin)
############################


@router.delete("/{id}")
async def revoke_invite(
    id: str,
    user=Depends(get_admin_user),
):
    invite = Invites.get_invite_by_id(id)

    if not invite:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Invite not found")

    if invite.accepted_at:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Invite has already been accepted"
        )

    result = Invites.revoke_invite(id)
    if not result:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to revoke invite"
        )

    return {"status": "ok", "message": "Invite revoked"}
