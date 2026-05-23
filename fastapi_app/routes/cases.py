"""Case lifecycle routes."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi_app.auth import AuthorisedActor, require_role
from fastapi_app.auth.session import require_user
from fastapi_app.db import models as m
from fastapi_app.db.session import get_db
from fastapi_app.state_machine import (
    StateTransitionBlocked,
    InvariantViolation,
    create_case_with_gates,
    transition,
)

router = APIRouter(prefix="/cases", tags=["cases"])


class CaseCreateIn(BaseModel):
    incident_date: date
    incident_continuing: bool = False
    respondent_email: str | None = None


class CaseOut(BaseModel):
    id: int
    state: str
    routed_to: str
    minor_complainant: bool
    cross_organisational: bool
    respondent_is_employer: bool
    blocked_reason: str | None = None
    incident_date: date


class TransitionIn(BaseModel):
    target: str
    reason: str
    payload: dict = Field(default_factory=dict)


@router.post("", response_model=CaseOut, status_code=status.HTTP_201_CREATED)
def create_case(
    body: CaseCreateIn,
    user: m.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    respondent: m.User | None = None
    if body.respondent_email:
        respondent = db.scalar(select(m.User).where(m.User.email == body.respondent_email))
    case, gates = create_case_with_gates(
        db,
        actor=user,
        complainant=user,
        respondent=respondent,
        entity_id=user.entity_id,
        incident_date=body.incident_date,
        incident_continuing=body.incident_continuing,
    )
    db.commit()
    return CaseOut(
        id=case.id,
        state=case.state,
        routed_to=case.routed_to,
        minor_complainant=case.minor_complainant,
        cross_organisational=case.cross_organisational,
        respondent_is_employer=case.respondent_is_employer,
        blocked_reason=gates.blocked_reason,
        incident_date=case.incident_date,
    )


@router.get("/{case_id}", response_model=CaseOut)
def get_case(
    case_id: int,
    actor: AuthorisedActor = Depends(require_role(
        "complainant", "respondent", "icc_member", "presiding_officer",
        "employer", "compliance_admin", "auditor", "district_officer",
        case_scope="any",
    )),
    db: Session = Depends(get_db),
):
    case = db.get(m.Case, case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found")
    return CaseOut(
        id=case.id, state=case.state, routed_to=case.routed_to,
        minor_complainant=case.minor_complainant,
        cross_organisational=case.cross_organisational,
        respondent_is_employer=case.respondent_is_employer,
        blocked_reason=case.closure_reason,
        incident_date=case.incident_date,
    )


@router.post("/{case_id}/transition", response_model=CaseOut)
def transition_case(
    case_id: int,
    body: TransitionIn,
    user: m.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    try:
        case = transition(
            db, case_id=case_id, target=body.target,
            actor=user, reason=body.reason, payload=body.payload,
        )
        db.commit()
    except StateTransitionBlocked as e:
        db.rollback()
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e))
    except InvariantViolation as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    return CaseOut(
        id=case.id, state=case.state, routed_to=case.routed_to,
        minor_complainant=case.minor_complainant,
        cross_organisational=case.cross_organisational,
        respondent_is_employer=case.respondent_is_employer,
        blocked_reason=case.closure_reason,
        incident_date=case.incident_date,
    )


@router.post("/{case_id}/serve_complaint_to_respondent")
def serve_complaint_to_respondent(
    case_id: int,
    actor: AuthorisedActor = Depends(require_role("presiding_officer")),
    db: Session = Depends(get_db),
):
    """Rule 7(2) service: PO authorises delivery of the full written complaint
    document to the respondent through the case-file channel.

    This is the document-distribution path; it is NOT the RA channel.
    """
    case = db.get(m.Case, case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found")
    doc = db.scalar(
        select(m.CaseDocument).where(
            m.CaseDocument.case_id == case_id,
            m.CaseDocument.doc_type == "complaint_written",
            m.CaseDocument.is_draft.is_(False),
        )
    )
    if doc is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "no authorised written complaint exists for this case",
        )
    from datetime import datetime
    doc.served_to_respondent_at = datetime.utcnow()
    db.commit()
    return {"served_at": doc.served_to_respondent_at.isoformat(), "document_id": doc.id}
