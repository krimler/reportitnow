"""Complainant Assistant: structure a narrative into a draft complaint."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from fastapi_app.ai_components.common import audit_ai_call, persist_draft_document
from fastapi_app.audit_chain import sha256_hex
from fastapi_app.db import models as m
from fastapi_app.llm import call_llm, load_prompt
from fastapi_app.schemas.ai_io import CAInput, CAOutput


def run_ca(
    db: Session,
    *,
    payload: CAInput,
    actor: m.User,
    entity_id: int,
) -> CAOutput:
    """Structure a complainant narrative, persist as a DRAFT document.

    The case must already exist (created via /cases with intake gates). The
    raw narrative is preserved verbatim alongside the structured output in
    accordance with the no-narrative-loss invariant.
    """
    system_prompt = load_prompt("CA")
    user_input = (
        f"COMPLAINANT METADATA: {payload.complainant_metadata.model_dump_json()}\n"
        f"ENTITY CONFIG: {payload.entity_config.model_dump_json()}\n\n"
        f"RAW NARRATIVE (verbatim — preserve user's wording):\n{payload.raw_narrative}"
    )
    resp = call_llm(
        system_prompt=system_prompt,
        user_input=user_input,
        component_hint="CA",
        max_tokens=2000,
    )

    # Always persist the verbatim raw narrative as a separate document.
    persist_draft_document(
        db,
        case_id=payload.case_id,
        doc_type="complaint_narrative_raw",
        content=payload.raw_narrative,
        ai_component=None,  # human-authored
    )

    # Try to parse the structured output; if invalid JSON, store as text blob.
    try:
        structured = json.loads(resp.text)
    except json.JSONDecodeError:
        structured = {"raw_llm_text": resp.text}

    # Flags (we re-check intake-gate signals from the metadata)
    pocso_flag = payload.complainant_metadata.age_band == "under_18"
    # employer/cross-org flags are determined at case-creation time, not here.
    # The CA just surfaces what it saw.
    output_dict = {
        "structured_complaint": structured.get("structured_complaint", structured),
        "raw_narrative_kept_verbatim": True,
        "flags": {"pocso": pocso_flag},
    }
    draft = persist_draft_document(
        db,
        case_id=payload.case_id,
        doc_type="complaint_written",
        content=output_dict,
        ai_component="CA",
    )

    audit_ai_call(
        db,
        entity_id=entity_id,
        component_id="CA",
        actor_user_id=actor.id,
        case_id=payload.case_id,
        input_payload={
            "case_id": payload.case_id,
            "raw_narrative_hash": sha256_hex(payload.raw_narrative),
        },
        output_payload={"draft_doc_id": draft.id, "pocso_flag": pocso_flag},
        model_id=resp.model,
        prompt_hash=resp.prompt_hash,
    )
    db.commit()

    return CAOutput(
        structured_complaint_draft_id=draft.id,
        pocso_flag=pocso_flag,
        employer_respondent_flag=False,
        cross_org_flag=False,
        stubbed=resp.stubbed,
    )
