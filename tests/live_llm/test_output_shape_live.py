"""Live-LLM output-shape probes.

Run the structured AI components (CA, IA Phase 1, IA Phase 2) against the
real model and verify the JSON they return parses and validates through the
declared Pydantic schemas. This is the most fragile contract in the POC —
JSON-shape compliance depends entirely on the LLM following the format
instructions in the prompt.
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy.orm import Session

from fastapi_app.ai_components.ca.runner import run_ca
from fastapi_app.ai_components.ia.runner import run_ia_phase1, run_ia_phase2
from fastapi_app.db import models as m
from fastapi_app.db.session import get_session_factory
from fastapi_app.schemas.ai_io import (
    CAInput,
    ComplainantMetadata,
    EntityConfig,
    EvidenceItem,
    IAPhase1Input,
    IAPhase2Input,
    RecordPage,
)
from fastapi_app.state_machine import create_case_with_gates


SYNTHETIC_NARRATIVE = (
    "On the morning of 2025-12-15, around 11:00, my colleague Mr. X stopped me "
    "in the 4th floor pantry. He made repeated comments about my appearance that "
    "I found unwelcome and asked me out despite my clearly stating I was not "
    "interested. The pattern continued in messages he sent later that day. "
    "Two coworkers were standing nearby during the pantry incident."
)

SYNTHETIC_RECORD = [
    RecordPage(page_no=1, text=(
        "Complainant statement (oral, transcribed): The complainant states "
        "that on 2025-12-15 at approximately 11:00 in the 4th-floor pantry, "
        "the respondent made repeated remarks about her appearance and "
        "persisted in asking her out after she declined."
    )),
    RecordPage(page_no=2, text=(
        "Respondent statement: The respondent denies the alleged remarks and "
        "states the pantry conversation was a brief greeting."
    )),
    RecordPage(page_no=3, text=(
        "Witness statement (Ms. Y): I was at the coffee machine and overheard "
        "the respondent ask the complainant out on a date. I did not hear the "
        "complainant's response."
    )),
    RecordPage(page_no=4, text=(
        "Exhibit 1: WhatsApp message transcript from respondent's phone, "
        "2025-12-15 14:22 IST. (Digital evidence.)"
    )),
]


def _make_case(seed_actor_email: str = "complainant@demo.local") -> tuple[int, int, int]:
    """Create a case via the seeded demo users; return (case_id, actor_id, entity_id).

    Primitive IDs only — returning ORM instances across a closed session
    raises DetachedInstanceError when the caller opens a fresh session.
    """
    factory = get_session_factory()
    with factory() as db:
        user = db.query(m.User).filter(m.User.email == seed_actor_email).one()
        respondent = db.query(m.User).filter(m.User.email == "respondent@demo.local").one()
        case, _ = create_case_with_gates(
            db,
            actor=user,
            complainant=user,
            respondent=respondent,
            entity_id=user.entity_id,
            incident_date=date.today(),
        )
        db.commit()
        return case.id, user.id, user.entity_id


def _db() -> Session:
    return get_session_factory()()


def test_ca_returns_structured_complaint_or_falls_back_cleanly():
    """CA always persists a draft document even if the LLM returns malformed
    JSON (the runner falls back to a wrapped raw_llm_text)."""
    case_id, user_id, entity_id = _make_case()
    with _db() as db:
        user = db.get(m.User, user_id)
        payload = CAInput(
            case_id=case_id,
            raw_narrative=SYNTHETIC_NARRATIVE,
            complainant_metadata=ComplainantMetadata(
                age_band="25_34", employment_status="employee",
                workplace_location_type="office",
            ),
            entity_config=EntityConfig(jurisdiction="IN-TN", gender_scope="inclusive"),
        )
        out = run_ca(db, payload=payload, actor=user, entity_id=entity_id)
        assert out.structured_complaint_draft_id > 0
        # The persisted draft is non-empty.
        doc = db.get(m.CaseDocument, out.structured_complaint_draft_id)
        assert doc is not None and doc.content_blob
        # The verbatim raw narrative is preserved as a separate doc.
        raw_doc = db.query(m.CaseDocument).filter(
            m.CaseDocument.case_id == case_id,
            m.CaseDocument.doc_type == "complaint_narrative_raw",
        ).first()
        assert raw_doc is not None
        assert SYNTHETIC_NARRATIVE.encode() == raw_doc.content_blob


def test_ia_phase1_emits_structured_evidence_items():
    """Phase 1 enumeration must produce a list of EvidenceItem-shaped objects."""
    case_id, _, entity_id = _make_case("po@demo.local")
    with _db() as db:
        po = db.query(m.User).filter(m.User.email == "po@demo.local").one()
        payload = IAPhase1Input(
            case_id=case_id, record=SYNTHETIC_RECORD, continuation_from_id=0,
        )
        out = run_ia_phase1(db, payload=payload, actor=po, entity_id=entity_id)

        # The runner already validates via EvidenceItem(**ei). We assert it
        # produced at least one item, and round-trips the model again.
        assert len(out.evidence_items) >= 1
        for ei in out.evidence_items:
            # Re-validate to catch any drift in field names.
            EvidenceItem.model_validate(ei.model_dump())
        # Persisted draft exists.
        draft = db.query(m.CaseDocument).filter(
            m.CaseDocument.case_id == case_id,
            m.CaseDocument.doc_type == "ia_phase1_evidence_list",
        ).order_by(m.CaseDocument.created_at.desc()).first()
        assert draft is not None and draft.content_blob
        parsed = json.loads(draft.content_blob.decode())
        assert "evidence_items" in parsed
        assert isinstance(parsed["evidence_items"], list)


def test_ia_phase2_synthesises_from_structured_list_only():
    """Phase 2 receives ONLY the evidence list (no raw record). Verify the
    response carries timeline + contradiction_table + summary fields."""
    case_id, _, entity_id = _make_case("po@demo.local")
    with _db() as db:
        po = db.query(m.User).filter(m.User.email == "po@demo.local").one()
        evidence_items = [
            EvidenceItem(id=1, source="complainant_statement",
                         claim="Conduct occurred on 2025-12-15 in the pantry",
                         locator="p.1", digital_evidence_flag=False),
            EvidenceItem(id=2, source="respondent_statement",
                         claim="Conduct is denied",
                         locator="p.2", digital_evidence_flag=False),
            EvidenceItem(id=3, source="witness_statement",
                         claim="Witness overheard the respondent ask the complainant out",
                         locator="p.3", digital_evidence_flag=False),
            EvidenceItem(id=4, source="digital_evidence",
                         claim="WhatsApp transcript from respondent's phone",
                         locator="p.4", digital_evidence_flag=True),
        ]
        out = run_ia_phase2(
            db,
            payload=IAPhase2Input(
                case_id=case_id, evidence_items=evidence_items,
                section_3_2_circumstances=[
                    "implied_promise_preferential", "implied_threat_detrimental",
                    "implied_threat_employment", "hostile_environment",
                    "humiliating_treatment_health_safety",
                ],
            ),
            actor=po, entity_id=entity_id,
        )

        assert out.neutral_factual_summary.strip(), "summary must be non-empty"
        # Timeline + contradiction table can be empty in degenerate cases; the
        # contract is that they're present, typed, and not absent.
        assert isinstance(out.timeline, list)
        assert isinstance(out.contradiction_table, list)
        # Digital evidence id=4 should be flagged for Section 63 review.
        assert 4 in out.bsa_section_63_review_required or out.stubbed, (
            f"expected id 4 in bsa_section_63_review_required, "
            f"got {out.bsa_section_63_review_required}"
        )


def test_ia_phase2_does_not_emit_credibility_in_structured_fields():
    """Even with a long Phase 2 reply, the typed output must not carry
    credibility/outcome fields (Pydantic schema enforces it; we just confirm)."""
    case_id, _, entity_id = _make_case("po@demo.local")
    with _db() as db:
        po = db.query(m.User).filter(m.User.email == "po@demo.local").one()
        out = run_ia_phase2(
            db,
            payload=IAPhase2Input(
                case_id=case_id,
                evidence_items=[EvidenceItem(
                    id=1, source="complainant_statement",
                    claim="Alleged conduct occurred", locator="p.1",
                )],
                section_3_2_circumstances=[],
            ),
            actor=po, entity_id=entity_id,
        )
        dumped = out.model_dump()
        for forbidden in ("credibility_score", "guilty", "outcome",
                           "outcome_recommended", "confidence"):
            assert forbidden not in dumped, f"{forbidden} leaked into structured output"
