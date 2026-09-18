"""HTTP Basic auth, scoped to one credential pair. A factory (not a single
shared check-against-N-pairs function) so each route's dependency is bound
to exactly the restaurant/super-admin it was built for — see config.py.
Open access if username/password isn't set, same as app/admin/auth.py.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from admin_service.config import Credentials

_basic_auth = HTTPBasic(auto_error=False)


def scoped_basic_auth(creds: Credentials) -> Callable[..., None]:
    def _require(
        credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic_auth)] = None,
    ) -> None:
        if not creds.username or not creds.password:
            return
        valid = bool(credentials) and secrets.compare_digest(
            credentials.username, creds.username
        ) and secrets.compare_digest(credentials.password, creds.password)
        if not valid:
            raise HTTPException(
                status_code=401,
                detail="Admin credentials required.",
                headers={"WWW-Authenticate": "Basic"},
            )

    return _require
