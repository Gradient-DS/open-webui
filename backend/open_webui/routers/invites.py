import datetime
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from typing import Optional

from open_webui.models.auths import Auths
from open_webui.models.config import Config
from open_webui.routers.auths import SessionUserResponse
from open_webui.models.invites import AcceptInviteForm, InviteForm, InviteModel, Invites
from open_webui.models.users import Users

from open_webui.utils.auth import (
    create_token,
    get_admin_user,
    get_password_hash,
    validate_password,
)
from open_webui.utils.misc import parse_duration, validate_email_format
from open_webui.utils.groups import apply_default_group_assignment
from open_webui.utils.access_control import get_permissions
from open_webui.env import (
    CLIENT_NAME,
    WEBUI_AUTH_COOKIE_SAME_SITE,
    WEBUI_AUTH_COOKIE_SECURE,
)
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


############################
# Validate Invite (Public)
############################


############################
# Accept Invite (Public)
############################


############################
# List Pending Invites (Admin)
############################


############################
# Resend Invite (Admin)
############################


############################
# Revoke Invite (Admin)
############################
