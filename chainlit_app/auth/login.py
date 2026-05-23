"""Chainlit-side login helper that hits FastAPI /auth/login.

Chainlit's @cl.password_auth_callback runs once per session; we use it to
mint a FastAPI session token and stash it in the Chainlit user object.
"""
from __future__ import annotations

from typing import Optional

import chainlit as cl
from httpx import HTTPStatusError

from chainlit_app.api_client import ApiClient


@cl.password_auth_callback
def auth_callback(username: str, password: str) -> Optional[cl.User]:
    api = ApiClient()
    try:
        data = api.login(username, password)
    except HTTPStatusError:
        return None
    return cl.User(
        identifier=data["email"],
        metadata={
            "user_id": data["user_id"],
            "display_name": data["display_name"] or data["email"],
            "roles": data["roles"],
            "token": data["token"],
        },
    )
