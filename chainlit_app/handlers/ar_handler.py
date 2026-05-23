"""Audit Reviewer handler — chain verification + anomaly metadata + Q&A."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import chainlit as cl

from chainlit_app.handlers.chat_helper import freeform_chat, replay_history


AR_GREETING = (
    "You're in the **Audit Reviewer** view. You have **metadata-only** "
    "access: timestamps, component IDs, hashed actor identifiers, input/"
    "output hashes. You do *not* have access to case content.\n\n"
    "Use the buttons to run chain verification or pull a metadata window. "
    "For procedural / forensic questions, type freely — the filter rejects "
    "any reply that would leak actor identities or case content."
)


async def start():
    actions = [
        cl.Action(name="verify_chain", payload={}, label="Verify audit chain"),
        cl.Action(name="export_window", payload={}, label="Export 7-day metadata window"),
    ]
    await cl.Message(content=AR_GREETING, actions=actions).send()
    await replay_history("auditor")


@cl.action_callback("verify_chain")
async def _verify(action):
    api = cl.user_session.get("api")
    out = api.audit_verify()
    text = f"Chain verification:\n```json\n{json.dumps(out, indent=2)}\n```"
    await cl.Message(content=text).send()


@cl.action_callback("export_window")
async def _export(action):
    api = cl.user_session.get("api")
    now = datetime.utcnow()
    start = now - timedelta(days=7)
    body = {
        "entity_id": cl.user_session.get("entity_id", 1),
        "query_window": {"start": start.isoformat(), "end": now.isoformat()},
    }
    out = api.ai_ar(body)
    text = f"AR output:\n```json\n{json.dumps(out, indent=2)[:2000]}\n```"
    await cl.Message(content=text).send()


async def on_message(message: cl.Message):
    await freeform_chat(role="auditor", message=message.content)
