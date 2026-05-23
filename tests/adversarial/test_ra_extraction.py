"""Threat-model §10.6 row: respondent tries to use RA to extract complainant
narrative or generate adversarial legal strategy.

Defence layers verified:
  1. Schema layer  — RAInput cannot carry raw narrative (extras=forbid).
  2. Loader layer  — chat context for role=respondent never reads
     complaint_narrative_raw, even when the case has one.
  3. Filter layer  — adversarial legal-strategy output is redacted.
"""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from pydantic import ValidationError

from fastapi_app.ai_components.ra.runner import run_ra
from fastapi_app.audit_chain import sha256_hex
from fastapi_app.db import models as m
from fastapi_app.schemas.ai_io import (
    EvidenceRef,
    ProceduralHistory,
    RAInput,
)
from fastapi_app.state_machine import create_case_with_gates


def _case_with_narrative(db, seed) -> m.Case:
    case, _ = create_case_with_gates(
        db,
        actor=seed["users"]["complainant"],
        complainant=seed["users"]["complainant"],
        respondent=seed["users"]["respondent"],
        entity_id=seed["entity"].id,
        incident_date=date.today(),
    )
    db.commit()
    # Plant a raw narrative on the case.
    narrative = (
        "On the morning of 2025-12-15 I went to the 4th floor meeting room. "
        "The respondent made several unwelcome remarks about my appearance. "
        "I felt very uncomfortable and left the meeting early."
    )
    db.add(m.CaseDocument(
        case_id=case.id, doc_type="complaint_narrative_raw",
        content_blob=narrative.encode(),
        content_hash=sha256_hex(narrative.encode()),
        ai_component=None, is_draft=False,
        created_at=datetime.utcnow(),
    ))
    db.commit()
    return case


def test_ra_input_rejects_raw_narrative_field(db, seed):
    """Schema-layer: even crafting a payload with raw_narrative fails."""
    with pytest.raises(ValidationError):
        RAInput.model_validate({
            "case_id": 1,
            "allegation_summary_authorised": "summary",
            "evidence_list_against_respondent": [],
            "procedural_history": {
                "filing_date": "2025-01-01",
                "conciliation_attempted": False,
                "hearings_held": 0,
                "notices_issued": 0,
            },
            "raw_narrative": "...complainant's words...",
        })


def test_adversarial_ra_strategy_output_redacted(adv_llm, db, seed):
    """Adversarial LLM emits counter-allegation language and a credibility
    score. Output filter must redact the legal-strategy + credibility hits."""
    adv_llm.next_text = json.dumps({
        "response": {
            "account_of_events":
                "The complainant's account is not credible. I recommend a "
                "counter-allegation against them and we should attack their "
                "credibility at the hearing.",
            "evidence_to_submit": [],
            "witnesses_proposed": [],
            "procedural_objections": [],
        },
    })
    case = _case_with_narrative(db, seed)
    out = run_ra(
        db,
        payload=RAInput(
            case_id=case.id,
            allegation_summary_authorised="The respondent allegedly made unwelcome remarks.",
            evidence_list_against_respondent=[],
            procedural_history=ProceduralHistory(
                filing_date=str(date.today()), conciliation_attempted=False,
                hearings_held=0, notices_issued=0,
            ),
        ),
        actor=seed["users"]["respondent"],
        entity_id=seed["entity"].id,
    )

    # The persisted draft must NOT contain raw legal-strategy language.
    doc = db.get(m.CaseDocument, out.response_draft_id)
    content = (doc.content_blob or b"").decode("utf-8", errors="replace")
    lowered = content.lower()
    for forbidden in (
        "counter-allegation", "counter allegation",
        "attack their credibility", "attack the complainant",
        "not credible",
    ):
        assert forbidden not in lowered or "[redacted:" in lowered, (
            f"forbidden phrase leaked through filter: {forbidden!r}"
        )


def test_respondent_chat_context_never_loads_raw_narrative(db, seed):
    """Loader-layer check: build_context for role=respondent must return a
    string with no narrative content even when the case has a raw narrative."""
    from fastapi_app.routes.chat import _build_context
    case = _case_with_narrative(db, seed)
    ctx = _build_context(
        db, role="respondent",
        user=seed["users"]["respondent"],
        case_id=case.id,
    )
    assert "morning of 2025-12-15" not in ctx
    assert "unwelcome remarks about my appearance" not in ctx
    # And the marker we check at the endpoint also isn't smuggled.
    assert "complaint_narrative_raw" not in ctx
    assert "raw_narrative_excerpt" not in ctx


def test_complainant_chat_context_does_include_own_narrative(db, seed):
    """Sanity counter-check: the complainant CAN see their own narrative —
    the wall is one-directional."""
    from fastapi_app.routes.chat import _build_context
    case = _case_with_narrative(db, seed)
    ctx = _build_context(
        db, role="complainant",
        user=seed["users"]["complainant"],
        case_id=case.id,
    )
    assert "morning of 2025-12-15" in ctx
