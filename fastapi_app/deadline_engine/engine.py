"""Statutory deadlines. The Act mixes business and calendar days; we follow
the Act, not a uniform convention.

    Respondent notice  Rule 7(2)        7 business days from filing
    Respondent reply   Rule 7(4)       10 business days from notice
    Inquiry complete   Section 11(4)   90 calendar days from filing
    Report             Section 13(1)   10 calendar days after inquiry
    Employer action    Section 13(4)   60 calendar days after report
    Appeal             Section 18(2)   90 calendar days after report
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi_app.db import models as m


def _is_business_day(d: date, holidays: set[date]) -> bool:
    return d.weekday() < 5 and d not in holidays


def _load_holidays(db: Session, entity_id: int) -> set[date]:
    rows = db.execute(
        select(m.HolidayCalendar.holiday_date).where(m.HolidayCalendar.entity_id == entity_id)
    ).all()
    return {row[0] for row in rows}


def business_days_after(
    db: Session, entity_id: int, start_date: date, n_days: int
) -> date:
    """Return the date that is `n_days` business days after `start_date`.

    Convention: `n_days=0` returns the next business day on or after start_date
    if start_date itself is not a business day, otherwise start_date. `n_days>0`
    advances that many business days.
    """
    if n_days < 0:
        raise ValueError("n_days must be >= 0")
    holidays = _load_holidays(db, entity_id)
    cur = start_date
    while not _is_business_day(cur, holidays):
        cur = cur + timedelta(days=1)
    remaining = n_days
    while remaining > 0:
        cur = cur + timedelta(days=1)
        if _is_business_day(cur, holidays):
            remaining -= 1
    return cur


def calendar_days_after(start_date: date, n_days: int) -> date:
    if n_days < 0:
        raise ValueError("n_days must be >= 0")
    return start_date + timedelta(days=n_days)


# --- Statute-specific helpers -------------------------------------------------

def compute_respondent_notice_deadline(db: Session, entity_id: int, filed_on: date) -> date:
    """Rule 7(2): 7 business days from filing."""
    return business_days_after(db, entity_id, filed_on, 7)


def compute_respondent_reply_deadline(db: Session, entity_id: int, notice_received_on: date) -> date:
    """Rule 7(4): 10 business days from notice receipt."""
    return business_days_after(db, entity_id, notice_received_on, 10)


def compute_inquiry_completion_deadline(filed_on: date) -> date:
    """Section 11(4): 90 calendar days from filing."""
    return calendar_days_after(filed_on, 90)


def compute_report_deadline(inquiry_completed_on: date) -> date:
    """Section 13(1): 10 calendar days after inquiry completion."""
    return calendar_days_after(inquiry_completed_on, 10)


def compute_employer_action_deadline(report_submitted_on: date) -> date:
    """Section 13(4): 60 calendar days after report."""
    return calendar_days_after(report_submitted_on, 60)


def compute_appeal_deadline(report_submitted_on: date) -> date:
    """Section 18(2): 90 calendar days after report."""
    return calendar_days_after(report_submitted_on, 90)


@dataclass
class DeadlineSet:
    respondent_notice_by: date | None
    respondent_reply_by: date | None
    inquiry_complete_by: date | None
    report_by: date | None
    employer_action_by: date | None
    appeal_by: date | None


def compute_all_deadlines(
    db: Session,
    entity_id: int,
    *,
    filed_on: date | None = None,
    notice_received_on: date | None = None,
    inquiry_completed_on: date | None = None,
    report_submitted_on: date | None = None,
) -> DeadlineSet:
    return DeadlineSet(
        respondent_notice_by=(
            compute_respondent_notice_deadline(db, entity_id, filed_on) if filed_on else None
        ),
        respondent_reply_by=(
            compute_respondent_reply_deadline(db, entity_id, notice_received_on)
            if notice_received_on else None
        ),
        inquiry_complete_by=(
            compute_inquiry_completion_deadline(filed_on) if filed_on else None
        ),
        report_by=(
            compute_report_deadline(inquiry_completed_on) if inquiry_completed_on else None
        ),
        employer_action_by=(
            compute_employer_action_deadline(report_submitted_on) if report_submitted_on else None
        ),
        appeal_by=(
            compute_appeal_deadline(report_submitted_on) if report_submitted_on else None
        ),
    )
