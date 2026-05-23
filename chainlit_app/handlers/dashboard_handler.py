"""Transparency Tier dashboard handler — open to all employees + Q&A."""
from __future__ import annotations

import json
from datetime import date

import chainlit as cl

from chainlit_app.handlers.chat_helper import freeform_chat, replay_history


async def start():
    api = cl.user_session.get("api")
    today = date.today()
    fy_start = today.year if today.month >= 4 else today.year - 1
    fy = f"{fy_start}-{str(fy_start + 1)[-2:]}"
    out = api.transparency(fy, training_sessions=4)
    release = out["release"]
    if release.get("suppressed"):
        body = (
            f"**Transparency Tier ({fy})**\n\n"
            f"Aggregate complaint figures are suppressed for this workplace "
            f"(reason: {release.get('reason')}).\n\n"
            f"ICC constituted: **{release.get('icc_constituted')}**.\n"
            f"Your Section 9 filing right is preserved at any time, regardless "
            f"of the dashboard view."
        )
    elif release.get("no_complaints_filed"):
        body = (
            f"**Transparency Tier ({fy})**\n\n"
            f"No complaints were filed in this fiscal year.\n\n"
            f"Workshops conducted: {release.get('training_sessions')}.\n"
            f"ICC constituted: **{release.get('icc_constituted')}**.\n"
        )
    elif release.get("small_n_suppressed"):
        body = (
            f"**Transparency Tier ({fy})**\n\n"
            f"Detailed figures are suppressed (small-n protection).\n\n"
            f"Workshops conducted: {release.get('training_sessions')}.\n"
            f"ICC constituted: **{release.get('icc_constituted')}**.\n"
        )
    else:
        body = (
            f"**Transparency Tier ({fy})** — figures are differentially "
            f"private (Laplace mechanism).\n\n"
            f"```json\n{json.dumps(release, indent=2)}\n```\n\n"
            f"ε spent: **{out['epsilon_spent']}** (cached: {out['cached']})"
        )
    await cl.Message(content=body + (
        "\n\n_Ask me anything about the dashboard or your filing rights._"
    )).send()
    role = "employee"
    roles = cl.user_session.get("roles") or []
    if "district_officer" in roles:
        role = "district_officer"
    await replay_history(role)


async def on_message(message: cl.Message):
    role = "employee"
    roles = cl.user_session.get("roles") or []
    if "district_officer" in roles:
        role = "district_officer"
    await freeform_chat(role=role, message=message.content)
