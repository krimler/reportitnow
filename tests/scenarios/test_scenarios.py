"""Parametrised runner for the YAML walkthroughs in this directory."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from fastapi_app.audit_chain import sha256_hex
from fastapi_app.db import models as m
from fastapi_app.state_machine import (
    StateTransitionBlocked,
    create_case_with_gates,
    transition,
)

SCENARIO_DIR = Path(__file__).parent


def _scenario_files() -> list[Path]:
    return sorted(SCENARIO_DIR.glob("*.yaml"))


@pytest.fixture
def cross_org_respondent(db, seed):
    """A user belonging to a *different* entity, used in the cross-org scenario."""
    other_entity = m.Entity(
        name="OtherCo", jurisdiction="IN-KA", gender_scope="inclusive",
        workforce_size=100, employer_type="private", created_at=datetime.utcnow(),
    )
    db.add(other_entity)
    db.flush()
    u = m.User(
        entity_id=other_entity.id, subject_id="x-org@other",
        email="x-org@other", display_name="Cross-Org Respondent",
        password_hash=None, date_of_birth=date(1990, 1, 1),
        is_minor=False, created_at=datetime.utcnow(),
    )
    db.add(u)
    db.commit()
    return u


def _authorise_complaint_then_file(db, seed, case, po):
    """Persist a written complaint, authorise it, then transition draft→filed."""
    doc = m.CaseDocument(
        case_id=case.id, doc_type="complaint_written",
        content_blob=b"[complaint]", content_hash=sha256_hex(b"[complaint]"),
        ai_component="CA", is_draft=False,
        authorised_by_user_id=po.id, authorised_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
    )
    db.add(doc)
    db.commit()
    transition(
        db, case_id=case.id, target="filed",
        actor=po, reason="written complaint authorised",
        payload={"written_complaint_doc_id": doc.id},
    )
    db.commit()


def _hearing_no_show(db, seed, case, *, side: str, notice: bool):
    """Record one hearing with the named side absent."""
    prev = db.query(m.Hearing).filter(m.Hearing.case_id == case.id).order_by(
        m.Hearing.scheduled_for.desc()
    ).first()
    prev_c = prev.consecutive_no_shows_complainant if prev else 0
    prev_r = prev.consecutive_no_shows_respondent if prev else 0
    cur_c_absent = side == "complainant"
    cur_r_absent = side == "respondent"

    h = m.Hearing(
        case_id=case.id,
        scheduled_for=datetime.utcnow() + timedelta(days=1),
        held_on=datetime.utcnow(),
        quorum_met=True,
        complainant_present=not cur_c_absent,
        respondent_present=not cur_r_absent,
        notice_issued_on=date.today() if notice else None,
        notice_period_days=15,
        consecutive_no_shows_complainant=(prev_c + 1 if cur_c_absent else 0),
        consecutive_no_shows_respondent=(prev_r + 1 if cur_r_absent else 0),
    )
    if h.consecutive_no_shows_respondent >= 3 and notice:
        h.is_ex_parte = True
    db.add(h)
    db.commit()


def _refresh_case(db, case):
    db.refresh(case)
    return case


def _run_scenario(scenario: dict, db, seed, cross_org_respondent):
    users = seed["users"]
    case = None

    for step in scenario["steps"]:
        if "action" in step:
            action = step["action"]
            if action == "create_case":
                case, _ = create_case_with_gates(
                    db,
                    actor=users[step.get("complainant", "complainant")],
                    complainant=users[step.get("complainant", "complainant")],
                    respondent=users[step["respondent"]],
                    entity_id=seed["entity"].id,
                    incident_date=date.today(),
                )
                db.commit()
            elif action == "create_case_cross_org":
                case, _ = create_case_with_gates(
                    db,
                    actor=users[step.get("complainant", "complainant")],
                    complainant=users[step.get("complainant", "complainant")],
                    respondent=cross_org_respondent,
                    entity_id=seed["entity"].id,
                    incident_date=date.today(),
                )
                db.commit()
            elif action == "try_transition":
                expect_block = step.get("expect_block", False)
                try:
                    transition(
                        db, case_id=case.id, target=step["target"],
                        actor=users[step["actor"]], reason="scenario",
                    )
                    db.commit()
                    if expect_block:
                        pytest.fail("expected StateTransitionBlocked but transition succeeded")
                except StateTransitionBlocked:
                    if not expect_block:
                        raise
            elif action == "authorise_complaint_then_file":
                _authorise_complaint_then_file(db, seed, case, users[step["actor"]])
            elif action == "transition":
                transition(
                    db, case_id=case.id, target=step["target"],
                    actor=users[step["actor"]], reason="scenario",
                    payload=step.get("payload", {}),
                )
                db.commit()
            elif action == "hearing_no_show":
                repeat = step.get("repeat", 1)
                for _ in range(repeat):
                    _hearing_no_show(db, seed, case,
                                     side=step["side"],
                                     notice=step.get("notice", True))
            elif action == "age_icc_committee":
                icc = seed["icc"]
                years = step.get("years_past_expiry", 1)
                icc.tenure_expires_on = date.today() - timedelta(days=365 * years)
                icc.defective_flag = True
                db.commit()
            elif action == "cpa_run":
                from fastapi_app.ai_components.cpa.runner import build_cpa_input, run_cpa
                today = datetime.utcnow()
                fy_start = today.year if today.month >= 4 else today.year - 1
                fy = f"{fy_start}-{str(fy_start + 1)[-2:]}"
                payload = build_cpa_input(
                    db, entity_id=seed["entity"].id, fiscal_year=fy,
                    training_sessions=4,
                )
                out = run_cpa(db, payload=payload, actor=users["compliance"],
                              entity_id=seed["entity"].id)
                # stash for assertions
                _run_scenario.last_cpa = out  # type: ignore[attr-defined]
            else:
                raise ValueError(f"unknown action: {action}")
        elif "assert" in step:
            spec = step["assert"]
            if case is not None:
                case = _refresh_case(db, case)
            if "state" in spec:
                assert case.state == spec["state"], f"expected {spec['state']}, got {case.state}"
            if "routed_to" in spec:
                assert case.routed_to == spec["routed_to"]
            if "minor_complainant" in spec:
                assert bool(case.minor_complainant) == spec["minor_complainant"]
            if "cross_organisational" in spec:
                assert bool(case.cross_organisational) == spec["cross_organisational"]
            if "respondent_is_employer" in spec:
                assert bool(case.respondent_is_employer) == spec["respondent_is_employer"]
            if "closure_reason" in spec:
                assert case.closure_reason == spec["closure_reason"]
            if "consecutive_no_shows_complainant" in spec:
                last = db.query(m.Hearing).filter(m.Hearing.case_id == case.id).order_by(
                    m.Hearing.scheduled_for.desc()).first()
                assert last and last.consecutive_no_shows_complainant == spec["consecutive_no_shows_complainant"]
            if "consecutive_no_shows_respondent" in spec:
                last = db.query(m.Hearing).filter(m.Hearing.case_id == case.id).order_by(
                    m.Hearing.scheduled_for.desc()).first()
                assert last and last.consecutive_no_shows_respondent == spec["consecutive_no_shows_respondent"]
            if "is_ex_parte" in spec:
                last = db.query(m.Hearing).filter(m.Hearing.case_id == case.id).order_by(
                    m.Hearing.scheduled_for.desc()).first()
                assert last and bool(last.is_ex_parte) == spec["is_ex_parte"]
            if "icc_defective" in spec:
                db.refresh(seed["icc"])
                assert bool(seed["icc"].defective_flag) == spec["icc_defective"]
            if "section_21_nil" in spec:
                last_cpa = getattr(_run_scenario, "last_cpa", None)
                assert last_cpa is not None, "cpa_run must be invoked before assert"
                assert last_cpa.section_21_report.nil_report == spec["section_21_nil"]


@pytest.mark.parametrize("scenario_path", _scenario_files(), ids=lambda p: p.stem)
def test_scenario(scenario_path, db, seed, cross_org_respondent):
    scenario = yaml.safe_load(scenario_path.read_text())
    _run_scenario(scenario, db, seed, cross_org_respondent)
