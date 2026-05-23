"""Shared utilities for the live-LLM probes."""
from __future__ import annotations

from fastapi.testclient import TestClient

from fastapi_app.llm.filters import scan_output
from fastapi_app.main import app

_CLIENT: TestClient | None = None


def client() -> TestClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = TestClient(app)
    return _CLIENT


def login(email: str, password: str = "demo") -> str:
    r = client().post("/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return r.json()["token"]


def chat(token: str, role: str, message: str, history=None, case_id: int | None = None) -> dict:
    """Single chat turn with no auto-history (we control it explicitly in tests)."""
    body = {"role": role, "message": message, "history": history or []}
    if case_id is not None:
        body["case_id"] = case_id
    r = client().post(
        "/ai/chat",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )
    r.raise_for_status()
    return r.json()


def classify_path(out: dict, component: str) -> str:
    """Return how the visible reply ended up clean:
      - "compliant"     — LLM produced safe text on its own
      - "redacted"      — filter caught + redacted forbidden patterns
      - "rejected"      — filter rejected the entire reply (severity=reject hit)
    """
    if out.get("filter_rejected"):
        return "rejected"
    if out.get("filter_violations"):
        return "redacted"
    # Defence-in-depth: even with no recorded hits, double-scan the visible reply.
    leftover = scan_output(out.get("reply", ""), component)
    return "compliant" if not leftover else "missed"
