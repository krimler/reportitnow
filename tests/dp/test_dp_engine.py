"""DP engine: clamping, suppression, workforce floor, budget caching."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from fastapi_app.db import models as m
from fastapi_app.dp_engine import (
    aggregate_entity_metrics,
    release_transparency_tier,
)
from fastapi_app.dp_engine.engine import EntityMetrics


def _entity_with_size(db, seed, *, size: int) -> m.Entity:
    e = seed["entity"]
    e.workforce_size = size
    db.commit()
    return e


def _seed_cases(db, seed, *, n_filed: int, n_resolved: int):
    """Insert n cases filed inside the current FY; n_resolved of them closed."""
    eid = seed["entity"].id
    today = datetime.utcnow()
    base = datetime(today.year if today.month >= 4 else today.year - 1, 4, 15)
    cases = []
    for i in range(n_filed):
        c = m.Case(
            entity_id=eid,
            state="closed" if i < n_resolved else "inquiry",
            routed_to="icc",
            incident_date=base.date(),
            filed_at=base + timedelta(days=i),
            created_at=datetime.utcnow(),
        )
        if i < n_resolved:
            c.closed_at = base + timedelta(days=i + 30)
        cases.append(c)
        db.add(c)
    db.commit()
    return cases


def _current_fy() -> str:
    today = datetime.utcnow()
    start = today.year if today.month >= 4 else today.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def test_small_workforce_fully_suppressed(db, seed):
    e = _entity_with_size(db, seed, size=42)
    _seed_cases(db, seed, n_filed=10, n_resolved=8)
    metrics = aggregate_entity_metrics(db, e.id, _current_fy(), training_sessions=2)
    r = release_transparency_tier(db, e, metrics)
    assert r.suppressed is True
    assert r.output["suppressed"] is True
    assert r.output["reason"] == "workforce_size_below_threshold"


def test_zero_complaints_releases_only_k_b(db, seed):
    e = _entity_with_size(db, seed, size=200)
    metrics = aggregate_entity_metrics(db, e.id, _current_fy(), training_sessions=4)
    r = release_transparency_tier(db, e, metrics)
    assert r.output["no_complaints_filed"] is True
    assert "cases_resolved_approx" not in r.output
    assert r.epsilon_spent == 0.0


def test_small_n_suppressed(db, seed):
    e = _entity_with_size(db, seed, size=200)
    _seed_cases(db, seed, n_filed=2, n_resolved=1)
    metrics = aggregate_entity_metrics(db, e.id, _current_fy(), training_sessions=4)
    r = release_transparency_tier(db, e, metrics)
    assert r.output["small_n_suppressed"] is True
    assert "cases_resolved_approx" not in r.output
    assert r.epsilon_spent == 0.0


def test_release_clamped_to_valid_ranges(db, seed):
    e = _entity_with_size(db, seed, size=200)
    _seed_cases(db, seed, n_filed=10, n_resolved=8)
    metrics = aggregate_entity_metrics(db, e.id, _current_fy(), training_sessions=4)
    for _ in range(100):
        r = release_transparency_tier(db, e, metrics, force_recompute=True)
        out = r.output
        assert 0 <= out["cases_resolved_approx"] <= metrics.cases_filed
        assert 0.0 <= out["resolution_rate_approx"] <= 1.0
        assert out["avg_filing_to_resolution_days_approx"] is None or (
            0 <= out["avg_filing_to_resolution_days_approx"] <= 150
        )


def test_budget_cached_within_fy(db, seed):
    e = _entity_with_size(db, seed, size=200)
    _seed_cases(db, seed, n_filed=10, n_resolved=8)
    metrics = aggregate_entity_metrics(db, e.id, _current_fy(), training_sessions=4)
    r1 = release_transparency_tier(db, e, metrics)
    assert r1.cached is False
    r2 = release_transparency_tier(db, e, metrics)
    assert r2.cached is True
    assert r1.output == r2.output


def test_epsilon_total_within_budget(db, seed):
    e = _entity_with_size(db, seed, size=200)
    _seed_cases(db, seed, n_filed=10, n_resolved=8)
    metrics = aggregate_entity_metrics(db, e.id, _current_fy(), training_sessions=4)
    r = release_transparency_tier(db, e, metrics, force_recompute=True)
    assert r.epsilon_spent <= 1.5 + 1e-6  # design §7.2 POC default total
