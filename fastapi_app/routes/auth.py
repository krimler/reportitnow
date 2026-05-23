"""Auth routes — login (standalone POC mode), logout, whoami."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fastapi_app.auth import (
    authenticate_password,
    create_session,
    current_roles,
    require_user,
)
from fastapi_app.db import models as m
from fastapi_app.db.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class LoginOut(BaseModel):
    token: str
    user_id: int
    email: str
    display_name: str | None
    roles: list[str]


@router.post("/login", response_model=LoginOut)
def login(payload: LoginIn, db: Session = Depends(get_db)) -> LoginOut:
    user = authenticate_password(db, payload.email, payload.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    token = create_session(db, user)
    roles = sorted(current_roles(db, user))
    return LoginOut(
        token=token, user_id=user.id, email=user.email,
        display_name=user.display_name, roles=roles,
    )


@router.get("/whoami", response_model=LoginOut)
def whoami(
    user: m.User = Depends(require_user),
    db: Session = Depends(get_db),
) -> LoginOut:
    roles = sorted(current_roles(db, user))
    return LoginOut(
        token="(redacted)", user_id=user.id, email=user.email,
        display_name=user.display_name, roles=roles,
    )
