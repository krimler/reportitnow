"""Shared pytest fixtures.

We point the suite at an in-memory SQLite database, force LLM stub mode, and
seed a fresh entity + roster of users per test session.
"""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest


@pytest.fixture(autouse=True, scope="session")
def _env_setup():
    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "test.db"
    # We use the namespaced var name (DATABASE_URL is reserved by Chainlit
    # for its Postgres data layer). Settings.database_url reads this alias.
    os.environ["REPORTITNOW_DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ.pop("DATABASE_URL", None)
    os.environ["LLM_STUB_MODE"] = "1"
    os.environ["AUDIT_CHAIN_HMAC_KEY"] = "test-hmac-key-fixed-for-determinism"
    os.environ["DP_WORKFORCE_FLOOR"] = "50"

    # Reset cached settings + engine after env override.
    from fastapi_app import config as cfg
    cfg._settings = None
    from fastapi_app.db.session import reset_engine_for_tests
    reset_engine_for_tests()
    yield
    tmp.cleanup()


@pytest.fixture
def db():
    from fastapi_app.db.session import get_session_factory, init_db
    init_db()
    factory = get_session_factory()
    with factory() as session:
        yield session


@pytest.fixture
def seed(db):
    """Seeds (and returns) an entity, ICC, and a roster of users."""
    from argon2 import PasswordHasher
    from fastapi_app.db import models as m

    today = date.today()
    ph = PasswordHasher()

    entity = m.Entity(
        name="TestCo", jurisdiction="IN-TN", gender_scope="inclusive",
        workforce_size=500, employer_type="private", created_at=datetime.utcnow(),
    )
    db.add(entity)
    db.flush()

    icc = m.IccCommittee(
        entity_id=entity.id,
        constituted_on=today - timedelta(days=180),
        tenure_expires_on=today + timedelta(days=365 * 3 - 180),
    )
    db.add(icc)
    db.flush()

    def _u(email: str, role: str, dob_off_yrs: int = -30, is_minor: bool = False):
        user = m.User(
            entity_id=entity.id,
            subject_id=email, email=email,
            display_name=email.split("@")[0],
            password_hash=ph.hash("demo"),
            date_of_birth=date(today.year + dob_off_yrs, today.month, today.day),
            is_minor=is_minor,
            created_at=datetime.utcnow(),
        )
        db.add(user)
        db.flush()
        db.add(m.RoleAssignment(
            user_id=user.id, role=role, case_id=None, valid_from=today,
        ))
        db.flush()
        return user

    users = {
        "complainant":    _u("c@t.local",  "complainant"),
        "respondent":     _u("r@t.local",  "respondent"),
        "po":             _u("po@t.local", "presiding_officer"),
        "icc1":           _u("i1@t.local", "icc_member"),
        "icc2":           _u("i2@t.local", "icc_member"),
        "ngo":            _u("n@t.local",  "icc_member"),
        "employer":       _u("e@t.local",  "employer"),
        "compliance":     _u("ca@t.local", "compliance_admin"),
        "auditor":        _u("au@t.local", "auditor"),
        "do":             _u("do@t.local", "district_officer"),
        "minor":          _u("m@t.local",  "complainant", dob_off_yrs=-16, is_minor=True),
    }

    # Wire ICC members
    for nm, member_type in [
        ("po", "presiding_officer"),
        ("icc1", "internal_woman"),
        ("icc2", "internal_woman"),
        ("ngo", "external_ngo"),
    ]:
        db.add(m.IccMember(
            committee_id=icc.id, user_id=users[nm].id,
            member_type=member_type, nominated_on=icc.constituted_on,
        ))
    db.commit()
    return {"entity": entity, "icc": icc, "users": users}
