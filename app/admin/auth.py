"""HTTP Basic auth gate for the admin page — mirrors ai-receptionist's
require_admin. No-ops (open access) only if ADMIN_USERNAME/ADMIN_PASSWORD
aren't set, which is fine for a first local check but NOT once this is on a
public Railway URL — set real credentials before sharing that link.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config.settings import settings

_basic_auth = HTTPBasic(auto_error=False)


def require_admin(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic_auth)] = None,
) -> None:
    if not settings.admin_username or not settings.admin_password:
        return
    valid = bool(credentials) and secrets.compare_digest(
        credentials.username, settings.admin_username
    ) and secrets.compare_digest(credentials.password, settings.admin_password)
    if not valid:
        raise HTTPException(
            status_code=401,
            detail="Admin credentials required.",
            headers={"WWW-Authenticate": "Basic"},
        )
