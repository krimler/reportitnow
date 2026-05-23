"""Respondent Assistant: draft a Rule 7(4) reply.

The runner never reads complaint_narrative_raw. RAInput has no raw_narrative
field, so a smuggled one would fail Pydantic validation; the LLM sees only
the PO-authorised allegation summary the caller passes in.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from fastapi_app.ai_components.common import audit_ai_call, persist_draft_document
from fastapi_app.db import models as m
from fastapi_app.llm import call_llm, load_prompt
from fastapi_app.schemas.ai_io import RAInput, RAOutput


def run_ra(
    db: Session,
    *,
    payload: RAInput,
    actor: m.User,
    entity_id: int,
) -> RAOutput:
    # Defence-in-depth: refuse to run if the payload somehow has any field
    # named like "raw_narrative" smuggled in via .__dict__. Pydantic should
    # already reject this at validation, but we double-check.
    extras = set(payload.model_dump().keys()) - set(RAInput.model_fields.keys())
    forbidden = {"raw_narrative", "narrative_verbatim", "complaint_narrative_raw"}
    if extras & forbidden:
        raise PermissionError(
            f"RA input includes forbidden field(s): {extras & forbidden}"
        )

    system_prompt = load_prompt("RA")
    user_input = (
        f"AUTHORISED ALLEGATION SUMMARY:\n{payload.allegation_summary_authorised}\n\n"
        f"PROCEDURAL HISTORY: {payload.procedural_history.model_dump_json()}\n"
        f"EVIDENCE REFERENCES: "
        f"{[e.model_dump() for e in payload.evidence_list_against_respondent]}\n"
    )
    resp = call_llm(
        system_prompt=system_prompt,
        user_input=user_input,
        component_hint="RA",
        max_tokens=2000,
    )

    try:
        structured = json.loads(resp.text)
    except json.JSONDecodeError:
        structured = {"raw_llm_text": resp.text}

    draft = persist_draft_document(
        db,
        case_id=payload.case_id,
        doc_type="respondent_reply",
        content=structured,
        ai_component="RA",
    )

    audit_ai_call(
        db,
        entity_id=entity_id,
        component_id="RA",
        actor_user_id=actor.id,
        case_id=payload.case_id,
        input_payload={
            "case_id": payload.case_id,
            "summary_len": len(payload.allegation_summary_authorised),
            "evidence_count": len(payload.evidence_list_against_respondent),
        },
        output_payload={"draft_doc_id": draft.id},
        model_id=resp.model,
        prompt_hash=resp.prompt_hash,
    )
    db.commit()

    response_obj = structured.get("response", {})
    return RAOutput(
        response_draft_id=draft.id,
        evidence_to_submit=response_obj.get("evidence_to_submit", []) or [],
        witnesses_proposed=response_obj.get("witnesses_proposed", []) or [],
        procedural_objections=response_obj.get("procedural_objections", []) or [],
        stubbed=resp.stubbed,
    )
