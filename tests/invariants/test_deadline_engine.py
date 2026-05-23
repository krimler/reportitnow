"""Statutory-deadline computation (Appendix B of paper)."""
from __future__ import annotations

from datetime import date

from fastapi_app.deadline_engine import (
    business_days_after,
    calendar_days_after,
    compute_respondent_notice_deadline,
    compute_inquiry_completion_deadline,
    compute_employer_action_deadline,
    compute_appeal_deadline,
)


def test_business_days_skips_weekends(db, seed):
    # Friday + 1 business day == Monday (no holidays between).
    friday = date(2025, 11, 7)  # Friday
    monday = business_days_after(db, seed["entity"].id, friday, 1)
    assert monday.weekday() == 0


def test_business_days_skips_holidays(db, seed):
    """Add a holiday and confirm the engine respects it."""
    from fastapi_app.db import models as m
    # Make Mon 2026-01-26 (Republic Day already seeded by bootstrap? not here) into a holiday
    holiday = date(2026, 1, 26)
    db.add(m.HolidayCalendar(
        entity_id=seed["entity"].id,
        holiday_date=holiday,
        description="Republic Day",
    ))
    db.commit()
    # Friday 2026-01-23 + 1 business day should skip both Sat/Sun and Mon-26.
    fri = date(2026, 1, 23)
    result = business_days_after(db, seed["entity"].id, fri, 1)
    assert result == date(2026, 1, 27)


def test_respondent_notice_7_business_days(db, seed):
    filed_on = date(2026, 3, 2)  # Monday
    expected = business_days_after(db, seed["entity"].id, filed_on, 7)
    assert compute_respondent_notice_deadline(
        db, seed["entity"].id, filed_on
    ) == expected


def test_inquiry_completion_90_calendar_days():
    filed_on = date(2026, 3, 2)
    assert compute_inquiry_completion_deadline(filed_on) == calendar_days_after(filed_on, 90)


def test_employer_action_60_calendar_days():
    report_on = date(2026, 5, 1)
    assert compute_employer_action_deadline(report_on) == calendar_days_after(report_on, 60)


def test_appeal_90_calendar_days_from_report():
    report_on = date(2026, 5, 1)
    assert compute_appeal_deadline(report_on) == calendar_days_after(report_on, 90)
