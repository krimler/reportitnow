"""/ai/chat — role-bounded chat with persistence.

Each turn: check the user holds the requested role, load the matching
<ROLE>_CHAT prompt, build the role-appropriate context (RA never sees
raw narrative; IA chat doesn't get the record either, only the structured
evidence list), call the LLM, persist both turns, audit-log it.
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi_app.audit_chain import append_entry, sha256_hex
from fastapi_app.auth import current_roles
from fastapi_app.auth.session import require_user
from fastapi_app.db import models as m
from fastapi_app.db.session import get_db
from fastapi_app.llm import call_llm, load_prompt
from fastapi_app.schemas.chat import ChatIn, ChatOut, ChatTurn

# 16 turns of context is plenty for continuity and keeps local Qwen happy.
HISTORY_MAX_TURNS = 16

router = APIRouter(prefix="/ai", tags=["ai-chat"])


ROLE_TO_PROMPT: dict[str, str] = {
    "complainant":        "CA_CHAT",
    "respondent":         "RA_CHAT",
    "icc_member":         "IA_CHAT",
    "presiding_officer":  "IA_CHAT",
    "compliance_admin":   "CPA_CHAT",
    "employer":           "CPA_CHAT",
    "auditor":            "AR_CHAT",
    "employee":           "DASHBOARD_CHAT",
    "district_officer":   "DASHBOARD_CHAT",
}


def _build_context(
    db: Session, *, role: str, user: m.User, case_id: int | None
) -> str:
    # The respondent branch will NOT read raw_narrative — that's the
    # Section 16 wall and it lives here, structurally.
    if case_id is None:
        return ""

    case = db.get(m.Case, case_id)
    if case is None or case.entity_id != user.entity_id:
        return ""

    if role == "complainant":
        doc = db.scalar(
            select(m.CaseDocument).where(
                m.CaseDocument.case_id == case.id,
                m.CaseDocument.doc_type == "complaint_narrative_raw",
            )
        )
        narrative = doc.content_blob.decode("utf-8", errors="replace") if doc and doc.content_blob else ""
        return (
            f"CASE CONTEXT (your own complaint):\n"
            f"  case_id: {case.id}\n  state: {case.state}\n"
            f"  routed_to: {case.routed_to}\n"
            f"  filed_at: {case.filed_at}\n"
            f"  your_narrative_excerpt:\n{narrative[:1500]}\n"
        )

    if role == "respondent":
        summary_doc = db.scalar(
            select(m.CaseDocument).where(
                m.CaseDocument.case_id == case.id,
                m.CaseDocument.doc_type == "allegation_summary_authorised",
                m.CaseDocument.is_draft.is_(False),
            )
        )
        summary = (
            summary_doc.content_blob.decode("utf-8", errors="replace")
            if summary_doc and summary_doc.content_blob else
            "[No allegation summary has been authorised by the Presiding "
            "Officer for AI-channel disclosure yet. You can still view the "
            "full written complaint through the case file (Rule 7(2)).]"
        )
        return (
            f"CASE CONTEXT (limited to PO-authorised disclosure):\n"
            f"  case_id: {case.id}\n  state: {case.state}\n"
            f"  authorised_summary:\n{summary[:1500]}\n"
        )

    if role in ("icc_member", "presiding_officer"):
        # Structured evidence list only — same guarantee as IA Phase 2.
        ev_doc = db.scalar(
            select(m.CaseDocument).where(
                m.CaseDocument.case_id == case.id,
                m.CaseDocument.doc_type == "ia_phase1_evidence_list",
            ).order_by(m.CaseDocument.created_at.desc())
        )
        evidence = (
            ev_doc.content_blob.decode("utf-8", errors="replace")
            if ev_doc and ev_doc.content_blob else "[no extracted evidence list yet]"
        )
        return (
            f"CASE CONTEXT (ICC view):\n"
            f"  case_id: {case.id}\n  state: {case.state}\n"
            f"  filed_at: {case.filed_at}\n  inquiry_started_at: {case.inquiry_started_at}\n"
            f"  structured_evidence_list:\n{evidence[:2000]}\n"
        )

    # compliance / employer / auditor / employee — no per-case content.
    return f"CASE CONTEXT: case_id {case.id} is referenced but no content is exposed to this role.\n"


@router.post("/chat", response_model=ChatOut)
def chat(
    body: ChatIn,
    user: m.User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ChatOut:
    roles = current_roles(db, user)
    if body.role not in roles:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"you do not hold role {body.role!r}; your roles are {sorted(roles)}",
        )
    prompt_name = ROLE_TO_PROMPT.get(body.role)
    if prompt_name is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"chat is not enabled for role {body.role!r}",
        )

    system_prompt = load_prompt(prompt_name)
    context = _build_context(db, role=body.role, user=user, case_id=body.case_id)
    user_input = (context + "\n\nUSER MESSAGE:\n" + body.message) if context else body.message

    # If the caller passes history, use it; otherwise load persisted turns
    # so a fresh session picks up where the user left off.
    if body.history:
        history_turns = [t.model_dump() for t in body.history]
    else:
        history_turns = _load_history_turns(db, user_id=user.id, role=body.role,
                                            limit=HISTORY_MAX_TURNS)

    resp = call_llm(
        system_prompt=system_prompt,
        user_input=user_input,
        component_hint=prompt_name,
        max_tokens=1200,
        history=history_turns,
    )

    # Regression guard: the loader already refuses to read raw narrative
    # for respondents, but check the assembled user_input too.
    if body.role == "respondent":
        forbidden_markers = ("complaint_narrative_raw", "raw_narrative_excerpt")
        for marker in forbidden_markers:
            if marker in user_input:
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    f"role-boundary regression: respondent chat received {marker!r}",
                )

    now = datetime.utcnow()
    db.add(m.ChatHistory(
        user_id=user.id, role=body.role, case_id=body.case_id,
        turn_role="user", content=body.message,
        component_id=prompt_name, created_at=now,
    ))
    db.add(m.ChatHistory(
        user_id=user.id, role=body.role, case_id=body.case_id,
        turn_role="assistant", content=resp.text,
        component_id=prompt_name, created_at=now,
    ))

    append_entry(
        db,
        entity_id=user.entity_id,
        component_id=prompt_name,
        actor_user_id=user.id,
        case_id=body.case_id,
        input_payload={
            "role": body.role,
            "case_id": body.case_id,
            "message_hash": sha256_hex(body.message),
            "history_len": len(history_turns),
        },
        output_payload={
            "reply_hash": sha256_hex(resp.text),
            "model": resp.model,
            "prompt_hash": resp.prompt_hash,
            "filter_violations": resp.filter_violations,
            "filter_rejected": resp.filter_rejected,
        },
    )
    db.commit()

    return ChatOut(
        reply=resp.text,
        model_id=resp.model,
        stubbed=resp.stubbed,
        prompt_hash=resp.prompt_hash,
        filter_violations=resp.filter_violations,
        filter_rejected=resp.filter_rejected,
    )


def _load_history_turns(
    db: Session, *, user_id: int, role: str, limit: int = HISTORY_MAX_TURNS,
) -> list[dict[str, str]]:
    """Load the most recent `limit` turns for (user, role) in chronological order."""
    rows = list(db.execute(
        select(m.ChatHistory)
        .where(m.ChatHistory.user_id == user_id, m.ChatHistory.role == role)
        .order_by(m.ChatHistory.created_at.desc(), m.ChatHistory.id.desc())
        .limit(limit)
    ).scalars())
    rows.reverse()
    return [{"role": r.turn_role, "content": r.content} for r in rows]


class HistoryTurn(BaseModel):
    turn_role: str
    content: str
    created_at: datetime


class HistoryOut(BaseModel):
    role: str
    turns: list[HistoryTurn]


@router.get("/chat/history", response_model=HistoryOut)
def get_chat_history(
    role: str = Query(..., description="Role bucket to fetch history for"),
    limit: int = Query(50, ge=1, le=200),
    user: m.User = Depends(require_user),
    db: Session = Depends(get_db),
) -> HistoryOut:
    """Return persisted chat history for the authenticated user + role.

    Used by Chainlit's on_chat_start to re-render past turns so the user sees
    continuity across browser sessions / tabs / restarts.
    """
    if role not in current_roles(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"role {role!r} not held")
    rows = list(db.execute(
        select(m.ChatHistory)
        .where(m.ChatHistory.user_id == user.id, m.ChatHistory.role == role)
        .order_by(m.ChatHistory.created_at.desc(), m.ChatHistory.id.desc())
        .limit(limit)
    ).scalars())
    rows.reverse()
    return HistoryOut(
        role=role,
        turns=[
            HistoryTurn(
                turn_role=r.turn_role, content=r.content, created_at=r.created_at,
            ) for r in rows
        ],
    )


@router.delete("/chat/history")
def clear_chat_history(
    role: str = Query(...),
    user: m.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if role not in current_roles(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"role {role!r} not held")
    deleted = db.query(m.ChatHistory).filter(
        m.ChatHistory.user_id == user.id,
        m.ChatHistory.role == role,
    ).delete(synchronize_session=False)
    db.commit()
    return {"deleted": deleted}
