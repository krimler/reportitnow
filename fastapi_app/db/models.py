"""ORM models mirroring schema.sql.

These deliberately match the SQL one-for-one — the SQL is the source of truth
because the `audit_log` table's append-only invariant lives at the SQL layer.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Entity(Base):
    __tablename__ = "entities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    jurisdiction: Mapped[str] = mapped_column(String)
    gender_scope: Mapped[str] = mapped_column(String, default="inclusive")
    workforce_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    employer_type: Mapped[str | None] = mapped_column(String, nullable=True)
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("entity_id", "subject_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("entities.id"))
    subject_id: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_minor: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class RoleAssignment(Base):
    __tablename__ = "role_assignments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String)
    case_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class IccCommittee(Base):
    __tablename__ = "icc_committees"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("entities.id"))
    constituted_on: Mapped[date] = mapped_column(Date)
    tenure_expires_on: Mapped[date] = mapped_column(Date)
    reconstitution_alert_sent_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    defective_flag: Mapped[bool] = mapped_column(Boolean, default=False)


class IccMember(Base):
    __tablename__ = "icc_members"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    committee_id: Mapped[int] = mapped_column(Integer, ForeignKey("icc_committees.id"))
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    member_type: Mapped[str] = mapped_column(String)
    nominated_on: Mapped[date] = mapped_column(Date)
    removed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    removal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("entities.id"))
    committee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String, default="draft")
    routed_to: Mapped[str] = mapped_column(String, default="icc")
    complainant_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    respondent_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    respondent_is_employer: Mapped[bool] = mapped_column(Boolean, default=False)
    cross_organisational: Mapped[bool] = mapped_column(Boolean, default=False)
    minor_complainant: Mapped[bool] = mapped_column(Boolean, default=False)
    incident_date: Mapped[date] = mapped_column(Date)
    incident_continuing: Mapped[bool] = mapped_column(Boolean, default=False)
    filed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    inquiry_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    report_due_by: Mapped[date | None] = mapped_column(Date, nullable=True)
    employer_action_due_by: Mapped[date | None] = mapped_column(Date, nullable=True)
    appeal_due_by: Mapped[date | None] = mapped_column(Date, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class CaseEvent(Base):
    __tablename__ = "case_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(Integer, ForeignKey("cases.id"))
    event_type: Mapped[str] = mapped_column(String)
    event_payload_json: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    audit_hash: Mapped[str] = mapped_column(String)


class CaseDocument(Base):
    __tablename__ = "case_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(Integer, ForeignKey("cases.id"))
    doc_type: Mapped[str] = mapped_column(String)
    content_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    content_hash: Mapped[str] = mapped_column(String)
    ai_component: Mapped[str | None] = mapped_column(String, nullable=True)
    is_draft: Mapped[bool] = mapped_column(Boolean, default=True)
    authorised_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    authorised_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    served_to_respondent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Hearing(Base):
    __tablename__ = "hearings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(Integer, ForeignKey("cases.id"))
    scheduled_for: Mapped[datetime] = mapped_column(DateTime)
    held_on: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    quorum_met: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    complainant_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    respondent_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    notice_issued_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    notice_period_days: Mapped[int] = mapped_column(Integer, default=15)
    is_ex_parte: Mapped[bool] = mapped_column(Boolean, default=False)
    consecutive_no_shows_complainant: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_no_shows_respondent: Mapped[int] = mapped_column(Integer, default=0)


class HearingAttendance(Base):
    __tablename__ = "hearing_attendance"
    hearing_id: Mapped[int] = mapped_column(Integer, ForeignKey("hearings.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    role_at_hearing: Mapped[str] = mapped_column(String)
    present: Mapped[bool] = mapped_column(Boolean)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (UniqueConstraint("entity_id", "seq"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer)
    entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("entities.id"))
    case_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    component_id: Mapped[str] = mapped_column(String)
    actor_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    input_hash: Mapped[str] = mapped_column(String)
    output_hash: Mapped[str] = mapped_column(String)
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    prev_hash: Mapped[str] = mapped_column(String)
    hash: Mapped[str] = mapped_column(String)


class HolidayCalendar(Base):
    __tablename__ = "holiday_calendar"
    entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("entities.id"), primary_key=True)
    holiday_date: Mapped[date] = mapped_column(Date, primary_key=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)


class ConsentRecord(Base):
    __tablename__ = "consent_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    purpose: Mapped[str] = mapped_column(String)
    granted: Mapped[bool] = mapped_column(Boolean)
    granted_at: Mapped[datetime] = mapped_column(DateTime)
    audit_hash: Mapped[str | None] = mapped_column(String, nullable=True)


class DpReleaseCache(Base):
    __tablename__ = "dp_release_cache"
    entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("entities.id"), primary_key=True)
    fy: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_version: Mapped[str] = mapped_column(String, primary_key=True)
    output_json: Mapped[str] = mapped_column(Text)
    epsilon_spent: Mapped[float] = mapped_column(Float)
    released_at: Mapped[datetime] = mapped_column(DateTime)


class Session_(Base):
    __tablename__ = "sessions"
    token: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class ChatHistory(Base):
    __tablename__ = "chat_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String)
    case_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    turn_role: Mapped[str] = mapped_column(String)        # 'user' | 'assistant'
    content: Mapped[str] = mapped_column(Text)
    component_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
