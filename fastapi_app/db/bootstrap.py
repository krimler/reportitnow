"""Seed the SQLite DB with a demo entity, ICC committee, users, and holidays.

Run once after installing:

    python -m fastapi_app.db.bootstrap

Idempotent: re-running will not duplicate seed rows (keyed by email/subject_id).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from argon2 import PasswordHasher
from sqlalchemy import select

from fastapi_app.db import models as m
from fastapi_app.db.session import get_session_factory, init_db

PH = PasswordHasher()

# (email, display_name, role, password, is_minor, dob_offset_years)
DEMO_USERS = [
    ("complainant@demo.local", "Asha Complainant", "complainant", "demo", False, -29),
    ("respondent@demo.local",  "Ravi Respondent",  "respondent",  "demo", False, -34),
    ("po@demo.local",          "Priya Presiding Officer", "presiding_officer", "demo", False, -47),
    ("icc1@demo.local",        "Internal Member 1", "icc_member", "demo", False, -41),
    ("icc2@demo.local",        "Internal Member 2", "icc_member", "demo", False, -38),
    ("ngo@demo.local",         "NGO External",      "icc_member", "demo", False, -52),
    ("employer@demo.local",    "HR Compliance",     "employer",   "demo", False, -45),
    ("compliance@demo.local",  "Compliance Admin",  "compliance_admin", "demo", False, -40),
    ("auditor@demo.local",     "Audit Reviewer",    "auditor",    "demo", False, -50),
    ("do@demo.local",          "District Officer",  "district_officer", "demo", False, -55),
    ("employee@demo.local",    "Generic Employee",  "employee",   "demo", False, -30),
    ("minor@demo.local",       "Minor User",        "complainant", "demo", True,  -16),
]


def seed() -> None:
    init_db()
    factory = get_session_factory()
    today = date.today()
    with factory() as db:
        # Entity
        entity = db.scalar(select(m.Entity).where(m.Entity.name == "Anonymised Pvt. Ltd."))
        if entity is None:
            entity = m.Entity(
                name="Anonymised Pvt. Ltd.",
                jurisdiction="IN-TN",
                gender_scope="inclusive",
                workforce_size=500,
                employer_type="private",
                created_at=datetime.utcnow(),
            )
            db.add(entity)
            db.flush()

        # ICC
        icc = db.scalar(select(m.IccCommittee).where(m.IccCommittee.entity_id == entity.id))
        if icc is None:
            icc = m.IccCommittee(
                entity_id=entity.id,
                constituted_on=today - timedelta(days=180),
                tenure_expires_on=today + timedelta(days=365 * 3 - 180),
            )
            db.add(icc)
            db.flush()

        # Users + role assignments
        for email, name, role, pw, is_minor, dob_off in DEMO_USERS:
            user = db.scalar(select(m.User).where(m.User.email == email))
            if user is None:
                user = m.User(
                    entity_id=entity.id,
                    subject_id=email,
                    email=email,
                    display_name=name,
                    password_hash=PH.hash(pw),
                    date_of_birth=date(today.year + dob_off, today.month, today.day),
                    is_minor=is_minor,
                    created_at=datetime.utcnow(),
                )
                db.add(user)
                db.flush()
            ra_exists = db.scalar(
                select(m.RoleAssignment).where(
                    m.RoleAssignment.user_id == user.id,
                    m.RoleAssignment.role == role,
                )
            )
            if ra_exists is None:
                db.add(m.RoleAssignment(
                    user_id=user.id,
                    role=role,
                    case_id=None,
                    valid_from=today,
                ))
                db.flush()

            # ICC members get a row in icc_members
            if role in ("presiding_officer", "icc_member"):
                if not db.scalar(select(m.IccMember).where(
                    m.IccMember.committee_id == icc.id,
                    m.IccMember.user_id == user.id,
                )):
                    member_type = (
                        "presiding_officer" if role == "presiding_officer"
                        else ("external_ngo" if email == "ngo@demo.local" else "internal_woman")
                    )
                    db.add(m.IccMember(
                        committee_id=icc.id,
                        user_id=user.id,
                        member_type=member_type,
                        nominated_on=icc.constituted_on,
                    ))

        # A couple of holidays
        for d, desc in [
            (date(today.year, 1, 26), "Republic Day"),
            (date(today.year, 8, 15), "Independence Day"),
            (date(today.year, 10, 2), "Gandhi Jayanti"),
        ]:
            if not db.get(m.HolidayCalendar, (entity.id, d)):
                db.add(m.HolidayCalendar(entity_id=entity.id, holiday_date=d, description=desc))

        db.commit()
        print(f"Seeded entity_id={entity.id}, ICC committee_id={icc.id}, users={len(DEMO_USERS)}")


if __name__ == "__main__":
    seed()
