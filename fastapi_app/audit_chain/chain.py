"""HMAC-chained audit log.

H_i = HMAC_SHA256(k, prev_hash || input_hash || output_hash || timestamp
                    || component_id || actor_user_id)
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi_app.config import get_settings
from fastapi_app.db import models as m

GENESIS_HASH = "0" * 64


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def hash_payload(payload: Any) -> str:
    if payload is None:
        return sha256_hex(b"")
    if isinstance(payload, (bytes, bytearray)):
        return sha256_hex(bytes(payload))
    if isinstance(payload, str):
        return sha256_hex(payload)
    return sha256_hex(json.dumps(payload, sort_keys=True, default=str))


def _entry_hash(
    *,
    key: bytes,
    prev_hash: str,
    input_hash: str,
    output_hash: str,
    timestamp: datetime,
    component_id: str,
    actor_user_id: int,
) -> str:
    msg = "|".join([
        prev_hash,
        input_hash,
        output_hash,
        timestamp.isoformat(timespec="microseconds"),
        component_id,
        str(actor_user_id),
    ]).encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _next_seq(db: Session, entity_id: int) -> tuple[int, str]:
    """Return (next_seq, prev_hash) for this entity's chain."""
    row = db.execute(
        select(m.AuditLog.seq, m.AuditLog.hash)
        .where(m.AuditLog.entity_id == entity_id)
        .order_by(m.AuditLog.seq.desc())
        .limit(1)
    ).first()
    if row is None:
        return 1, GENESIS_HASH
    return row.seq + 1, row.hash


def append_entry(
    db: Session,
    *,
    entity_id: int,
    component_id: str,
    actor_user_id: int,
    input_payload: Any,
    output_payload: Any,
    case_id: int | None = None,
    timestamp: datetime | None = None,
) -> m.AuditLog:
    # Caller commits; we just append.
    settings = get_settings()
    key = settings.audit_chain_hmac_key.encode("utf-8")
    ts = timestamp or datetime.utcnow()
    input_hash = hash_payload(input_payload)
    output_hash = hash_payload(output_payload)
    seq, prev_hash = _next_seq(db, entity_id)
    h = _entry_hash(
        key=key,
        prev_hash=prev_hash,
        input_hash=input_hash,
        output_hash=output_hash,
        timestamp=ts,
        component_id=component_id,
        actor_user_id=actor_user_id,
    )
    entry = m.AuditLog(
        seq=seq,
        entity_id=entity_id,
        case_id=case_id,
        component_id=component_id,
        actor_user_id=actor_user_id,
        input_hash=input_hash,
        output_hash=output_hash,
        timestamp=ts,
        prev_hash=prev_hash,
        hash=h,
    )
    db.add(entry)
    db.flush()
    return entry


@dataclass
class ChainVerification:
    is_valid: bool
    entries_checked: int
    first_divergence_at: int | None
    divergence_component: str | None
    divergence_timestamp: datetime | None


def verify_chain(db: Session, entity_id: int) -> ChainVerification:
    """Walk the chain from seq=1 forward, recomputing each hash."""
    settings = get_settings()
    key = settings.audit_chain_hmac_key.encode("utf-8")
    rows = list(db.execute(
        select(m.AuditLog)
        .where(m.AuditLog.entity_id == entity_id)
        .order_by(m.AuditLog.seq.asc())
    ).scalars())

    prev_hash = GENESIS_HASH
    expected_seq = 1
    for entry in rows:
        if entry.seq != expected_seq:
            return ChainVerification(
                is_valid=False,
                entries_checked=expected_seq - 1,
                first_divergence_at=entry.seq,
                divergence_component=entry.component_id,
                divergence_timestamp=entry.timestamp,
            )
        if entry.prev_hash != prev_hash:
            return ChainVerification(
                is_valid=False,
                entries_checked=expected_seq - 1,
                first_divergence_at=entry.seq,
                divergence_component=entry.component_id,
                divergence_timestamp=entry.timestamp,
            )
        recomputed = _entry_hash(
            key=key,
            prev_hash=entry.prev_hash,
            input_hash=entry.input_hash,
            output_hash=entry.output_hash,
            timestamp=entry.timestamp,
            component_id=entry.component_id,
            actor_user_id=entry.actor_user_id,
        )
        if recomputed != entry.hash:
            return ChainVerification(
                is_valid=False,
                entries_checked=expected_seq - 1,
                first_divergence_at=entry.seq,
                divergence_component=entry.component_id,
                divergence_timestamp=entry.timestamp,
            )
        prev_hash = entry.hash
        expected_seq += 1
    return ChainVerification(
        is_valid=True,
        entries_checked=len(rows),
        first_divergence_at=None,
        divergence_component=None,
        divergence_timestamp=None,
    )
