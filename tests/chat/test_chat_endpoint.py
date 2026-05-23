"""Chat endpoint smoke + role-boundary checks (non-adversarial)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from fastapi.testclient import TestClient
from fastapi_app.audit_chain import sha256_hex
from fastapi_app.db import models as m
from fastapi_app.main import app


def _login(client: TestClient, email: str) -> str:
    r = client.post("/auth/login", json={"email": email, "password": "demo"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_demo(db_path: str | None = None) -> None:
    """Run the bootstrap so demo users exist in the file-backed DB."""
    from fastapi_app.db.bootstrap import seed
    seed()


def test_complainant_chat_works(seed, db):
    _seed_demo()
    client = TestClient(app)
    token = _login(client, "complainant@demo.local")
    r = client.post("/ai/chat", headers=_bearer(token), json={
        "role": "complainant",
        "message": "What is the deadline for filing a complaint?",
        "history": [],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["reply"], str) and body["reply"]
    assert body["stubbed"] is True
    assert body["filter_rejected"] is False


def test_chat_role_mismatch_is_forbidden(seed, db):
    _seed_demo()
    client = TestClient(app)
    # Complainant tries to use the auditor chat — must be rejected.
    token = _login(client, "complainant@demo.local")
    r = client.post("/ai/chat", headers=_bearer(token), json={
        "role": "auditor", "message": "verify the chain please",
    })
    assert r.status_code == 403


def test_history_capped_at_20(seed, db):
    """Pydantic max_length should reject oversized history."""
    _seed_demo()
    client = TestClient(app)
    token = _login(client, "complainant@demo.local")
    history = [{"role": "user", "content": f"q{i}"} for i in range(25)]
    r = client.post("/ai/chat", headers=_bearer(token), json={
        "role": "complainant", "message": "hi", "history": history,
    })
    assert r.status_code == 422  # Pydantic validation error


def test_extras_rejected_at_schema(seed, db):
    _seed_demo()
    client = TestClient(app)
    token = _login(client, "complainant@demo.local")
    r = client.post("/ai/chat", headers=_bearer(token), json={
        "role": "complainant", "message": "hi",
        "raw_narrative": "smuggle attempt",  # extra forbidden
    })
    assert r.status_code == 422


def test_chat_writes_audit_entry(seed, db):
    _seed_demo()
    client = TestClient(app)
    token = _login(client, "complainant@demo.local")
    me = client.get("/auth/whoami", headers=_bearer(token)).json()
    from fastapi_app.db.session import get_session_factory
    # Resolve the demo user's entity (bootstrap creates a separate entity from
    # the `seed` fixture's TestCo). Audit chains are per-entity, so scoping
    # the query to this entity is required.
    with get_session_factory()() as s:
        user_row = s.get(m.User, me["user_id"])
        entity_id = user_row.entity_id
        before = s.scalar(
            select(m.AuditLog)
            .where(m.AuditLog.entity_id == entity_id)
            .order_by(m.AuditLog.seq.desc()).limit(1)
        )
        before_seq = before.seq if before else 0
    r = client.post("/ai/chat", headers=_bearer(token), json={
        "role": "complainant", "message": "What is the filing deadline?",
    })
    assert r.status_code == 200, r.text
    with get_session_factory()() as s:
        latest = s.scalar(
            select(m.AuditLog)
            .where(m.AuditLog.entity_id == entity_id)
            .order_by(m.AuditLog.seq.desc()).limit(1)
        )
    assert latest is not None and latest.seq > before_seq, (
        f"expected new audit entry in entity {entity_id}, "
        f"before={before_seq}, after={latest.seq if latest else None}"
    )
    assert latest.component_id == "CA_CHAT"
