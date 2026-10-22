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
    # Compare as bytes: compare_digest raises TypeError on non-ASCII str, which
    # turned a bad login like username "é" into a 500. Both compares always run.
    valid = False
    if credentials:
        user_ok = secrets.compare_digest(
            credentials.username.encode(), settings.admin_username.encode()
        )
        pass_ok = secrets.compare_digest(
            credentials.password.encode(), settings.admin_password.encode()
        )
        valid = user_ok and pass_ok
    if not valid:
        raise HTTPException(
            status_code=401,
            detail="Admin credentials required.",
            headers={"WWW-Authenticate": "Basic"},
        )
