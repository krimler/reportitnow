"""Report Assistant: assemble the Section 13 report with four distribution copies.

Findings + conclusions are explicitly excluded from generation (placeholder
text only) — those are ICC authorship under the design's role-bounded scope.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from fastapi_app.ai_components.common import audit_ai_call, persist_draft_document
from fastapi_app.db import models as m
from fastapi_app.llm import call_llm, load_prompt
from fastapi_app.schemas.ai_io import (
    ReportDocument,
    RPAInput,
    RPAOutput,
)

DISTRIBUTION = ("employer", "district_officer", "complainant", "respondent")


def run_rpa(
    db: Session,
    *,
    payload: RPAInput,
    actor: m.User,
    entity_id: int,
) -> RPAOutput:
    if not payload.quorum_attestation:
        raise ValueError("quorum_attestation must be True per Rule 7(7)")

    system_prompt = load_prompt("RPA")
    user_input = (
        f"PROCEDURAL HISTORY: {payload.procedural_history.model_dump_json()}\n"
        f"COMMITTEE: {payload.committee_composition.model_dump_json()}\n"
        f"OUTCOME (ICC-set, not AI-set): {payload.outcome}\n\n"
        f"INQUIRY RECORD SUMMARY (from IA Phase 2):\n"
        f"{payload.inquiry_record_summary.model_dump_json(indent=2)}\n"
    )
    resp = call_llm(
        system_prompt=system_prompt,
        user_input=user_input,
        component_hint="RPA",
        max_tokens=3500,
    )
    try:
        parsed = json.loads(resp.text)
    except json.JSONDecodeError:
        parsed = {}

    base_sections = parsed.get("report", {})
    # Hard-enforce: sections 7 and 8 must be placeholders.
    base_sections["findings_of_fact"] = "[ICC AUTHORSHIP — do not generate]"
    base_sections["conclusions"] = "[ICC AUTHORSHIP — do not generate]"

    copies: dict[str, ReportDocument] = {}
    doc_ids: list[int] = []
    for recipient in DISTRIBUTION:
        report_doc = ReportDocument(
            recipient=recipient,
            structured_sections=base_sections,
            draft_pending_icc_review=True,
        )
        copies[recipient] = report_doc
        # Persist each copy as a separate draft document for the distribution chain.
        d = persist_draft_document(
            db,
            case_id=payload.case_id,
            doc_type=f"inquiry_report_{recipient}",
            content=report_doc.model_dump(),
            ai_component="RPA",
        )
        doc_ids.append(d.id)

    # Also persist a primary "inquiry_report" draft (this is the one whose
    # authorisation drives state advancement to employer_action).
    primary = persist_draft_document(
        db,
        case_id=payload.case_id,
        doc_type="inquiry_report",
        content=base_sections,
        ai_component="RPA",
    )
    doc_ids.append(primary.id)

    audit_ai_call(
        db,
        entity_id=entity_id,
        component_id="RPA",
        actor_user_id=actor.id,
        case_id=payload.case_id,
        input_payload={
            "case_id": payload.case_id,
            "outcome": payload.outcome,
            "quorum_attestation": payload.quorum_attestation,
        },
        output_payload={"draft_doc_ids": doc_ids},
        model_id=resp.model,
        prompt_hash=resp.prompt_hash,
    )
    db.commit()

    return RPAOutput(
        report_employer_copy=copies["employer"],
        report_do_copy=copies["district_officer"],
        report_complainant_copy=copies["complainant"],
        report_respondent_copy=copies["respondent"],
        report_document_ids=doc_ids,
        stubbed=resp.stubbed,
    )
