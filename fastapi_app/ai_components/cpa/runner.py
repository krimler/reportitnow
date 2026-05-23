"""Compliance Assistant: four legally distinct annual outputs.

The CPA reads aggregated case data only (no narratives) and assembles the
Section 21/Rule 14 ICC report, Section 22 employer report, Board's Report
extract (Rule 8(5)(x)), and the state portal payload. The audit chain entry
binds the model identifier and prompt hash so a Rule 14 filing is reproducible.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fastapi_app.ai_components.common import audit_ai_call, persist_draft_document
from fastapi_app.db import models as m
from fastapi_app.dp_engine import aggregate_entity_metrics
from fastapi_app.llm import call_llm, load_prompt
from fastapi_app.schemas.ai_io import (
    AggregatedCaseData,
    BoardReportExtract,
    CPAInput,
    CPAOutput,
    ICCTenureState,
    Section21Report,
    Section22Report,
    TrainingCalendar,
)


def build_cpa_input(
    db: Session, *, entity_id: int, fiscal_year: str, training_sessions: int = 0
) -> CPAInput:
    metrics = aggregate_entity_metrics(
        db, entity_id, fiscal_year, training_sessions=training_sessions
    )
    icc = db.execute(
        select(m.IccCommittee)
        .where(m.IccCommittee.entity_id == entity_id)
        .order_by(m.IccCommittee.constituted_on.desc())
        .limit(1)
    ).scalar()
    if icc is None:
        tenure = ICCTenureState(
            constituted_on=str(date.today()),
            tenure_expires_on=str(date.today()),
            days_to_expiry=0,
        )
    else:
        delta = (icc.tenure_expires_on - date.today()).days
        tenure = ICCTenureState(
            constituted_on=icc.constituted_on.isoformat(),
            tenure_expires_on=icc.tenure_expires_on.isoformat(),
            days_to_expiry=delta,
        )
    return CPAInput(
        entity_id=entity_id,
        fiscal_year=fiscal_year,
        aggregated_case_data=AggregatedCaseData(
            complaints_received=metrics.cases_filed,
            complaints_disposed=metrics.cases_resolved,
            complaints_pending_over_90d=metrics.cases_pending_over_90d,
            nature_of_action_taken=[],
        ),
        icc_tenure_state=tenure,
        training_calendar=TrainingCalendar(
            workshops_conducted=training_sessions,
            next_scheduled=None,
        ),
    )


def run_cpa(
    db: Session,
    *,
    payload: CPAInput,
    actor: m.User,
    entity_id: int,
) -> CPAOutput:
    system_prompt = load_prompt("CPA")
    user_input = payload.model_dump_json(indent=2)
    resp = call_llm(
        system_prompt=system_prompt,
        user_input=user_input,
        component_hint="CPA",
        max_tokens=2000,
    )

    agg = payload.aggregated_case_data
    nil = agg.complaints_received == 0
    section21 = Section21Report(
        fy=payload.fiscal_year,
        complaints_received=agg.complaints_received,
        complaints_disposed=agg.complaints_disposed,
        complaints_pending_over_90d=agg.complaints_pending_over_90d,
        workshops_conducted=payload.training_calendar.workshops_conducted,
        nature_of_action_taken=agg.nature_of_action_taken,
        nil_report=nil,
    )
    section22 = Section22Report(
        fy=payload.fiscal_year,
        complaints_filed=agg.complaints_received,
        complaints_disposed=agg.complaints_disposed,
    )
    board = BoardReportExtract(
        fy=payload.fiscal_year,
        complaints_received=agg.complaints_received,
        complaints_disposed=agg.complaints_disposed,
        complaints_pending_over_90d=agg.complaints_pending_over_90d,
    )
    state_payload = {
        "portal": "tn_posh_go_ms_64",
        "entity_id": entity_id,
        "fy": payload.fiscal_year,
        "icc": payload.icc_tenure_state.model_dump(),
        "complaints": {
            "received": agg.complaints_received,
            "disposed": agg.complaints_disposed,
            "pending_over_90d": agg.complaints_pending_over_90d,
        },
        "workshops": payload.training_calendar.workshops_conducted,
        "llm_note_hash": resp.prompt_hash,
    }

    output = CPAOutput(
        section_21_report=section21,
        section_22_report=section22,
        board_report_extract=board,
        state_portal_payload=state_payload,
        stubbed=resp.stubbed,
    )

    # Persist as drafts (the four legally distinct outputs).
    for doc_type, content in [
        ("annual_report_section_21", section21.model_dump()),
        ("annual_report_section_22", section22.model_dump()),
        ("board_report_extract", board.model_dump()),
        ("state_portal_payload", state_payload),
    ]:
        persist_draft_document(
            db,
            case_id=0,  # entity-wide, not case-specific
            doc_type=doc_type,
            content=content,
            ai_component="CPA",
        ) if False else None
    # (Note: case_documents requires case_id; we leave entity-wide docs to the
    # routes layer to persist via a separate compliance_reports table or attach
    # to a synthetic "compliance" case. POC keeps it returned via API only.)

    audit_ai_call(
        db,
        entity_id=entity_id,
        component_id="CPA",
        actor_user_id=actor.id,
        case_id=None,
        input_payload={
            "fy": payload.fiscal_year,
            "received": agg.complaints_received,
            "disposed": agg.complaints_disposed,
            "pending_over_90d": agg.complaints_pending_over_90d,
        },
        output_payload={"nil_report": nil},
        model_id=resp.model,
        prompt_hash=resp.prompt_hash,
    )
    db.commit()
    return output
