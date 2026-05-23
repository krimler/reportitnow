"""Chainlit entrypoint — routes each authenticated user to a role-specific handler.

A user with multiple roles is offered a one-shot role chooser on first connect.
"""
from __future__ import annotations

import chainlit as cl

# Importing the login module registers the @cl.password_auth_callback.
from chainlit_app.auth import login  # noqa: F401
from chainlit_app.api_client import ApiClient
from chainlit_app.handlers import (
    ar_handler,
    ca_handler,
    cpa_handler,
    dashboard_handler,
    ia_handler,
    ra_handler,
)
from chainlit_app.ui.consent_modal import ask_consent


ROLE_TO_HANDLER = {
    "complainant":      (ca_handler,        "Complainant Assistant"),
    "respondent":       (ra_handler,        "Respondent Assistant"),
    "presiding_officer":(ia_handler,        "ICC / Inquiry Assistant"),
    "icc_member":       (ia_handler,        "ICC / Inquiry Assistant"),
    "compliance_admin": (cpa_handler,       "Compliance Assistant"),
    "employer":         (cpa_handler,       "Compliance Assistant"),
    "auditor":          (ar_handler,        "Audit Reviewer"),
    "employee":         (dashboard_handler, "Transparency Dashboard"),
    "district_officer": (dashboard_handler, "Transparency Dashboard"),
}


@cl.on_chat_start
async def on_chat_start():
    user = cl.user_session.get("user")
    if user is None:
        await cl.Message(content="Not authenticated. Reload.").send()
        return

    token = user.metadata.get("token")
    api = ApiClient(token=token)
    cl.user_session.set("api", api)

    me = api.whoami()
    cl.user_session.set("entity_id", me.get("entity_id"))
    roles = me.get("roles", [])
    cl.user_session.set("roles", roles)

    if not roles:
        await cl.Message(content=(
            "You have no role assignment. Ask your Compliance Admin to "
            "assign a role."
        )).send()
        return

    chosen = await _choose_role(roles)
    handler, label = ROLE_TO_HANDLER.get(chosen, (dashboard_handler, "Dashboard"))

    if chosen == "complainant":
        granted = await ask_consent()
        if not granted:
            await cl.Message(content=(
                "Without DPDP consent the AI assistant cannot run. You may "
                "still file directly with the ICC at any time under Section 9."
            )).send()
            return

    cl.user_session.set("active_role", chosen)
    cl.user_session.set("active_handler", handler)
    await cl.Message(content=f"👋 Welcome, {user.identifier}. Loading **{label}**.").send()
    await handler.start()


async def _choose_role(roles: list[str]) -> str:
    if len(roles) == 1:
        return roles[0]
    actions = [
        cl.Action(name=f"role_{r}", payload={"role": r}, label=f"Continue as {r}")
        for r in roles if r in ROLE_TO_HANDLER
    ]
    if not actions:
        return roles[0]
    res = await cl.AskActionMessage(
        content="You have multiple roles. Pick one for this session:",
        actions=actions,
    ).send()
    if res:
        return res.get("payload", {}).get("role") or roles[0]
    return roles[0]


@cl.on_message
async def on_message(message: cl.Message):
    handler = cl.user_session.get("active_handler")
    if handler is None:
        await cl.Message(content="(no handler bound — pick a role first)").send()
        return
    await handler.on_message(message)
