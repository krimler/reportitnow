"""Email + password sessions. The token + TTL shape stays the same when the
identity source moves to OIDC/SAML against an HRMS."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi_app.db import models as m
from fastapi_app.db.session import get_db

PH = PasswordHasher()
SESSION_TTL = timedelta(hours=8)


def authenticate_password(db: Session, email: str, password: str) -> m.User | None:
    user = db.scalar(select(m.User).where(m.User.email == email))
    if user is None or user.password_hash is None:
        return None
    try:
        PH.verify(user.password_hash, password)
    except VerifyMismatchError:
        return None
    return user


def create_session(db: Session, user: m.User) -> str:
    token = secrets.token_urlsafe(32)
    sess = m.Session_(
        token=token,
        user_id=user.id,
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + SESSION_TTL,
    )
    db.add(sess)
    db.commit()
    return token


def resolve_session(db: Session, token: str) -> m.User | None:
    sess = db.get(m.Session_, token)
    if sess is None:
        return None
    if sess.expires_at < datetime.utcnow():
        return None
    return db.get(m.User, sess.user_id)


def require_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> m.User:
    """FastAPI dependency: extracts user from Authorization: Bearer <token>."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    user = resolve_session(db, token)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired session")
    return user
