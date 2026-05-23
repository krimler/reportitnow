"""Audit chain export + verification."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi_app.audit_chain import verify_chain
from fastapi_app.auth import AuthorisedActor, require_role
from fastapi_app.db import models as m
from fastapi_app.db.session import get_db

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/verify")
def verify(
    actor: AuthorisedActor = Depends(require_role("auditor", "district_officer", "compliance_admin")),
    db: Session = Depends(get_db),
):
    result = verify_chain(db, actor.user.entity_id)
    return {
        "is_valid": result.is_valid,
        "entries_checked": result.entries_checked,
        "first_divergence_at": result.first_divergence_at,
        "divergence_component": result.divergence_component,
        "divergence_timestamp": result.divergence_timestamp,
    }


@router.get("/export")
def export_metadata(
    actor: AuthorisedActor = Depends(require_role("auditor", "district_officer")),
    db: Session = Depends(get_db),
):
    """Metadata-only export — no case content, no actor plaintext beyond ID."""
    rows = db.execute(
        select(m.AuditLog).where(m.AuditLog.entity_id == actor.user.entity_id)
        .order_by(m.AuditLog.seq.asc())
    ).scalars().all()
    return [
        {
            "seq": r.seq,
            "component_id": r.component_id,
            "case_id": r.case_id,
            "timestamp": r.timestamp.isoformat(),
            "input_hash": r.input_hash,
            "output_hash": r.output_hash,
            "prev_hash": r.prev_hash,
            "hash": r.hash,
        }
        for r in rows
    ]
