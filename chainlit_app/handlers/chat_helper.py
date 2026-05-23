"""Chat helper used by every per-role Chainlit handler.

History lives in the FastAPI side; we just call /ai/chat and replay the
saved turns on session start.
"""
from __future__ import annotations

from typing import Any

import chainlit as cl


async def replay_history(role: str, *, limit: int = 30) -> int:
    api = cl.user_session.get("api")
    if api is None:
        return 0
    try:
        hist = api.chat_history(role, limit=limit)
    except Exception:
        return 0
    turns = hist.get("turns", [])
    if not turns:
        return 0
    await cl.Message(content=(
        f"_↻ Resumed from previous session — {len(turns)} prior turn(s) shown "
        f"below. Type `/clear` to start a fresh conversation._"
    )).send()
    for t in turns:
        author = "You" if t["turn_role"] == "user" else "Assistant"
        await cl.Message(content=t["content"], author=author).send()
    return len(turns)


async def freeform_chat(
    *,
    role: str,
    message: str,
    case_id: int | None = None,
) -> dict[str, Any]:
    # /clear wipes history for this (user, role) on the server.
    api = cl.user_session.get("api")
    if api is None:
        await cl.Message(content="(not logged in)").send()
        return {}

    if message.strip() == "/clear":
        try:
            res = api.clear_chat_history(role)
            await cl.Message(content=(
                f"_Cleared **{res.get('deleted', 0)}** prior turn(s) for this role._"
            )).send()
        except Exception as e:  # noqa: BLE001
            await cl.Message(content=f"Couldn't clear history: {e}").send()
        return {"cleared": True}

    try:
        out = api.chat(role=role, message=message, history=None, case_id=case_id)
    except Exception as e:  # noqa: BLE001 — surface backend errors verbatim
        await cl.Message(content=f"Backend error: {e}").send()
        return {}

    reply = out["reply"]
    suffix = ""
    if out.get("filter_violations"):
        if out.get("filter_rejected"):
            suffix = (
                "\n\n*— output rejected by role-boundary filter; this exchange "
                "is recorded in the audit chain.*"
            )
        else:
            suffix = (
                "\n\n*— some content was redacted by the role-boundary filter "
                f"({len(out['filter_violations'])} hit(s)).*"
            )
    badge = "\n\n_LLM (stubbed)_" if out.get("stubbed") else ""
    await cl.Message(content=reply + suffix + badge).send()
    return out
