"""HTTP Basic auth gate for the admin page. No-ops (open access) if
ADMIN_USERNAME/ADMIN_PASSWORD aren't set — set real credentials before
sharing a public URL.
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
