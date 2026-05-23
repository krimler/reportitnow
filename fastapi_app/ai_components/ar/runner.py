"""Audit Reviewer: metadata-only chain verification + anomaly detection."""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi_app.audit_chain import verify_chain
from fastapi_app.db import models as m
from fastapi_app.schemas.ai_io import (
    AnomalyFlag,
    ARInput,
    AROutput,
    ChainVerificationOut,
)


def _hash_actor(actor_id: int) -> str:
    return hashlib.sha256(f"actor:{actor_id}".encode()).hexdigest()[:16]


def _detect_anomalies(rows: list[m.AuditLog]) -> list[AnomalyFlag]:
    flags: list[AnomalyFlag] = []
    # 1. high-frequency: > 50 entries by a single actor in window
    per_actor = Counter(r.actor_user_id for r in rows)
    for actor_id, n in per_actor.items():
        if n > 50:
            flags.append(AnomalyFlag(
                kind="high_frequency_access",
                detail=f"{n} entries in window",
                actor_hash=_hash_actor(actor_id),
            ))
    # 2. after-hours non-PO (proxy: any non-state-machine component at 0-6 UTC)
    for r in rows:
        if r.component_id not in ("state_machine", "consent") and (0 <= r.timestamp.hour <= 5):
            flags.append(AnomalyFlag(
                kind="after_hours_non_po_access",
                detail=f"{r.component_id} at {r.timestamp.isoformat()}",
                actor_hash=_hash_actor(r.actor_user_id),
            ))
    # 3. bulk cross-case access: same actor touching > 5 distinct cases in window
    by_actor_cases: dict[int, set[int]] = defaultdict(set)
    for r in rows:
        if r.case_id is not None:
            by_actor_cases[r.actor_user_id].add(r.case_id)
    for actor_id, cases in by_actor_cases.items():
        if len(cases) > 5:
            flags.append(AnomalyFlag(
                kind="bulk_cross_case_access",
                detail=f"actor touched {len(cases)} cases in window",
                actor_hash=_hash_actor(actor_id),
            ))
    return flags


def run_ar(db: Session, *, payload: ARInput) -> AROutput:
    start = datetime.fromisoformat(payload.query_window.start)
    end = datetime.fromisoformat(payload.query_window.end)
    rows = list(db.execute(
        select(m.AuditLog).where(
            m.AuditLog.entity_id == payload.entity_id,
            m.AuditLog.timestamp >= start,
            m.AuditLog.timestamp < end,
        ).order_by(m.AuditLog.seq.asc())
    ).scalars())
    verification = verify_chain(db, payload.entity_id)
    return AROutput(
        chain_verification_result=ChainVerificationOut(
            is_valid=verification.is_valid,
            entries_checked=verification.entries_checked,
            first_divergence_at=verification.first_divergence_at,
        ),
        access_anomaly_flags=_detect_anomalies(rows),
    )
