"""Differential privacy engine for the Transparency Tier.

    M(D) = (clamp(c + Lap(Δc/ε)), clamp(c/n + Lap(Δr/ε)),
            clamp(τ + Lap(Δτ/ε)), k, b)

    Δc = 1, Δr ≤ 2/n, Δτ ≤ L/n with L = 150 days.

n ∈ {0,1,2} releases only k and b. Workforce < 50 is fully suppressed. The
(entity, FY, dataset_version) tuple is cached, so the ε budget is consumed
at most once per FY.
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fastapi_app.config import get_settings
from fastapi_app.db import models as m


def laplace_noise(scale: float, *, rng: secrets.SystemRandom | None = None) -> float:
    """Inverse-CDF draw from Lap(0, scale). Uses SystemRandom — not random —
    so the noise can't be reproduced by seeding."""
    rng = rng or secrets.SystemRandom()
    u = rng.random()
    eps = 1e-12
    u = min(max(u, eps), 1 - eps) - 0.5
    import math
    return -scale * math.copysign(math.log(1 - 2 * abs(u)), u)


# --- Underlying statistics --------------------------------------------------

@dataclass
class EntityMetrics:
    fy: str
    cases_filed: int                    # n
    cases_resolved: int                 # c (closed or terminated by ICC outcome)
    cases_pending_over_90d: int
    mean_filing_to_resolution_days: float | None  # τ
    training_sessions: int              # k
    icc_constituted: bool               # b
    icc_tenure_expires: str | None
    dataset_version: str                # hash of inputs so re-queries can dedupe


def _fy_window(fy: str) -> tuple[datetime, datetime]:
    """Indian fiscal year: e.g. '2025-26' is 2025-04-01 → 2026-03-31."""
    start_yr = int(fy.split("-")[0])
    return (datetime(start_yr, 4, 1), datetime(start_yr + 1, 4, 1))


def aggregate_entity_metrics(
    db: Session, entity_id: int, fy: str, *, training_sessions: int = 0
) -> EntityMetrics:
    start, end = _fy_window(fy)
    # cases filed in FY
    cases_filed_q = select(func.count(m.Case.id)).where(
        m.Case.entity_id == entity_id,
        m.Case.filed_at.is_not(None),
        m.Case.filed_at >= start,
        m.Case.filed_at < end,
    )
    cases_filed = db.scalar(cases_filed_q) or 0

    # cases resolved (closed within FY)
    cases_resolved_q = select(func.count(m.Case.id)).where(
        m.Case.entity_id == entity_id,
        m.Case.closed_at.is_not(None),
        m.Case.closed_at >= start,
        m.Case.closed_at < end,
        m.Case.state == "closed",
    )
    cases_resolved = db.scalar(cases_resolved_q) or 0

    # pending > 90 days (filed_at + 90 days < min(now, end))
    from datetime import timedelta
    now = datetime.utcnow()
    cutoff = min(now, end) - timedelta(days=90)
    pending_q = select(func.count(m.Case.id)).where(
        m.Case.entity_id == entity_id,
        m.Case.filed_at.is_not(None),
        m.Case.filed_at >= start,
        m.Case.filed_at < end,
        m.Case.filed_at <= cutoff,
        m.Case.state.notin_(("closed", "terminated")),
    )
    cases_pending_over_90d = db.scalar(pending_q) or 0

    # mean filing-to-resolution
    resolved_rows = db.execute(
        select(m.Case.filed_at, m.Case.closed_at).where(
            m.Case.entity_id == entity_id,
            m.Case.closed_at.is_not(None),
            m.Case.filed_at.is_not(None),
            m.Case.closed_at >= start,
            m.Case.closed_at < end,
            m.Case.state == "closed",
        )
    ).all()
    if resolved_rows:
        deltas = [(r.closed_at - r.filed_at).days for r in resolved_rows]
        mean_tau: float | None = sum(deltas) / len(deltas)
    else:
        mean_tau = None

    # ICC
    icc = db.execute(
        select(m.IccCommittee)
        .where(m.IccCommittee.entity_id == entity_id)
        .order_by(m.IccCommittee.constituted_on.desc())
        .limit(1)
    ).scalar()
    icc_constituted = icc is not None and not icc.defective_flag
    tenure_expiry = icc.tenure_expires_on.isoformat() if icc else None

    # Dataset version: hash of underlying counts so re-queries within FY hit cache
    payload = json.dumps({
        "n": cases_filed,
        "c": cases_resolved,
        "p": cases_pending_over_90d,
        "tau": mean_tau,
        "k": training_sessions,
        "b": icc_constituted,
    }, sort_keys=True, default=str)
    import hashlib
    dataset_version = hashlib.sha256(payload.encode()).hexdigest()[:16]

    return EntityMetrics(
        fy=fy,
        cases_filed=cases_filed,
        cases_resolved=cases_resolved,
        cases_pending_over_90d=cases_pending_over_90d,
        mean_filing_to_resolution_days=mean_tau,
        training_sessions=training_sessions,
        icc_constituted=icc_constituted,
        icc_tenure_expires=tenure_expiry,
        dataset_version=dataset_version,
    )


# --- Releases ---------------------------------------------------------------

def release_compliance_tier(metrics: EntityMetrics) -> dict[str, Any]:
    """Exact figures for ICC / employer / DO. No DP."""
    return {
        "tier": "compliance",
        "fy": metrics.fy,
        "cases_filed": metrics.cases_filed,
        "cases_disposed": metrics.cases_resolved,
        "cases_pending_over_90d": metrics.cases_pending_over_90d,
        "mean_filing_to_resolution_days": metrics.mean_filing_to_resolution_days,
        "training_sessions": metrics.training_sessions,
        "icc_constituted": metrics.icc_constituted,
        "icc_tenure_expires": metrics.icc_tenure_expires,
    }


@dataclass
class TransparencyRelease:
    output: dict[str, Any]
    epsilon_spent: float
    suppressed: bool
    cached: bool


def _round_int(x: float) -> int:
    return int(round(x))


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def release_transparency_tier(
    db: Session,
    entity: m.Entity,
    metrics: EntityMetrics,
    *,
    rng: secrets.SystemRandom | None = None,
    force_recompute: bool = False,
) -> TransparencyRelease:
    settings = get_settings()

    if (entity.workforce_size or 0) < settings.dp_workforce_floor:
        return TransparencyRelease(
            output={
                "tier": "transparency",
                "fy": metrics.fy,
                "suppressed": True,
                "reason": "workforce_size_below_threshold",
                "icc_constituted": metrics.icc_constituted,
                "your_filing_rights": {"section_9_link": "/policy/section-9-summary"},
            },
            epsilon_spent=0.0,
            suppressed=True,
            cached=False,
        )

    if not force_recompute:
        cached = db.get(
            m.DpReleaseCache,
            (entity.id, metrics.fy, metrics.dataset_version),
        )
        if cached is not None:
            return TransparencyRelease(
                output=json.loads(cached.output_json),
                epsilon_spent=cached.epsilon_spent,
                suppressed=json.loads(cached.output_json).get("suppressed", False),
                cached=True,
            )

    n = metrics.cases_filed
    c = metrics.cases_resolved

    if n == 0:
        out = {
            "tier": "transparency",
            "fy": metrics.fy,
            "no_complaints_filed": True,
            "training_sessions": metrics.training_sessions,
            "icc_constituted": metrics.icc_constituted,
            "your_filing_rights": {"section_9_link": "/policy/section-9-summary"},
        }
        _persist_cache(db, entity.id, metrics, out, 0.0)
        return TransparencyRelease(output=out, epsilon_spent=0.0, suppressed=False, cached=False)

    if n in (1, 2):
        out = {
            "tier": "transparency",
            "fy": metrics.fy,
            "small_n_suppressed": True,
            "training_sessions": metrics.training_sessions,
            "icc_constituted": metrics.icc_constituted,
            "your_filing_rights": {"section_9_link": "/policy/section-9-summary"},
        }
        _persist_cache(db, entity.id, metrics, out, 0.0)
        return TransparencyRelease(output=out, epsilon_spent=0.0, suppressed=True, cached=False)

    eps_c = settings.dp_epsilon_count
    eps_r = settings.dp_epsilon_rate
    eps_t = settings.dp_epsilon_time
    L = settings.dp_max_resolution_days

    delta_c = 1.0
    delta_r = 2.0 / max(n, 1)
    delta_t = L / max(n, 1)

    noisy_c = c + laplace_noise(delta_c / eps_c, rng=rng)
    noisy_c = _round_int(_clamp(noisy_c, 0, n))

    noisy_rate = (c / n) + laplace_noise(delta_r / eps_r, rng=rng)
    noisy_rate = _clamp(noisy_rate, 0.0, 1.0)

    tau = metrics.mean_filing_to_resolution_days
    if tau is None:
        noisy_tau: float | None = None
    else:
        noisy_tau = tau + laplace_noise(delta_t / eps_t, rng=rng)
        noisy_tau = _round_int(_clamp(noisy_tau, 0, L))

    epsilon_spent = eps_c + eps_r + (eps_t if tau is not None else 0.0)

    out = {
        "tier": "transparency",
        "fy": metrics.fy,
        "cases_resolved_approx": noisy_c,
        "resolution_rate_approx": round(noisy_rate, 4),
        "avg_filing_to_resolution_days_approx": noisy_tau,
        "training_sessions": metrics.training_sessions,
        "icc_constituted": metrics.icc_constituted,
        "your_filing_rights": {"section_9_link": "/policy/section-9-summary"},
        "_dp": {
            "epsilon_spent": epsilon_spent,
            "epsilon_breakdown": {"c": eps_c, "r": eps_r, "tau": eps_t},
            "delta_breakdown": {"c": delta_c, "r": delta_r, "tau": delta_t},
        },
    }

    _persist_cache(db, entity.id, metrics, out, epsilon_spent)
    return TransparencyRelease(
        output=out, epsilon_spent=epsilon_spent, suppressed=False, cached=False
    )


def _persist_cache(
    db: Session,
    entity_id: int,
    metrics: EntityMetrics,
    output: dict[str, Any],
    epsilon_spent: float,
) -> None:
    existing = db.get(m.DpReleaseCache, (entity_id, metrics.fy, metrics.dataset_version))
    if existing is not None:
        existing.output_json = json.dumps(output)
        existing.epsilon_spent = epsilon_spent
        existing.released_at = datetime.utcnow()
    else:
        db.add(m.DpReleaseCache(
            entity_id=entity_id,
            fy=metrics.fy,
            dataset_version=metrics.dataset_version,
            output_json=json.dumps(output),
            epsilon_spent=epsilon_spent,
            released_at=datetime.utcnow(),
        ))
    db.flush()
