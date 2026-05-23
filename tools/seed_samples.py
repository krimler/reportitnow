"""Seed a fixture case so the chat samples have real context to ground on.

Idempotent: re-running drops the prior fixture and rebuilds it. Touches the
live data/reportitnow.db, so stop the stack or run before ./run.sh up if you
want the new state to be visible to the running services.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Allow running from repo root via `python -m tools.seed_samples`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, select  # noqa: E402

from fastapi_app.audit_chain import append_entry, sha256_hex  # noqa: E402
from fastapi_app.db import models as m  # noqa: E402
from fastapi_app.db.session import get_session_factory, init_db  # noqa: E402
from fastapi_app.state_machine import create_case_with_gates, transition  # noqa: E402


NARRATIVE = (
    "On Monday 8 December 2025 around 11:15 in the 4th floor pantry, "
    "Mr. R made repeated unwelcome comments about my appearance. When I "
    "tried to leave, he stood between me and the door for several seconds. "
    "He has continued sending me messages on the company chat that I have "
    "asked him to stop sending. Ms. K and Ms. P were at the coffee machine "
    "and saw part of what happened."
)

ALLEGATION_SUMMARY = (
    "On 8 December 2025 the respondent is alleged to have made repeated "
    "unwelcome comments to the complainant in a workplace common area and "
    "to have continued unwelcome contact through the company chat after "
    "being asked to stop. Two co-workers are named as having witnessed "
    "part of the in-person incident."
)

EVIDENCE_ITEMS = [
    {"id": 1, "source": "complainant_statement",
     "claim": "Repeated unwelcome remarks in the pantry on 2025-12-08",
     "locator": "p.1 para.1", "digital_evidence_flag": False},
    {"id": 2, "source": "respondent_statement",
     "claim": "Pantry conversation was a brief greeting; messages were work-related",
     "locator": "p.3 para.2", "digital_evidence_flag": False},
    {"id": 3, "source": "witness_statement",
     "claim": "Witness K overheard part of the pantry exchange",
     "locator": "p.5 para.1", "digital_evidence_flag": False},
    {"id": 4, "source": "digital_evidence",
     "claim": "Company-chat message log between the parties 2025-12-08 to 2025-12-12",
     "locator": "exhibit 1", "digital_evidence_flag": True},
]


def _doc(case_id: int, doc_type: str, content: str | dict, *, draft: bool,
         authorised_by: int | None, ai_component: str | None) -> m.CaseDocument:
    blob = (content if isinstance(content, str)
            else json.dumps(content, sort_keys=True)).encode("utf-8")
    return m.CaseDocument(
        case_id=case_id,
        doc_type=doc_type,
        content_blob=blob,
        content_hash=sha256_hex(blob),
        ai_component=ai_component,
        is_draft=draft,
        authorised_by_user_id=authorised_by,
        authorised_at=datetime.utcnow() if not draft else None,
        created_at=datetime.utcnow(),
    )


def _wipe_existing_fixture(db) -> None:
    """If a previous fixture run left state behind, clear it cleanly so the
    audit chain stays consistent for the rebuild."""
    cases = db.execute(select(m.Case)).scalars().all()
    if not cases:
        return
    case_ids = [c.id for c in cases]
    # Order matters: child rows first.
    db.execute(delete(m.HearingAttendance).where(
        m.HearingAttendance.hearing_id.in_(
            db.execute(select(m.Hearing.id).where(m.Hearing.case_id.in_(case_ids))).scalars().all()
        )
    ))
    db.execute(delete(m.Hearing).where(m.Hearing.case_id.in_(case_ids)))
    db.execute(delete(m.CaseDocument).where(m.CaseDocument.case_id.in_(case_ids)))
    db.execute(delete(m.CaseEvent).where(m.CaseEvent.case_id.in_(case_ids)))
    db.execute(delete(m.AuditLog).where(m.AuditLog.case_id.in_(case_ids)))
    db.execute(delete(m.ChatHistory))  # also clear any chat history from prior runs
    db.execute(delete(m.Case).where(m.Case.id.in_(case_ids)))
    db.commit()


def seed() -> int:
    init_db()
    factory = get_session_factory()
    with factory() as db:
        _wipe_existing_fixture(db)

        complainant = db.scalar(select(m.User).where(m.User.email == "complainant@demo.local"))
        respondent  = db.scalar(select(m.User).where(m.User.email == "respondent@demo.local"))
        po          = db.scalar(select(m.User).where(m.User.email == "po@demo.local"))
        icc1        = db.scalar(select(m.User).where(m.User.email == "icc1@demo.local"))
        icc2        = db.scalar(select(m.User).where(m.User.email == "icc2@demo.local"))
        if not all([complainant, respondent, po, icc1, icc2]):
            print("Missing demo users — run `python -m fastapi_app.db.bootstrap` first.")
            sys.exit(1)

        incident_day = date.today() - timedelta(days=20)
        case, _ = create_case_with_gates(
            db,
            actor=complainant,
            complainant=complainant,
            respondent=respondent,
            entity_id=complainant.entity_id,
            incident_date=incident_day,
        )
        db.commit()

        db.add(_doc(case.id, "complaint_narrative_raw", NARRATIVE,
                    draft=False, authorised_by=None, ai_component=None))
        db.flush()

        written = _doc(case.id, "complaint_written",
                       {"structured_complaint": NARRATIVE}, draft=False,
                       authorised_by=po.id, ai_component="CA")
        db.add(written); db.flush()

        db.add(_doc(case.id, "allegation_summary_authorised", ALLEGATION_SUMMARY,
                    draft=False, authorised_by=po.id, ai_component=None))
        db.flush()

        db.add(_doc(case.id, "ia_phase1_evidence_list",
                    {"evidence_items": EVIDENCE_ITEMS}, draft=False,
                    authorised_by=po.id, ai_component="IA"))
        db.commit()

        transition(db, case_id=case.id, target="filed",
                   actor=po, reason="PO authorised written complaint",
                   payload={"written_complaint_doc_id": written.id})
        db.commit()

        transition(db, case_id=case.id, target="inquiry",
                   actor=po, reason="No conciliation requested")
        db.commit()

        h1 = m.Hearing(
            case_id=case.id,
            scheduled_for=datetime.utcnow() - timedelta(days=8),
            held_on=datetime.utcnow() - timedelta(days=8),
            quorum_met=True,
            complainant_present=True, respondent_present=True,
            notice_issued_on=date.today() - timedelta(days=14),
            notice_period_days=15,
        )
        db.add(h1); db.flush()
        for u, role in [(po, "presiding_officer"), (icc1, "icc_member"), (icc2, "icc_member"),
                         (complainant, "complainant"), (respondent, "respondent")]:
            db.add(m.HearingAttendance(hearing_id=h1.id, user_id=u.id,
                                       role_at_hearing=role,
                                       present=(role != "respondent" or True)))

        h2 = m.Hearing(
            case_id=case.id,
            scheduled_for=datetime.utcnow() - timedelta(days=2),
            held_on=datetime.utcnow() - timedelta(days=2),
            quorum_met=True,
            complainant_present=True, respondent_present=False,
            notice_issued_on=date.today() - timedelta(days=17),
            notice_period_days=15,
            consecutive_no_shows_respondent=1,
        )
        db.add(h2); db.flush()
        for u, role, present in [
            (po, "presiding_officer", True), (icc1, "icc_member", True),
            (icc2, "icc_member", True), (complainant, "complainant", True),
            (respondent, "respondent", False),
        ]:
            db.add(m.HearingAttendance(hearing_id=h2.id, user_id=u.id,
                                       role_at_hearing=role, present=present))

        append_entry(db, entity_id=case.entity_id, component_id="state_machine",
                     actor_user_id=po.id, case_id=case.id,
                     input_payload={"action": "schedule_hearing", "hearing_id": h1.id},
                     output_payload={"quorum_met": True})
        append_entry(db, entity_id=case.entity_id, component_id="state_machine",
                     actor_user_id=po.id, case_id=case.id,
                     input_payload={"action": "schedule_hearing", "hearing_id": h2.id},
                     output_payload={"respondent_present": False,
                                     "consecutive_no_shows_respondent": 1})
        db.commit()

        print(f"Seeded fixture case_id={case.id} in state '{case.state}' for {complainant.email}")
        return case.id


if __name__ == "__main__":
    seed()
