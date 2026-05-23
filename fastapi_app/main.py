"""ReportItNow-POC FastAPI entrypoint.

Run:
    uvicorn fastapi_app.main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

from fastapi import FastAPI

from fastapi_app.db.session import init_db
from fastapi_app.routes import ai, audit, auth, cases, chat, dashboard, documents, hearings

app = FastAPI(
    title="ReportItNow-POC",
    description=(
        "Proof-of-concept FastAPI backend for the ReportItNow design doc. "
        "State machine, audit chain, six role-scoped AI components, "
        "two-tier transparency dashboard with differential privacy."
    ),
    version="0.1.0",
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "ReportItNow-POC",
        "docs": "/docs",
        "design": "see ../design.md",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(cases.router)
app.include_router(documents.router)
app.include_router(hearings.router)
app.include_router(ai.router)
app.include_router(chat.router)
app.include_router(dashboard.router)
app.include_router(audit.router)
