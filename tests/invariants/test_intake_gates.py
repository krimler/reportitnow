"""§5.2 intake gates: minor → POCSO, employer-respondent → LCC."""
from __future__ import annotations

from datetime import date

import pytest

from fastapi_app.state_machine import (
    StateTransitionBlocked,
    apply_intake_gates,
    create_case_with_gates,
    transition,
)


def test_minor_complainant_gate_routes_to_pocso(db, seed):
    users = seed["users"]
    case, gates = create_case_with_gates(
        db,
        actor=users["minor"],
        complainant=users["minor"],
        respondent=users["respondent"],
        entity_id=seed["entity"].id,
        incident_date=date.today(),
    )
    db.commit()
    assert gates.routed_to == "pocso_police"
    assert gates.minor_complainant is True
    assert case.state == "terminated"
    assert case.closure_reason == "POCSO_REPORTING_REQUIRED"


def test_employer_respondent_gate_routes_to_lcc(db, seed):
    """When the respondent has the 'employer' role, route to LCC, not ICC."""
    users = seed["users"]
    case, gates = create_case_with_gates(
        db,
        actor=users["complainant"],
        complainant=users["complainant"],
        respondent=users["employer"],
        entity_id=seed["entity"].id,
        incident_date=date.today(),
    )
    db.commit()
    assert gates.routed_to == "lcc"
    assert gates.employer_respondent is True
    assert case.respondent_is_employer is True


def test_standard_case_routes_to_icc(db, seed):
    users = seed["users"]
    case, gates = create_case_with_gates(
        db,
        actor=users["complainant"],
        complainant=users["complainant"],
        respondent=users["respondent"],
        entity_id=seed["entity"].id,
        incident_date=date.today(),
    )
    db.commit()
    assert gates.routed_to == "icc"
    assert case.state == "draft"


def test_pocso_routed_case_blocks_transitions(db, seed):
    """POCSO-routed cases cannot run through the standard ICC state machine."""
    users = seed["users"]
    case, gates = create_case_with_gates(
        db,
        actor=users["minor"],
        complainant=users["minor"],
        respondent=users["respondent"],
        entity_id=seed["entity"].id,
        incident_date=date.today(),
    )
    db.commit()
    with pytest.raises(StateTransitionBlocked):
        transition(db, case_id=case.id, target="filed", actor=users["po"], reason="x")
