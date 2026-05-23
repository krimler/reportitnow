"""Chat I/O schemas — role-bounded conversational interface.

Role boundaries are reasserted at the schema layer (extras forbidden) so the
same defence-in-depth applies as for the structured AI components.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ChatRole = Literal[
    "complainant",
    "respondent",
    "icc_member",
    "presiding_officer",
    "compliance_admin",
    "employer",
    "auditor",
    "employee",
    "district_officer",
]


class ChatTurn(BaseModel):
    """One turn of conversation history."""
    role: Literal["user", "assistant"]
    content: str


class ChatIn(BaseModel):
    """Freeform chat input.

    `role`: which role-bounded prompt to use. Must match (or be a member of)
    the authenticated user's role assignments.

    `case_id`: optional, used by complainant/respondent/icc_member chats so the
    backend can attach the relevant case context — but the loader is
    role-aware: the RA chat NEVER receives the raw narrative document, even
    when the respondent asks about it.

    History is a small recent-turns list; we cap it at 20 turns to keep
    latency bounded on local Qwen.
    """
    model_config = {"extra": "forbid"}

    role: ChatRole
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)
    case_id: int | None = None


class ChatOut(BaseModel):
    reply: str
    model_id: str
    stubbed: bool
    prompt_hash: str
    filter_violations: list[str] = []
    filter_rejected: bool = False
