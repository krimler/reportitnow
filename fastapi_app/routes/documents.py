"""Document routes: list, fetch, PO authorise/reject."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi_app.audit_chain import append_entry
from fastapi_app.auth import AuthorisedActor, require_role
from fastapi_app.db import models as m
from fastapi_app.db.session import get_db

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentOut(BaseModel):
    id: int
    case_id: int
    doc_type: str
    ai_component: str | None
    is_draft: bool
    authorised_by_user_id: int | None
    authorised_at: datetime | None
    created_at: datetime
    content_preview: str

    @classmethod
    def from_model(cls, d: m.CaseDocument) -> "DocumentOut":
        preview = ""
        if d.content_blob is not None:
            preview = d.content_blob[:400].decode("utf-8", errors="replace")
        return cls(
            id=d.id, case_id=d.case_id, doc_type=d.doc_type,
            ai_component=d.ai_component, is_draft=d.is_draft,
            authorised_by_user_id=d.authorised_by_user_id,
            authorised_at=d.authorised_at, created_at=d.created_at,
            content_preview=preview,
        )


@router.get("/case/{case_id}", response_model=list[DocumentOut])
def list_documents_for_case(
    case_id: int,
    actor: AuthorisedActor = Depends(require_role(
        "complainant", "respondent", "icc_member", "presiding_officer",
        case_scope="any",
    )),
    db: Session = Depends(get_db),
):
    docs = db.execute(
        select(m.CaseDocument)
        .where(m.CaseDocument.case_id == case_id)
        .order_by(m.CaseDocument.created_at.asc())
    ).scalars().all()
    return [DocumentOut.from_model(d) for d in docs]


@router.post("/{doc_id}/authorise", response_model=DocumentOut)
def authorise_document(
    doc_id: int,
    actor: AuthorisedActor = Depends(require_role("presiding_officer")),
    db: Session = Depends(get_db),
):
    doc = db.get(m.CaseDocument, doc_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    if not doc.is_draft:
        return DocumentOut.from_model(doc)
    doc.is_draft = False
    doc.authorised_by_user_id = actor.user.id
    doc.authorised_at = datetime.utcnow()

    case = db.get(m.Case, doc.case_id)
    if case is not None:
        append_entry(
            db,
            entity_id=case.entity_id,
            component_id="state_machine",
            actor_user_id=actor.user.id,
            case_id=case.id,
            input_payload={"doc_id": doc_id, "action": "authorise"},
            output_payload={"doc_id": doc_id, "is_draft": False},
        )
    db.commit()
    return DocumentOut.from_model(doc)


@router.post("/{doc_id}/reject", response_model=DocumentOut)
def reject_document(
    doc_id: int,
    actor: AuthorisedActor = Depends(require_role("presiding_officer")),
    db: Session = Depends(get_db),
):
    """Rejection keeps draft status; appends an audit entry for visibility."""
    doc = db.get(m.CaseDocument, doc_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    case = db.get(m.Case, doc.case_id)
    if case is not None:
        append_entry(
            db,
            entity_id=case.entity_id,
            component_id="state_machine",
            actor_user_id=actor.user.id,
            case_id=case.id,
            input_payload={"doc_id": doc_id, "action": "reject"},
            output_payload={"doc_id": doc_id, "is_draft": True},
        )
    db.commit()
    return DocumentOut.from_model(doc)
