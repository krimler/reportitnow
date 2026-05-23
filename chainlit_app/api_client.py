"""Thin HTTP client wrapping the FastAPI backend for Chainlit handlers."""
from __future__ import annotations

import os
from typing import Any

import httpx

BASE_URL = os.environ.get("FASTAPI_BASE_URL", "http://127.0.0.1:8000")


class ApiClient:
    def __init__(self, token: str | None = None):
        self.token = token
        self.client = httpx.Client(base_url=BASE_URL, timeout=60.0)

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def login(self, email: str, password: str) -> dict[str, Any]:
        r = self.client.post(
            "/auth/login", json={"email": email, "password": password},
        )
        r.raise_for_status()
        data = r.json()
        self.token = data["token"]
        return data

    def whoami(self) -> dict[str, Any]:
        r = self.client.get("/auth/whoami", headers=self._headers())
        r.raise_for_status()
        return r.json()

    def create_case(self, body: dict[str, Any]) -> dict[str, Any]:
        r = self.client.post("/cases", json=body, headers=self._headers())
        r.raise_for_status()
        return r.json()

    def get_case(self, case_id: int) -> dict[str, Any]:
        r = self.client.get(f"/cases/{case_id}", headers=self._headers())
        r.raise_for_status()
        return r.json()

    def transition_case(self, case_id: int, body: dict[str, Any]) -> dict[str, Any]:
        r = self.client.post(
            f"/cases/{case_id}/transition", json=body, headers=self._headers()
        )
        r.raise_for_status()
        return r.json()

    def list_documents(self, case_id: int) -> list[dict[str, Any]]:
        r = self.client.get(f"/documents/case/{case_id}", headers=self._headers())
        r.raise_for_status()
        return r.json()

    def authorise_document(self, doc_id: int) -> dict[str, Any]:
        r = self.client.post(
            f"/documents/{doc_id}/authorise", headers=self._headers()
        )
        r.raise_for_status()
        return r.json()

    def serve_complaint(self, case_id: int) -> dict[str, Any]:
        r = self.client.post(
            f"/cases/{case_id}/serve_complaint_to_respondent", headers=self._headers()
        )
        r.raise_for_status()
        return r.json()

    def ai_ca(self, body: dict[str, Any]) -> dict[str, Any]:
        r = self.client.post("/ai/CA/structure_narrative", json=body, headers=self._headers())
        r.raise_for_status()
        return r.json()

    def ai_ra(self, body: dict[str, Any]) -> dict[str, Any]:
        r = self.client.post("/ai/RA/draft_response", json=body, headers=self._headers())
        r.raise_for_status()
        return r.json()

    def ai_ia_p1(self, body: dict[str, Any]) -> dict[str, Any]:
        r = self.client.post("/ai/IA/phase1_extract", json=body, headers=self._headers())
        r.raise_for_status()
        return r.json()

    def ai_ia_p2(self, body: dict[str, Any]) -> dict[str, Any]:
        r = self.client.post("/ai/IA/phase2_synthesise", json=body, headers=self._headers())
        r.raise_for_status()
        return r.json()

    def ai_rpa(self, body: dict[str, Any]) -> dict[str, Any]:
        r = self.client.post("/ai/RPA/draft_report", json=body, headers=self._headers())
        r.raise_for_status()
        return r.json()

    def ai_cpa(self, fiscal_year: str, training_sessions: int = 0) -> dict[str, Any]:
        r = self.client.post(
            "/ai/CPA/build_annual_reports",
            params={"fiscal_year": fiscal_year, "training_sessions": training_sessions},
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    def ai_ar(self, body: dict[str, Any]) -> dict[str, Any]:
        r = self.client.post("/ai/AR/verify", json=body, headers=self._headers())
        r.raise_for_status()
        return r.json()

    def transparency(self, fiscal_year: str, training_sessions: int = 0) -> dict[str, Any]:
        r = self.client.get(
            "/dashboard/transparency",
            params={"fiscal_year": fiscal_year, "training_sessions": training_sessions},
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    def compliance_tier(self, fiscal_year: str, training_sessions: int = 0) -> dict[str, Any]:
        r = self.client.get(
            "/dashboard/compliance",
            params={"fiscal_year": fiscal_year, "training_sessions": training_sessions},
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    def audit_verify(self) -> dict[str, Any]:
        r = self.client.get("/audit/verify", headers=self._headers())
        r.raise_for_status()
        return r.json()

    def chat(
        self,
        *,
        role: str,
        message: str,
        history: list[dict[str, str]] | None = None,
        case_id: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "role": role,
            "message": message,
            "history": history or [],
        }
        if case_id is not None:
            body["case_id"] = case_id
        r = self.client.post("/ai/chat", json=body, headers=self._headers())
        r.raise_for_status()
        return r.json()

    def chat_history(self, role: str, limit: int = 50) -> dict[str, Any]:
        r = self.client.get(
            "/ai/chat/history",
            params={"role": role, "limit": limit},
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    def clear_chat_history(self, role: str) -> dict[str, Any]:
        r = self.client.delete(
            "/ai/chat/history",
            params={"role": role},
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()
