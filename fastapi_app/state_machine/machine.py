"""POSH case state machine.

Each transition: state-allowed → role-allowed → invariant → event row →
audit-log row → state update. One SQL transaction; any failure rolls the
whole thing back.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi_app.audit_chain import append_entry
from fastapi_app.db import models as m
from fastapi_app.deadline_engine import compute_all_deadlines



STATES = [
    "draft",
    "filed",
    "conciliation",
    "conciliation_breached",
    "inquiry",
    "report",
    "employer_action",
    "appeal",
    "closed",
    "terminated",
]

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"filed", "terminated"},
    "filed": {"conciliation", "inquiry", "terminated"},
    "conciliation": {"closed", "conciliation_breached", "inquiry", "terminated"},
    "conciliation_breached": {"inquiry", "terminated"},
    "inquiry": {"report", "terminated"},
    "report": {"employer_action", "terminated"},
    "employer_action": {"closed", "appeal", "terminated"},
    "appeal": {"closed", "terminated"},
    "closed": set(),
    "terminated": set(),
}

# Roles permitted to initiate each target transition (case-scoped).
ROLE_PERMITTED: dict[str, set[str]] = {
    "filed":                {"complainant", "presiding_officer"},
    "conciliation":         {"complainant"},
    "conciliation_breached":{"presiding_officer", "icc_member"},
    "inquiry":              {"presiding_officer", "icc_member", "complainant"},
    "report":               {"presiding_officer", "icc_member"},
    "employer_action":      {"presiding_officer"},
    "closed":               {"presiding_officer", "employer"},
    "appeal":               {"complainant", "respondent"},
    "terminated":           {"presiding_officer", "complainant"},
}



class StateTransitionBlocked(Exception):
    """Raised when a transition is structurally forbidden (state or role)."""


class InvariantViolation(Exception):
    """Raised when transition-specific preconditions are not met."""



@dataclass
class IntakeGateResult:
    routed_to: str                  # 'icc' | 'lcc' | 'pocso_police'
    minor_complainant: bool
    employer_respondent: bool
    cross_organisational: bool
    blocked_reason: str | None      # e.g. POCSO_REPORTING_REQUIRED


def _is_minor(complainant: m.User | None, incident_date: date) -> bool:
    if complainant is None:
        return False
    if complainant.is_minor:
        return True
    if complainant.date_of_birth is None:
        return False
    age_at_incident = (
        incident_date.year - complainant.date_of_birth.year
        - ((incident_date.month, incident_date.day)
           < (complainant.date_of_birth.month, complainant.date_of_birth.day))
    )
    return age_at_incident < 18


def _has_role(db: Session, user_id: int, role: str) -> bool:
    return db.scalar(
        select(m.RoleAssignment).where(
            m.RoleAssignment.user_id == user_id,
            m.RoleAssignment.role == role,
            m.RoleAssignment.revoked_at.is_(None),
        )
    ) is not None


def apply_intake_gates(
    db: Session,
    *,
    complainant: m.User | None,
    respondent: m.User | None,
    incident_date: date,
    case_entity_id: int,
) -> IntakeGateResult:
    minor = _is_minor(complainant, incident_date)
    employer_resp = bool(
        respondent
        and (_has_role(db, respondent.id, "employer")
             or _has_role(db, respondent.id, "cmo_board"))
    )
    cross_org = bool(
        respondent
        and respondent.entity_id is not None
        and respondent.entity_id != case_entity_id
    )

    if minor:
        return IntakeGateResult(
            routed_to="pocso_police",
            minor_complainant=True,
            employer_respondent=employer_resp,
            cross_organisational=cross_org,
            blocked_reason="POCSO_REPORTING_REQUIRED",
        )
    if employer_resp:
        return IntakeGateResult(
            routed_to="lcc",
            minor_complainant=False,
            employer_respondent=True,
            cross_organisational=cross_org,
            blocked_reason=None,
        )
    return IntakeGateResult(
        routed_to="icc",
        minor_complainant=False,
        employer_respondent=False,
        cross_organisational=cross_org,
        blocked_reason=None,
    )


def create_case_with_gates(
    db: Session,
    *,
    actor: m.User,
    complainant: m.User | None,
    respondent: m.User | None,
    entity_id: int,
    incident_date: date,
    incident_continuing: bool = False,
) -> tuple[m.Case, IntakeGateResult]:
    # POCSO routes leave the case terminated with the reason in closure_reason;
    # the PO triggers the external mandatory-reporting path separately.
    gates = apply_intake_gates(
        db,
        complainant=complainant,
        respondent=respondent,
        incident_date=incident_date,
        case_entity_id=entity_id,
    )
    committee_id: int | None = None
    if gates.routed_to == "icc":
        icc = db.execute(
            select(m.IccCommittee)
            .where(m.IccCommittee.entity_id == entity_id,
                   m.IccCommittee.defective_flag.is_(False))
            .order_by(m.IccCommittee.constituted_on.desc()).limit(1)
        ).scalar()
        committee_id = icc.id if icc else None

    case = m.Case(
        entity_id=entity_id,
        committee_id=committee_id,
        state="terminated" if gates.routed_to == "pocso_police" else "draft",
        routed_to=gates.routed_to,
        complainant_user_id=complainant.id if complainant else None,
        respondent_user_id=respondent.id if respondent else None,
        respondent_is_employer=gates.employer_respondent,
        cross_organisational=gates.cross_organisational,
        minor_complainant=gates.minor_complainant,
        incident_date=incident_date,
        incident_continuing=incident_continuing,
        closure_reason=gates.blocked_reason,
        created_at=datetime.utcnow(),
    )
    db.add(case)
    db.flush()
    payload = {
        "case_id": case.id,
        "routed_to": gates.routed_to,
        "minor": gates.minor_complainant,
        "employer_resp": gates.employer_respondent,
        "cross_org": gates.cross_organisational,
        "blocked_reason": gates.blocked_reason,
    }
    db.add(m.CaseEvent(
        case_id=case.id,
        event_type="intake_gates_evaluated",
        event_payload_json=json.dumps(payload),
        actor_user_id=actor.id,
        occurred_at=datetime.utcnow(),
        audit_hash="",
    ))
    entry = append_entry(
        db,
        entity_id=entity_id,
        component_id="state_machine",
        actor_user_id=actor.id,
        case_id=case.id,
        input_payload={"intake": payload},
        output_payload={"state": case.state, "routed_to": case.routed_to},
    )
    # Backfill the audit hash onto the event row for forensic lookup.
    last_event = db.execute(
        select(m.CaseEvent).where(m.CaseEvent.case_id == case.id).order_by(m.CaseEvent.id.desc()).limit(1)
    ).scalar_one()
    last_event.audit_hash = entry.hash
    db.flush()
    return case, gates



def _filed_invariants(db: Session, case: m.Case, payload: dict[str, Any]) -> None:
    # A complaint must reference an authorised written-complaint document
    doc_id = payload.get("written_complaint_doc_id")
    if doc_id is None:
        raise InvariantViolation("filed requires written_complaint_doc_id in payload")
    doc = db.get(m.CaseDocument, doc_id)
    if doc is None or doc.case_id != case.id:
        raise InvariantViolation("written_complaint_doc_id does not match this case")
    if doc.is_draft:
        raise InvariantViolation("written complaint must be PO-authorised before filing")
    if doc.doc_type not in ("complaint_written", "complaint_written_via_oral_pathway"):
        raise InvariantViolation(f"doc_type {doc.doc_type!r} is not a valid filed complaint")
    # No-gatekeeping invariant: do not auto-screen content; PO authorisation
    # already covers procedural fitness.


def _conciliation_invariants(_db: Session, _case: m.Case, payload: dict[str, Any]) -> None:
    if not payload.get("complainant_requested"):
        raise InvariantViolation("conciliation requires complainant request (Section 10)")
    if payload.get("monetary_settlement"):
        raise InvariantViolation("Section 10 prohibits monetary settlement at conciliation")


def _conciliation_breached_invariants(_db: Session, _case: m.Case, payload: dict[str, Any]) -> None:
    if not payload.get("breach_reason"):
        raise InvariantViolation("conciliation_breached requires breach_reason")


def _inquiry_invariants(_db: Session, case: m.Case, _payload: dict[str, Any]) -> None:
    if case.state not in ("filed", "conciliation", "conciliation_breached"):
        raise InvariantViolation(
            f"cannot enter inquiry from {case.state}"
        )


def _report_invariants(db: Session, case: m.Case, payload: dict[str, Any]) -> None:
    # Report must not predate inquiry completion. We also expect a quorum-met
    # final hearing.
    final = db.execute(
        select(m.Hearing)
        .where(m.Hearing.case_id == case.id)
        .order_by(m.Hearing.scheduled_for.desc()).limit(1)
    ).scalar()
    if final is None:
        raise InvariantViolation("report requires at least one hearing on record")
    if final.quorum_met is False:
        raise InvariantViolation("final hearing lacked quorum (Rule 7(7))")


def _employer_action_invariants(db: Session, case: m.Case, payload: dict[str, Any]) -> None:
    # Report doc must exist and be authorised before employer action.
    if not db.scalar(
        select(m.CaseDocument).where(
            m.CaseDocument.case_id == case.id,
            m.CaseDocument.doc_type == "inquiry_report",
            m.CaseDocument.is_draft.is_(False),
        )
    ):
        raise InvariantViolation("employer_action requires an authorised inquiry_report")


def _appeal_invariants(_db: Session, _case: m.Case, payload: dict[str, Any]) -> None:
    if "appealing_party" not in payload:
        raise InvariantViolation("appeal requires 'appealing_party' in payload")


def _terminated_invariants(_db: Session, _case: m.Case, payload: dict[str, Any]) -> None:
    if not payload.get("termination_reason"):
        raise InvariantViolation("terminated requires termination_reason")


INVARIANT_CHECKS: dict[str, Callable[[Session, m.Case, dict[str, Any]], None]] = {
    "filed": _filed_invariants,
    "conciliation": _conciliation_invariants,
    "conciliation_breached": _conciliation_breached_invariants,
    "inquiry": _inquiry_invariants,
    "report": _report_invariants,
    "employer_action": _employer_action_invariants,
    "appeal": _appeal_invariants,
    "terminated": _terminated_invariants,
}



def _apply_transition_side_effects(
    db: Session, case: m.Case, target: str, now: datetime
) -> None:
    """Set timestamp + deadline fields based on the new state."""
    if target == "filed":
        case.filed_at = now
        deadlines = compute_all_deadlines(
            db, case.entity_id, filed_on=case.filed_at.date()
        )
        case.report_due_by = deadlines.inquiry_complete_by  # 90 days from filing
    elif target == "inquiry":
        case.inquiry_started_at = case.inquiry_started_at or now
    elif target == "report":
        deadlines = compute_all_deadlines(
            db, case.entity_id, inquiry_completed_on=now.date()
        )
        case.report_due_by = deadlines.report_by
        case.employer_action_due_by = deadlines.employer_action_by
        case.appeal_due_by = deadlines.appeal_by
    elif target in ("closed", "terminated"):
        case.closed_at = now



def transition(
    db: Session,
    *,
    case_id: int,
    target: str,
    actor: m.User,
    reason: str,
    payload: dict[str, Any] | None = None,
) -> m.Case:
    """Run a state transition. Caller commits on success or rolls back on raise."""
    payload = payload or {}
    case = db.get(m.Case, case_id)
    if case is None:
        raise StateTransitionBlocked(f"case {case_id} does not exist")
    if case.routed_to != "icc":
        # LCC- or POCSO-routed cases do not run through ICC state machine here.
        raise StateTransitionBlocked(
            f"case {case_id} is routed to {case.routed_to}; not ICC-scoped"
        )
    current = case.state
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise StateTransitionBlocked(
            f"{current} → {target} is not an allowed transition"
        )

    # Role check
    actor_roles = {
        row.role for row in db.execute(
            select(m.RoleAssignment).where(
                m.RoleAssignment.user_id == actor.id,
                m.RoleAssignment.revoked_at.is_(None),
            )
        ).scalars()
    }
    permitted = ROLE_PERMITTED.get(target, set())
    if not (actor_roles & permitted):
        raise StateTransitionBlocked(
            f"actor role(s) {actor_roles} cannot initiate {current} → {target}"
        )

    # Invariant check
    check = INVARIANT_CHECKS.get(target)
    if check:
        check(db, case, payload)

    now = datetime.utcnow()
    # Side effects: timestamps + deadlines
    _apply_transition_side_effects(db, case, target, now)
    case.state = target

    event_payload = {"from": current, "to": target, "reason": reason, **payload}
    event = m.CaseEvent(
        case_id=case.id,
        event_type="state_transition",
        event_payload_json=json.dumps(event_payload, default=str),
        actor_user_id=actor.id,
        occurred_at=now,
        audit_hash="",
    )
    db.add(event)
    db.flush()

    entry = append_entry(
        db,
        entity_id=case.entity_id,
        component_id="state_machine",
        actor_user_id=actor.id,
        case_id=case.id,
        input_payload={"from": current, "target": target, "payload": payload},
        output_payload={"state": case.state, "reason": reason},
        timestamp=now,
    )
    event.audit_hash = entry.hash
    db.flush()
    return case
