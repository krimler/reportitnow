"""AI component routes — six endpoints, each role-scoped at the FastAPI layer.

Schema-layer role boundaries (e.g. RAInput has no raw_narrative) are enforced
in `fastapi_app/schemas/ai_io.py`. These routes enforce the *role allowed to
invoke* dimension.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from fastapi_app.ai_components import (
    ar as ar_mod,
    ca as ca_mod,
    cpa as cpa_mod,
    ia as ia_mod,
    ra as ra_mod,
    rpa as rpa_mod,
)
from fastapi_app.ai_components.cpa.runner import build_cpa_input
from fastapi_app.auth import AuthorisedActor, require_role
from fastapi_app.db import models as m
from fastapi_app.db.session import get_db
from fastapi_app.schemas.ai_io import (
    ARInput, AROutput,
    CAInput, CAOutput,
    CPAOutput,
    IAPhase1Input, IAPhase1Output,
    IAPhase2Input, IAPhase2Output,
    RAInput, RAOutput,
    RPAInput, RPAOutput,
)

router = APIRouter(prefix="/ai", tags=["ai-components"])


@router.post("/CA/structure_narrative", response_model=CAOutput)
def ca_structure_narrative(
    body: CAInput,
    actor: AuthorisedActor = Depends(require_role("complainant", case_scope="own")),
    db: Session = Depends(get_db),
) -> CAOutput:
    case = db.get(m.Case, body.case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found")
    if case.complainant_user_id != actor.user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your case")
    return ca_mod.run_ca(
        db, payload=body, actor=actor.user, entity_id=case.entity_id
    )


@router.post("/RA/draft_response", response_model=RAOutput)
def ra_draft_response(
    body: RAInput,
    actor: AuthorisedActor = Depends(require_role("respondent", case_scope="own")),
    db: Session = Depends(get_db),
) -> RAOutput:
    case = db.get(m.Case, body.case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found")
    if case.respondent_user_id != actor.user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your case")
    return ra_mod.run_ra(
        db, payload=body, actor=actor.user, entity_id=case.entity_id
    )


@router.post("/IA/phase1_extract", response_model=IAPhase1Output)
def ia_phase1(
    body: IAPhase1Input,
    actor: AuthorisedActor = Depends(require_role(
        "presiding_officer", "icc_member", case_scope="committee"
    )),
    db: Session = Depends(get_db),
) -> IAPhase1Output:
    case = db.get(m.Case, body.case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found")
    return ia_mod.run_ia_phase1(
        db, payload=body, actor=actor.user, entity_id=case.entity_id
    )


@router.post("/IA/phase2_synthesise", response_model=IAPhase2Output)
def ia_phase2(
    body: IAPhase2Input,
    actor: AuthorisedActor = Depends(require_role(
        "presiding_officer", "icc_member", case_scope="committee"
    )),
    db: Session = Depends(get_db),
) -> IAPhase2Output:
    case = db.get(m.Case, body.case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found")
    return ia_mod.run_ia_phase2(
        db, payload=body, actor=actor.user, entity_id=case.entity_id
    )


@router.post("/RPA/draft_report", response_model=RPAOutput)
def rpa_draft_report(
    body: RPAInput,
    actor: AuthorisedActor = Depends(require_role(
        "presiding_officer", "icc_member", case_scope="committee"
    )),
    db: Session = Depends(get_db),
) -> RPAOutput:
    case = db.get(m.Case, body.case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found")
    return rpa_mod.run_rpa(
        db, payload=body, actor=actor.user, entity_id=case.entity_id
    )


@router.post("/CPA/build_annual_reports", response_model=CPAOutput)
def cpa_build_reports(
    fiscal_year: str,
    training_sessions: int = 0,
    actor: AuthorisedActor = Depends(require_role(
        "compliance_admin", "presiding_officer", "employer"
    )),
    db: Session = Depends(get_db),
) -> CPAOutput:
    entity_id = actor.user.entity_id
    payload = build_cpa_input(
        db, entity_id=entity_id, fiscal_year=fiscal_year,
        training_sessions=training_sessions,
    )
    return cpa_mod.run_cpa(
        db, payload=payload, actor=actor.user, entity_id=entity_id
    )


@router.post("/AR/verify", response_model=AROutput)
def ar_verify(
    body: ARInput,
    actor: AuthorisedActor = Depends(require_role("auditor")),
    db: Session = Depends(get_db),
) -> AROutput:
    return ar_mod.run_ar(db, payload=body)
