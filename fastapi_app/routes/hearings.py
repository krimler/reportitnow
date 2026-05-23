"""Hearing routes with Rule 7(7) quorum check at the API boundary.

POC note: the design says the quorum check should sit at the DB layer (trigger)
so it cannot be bypassed. Here we enforce it inside the create_hearing handler
and also re-check inside the state machine's report invariants. Moving the
check into a SQLAlchemy event listener or SQLite trigger is straightforward
and is the prod path.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi_app.audit_chain import append_entry
from fastapi_app.auth import AuthorisedActor, require_role
from fastapi_app.db import models as m
from fastapi_app.db.session import get_db

router = APIRouter(prefix="/hearings", tags=["hearings"])


class HearingMember(BaseModel):
    user_id: int
    role_at_hearing: str
    present: bool


class HearingCreateIn(BaseModel):
    case_id: int
    scheduled_for: datetime
    notice_issued_on: date | None = None
    notice_period_days: int = 15
    members: list[HearingMember]
    complainant_present: bool | None = None
    respondent_present: bool | None = None


class HearingOut(BaseModel):
    id: int
    case_id: int
    scheduled_for: datetime
    held_on: datetime | None
    quorum_met: bool
    notice_issued_on: date | None
    is_ex_parte: bool
    consecutive_no_shows_complainant: int
    consecutive_no_shows_respondent: int


def _check_quorum(db: Session, case: m.Case, members: list[HearingMember]) -> None:
    """Rule 7(7): minimum 3 ICC members including the Presiding Officer."""
    if case.committee_id is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "case has no committee assigned; cannot schedule hearing",
        )
    present_members = [mb for mb in members if mb.present
                       and mb.role_at_hearing in ("presiding_officer", "icc_member")]
    if len(present_members) < 3:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"quorum not met: {len(present_members)} ICC member(s) present (need >=3)",
        )
    has_po = any(mb.role_at_hearing == "presiding_officer" for mb in present_members)
    if not has_po:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "quorum not met: Presiding Officer must be present (Rule 7(7))",
        )


@router.post("", response_model=HearingOut, status_code=status.HTTP_201_CREATED)
def create_hearing(
    body: HearingCreateIn,
    actor: AuthorisedActor = Depends(require_role("presiding_officer", "icc_member")),
    db: Session = Depends(get_db),
):
    case = db.get(m.Case, body.case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found")
    if case.routed_to != "icc":
        raise HTTPException(status.HTTP_409_CONFLICT, "case not ICC-routed")
    _check_quorum(db, case, body.members)

    prev_hearing = db.execute(
        select(m.Hearing).where(m.Hearing.case_id == case.id)
        .order_by(m.Hearing.scheduled_for.desc()).limit(1)
    ).scalar()
    prev_no_shows_complainant = prev_hearing.consecutive_no_shows_complainant if prev_hearing else 0
    prev_no_shows_respondent = prev_hearing.consecutive_no_shows_respondent if prev_hearing else 0

    cur_complainant_absent = body.complainant_present is False
    cur_respondent_absent = body.respondent_present is False

    hearing = m.Hearing(
        case_id=body.case_id,
        scheduled_for=body.scheduled_for,
        held_on=datetime.utcnow(),
        quorum_met=True,
        complainant_present=body.complainant_present,
        respondent_present=body.respondent_present,
        notice_issued_on=body.notice_issued_on,
        notice_period_days=body.notice_period_days,
        is_ex_parte=False,
        consecutive_no_shows_complainant=(
            prev_no_shows_complainant + 1 if cur_complainant_absent else 0
        ),
        consecutive_no_shows_respondent=(
            prev_no_shows_respondent + 1 if cur_respondent_absent else 0
        ),
    )
    # 3 consecutive complainant no-shows with notice → termination workflow flag.
    # 3 consecutive respondent no-shows with notice → ex-parte from this hearing.
    if hearing.consecutive_no_shows_respondent >= 3 and body.notice_issued_on is not None:
        hearing.is_ex_parte = True
    db.add(hearing)
    db.flush()
    for mb in body.members:
        db.add(m.HearingAttendance(
            hearing_id=hearing.id, user_id=mb.user_id,
            role_at_hearing=mb.role_at_hearing, present=mb.present,
        ))
    append_entry(
        db,
        entity_id=case.entity_id,
        component_id="state_machine",
        actor_user_id=actor.user.id,
        case_id=case.id,
        input_payload={
            "action": "create_hearing",
            "members": [mb.model_dump() for mb in body.members],
        },
        output_payload={
            "hearing_id": hearing.id,
            "is_ex_parte": hearing.is_ex_parte,
        },
    )
    db.commit()
    return HearingOut(
        id=hearing.id, case_id=hearing.case_id,
        scheduled_for=hearing.scheduled_for, held_on=hearing.held_on,
        quorum_met=bool(hearing.quorum_met),
        notice_issued_on=hearing.notice_issued_on,
        is_ex_parte=hearing.is_ex_parte,
        consecutive_no_shows_complainant=hearing.consecutive_no_shows_complainant,
        consecutive_no_shows_respondent=hearing.consecutive_no_shows_respondent,
    )


@router.get("/case/{case_id}", response_model=list[HearingOut])
def list_hearings(
    case_id: int,
    actor: AuthorisedActor = Depends(require_role(
        "icc_member", "presiding_officer", "complainant", "respondent",
        case_scope="any",
    )),
    db: Session = Depends(get_db),
):
    hs = db.execute(
        select(m.Hearing).where(m.Hearing.case_id == case_id)
        .order_by(m.Hearing.scheduled_for.asc())
    ).scalars().all()
    return [HearingOut(
        id=h.id, case_id=h.case_id, scheduled_for=h.scheduled_for,
        held_on=h.held_on, quorum_met=bool(h.quorum_met),
        notice_issued_on=h.notice_issued_on, is_ex_parte=h.is_ex_parte,
        consecutive_no_shows_complainant=h.consecutive_no_shows_complainant,
        consecutive_no_shows_respondent=h.consecutive_no_shows_respondent,
    ) for h in hs]
