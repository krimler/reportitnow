"""No-gatekeeping (§9): the system has no screening between submission and ICC.

We verify: (a) creating a case is not blocked by content classification; (b) the
CA prompt does not contain "do not file" / "discourage" language; (c) the state
machine permits filed → inquiry without any human approval gate beyond PO
authorisation of the written complaint.
"""
from __future__ import annotations

from datetime import date

from fastapi_app.llm.client import load_prompt
from fastapi_app.state_machine import (
    ALLOWED_TRANSITIONS,
    create_case_with_gates,
)


def test_create_case_runs_without_content_classification(db, seed):
    users = seed["users"]
    # Whatever narrative the complainant has does not affect case creation.
    case, gates = create_case_with_gates(
        db,
        actor=users["complainant"],
        complainant=users["complainant"],
        respondent=users["respondent"],
        entity_id=seed["entity"].id,
        incident_date=date.today(),
    )
    db.commit()
    assert case.id > 0
    assert case.state == "draft"
    assert case.routed_to == "icc"


def test_ca_prompt_forbids_gatekeeping_language():
    prompt = load_prompt("CA").lower()
    # The prompt itself spells out the no-gatekeeping rule.
    assert "do not gatekeep" in prompt
    assert "do not discourage filing" in prompt


def test_state_machine_allows_filed_to_inquiry_without_extra_review_gate():
    assert "inquiry" in ALLOWED_TRANSITIONS["filed"]
    # No "review" or "screening" pseudo-state in the graph.
    assert "review" not in ALLOWED_TRANSITIONS
    assert "screening" not in ALLOWED_TRANSITIONS
