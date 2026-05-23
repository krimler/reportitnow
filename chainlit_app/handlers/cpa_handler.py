"""Compliance Assistant handler — annual reports + ICC tenure tracker + Q&A."""
from __future__ import annotations

import json
from datetime import date

import chainlit as cl

from chainlit_app.handlers.chat_helper import freeform_chat, replay_history


CPA_GREETING = (
    "You're in the **Compliance Assistant** view. From here you can:\n\n"
    "  • Generate the four annual outputs (Section 21 / Rule 14 ICC report, "
    "Section 22 employer report, Board's Report extract, state portal payload).\n"
    "  • Check ICC tenure status (Section 4(3)).\n"
    "  • Review aggregated case data on the **Compliance Tier** dashboard.\n\n"
    "Use the buttons for structured runs, or just type a question — I'll "
    "answer within the CPA's role limits (aggregates only; no case content)."
)


async def start():
    actions = [
        cl.Action(name="generate_annual", payload={}, label=f"Generate annual reports ({_default_fy()})"),
        cl.Action(name="compliance_dash", payload={}, label="Open Compliance Tier dashboard"),
    ]
    await cl.Message(content=CPA_GREETING, actions=actions).send()
    role = "compliance_admin" if "compliance_admin" in (cl.user_session.get("roles") or []) else "employer"
    await replay_history(role)


def _default_fy() -> str:
    today = date.today()
    start = today.year if today.month >= 4 else today.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


@cl.action_callback("generate_annual")
async def _annual(action):
    api = cl.user_session.get("api")
    fy = _default_fy()
    out = api.ai_cpa(fy, training_sessions=4)
    text = (
        f"**Annual reports generated for FY {fy}**\n\n"
        f"```json\n{json.dumps(out, indent=2)[:2000]}\n```"
    )
    await cl.Message(content=text).send()


@cl.action_callback("compliance_dash")
async def _dash(action):
    api = cl.user_session.get("api")
    fy = _default_fy()
    out = api.compliance_tier(fy, training_sessions=4)
    text = (
        f"**Compliance Tier ({fy})** — exact figures, no DP applied\n\n"
        f"```json\n{json.dumps(out, indent=2)}\n```"
    )
    await cl.Message(content=text).send()


async def on_message(message: cl.Message):
    role = "compliance_admin" if "compliance_admin" in cl.user_session.get("roles", []) else "employer"
    await freeform_chat(role=role, message=message.content)
