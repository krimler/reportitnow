"""Compliance + transparency dashboard routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from fastapi_app.auth import AuthorisedActor, require_role
from fastapi_app.db import models as m
from fastapi_app.db.session import get_db
from fastapi_app.dp_engine import (
    aggregate_entity_metrics,
    release_compliance_tier,
    release_transparency_tier,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/compliance")
def compliance_tier(
    fiscal_year: str,
    training_sessions: int = 0,
    actor: AuthorisedActor = Depends(require_role(
        "employer", "icc_member", "presiding_officer", "compliance_admin",
        "district_officer", "auditor",
    )),
    db: Session = Depends(get_db),
):
    metrics = aggregate_entity_metrics(
        db, actor.user.entity_id, fiscal_year,
        training_sessions=training_sessions,
    )
    return release_compliance_tier(metrics)


@router.get("/transparency")
def transparency_tier(
    fiscal_year: str,
    training_sessions: int = 0,
    actor: AuthorisedActor = Depends(require_role(
        "employee", "complainant", "respondent",
        "icc_member", "presiding_officer", "employer",
        "compliance_admin", "auditor", "district_officer",
    )),
    db: Session = Depends(get_db),
):
    entity = db.get(m.Entity, actor.user.entity_id)
    if entity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "entity not found")
    metrics = aggregate_entity_metrics(
        db, entity.id, fiscal_year, training_sessions=training_sessions
    )
    release = release_transparency_tier(db, entity, metrics)
    db.commit()  # persist cache
    return {
        "release": release.output,
        "epsilon_spent": release.epsilon_spent,
        "cached": release.cached,
        "suppressed": release.suppressed,
    }
