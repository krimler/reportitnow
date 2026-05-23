"""Role-scoped FastAPI dependency. require_role(*roles, case_scope=...) checks
both the user's role assignments and (when case_id is in the path) whether
they belong on the case."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi_app.auth.session import require_user
from fastapi_app.db import models as m
from fastapi_app.db.session import get_db


@dataclass
class AuthorisedActor:
    user: m.User
    roles: set[str]
    case_id: int | None = None


def current_roles(db: Session, user: m.User) -> set[str]:
    rows = db.execute(
        select(m.RoleAssignment).where(
            m.RoleAssignment.user_id == user.id,
            m.RoleAssignment.revoked_at.is_(None),
        )
    ).scalars().all()
    return {r.role for r in rows}


def _user_on_case(db: Session, user_id: int, case_id: int) -> bool:
    case = db.get(m.Case, case_id)
    if case is None:
        return False
    if case.complainant_user_id == user_id:
        return True
    if case.respondent_user_id == user_id:
        return True
    return False


def _user_on_icc_for_case(db: Session, user_id: int, case_id: int) -> bool:
    case = db.get(m.Case, case_id)
    if case is None or case.committee_id is None:
        return False
    return db.scalar(
        select(m.IccMember).where(
            m.IccMember.committee_id == case.committee_id,
            m.IccMember.user_id == user_id,
            m.IccMember.removed_on.is_(None),
        )
    ) is not None


CaseScope = Literal["any", "own", "committee", "entity"]


def require_role(
    *allowed_roles: str,
    case_scope: CaseScope = "any",
):
    """Build a FastAPI dependency enforcing role + case scope.

    Use as ``actor: AuthorisedActor = Depends(require_role(\"icc_member\",
    case_scope=\"committee\"))``. The case_id is looked up from path params if
    available; if it's not on the path, case_scope is treated as 'any'.
    """
    allowed = set(allowed_roles)

    def _dep(
        case_id: int | None = None,
        user: m.User = Depends(require_user),
        db: Session = Depends(get_db),
    ) -> AuthorisedActor:
        roles = current_roles(db, user)
        if not roles & allowed:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"role(s) {roles} not in allowed set {allowed}",
            )
        if case_scope == "own" and case_id is not None:
            if not _user_on_case(db, user.id, case_id):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "case_scope=own violated")
        elif case_scope == "committee" and case_id is not None:
            if not _user_on_icc_for_case(db, user.id, case_id):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "case_scope=committee violated")
        elif case_scope == "entity":
            # any role assignment to this entity already satisfies (POC).
            pass
        return AuthorisedActor(user=user, roles=roles, case_id=case_id)

    return _dep
