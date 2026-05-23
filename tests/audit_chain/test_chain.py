"""Audit-chain forward-walk consistency + tamper detection."""
from __future__ import annotations

from fastapi_app.audit_chain import append_entry, verify_chain


def test_empty_chain_is_valid(db, seed):
    result = verify_chain(db, seed["entity"].id)
    assert result.is_valid is True
    assert result.entries_checked == 0


def test_chain_grows_and_verifies(db, seed):
    eid = seed["entity"].id
    actor = seed["users"]["po"].id
    for i in range(20):
        append_entry(
            db,
            entity_id=eid,
            component_id="state_machine",
            actor_user_id=actor,
            case_id=None,
            input_payload={"i": i},
            output_payload={"ok": True},
        )
    db.commit()
    result = verify_chain(db, eid)
    assert result.is_valid is True
    assert result.entries_checked == 20


def test_chain_detects_tampering(db, seed):
    from fastapi_app.db import models as m
    eid = seed["entity"].id
    actor = seed["users"]["po"].id
    for i in range(5):
        append_entry(
            db,
            entity_id=eid,
            component_id="state_machine",
            actor_user_id=actor,
            input_payload={"i": i},
            output_payload={"ok": True},
        )
    db.commit()

    # Now tamper with entry seq=3 by overwriting the output_hash.
    entry = db.query(m.AuditLog).filter(
        m.AuditLog.entity_id == eid,
        m.AuditLog.seq == 3,
    ).one()
    entry.output_hash = "0" * 64
    db.commit()

    result = verify_chain(db, eid)
    assert result.is_valid is False
    assert result.first_divergence_at == 3


def test_chain_detects_skipped_seq(db, seed):
    """If a row's seq is overwritten to skip a slot, verification fails."""
    from fastapi_app.db import models as m
    eid = seed["entity"].id
    actor = seed["users"]["po"].id
    for i in range(3):
        append_entry(
            db,
            entity_id=eid,
            component_id="state_machine",
            actor_user_id=actor,
            input_payload={"i": i},
            output_payload={"ok": True},
        )
    db.commit()
    entry = db.query(m.AuditLog).filter(
        m.AuditLog.entity_id == eid, m.AuditLog.seq == 2,
    ).one()
    db.delete(entry)
    db.commit()
    result = verify_chain(db, eid)
    assert result.is_valid is False
    assert result.first_divergence_at == 3
