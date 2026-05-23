"""Rule 7(7): hearings require ≥3 ICC members including the Presiding Officer."""
from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from fastapi_app.routes.hearings import _check_quorum, HearingMember
from fastapi_app.db import models as m

import pytest
from fastapi import HTTPException


def _hm(uid: int, role: str, present: bool = True) -> HearingMember:
    return HearingMember(user_id=uid, role_at_hearing=role, present=present)


def test_quorum_under_three_rejected(db, seed):
    case = m.Case(
        entity_id=seed["entity"].id,
        committee_id=seed["icc"].id,
        state="inquiry",
        routed_to="icc",
        incident_date=datetime.utcnow().date(),
        created_at=datetime.utcnow(),
    )
    db.add(case)
    db.flush()
    with pytest.raises(HTTPException) as exc:
        _check_quorum(db, case, [
            _hm(seed["users"]["po"].id,   "presiding_officer"),
            _hm(seed["users"]["icc1"].id, "icc_member"),
        ])
    assert exc.value.status_code == 422
    assert "quorum not met" in exc.value.detail.lower()


def test_quorum_without_po_rejected(db, seed):
    case = m.Case(
        entity_id=seed["entity"].id, committee_id=seed["icc"].id, state="inquiry",
        routed_to="icc", incident_date=datetime.utcnow().date(),
        created_at=datetime.utcnow(),
    )
    db.add(case)
    db.flush()
    with pytest.raises(HTTPException) as exc:
        _check_quorum(db, case, [
            _hm(seed["users"]["icc1"].id, "icc_member"),
            _hm(seed["users"]["icc2"].id, "icc_member"),
            _hm(seed["users"]["ngo"].id,  "icc_member"),
        ])
    assert exc.value.status_code == 422
    assert "presiding officer" in exc.value.detail.lower()


def test_quorum_three_with_po_accepted(db, seed):
    case = m.Case(
        entity_id=seed["entity"].id, committee_id=seed["icc"].id, state="inquiry",
        routed_to="icc", incident_date=datetime.utcnow().date(),
        created_at=datetime.utcnow(),
    )
    db.add(case)
    db.flush()
    # Should NOT raise.
    _check_quorum(db, case, [
        _hm(seed["users"]["po"].id,   "presiding_officer"),
        _hm(seed["users"]["icc1"].id, "icc_member"),
        _hm(seed["users"]["icc2"].id, "icc_member"),
    ])
